import os
import tempfile

# Keep tests from touching the real cache/history files.
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="modelpulse-test-")
