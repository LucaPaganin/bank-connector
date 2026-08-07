"""Terminal setup assistant persistence and validation."""
from pathlib import Path


def test_write_env_file_replaces_required_values_without_losing_comments(tmp_path: Path):
    from bank_connector.setup_cli import write_env_file

    path = tmp_path / ".env"
    path.write_text(
        "# Connector settings\n"
        "ACTUAL_URL=CHANGEME\n"
        "EXTRA_SETTING=leave-me\n"
    )

    write_env_file(
        path,
        {
            "ACTUAL_URL": "https://actual.example.test:5006",
            "BANK_CONN_SYNC_TOKEN": "secret-value",
        },
    )

    assert path.read_text() == (
        "# Connector settings\n"
        "ACTUAL_URL=https://actual.example.test:5006\n"
        "EXTRA_SETTING=leave-me\n"
        "BANK_CONN_SYNC_TOKEN=secret-value\n"
    )


def test_validate_setup_reports_missing_key_and_placeholder_values(tmp_path: Path):
    from bank_connector.setup_cli import validate_setup

    accounts = tmp_path / "accounts.json"
    accounts.write_text('{"application_id": "app-id", "accounts": []}')
    env_file = tmp_path / ".env"
    env_file.write_text("ACTUAL_URL=CHANGEME\n")

    issues = validate_setup(accounts, env_file, tmp_path / "private.pem")

    assert "private.pem is missing" in issues
    assert "ACTUAL_URL is missing or still CHANGEME" in issues


def test_run_cli_validate_prints_ready_when_docker_inputs_are_complete(tmp_path: Path, capsys):
    from bank_connector.setup_cli import REQUIRED_ENV_VARS, run_cli

    (tmp_path / "accounts.json").write_text('{"application_id": "app-id", "accounts": []}')
    (tmp_path / "private.pem").write_text("private key")
    (tmp_path / ".env").write_text(
        "\n".join(f"{key}=configured" for key in REQUIRED_ENV_VARS) + "\n"
    )

    assert run_cli(["validate"], root=tmp_path) == 0
    assert "ready for docker compose" in capsys.readouterr().out


def test_apply_configuration_updates_runtime_files_and_preserves_accounts(tmp_path: Path):
    from bank_connector.setup_cli import apply_configuration

    (tmp_path / "accounts.json").write_text(
        '{"accounts": [{"id": 7, "session_id": "keep-me"}]}'
    )
    (tmp_path / ".env").write_text("# keep comments\nACTUAL_URL=CHANGEME\n")

    apply_configuration(
        tmp_path,
        {
            "application_id": "enable-banking-app",
            "pem_path": "./private.pem",
            "redirect_url": "https://bank.example.test/callback",
            "account_holder_name": "Luca Rossi",
            "actual_hostname": "actual.example.test",
            "actual_url": "https://actual.example.test:5006",
            "actual_password": "actual-secret",
            "actual_encryption_password": "",
            "sync_token": "sync-secret",
            "base_url": "https://bank.example.test",
        },
    )

    import json

    config = json.loads((tmp_path / "accounts.json").read_text())
    assert config["application_id"] == "enable-banking-app"
    assert config["actual"]["url"] == "https://actual.example.test:5006"
    assert config["accounts"] == [{"id": 7, "session_id": "keep-me"}]
    env_text = (tmp_path / ".env").read_text()
    assert "# keep comments" in env_text
    assert "BANK_CONN_SYNC_TOKEN=sync-secret" in env_text
    assert "BANK_CONN_REDIRECT_URL=https://bank.example.test/callback" in env_text


def test_configuration_wizard_collects_answers_and_writes_files(tmp_path: Path):
    from bank_connector.setup_cli import run_configuration_wizard

    (tmp_path / "private.pem").write_text("private key")
    answers = {
        "Enable Banking application ID": "enable-app",
        "Public HTTPS URL": "https://bank.example.test",
        "Actual TLS hostname": "actual.example.test",
        "Actual server URL": "https://actual.example.test:5006",
        "Actual password": "actual-password",
        "Actual encryption password (optional)": "",
        "Account holder names (comma-separated)": "Luca Rossi",
    }

    def ask(label, **_kwargs):
        return answers[label]

    assert run_configuration_wizard(
        tmp_path,
        ask=ask,
        confirm=lambda *_args, **_kwargs: True,
        token_factory=lambda: "generated-token",
    ) == 0

    assert '"application_id": "enable-app"' in (tmp_path / "accounts.json").read_text()
    env_text = (tmp_path / ".env").read_text()
    assert "BANK_CONN_SYNC_TOKEN=generated-token" in env_text
    assert "BANK_CONN_REDIRECT_URL=https://bank.example.test/callback" in env_text
