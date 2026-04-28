"""Project-wide constants and paths.

`ROOT` resolves to the project directory (the parent of this package), so
`accounts.json`, `state.json`, `private.pem` and `actual-cache/` keep their
existing locations.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "accounts.json"
STATE_FILE = ROOT / "state.json"
PEM_DEFAULT = ROOT / "private.pem"
ACTUAL_DATA_DIR = ROOT / "actual-cache"

EB_API = "https://api.enablebanking.com"
HOST = "127.0.0.1"
PORT = 3000
SYNC_INTERVAL_HOURS = 24


def default_redirect_url() -> str:
    return f"http://localhost:{PORT}/callback"
