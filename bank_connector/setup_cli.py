"""Terminal configuration assistant helpers.

The interactive Rich UI lives here so configuration can be completed without
editing JSON or Compose environment files by hand.
"""
import argparse
import json
import secrets
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt


REQUIRED_ENV_VARS = (
    "ACTUAL_HOSTNAME",
    "ACTUAL_URL",
    "ACTUAL_PASSWORD",
    "ENABLE_BANKING_APPLICATION_ID",
    "BANK_CONN_SYNC_TOKEN",
    "BANK_CONN_BASE_URL",
    "BANK_CONN_REDIRECT_URL",
)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and not key.startswith("#"):
            values[key] = value
    return values


def validate_setup(accounts_path: Path, env_path: Path, pem_path: Path) -> list[str]:
    """Return operator-facing issues that block a Docker deployment."""
    issues: list[str] = []
    if not accounts_path.is_file():
        issues.append("accounts.json is missing")
    if not pem_path.is_file():
        issues.append("private.pem is missing")

    values = _read_env_file(env_path)
    for key in REQUIRED_ENV_VARS:
        if not values.get(key) or values[key] == "CHANGEME":
            issues.append(f"{key} is missing or still CHANGEME")
    return issues


def write_env_file(path: Path, updates: dict[str, str]) -> None:
    """Replace named dotenv variables while preserving comments and unknown keys."""
    path = Path(path)
    existing_lines = path.read_text() .splitlines() if path.exists() else []
    pending = dict(updates)
    output: list[str] = []

    for line in existing_lines:
        key, separator, _value = line.partition("=")
        if separator and key in pending:
            output.append(f"{key}={pending.pop(key)}")
        else:
            output.append(line)

    output.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text("\n".join(output) + "\n")


def apply_configuration(root: Path, values: dict[str, str]) -> None:
    """Persist wizard answers without removing already-authorized bank accounts."""
    root = Path(root)
    config_path = root / "accounts.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    config["application_id"] = values["application_id"]
    config["pem_path"] = values["pem_path"]
    config["redirect_url"] = values["redirect_url"]
    config["account_holder_name"] = values["account_holder_name"]
    actual = config.setdefault("actual", {})
    actual["url"] = values["actual_url"]
    actual["password"] = values["actual_password"]
    actual["encryption_password"] = values["actual_encryption_password"] or None
    config.setdefault("accounts", [])
    config_path.write_text(json.dumps(config, indent=2) + "\n")

    write_env_file(
        root / ".env",
        {
            "ACTUAL_HOSTNAME": values["actual_hostname"],
            "ACTUAL_URL": values["actual_url"],
            "ACTUAL_PASSWORD": values["actual_password"],
            "ACTUAL_ENCRYPTION_PASSWORD": values["actual_encryption_password"],
            "ENABLE_BANKING_APPLICATION_ID": values["application_id"],
            "BANK_CONN_SYNC_TOKEN": values["sync_token"],
            "BANK_CONN_BASE_URL": values["base_url"],
            "BANK_CONN_REDIRECT_URL": values["redirect_url"],
        },
    )


def run_configuration_wizard(
    root: Path,
    *,
    ask=Prompt.ask,
    confirm=Confirm.ask,
    token_factory=secrets.token_urlsafe,
    console: Console | None = None,
) -> int:
    """Interactively gather Docker deployment settings without echoing secrets."""
    root = Path(root)
    output = console or Console()
    config_path = root / "accounts.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    env_path = root / ".env"
    if not env_path.exists() and (root / ".env.example").exists():
        env_path.write_text((root / ".env.example").read_text())
    env = _read_env_file(env_path)
    actual = config.get("actual", {})

    output.print(
        Panel.fit(
            "[bold]Bank Connector setup[/bold]\n"
            "Secrets are never printed. Existing bank connections are preserved.",
            border_style="cyan",
        )
    )

    def default(value: str | None, fallback: str = "") -> str:
        return value if value and value != "CHANGEME" else fallback

    def secret_value(label: str, existing: str) -> str:
        if existing and existing != "CHANGEME" and confirm(
            f"Keep existing {label.lower()}?", default=True
        ):
            return existing
        return ask(label, password=True)

    application_id = ask(
        "Enable Banking application ID",
        default=default(env.get("ENABLE_BANKING_APPLICATION_ID"), config.get("application_id", "")),
    )
    base_url = ask(
        "Public HTTPS URL",
        default=default(env.get("BANK_CONN_BASE_URL"), "https://"),
    ).rstrip("/")
    actual_hostname = ask(
        "Actual TLS hostname",
        default=default(env.get("ACTUAL_HOSTNAME")),
    )
    actual_url = ask(
        "Actual server URL",
        default=default(env.get("ACTUAL_URL"), actual.get("url", "")),
    )
    actual_password = secret_value(
        "Actual password", default(env.get("ACTUAL_PASSWORD"), actual.get("password", ""))
    )
    encryption_password = secret_value(
        "Actual encryption password (optional)",
        default(env.get("ACTUAL_ENCRYPTION_PASSWORD"), actual.get("encryption_password", "")),
    )
    account_holder_name = ask(
        "Account holder names (comma-separated)",
        default=config.get("account_holder_name", ""),
    )
    existing_token = default(env.get("BANK_CONN_SYNC_TOKEN"))
    sync_token = (
        existing_token
        if existing_token and confirm("Keep existing sync token?", default=True)
        else token_factory()
    )

    values = {
        "application_id": application_id,
        "pem_path": "./private.pem",
        "redirect_url": f"{base_url}/callback",
        "account_holder_name": account_holder_name,
        "actual_hostname": actual_hostname,
        "actual_url": actual_url,
        "actual_password": actual_password,
        "actual_encryption_password": encryption_password,
        "sync_token": sync_token,
        "base_url": base_url,
    }
    apply_configuration(root, values)

    issues = validate_setup(config_path, env_path, root / "private.pem")
    if issues:
        for issue in issues:
            output.print(f"[red]x[/red] {issue}")
        return 1
    output.print("[green]Configuration saved. Ready for docker compose.[/green]")
    return 0


def run_cli(argv: list[str], *, root: Path, console: Console | None = None) -> int:
    """Run a setup-assistant subcommand and return its shell exit status."""
    parser = argparse.ArgumentParser(prog="bank-connector")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("validate", help="check files and Docker settings")
    subcommands.add_parser("configure", help="run the interactive configuration wizard")
    args = parser.parse_args(argv)
    output = console or Console()

    if args.command == "validate":
        issues = validate_setup(root / "accounts.json", root / ".env", root / "private.pem")
        if issues:
            for issue in issues:
                output.print(f"[red]x[/red] {issue}")
            return 1
        output.print("[green]ready for docker compose[/green]")
        return 0

    return run_configuration_wizard(root, console=output)
