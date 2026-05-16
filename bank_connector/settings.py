"""Project-wide constants and paths.

`ROOT` resolves to the project directory (the parent of this package), so the
defaults for `accounts.json`, `state.json`, `private.pem` and `actual-cache/`
keep their existing locations.

Every tunable below can be overridden via a `BANK_CONN_<NAME>` environment
variable. With no env vars set the values are identical to the historical
hardcoded defaults, so behaviour is unchanged unless you opt in.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env_str(name: str, default: str) -> str:
    return os.getenv(f"BANK_CONN_{name}", default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(f"BANK_CONN_{name}")
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(f"BANK_CONN_{name}")
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes")


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(f"BANK_CONN_{name}")
    return Path(raw) if raw else default


CONFIG_FILE = _env_path("CONFIG_FILE", ROOT / "accounts.json")
STATE_FILE = _env_path("STATE_FILE", ROOT / "state.json")
PEM_DEFAULT = _env_path("PEM_PATH", ROOT / "private.pem")
ACTUAL_DATA_DIR = _env_path("ACTUAL_DATA_DIR", ROOT / "actual-cache")
SSL_CRT_FILE = _env_path("SSL_CRT_FILE", ROOT / "sukuna.cormorant-bleak.ts.net.crt")
SSL_KEY_FILE = _env_path("SSL_KEY_FILE", ROOT / "sukuna.cormorant-bleak.ts.net.key")

EB_API = _env_str("EB_API", "https://api.enablebanking.com")
HOST = _env_str("HOST", "0.0.0.0")
PORT = _env_int("PORT", 3000)
SYNC_INTERVAL_HOURS = _env_int("SYNC_INTERVAL_HOURS", 24)
# Set BANK_CONN_SYNC_ENABLED=1 to enable the background auto-sync scheduler;
# off by default so the app can be used purely for CSV export without an Actual
# Budget instance. Manual POST /sync is unaffected by this flag.
SYNC_ENABLED: bool = _env_bool("SYNC_ENABLED", False)


def default_base_url() -> str:
    return _env_str("BASE_URL", f"https://sukuna.cormorant-bleak.ts.net:{PORT}")


def default_redirect_url() -> str:
    return f"{default_base_url()}/callback"
