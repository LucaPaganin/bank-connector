"""Repositories for the two JSON files that hold all persistent state.

Kept deliberately dumb — load/save full documents, no schema, no migrations.
Callers mutate the returned dicts and pass them back to `save`.
"""
import json
from pathlib import Path


class ConfigRepository:
    """Read/write `accounts.json` (Application ID, PEM path, Actual creds, accounts)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict:
        with open(self.path) as f:
            return json.load(f)

    def save(self, cfg: dict) -> None:
        with open(self.path, "w") as f:
            json.dump(cfg, f, indent=2)


class StateRepository:
    """Read/write `state.json` (sync state + transient pending OAuth handshakes)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict:
        if self.path.exists():
            with open(self.path) as f:
                return json.load(f)
        return {"accounts": {}, "pending_oauth": {}}

    def save(self, state: dict) -> None:
        with open(self.path, "w") as f:
            json.dump(state, f, indent=2)
