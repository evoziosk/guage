from __future__ import annotations

import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from src.main import main

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log_file = Path.home() / "widget_error.log"
        with open(log_file, "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        raise
