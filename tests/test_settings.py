"""Settings layer: BANK_CONN_* env overrides and defaults."""
import importlib

import pytest

import bank_connector.settings as settings_mod


@pytest.fixture
def fresh_settings(monkeypatch):
    """Reload settings.py with a controlled environment."""

    def _reload():
        return importlib.reload(settings_mod)

    yield _reload
    # monkeypatch restores the environment first; reload once more so the
    # module returns to its default (env-free) state for other tests.
    importlib.reload(settings_mod)


def test_defaults_unchanged_without_env(monkeypatch, fresh_settings):
    for k in [
        "BANK_CONN_PORT",
        "BANK_CONN_HOST",
        "BANK_CONN_SYNC_ENABLED",
        "BANK_CONN_SYNC_INTERVAL_HOURS",
        "BANK_CONN_EB_API",
        "BANK_CONN_BASE_URL",
    ]:
        monkeypatch.delenv(k, raising=False)
    s = fresh_settings()
    assert s.PORT == 3000
    assert s.HOST == "0.0.0.0"
    assert s.SYNC_ENABLED is False
    assert s.SYNC_INTERVAL_HOURS == 24
    assert s.EB_API == "https://api.enablebanking.com"
    assert s.default_redirect_url().endswith(":3000/callback")


def test_env_overrides_apply(monkeypatch, fresh_settings):
    monkeypatch.setenv("BANK_CONN_PORT", "8080")
    monkeypatch.setenv("BANK_CONN_HOST", "127.0.0.1")
    monkeypatch.setenv("BANK_CONN_SYNC_ENABLED", "yes")
    monkeypatch.setenv("BANK_CONN_SYNC_INTERVAL_HOURS", "6")
    monkeypatch.setenv("BANK_CONN_EB_API", "https://example.test/")
    s = fresh_settings()
    assert s.PORT == 8080
    assert s.HOST == "127.0.0.1"
    assert s.SYNC_ENABLED is True
    assert s.SYNC_INTERVAL_HOURS == 6
    assert s.EB_API == "https://example.test/"
    assert s.default_redirect_url().endswith(":8080/callback")


def test_base_url_override(monkeypatch, fresh_settings):
    monkeypatch.setenv("BANK_CONN_BASE_URL", "https://my.host:9000")
    s = fresh_settings()
    assert s.default_base_url() == "https://my.host:9000"
    assert s.default_redirect_url() == "https://my.host:9000/callback"


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("YES", True), ("0", False), ("", False), ("nope", False)],
)
def test_env_bool_parsing(monkeypatch, fresh_settings, raw, expected):
    monkeypatch.setenv("BANK_CONN_SYNC_ENABLED", raw)
    assert fresh_settings().SYNC_ENABLED is expected


def test_env_int_invalid_falls_back(monkeypatch, fresh_settings):
    monkeypatch.setenv("BANK_CONN_PORT", "not-a-number")
    assert fresh_settings().PORT == 3000


def test_path_override(monkeypatch, fresh_settings, tmp_path):
    cfg = tmp_path / "custom-accounts.json"
    monkeypatch.setenv("BANK_CONN_CONFIG_FILE", str(cfg))
    s = fresh_settings()
    assert s.CONFIG_FILE == cfg
