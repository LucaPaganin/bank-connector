"""JSON repositories: round-trip and absent-file behaviour."""
from bank_connector.storage import ConfigRepository, StateRepository


def test_config_round_trip(tmp_path):
    path = tmp_path / "accounts.json"
    repo = ConfigRepository(path)
    assert repo.exists() is False

    cfg = {"application_id": "abc", "accounts": [{"id": 1}]}
    repo.save(cfg)
    assert repo.exists() is True
    assert repo.load() == cfg


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    repo = StateRepository(path)
    state = {"accounts": {"1": {"last_sync_date": "2026-05-16"}}, "pending_oauth": {}}
    repo.save(state)
    assert repo.load() == state


def test_state_load_default_when_missing(tmp_path):
    repo = StateRepository(tmp_path / "missing.json")
    assert repo.load() == {"accounts": {}, "pending_oauth": {}}
