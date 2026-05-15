"""Project-wide constants and paths.

`ROOT` resolves to the project directory (the parent of this package), so
`accounts.json`, `state.json`, `private.pem` and `actual-cache/` keep their
existing locations.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "accounts.json"
STATE_FILE = ROOT / "state.json"
PEM_DEFAULT = ROOT / "private.pem"
ACTUAL_DATA_DIR = ROOT / "actual-cache"
SSL_CRT_FILE = ROOT / "sukuna.cormorant-bleak.ts.net.crt"
SSL_KEY_FILE = ROOT / "sukuna.cormorant-bleak.ts.net.key"

EB_API = "https://api.enablebanking.com"
HOST = "0.0.0.0"
PORT = 3000
SYNC_INTERVAL_HOURS = 24
# Set BC_SYNC_ENABLED=1 to enable the background auto-sync scheduler; off by
# default so the app can be used purely for CSV export without an Actual
# Budget instance. Manual POST /sync is unaffected by this flag.
SYNC_ENABLED: bool = os.getenv("BC_SYNC_ENABLED", "").lower() in ("1", "true", "yes")


def default_base_url() -> str:
    return f"https://sukuna.cormorant-bleak.ts.net:{PORT}"

def default_redirect_url() -> str:
    return f"{default_base_url()}/callback"
