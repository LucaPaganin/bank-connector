"""Runtime server configuration in the composition root."""
from unittest.mock import MagicMock


def test_main_uses_plain_http_when_tls_files_are_not_present(monkeypatch, tmp_path):
    """TLS is terminated by the reverse proxy in the Docker deployment."""
    import bank_connector.cli as cli

    config_file = tmp_path / "accounts.json"
    config_file.write_text('{"application_id": "test-app"}')
    cli.CONFIG_FILE = config_file
    cli.STATE_FILE = tmp_path / "state.json"
    cli.ACTUAL_DATA_DIR = tmp_path / "actual-cache"
    cli.SSL_CRT_FILE = tmp_path / "missing.crt"
    cli.SSL_KEY_FILE = tmp_path / "missing.key"
    cli.SYNC_ENABLED = False

    fake_client = MagicMock()
    monkeypatch.setattr(cli, "EnableBankingClient", fake_client)
    monkeypatch.setattr(cli, "SyncService", MagicMock())
    app = MagicMock()
    monkeypatch.setattr(cli, "create_app", lambda **_kwargs: app)

    cli.main()

    assert app.run.call_args.kwargs["ssl_context"] is None


def test_main_delegates_setup_subcommands_to_terminal_assistant(monkeypatch):
    import bank_connector.cli as cli

    invoked = MagicMock(return_value=7)
    monkeypatch.setattr(cli, "run_setup_cli", invoked)

    assert cli.main(["validate"]) == 7
    invoked.assert_called_once_with(["validate"], root=cli.ROOT)
