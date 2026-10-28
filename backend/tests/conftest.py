import os
import sys
import tempfile
from pathlib import Path

# Configure an isolated runtime before importing the app.
_TMP = Path(tempfile.mkdtemp(prefix="qumail-test-"))
os.environ["QMAIL_DATA_DIR"] = str(_TMP)
os.environ["QMAIL_DEMO_MODE"] = "1"
os.environ["FRONTEND_BUILD_DIR"] = str(_TMP / "no-build")
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
