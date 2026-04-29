"""Fetch and display transactions from a connected bank account.

Usage:
    uv run python fetch.py                          # list connected accounts
    uv run python fetch.py -a 0                     # last 30 days for account #0
    uv run python fetch.py -a <uid> --from 2026-01-01
    uv run python fetch.py -a 0 --raw               # dump raw JSON
    uv run python fetch.py -a 0 --save out.json     # save raw JSON to file
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

import bank_connector  # applies patch_actualpy eagerly
from bank_connector.enable_banking import EnableBankingClient
from bank_connector.parsing import ParsedTransaction, parse_own_names, parse_transaction
from bank_connector.settings import CONFIG_FILE
from bank_connector.storage import ConfigRepository

console = Console()


def _build_client(cfg: dict) -> EnableBankingClient:
    return EnableBankingClient(
        application_id=cfg["application_id"],
        pem_path=Path(cfg["pem_path"]),
        redirect_url=cfg.get("redirect_url", "http://localhost:3000/callback"),
    )


def _print_accounts(accounts: list[dict]) -> None:
    table = Table(title="Connected accounts", show_lines=False)
    table.add_column("#", style="dim", justify="right")
    table.add_column("UID", style="cyan")
    table.add_column("Name")
    table.add_column("IBAN")
    table.add_column("Actual account")
    for i, acc in enumerate(accounts):
        table.add_row(
            str(i),
            acc.get("uid", "—"),
            acc.get("name", "—"),
            acc.get("iban", "—"),
            acc.get("actual_account", "—"),
        )
    console.print(table)


def _print_transactions(
    raw: list[dict],
    own_names: frozenset[str],
    title: str,
) -> None:
    parsed: list[ParsedTransaction] = [parse_transaction(t, own_names) for t in raw]

    table = Table(title=title, show_lines=True)
    table.add_column("Date", style="dim")
    table.add_column("St", style="dim", justify="center")
    table.add_column("Amount", justify="right", min_width=10)
    table.add_column("Payee")
    table.add_column("Notes")
    table.add_column("Ref", style="dim")

    for p in parsed:
        colour = "green" if p.amount >= 0 else "red"
        table.add_row(
            str(p.date),
            p.status,
            f"[{colour}]{p.amount:+.2f}[/{colour}]",
            p.payee,
            p.notes,
            (p.ref[:28] + "…") if len(p.ref) > 29 else p.ref,
        )

    console.print(table)
    console.print(f"[bold]{len(parsed)}[/bold] transaction(s)\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch transactions from a connected bank account.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=str(CONFIG_FILE),
        metavar="FILE",
        help=f"Path to accounts.json (default: {CONFIG_FILE})",
    )
    parser.add_argument(
        "-a", "--account",
        metavar="IDX|UID",
        help="Account index (0-based) or UID; omit to list accounts",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        metavar="YYYY-MM-DD",
        default=None,
        help="Earliest transaction date (default: 30 days ago)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw JSON instead of a table",
    )
    parser.add_argument(
        "--save",
        metavar="FILE",
        help="Write raw JSON to FILE",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        console.print(f"[red]Config not found:[/red] {config_path}")
        sys.exit(1)

    cfg = ConfigRepository(config_path).load()
    accounts: list[dict] = cfg.get("accounts", [])

    if not accounts:
        console.print(
            "[yellow]No accounts in accounts.json.[/yellow] "
            "Start the connector and connect a bank first."
        )
        sys.exit(1)

    if args.account is None:
        _print_accounts(accounts)
        console.print("Pass [bold]-a INDEX[/bold] to fetch transactions for an account.")
        sys.exit(0)

    # Resolve account by index or UID.
    account: dict | None = None
    try:
        account = accounts[int(args.account)]
    except (ValueError, TypeError):
        account = next((a for a in accounts if a.get("uid") == args.account), None)
    except IndexError:
        pass

    if account is None:
        console.print(f"[red]Account '{args.account}' not found.[/red]")
        _print_accounts(accounts)
        sys.exit(1)

    date_from = (
        datetime.date.fromisoformat(args.date_from)
        if args.date_from
        else datetime.date.today() - datetime.timedelta(days=30)
    )

    client = _build_client(cfg)
    uid = account["uid"]
    label = account.get("name") or uid

    with console.status(f"Fetching transactions for [bold]{label}[/bold] from {date_from}…"):
        txns = client.fetch_transactions(uid, date_from)

    if args.save:
        Path(args.save).write_text(json.dumps(txns, indent=2), encoding="utf-8")
        console.print(f"[green]Saved {len(txns)} transactions → {args.save}[/green]")

    if args.raw:
        console.print_json(json.dumps(txns))
        return

    own_names = parse_own_names(cfg.get("account_holder_name", ""))
    _print_transactions(
        txns,
        own_names,
        title=f"{label}  ·  {date_from} → {datetime.date.today()}",
    )


if __name__ == "__main__":
    main()
