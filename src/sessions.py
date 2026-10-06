from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.models import ActiveSession


def is_pid_running(pid: int) -> bool:
    """Cross-platform check if a process ID is currently alive."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return False
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError as err:
            import errno
            return err.errno == errno.EPERM


def is_file_locked(path: Path) -> bool:
    """Cross-platform check if a file is held locked exclusively by another process."""
    if not path.exists():
        return False
    if sys.platform == "win32":
        try:
            # On Windows, opening with write sharing blocked will raise PermissionError if held
            with open(path, "r+b"):
                return False
        except (PermissionError, OSError):
            return True
        except Exception:
            return False
    else:
        try:
            import fcntl
            fd = os.open(str(path), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            except (BlockingIOError, OSError):
                return True
            finally:
                os.close(fd)
        except Exception:
            return False


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1].strip()
    return s


class SessionTracker:
    """Discovers currently running tasks and sessions across Claude Code, Codex, and Antigravity.

    Zero network, ultra-fast local checks (< 3ms) with thread-safe caching.
    """

    def __init__(self, cache_ttl: float = 3.0) -> None:
        self.cache_ttl = cache_ttl
        self._last_scan: float = 0.0
        self._cached: List[ActiveSession] = []
        self._lock = threading.Lock()

    # --- Candidate directories ---
    @staticmethod
    def _claude_dirs() -> List[Path]:
        dirs: List[Path] = []
        custom = os.environ.get("CLAUDE_CONFIG_DIR")
        if custom:
            dirs.append(Path(custom))
        dirs.append(Path.home() / ".claude")
        if sys.platform == "win32":
            user_profile = os.environ.get("USERPROFILE")
            if user_profile:
                dirs.append(Path(user_profile) / ".claude")
            for root in (r"\\wsl.localhost", r"\\wsl$"):
                try:
                    for distro in Path(root).iterdir():
                        for h in (distro / "home").iterdir():
                            d = h / ".claude"
                            if d.exists() and d not in dirs:
                                dirs.append(d)
                except Exception:
                    pass
        return dirs

    @staticmethod
    def _codex_dirs() -> List[Path]:
        dirs: List[Path] = []
        custom = os.environ.get("CODEX_HOME")
        if custom:
            dirs.append(Path(custom))
        dirs.append(Path.home() / ".codex")
        if sys.platform == "win32":
            user_profile = os.environ.get("USERPROFILE")
            if user_profile:
                dirs.append(Path(user_profile) / ".codex")
        return dirs

    @staticmethod
    def _antigravity_dirs() -> List[Path]:
        dirs: List[Path] = [
            Path.home() / ".gemini" / "antigravity-cli",
        ]
        if sys.platform == "win32":
            user_profile = os.environ.get("USERPROFILE")
            if user_profile:
                dirs.append(Path(user_profile) / ".gemini" / "antigravity-cli")
        return dirs

    # --- Scanner methods ---
    def scan_claude(self) -> List[ActiveSession]:
        sessions: List[ActiveSession] = []
        seen_pids = set()

        for c_dir in self._claude_dirs():
            sessions_dir = c_dir / "sessions"
            if not sessions_dir.exists():
                continue
            for sf in sessions_dir.glob("*.json"):
                try:
                    d = json.loads(sf.read_text(encoding="utf-8", errors="replace"))
                    pid = d.get("pid")
                    if not pid or pid in seen_pids:
                        continue
                    if not is_pid_running(int(pid)):
                        continue
                    seen_pids.add(pid)

                    s_id = d.get("sessionId") or sf.stem
                    raw_title = d.get("name") or "Claude Session"
                    cwd = d.get("cwd")
                    status = d.get("status", "running")
                    started = float(d.get("startedAt", 0)) / 1000.0 if d.get("startedAt") else None
                    updated = float(d.get("updatedAt", 0)) / 1000.0 if d.get("updatedAt") else None

                    detail = None
                    if cwd:
                        folder = Path(cwd).name
                        detail = f"{folder} ({status})" if folder else f"status: {status}"
                    else:
                        detail = f"status: {status}"

                    # Detect active model from recent transcript
                    model_id = None
                    t_matches = list(c_dir.glob(f"projects/*/{s_id}.jsonl"))
                    if t_matches:
                        try:
                            with open(t_matches[0], "rb") as tf:
                                sz = t_matches[0].stat().st_size
                                tf.seek(max(0, sz - 8192))
                                t_lines = tf.read().decode("utf-8", errors="ignore").splitlines()
                            for tl in reversed(t_lines):
                                if "model" in tl:
                                    try:
                                        t_obj = json.loads(tl)
                                        msg = t_obj.get("message")
                                        if isinstance(msg, dict) and msg.get("model"):
                                            model_id = msg["model"]
                                            break
                                        if t_obj.get("model") and isinstance(t_obj["model"], str):
                                            model_id = t_obj["model"]
                                            break
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                    sessions.append(
                        ActiveSession(
                            provider_id="claude",
                            session_id=str(s_id),
                            title=_clean_str(raw_title),
                            status=status,
                            detail=detail,
                            cwd=cwd,
                            started_at=started,
                            updated_at=updated,
                            pid=int(pid),
                            model_id=model_id,
                        )
                    )
                except Exception:
                    continue
        return sessions

    def scan_codex(self) -> List[ActiveSession]:
        sessions: List[ActiveSession] = []
        seen_ids = set()

        for cx_dir in self._codex_dirs():
            locks_dir = cx_dir / "thread-writer-locks"
            if not locks_dir.exists():
                continue

            # Load thread names from session_index.jsonl
            idx_map: Dict[str, str] = {}
            idx_file = cx_dir / "session_index.jsonl"
            if idx_file.exists():
                try:
                    for line in idx_file.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.strip():
                            try:
                                item = json.loads(line)
                                if "id" in item and "thread_name" in item:
                                    idx_map[item["id"]] = item["thread_name"]
                            except Exception:
                                pass
                except Exception:
                    pass

            for lf in locks_dir.glob("*.lock"):
                if lf.name.startswith("."):
                    continue
                if not is_file_locked(lf):
                    continue

                tid = lf.stem
                if tid in seen_ids:
                    continue

                # Verify if this thread is actually actively executing right now:
                # 1. Rollout file exists and was modified within the last 180 seconds.
                # 2. Last event is not a task_complete / turn_complete / session_complete.
                now = time.time()
                rfiles = list(cx_dir.glob(f"sessions/**/rollout*{tid}*.jsonl"))
                if rfiles:
                    rf = rfiles[0]
                    try:
                        mtime = rf.stat().st_mtime
                        if now - mtime > 180:
                            continue  # Lock held by idle daemon or app-server, not active work

                        # Check tail for completion event
                        is_active = True
                        with open(rf, "rb") as f:
                            sz = rf.stat().st_size
                            f.seek(max(0, sz - 8192))
                            lines = f.read().decode("utf-8", errors="ignore").splitlines()
                        for l in reversed(lines):
                            if not l.strip():
                                continue
                            try:
                                ev = json.loads(l)
                                ev_type = ev.get("type")
                                payload = ev.get("payload") or {}
                                p_type = payload.get("type")
                                if ev_type in ("task_complete", "turn_complete", "session_complete") or p_type in ("task_complete", "turn_complete"):
                                    is_active = False
                                    break
                            except Exception:
                                continue
                        if not is_active:
                            continue
                    except Exception:
                        pass
                elif (cx_dir / "sessions").exists():
                    # If sessions dir exists but no rollout for this thread, skip
                    continue

                seen_ids.add(tid)

                raw_title = idx_map.get(tid, f"Thread {tid[:8]}")
                # Clean up overly long companion prefixes if present
                clean_title = raw_title
                if "Codex Companion Task: Repository " in clean_title:
                    clean_title = clean_title.replace("Codex Companion Task: Repository ", "").strip()
                    # e.g., "/home/user/project (Linux/WSL, bash), branch main..." -> "project: Task"
                    first_part = clean_title.split(",")[0].split("(")[0].strip()
                    if "/" in first_part:
                        first_part = Path(first_part).name
                    clean_title = f"{first_part}: Active Task"

                # Check recent companion log in /tmp or temp dir for latest action notation
                detail = None
                tmp_dirs = [Path("/tmp")]
                if sys.platform == "win32":
                    t_env = os.environ.get("TEMP")
                    if t_env:
                        tmp_dirs.append(Path(t_env))

                for tdir in tmp_dirs:
                    if not tdir.exists():
                        continue
                    try:
                        for log_file in tdir.glob("**/codex-*.log"):
                            if log_file.is_file():
                                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                                for line in reversed(lines[-8:]):
                                    line_clean = line.strip()
                                    if "[codex] Running command:" in line_clean:
                                        cmd = line_clean.split("Running command:")[-1].strip()
                                        detail = f"Running: {cmd[:42]}..."
                                        break
                                    elif "[codex] Assistant message" in line_clean:
                                        detail = "Assistant reasoning..."
                                        break
                            if detail:
                                break
                    except Exception:
                        pass
                    if detail:
                        break

                # Extract active model from rollout or companion log
                model_id = None
                if rfiles:
                    try:
                        with open(rfiles[0], "rb") as rf_obj:
                            # Read first 8KB for session_meta or initial turns
                            head_lines = rf_obj.read(8192).decode("utf-8", errors="ignore").splitlines()
                        for hl in head_lines:
                            if "model" in hl:
                                try:
                                    h_obj = json.loads(hl)
                                    payload = h_obj.get("payload") or {}
                                    if isinstance(payload, dict) and payload.get("model"):
                                        model_id = payload["model"]
                                        break
                                except Exception:
                                    pass
                    except Exception:
                        pass

                sessions.append(
                    ActiveSession(
                        provider_id="codex",
                        session_id=tid,
                        title=_clean_str(clean_title),
                        status="running",
                        detail=detail or "Task running",
                        model_id=model_id,
                    )
                )

        return sessions

    def scan_antigravity(self) -> List[ActiveSession]:
        sessions: List[ActiveSession] = []
        seen_ids = set()

        for ag_dir in self._antigravity_dirs():
            presence_dir = ag_dir / "presence"
            if not presence_dir.exists():
                continue

            # Query conversation summaries db for titles & status
            conv_map: Dict[str, Tuple[str, str, Optional[str]]] = {}
            db_file = ag_dir / "conversation_summaries.db"
            if db_file.exists():
                try:
                    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=1.0)
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT conversation_id, title, status, last_modified_time "
                        "FROM conversation_summaries WHERE killed = 0"
                    )
                    for row in cursor.fetchall():
                        conv_map[row[0]] = (row[1] or "Antigravity Task", row[2] or "running", row[3])
                    conn.close()
                except Exception:
                    pass

            # Extract configured model from settings.json
            ag_model = None
            settings_file = ag_dir / "settings.json"
            if settings_file.exists():
                try:
                    s_data = json.loads(settings_file.read_text(encoding="utf-8", errors="replace"))
                    ag_model = s_data.get("model")
                except Exception:
                    pass

            for lf in presence_dir.glob("*.lock"):
                if lf.name.startswith("."):
                    continue
                if not is_file_locked(lf):
                    continue

                cid = lf.stem
                if cid in seen_ids:
                    continue

                # Check transcript log for latest activity / tool action
                detail = None
                tfile = ag_dir / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
                if tfile.exists():
                    try:
                        # If transcript hasn't been touched in over 1 hour, consider idle
                        if time.time() - tfile.stat().st_mtime > 3600:
                            continue

                        with open(tfile, "rb") as f:
                            sz = tfile.stat().st_size
                            f.seek(max(0, sz - 8192))
                            lines = f.read().decode("utf-8", errors="ignore").splitlines()
                            for l in reversed(lines):
                                if not l.strip():
                                    continue
                                try:
                                    entry = json.loads(l)
                                    tool_calls = entry.get("tool_calls") or []
                                    if tool_calls:
                                        tc = tool_calls[-1]
                                        args = tc.get("args")
                                        if isinstance(args, str):
                                            try:
                                                args = json.loads(args)
                                            except Exception:
                                                pass
                                        if isinstance(args, dict):
                                            act = args.get("toolAction") or args.get("toolSummary")
                                            if act:
                                                detail = _clean_str(act)
                                                break
                                        detail = tc.get("name")
                                        break
                                    elif entry.get("thinking"):
                                        detail = "Thinking..."
                                        break
                                except Exception:
                                    continue
                    except Exception:
                        pass

                seen_ids.add(cid)
                info = conv_map.get(cid, ("Antigravity Session", "running", None))
                title = info[0]
                status_raw = info[1]
                status = "running" if "RUNNING" in status_raw else status_raw

                sessions.append(
                    ActiveSession(
                        provider_id="antigravity",
                        session_id=cid,
                        title=_clean_str(title),
                        status=status,
                        detail=detail or "Session active",
                        model_id=ag_model or "gemini-3.1-pro",
                    )
                )

        return sessions

    def scan_opencode(self) -> List[ActiveSession]:
        """Discover running or recently active OpenCode tasks and sessions."""
        from src.collectors.opencode import find_opencode_db

        sessions: List[ActiveSession] = []
        seen_titles = set()
        seen_pids = set()

        # 1. Directly scan active running OpenCode processes across Linux/WSL/Windows
        if sys.platform != "win32":
            import glob
            for p in glob.glob("/proc/[0-9]*/cmdline"):
                try:
                    with open(p, "rb") as f:
                        raw = f.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
                    # Match actual opencode process (exclude python scripts, wrappers, grep)
                    if "opencode" in raw.lower() and "python" not in raw.lower() and "grep" not in raw.lower() and not raw.startswith("/bin/bash"):
                        tokens = raw.split()
                        if any("opencode" in t.lower() for t in tokens) and "run" in tokens:
                            pid = int(p.split("/")[2])
                            if pid in seen_pids:
                                continue
                            seen_pids.add(pid)

                            title = None
                            model = None
                            for i, t in enumerate(tokens):
                                if t == "--title" and i + 1 < len(tokens):
                                    words = []
                                    for w in tokens[i + 1:]:
                                        if w.startswith("-") or w.startswith("Repository"):
                                            break
                                        words.append(w.strip("\"'"))
                                    title = " ".join(words)
                                if (t == "-m" or t == "--model") and i + 1 < len(tokens):
                                    model = tokens[i + 1]

                            # Determine start time
                            ctime = None
                            try:
                                ctime = os.path.getctime(f"/proc/{pid}")
                            except Exception:
                                pass

                            clean_title = _clean_str(title or "OpenCode Task")
                            seen_titles.add(clean_title)
                            sessions.append(
                                ActiveSession(
                                    provider_id="opencode",
                                    session_id=f"proc_{pid}",
                                    title=clean_title,
                                    status="running",
                                    detail=f"model: {model}" if model else "running",
                                    started_at=ctime,
                                    pid=pid,
                                    model_id=model,
                                )
                            )
                except Exception:
                    pass

        # 2. Check OpenCode database for active sessions not yet caught
        db_path = find_opencode_db()
        if db_path and db_path.exists():
            temp_dir = tempfile.gettempdir()
            temp_db = Path(temp_dir) / f"opencode_sess_{os.getpid()}.db"
            try:
                shutil.copy2(db_path, temp_db)
                wal_file = db_path.parent / (db_path.name + "-wal")
                if wal_file.exists():
                    shutil.copy2(wal_file, Path(temp_dir) / f"{temp_db.name}-wal")
            except Exception:
                temp_db = db_path

            try:
                conn = sqlite3.connect(str(temp_db), timeout=2.0)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, title, directory, model, time_created, time_updated, time_idle, time_archived, time_suspended, idle_outcome "
                    "FROM session_v2 ORDER BY time_updated DESC LIMIT 5;"
                )
                now_ms = time.time() * 1000.0
                for row in cursor.fetchall():
                    sid, title, directory, model_raw, tc, tu, ti, ta, ts, io = row
                    clean_t = _clean_str(title or "OpenCode Task")
                    if clean_t in seen_titles:
                        # Match with process found above to add directory/model details
                        for existing in sessions:
                            if existing.title == clean_t:
                                proj = Path(directory).name if directory else ""
                                m_name = ""
                                if model_raw:
                                    try:
                                        m_name = json.loads(model_raw).get("id", "")
                                    except Exception:
                                        pass
                                if m_name and not existing.model_id:
                                    existing.model_id = m_name
                                parts = [p for p in (proj, m_name) if p]
                                if parts:
                                    existing.detail = " • ".join(parts)
                                if directory:
                                    existing.cwd = directory
                                if tc:
                                    existing.started_at = float(tc) / 1000.0
                        continue

                    # If not matched to process, check if database record is active:
                    # Not idle, not archived, not terminal outcome
                    is_db_active = (
                        ti is None
                        and ta is None
                        and io is None
                        and (now_ms - tu < 600 * 1000.0 if tu else False)
                    )
                    if not is_db_active:
                        continue

                    m_name = ""
                    if model_raw:
                        try:
                            m_obj = json.loads(model_raw)
                            m_name = m_obj.get("id", "")
                        except Exception:
                            pass

                    proj = Path(directory).name if directory else ""
                    detail_parts = [p for p in (proj, m_name) if p]
                    detail = " • ".join(detail_parts) if detail_parts else "OpenCode session"

                    sessions.append(
                        ActiveSession(
                            provider_id="opencode",
                            session_id=sid,
                            title=clean_t,
                            status="running",
                            detail=detail,
                            cwd=directory,
                            started_at=float(tc) / 1000.0 if tc else None,
                            model_id=m_name or None,
                        )
                    )
                conn.close()
            except Exception:
                pass
            finally:
                if temp_db != db_path:
                    try:
                        temp_db.unlink(missing_ok=True)
                        (Path(temp_dir) / f"{temp_db.name}-wal").unlink(missing_ok=True)
                        (Path(temp_dir) / f"{temp_db.name}-shm").unlink(missing_ok=True)
                    except Exception:
                        pass

        return sessions

    def scan_all(self, force: bool = False) -> List[ActiveSession]:
        """Scan all providers for active sessions with short caching."""
        now = time.time()
        with self._lock:
            if not force and (now - self._last_scan < self.cache_ttl):
                return list(self._cached)

        results: List[ActiveSession] = []
        try:
            results.extend(self.scan_claude())
        except Exception:
            pass
        try:
            results.extend(self.scan_codex())
        except Exception:
            pass
        try:
            results.extend(self.scan_antigravity())
        except Exception:
            pass
        try:
            results.extend(self.scan_opencode())
        except Exception:
            pass

        with self._lock:
            self._cached = results
            self._last_scan = now

        return list(results)
