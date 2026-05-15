"""Import transactions from a local Enable Banking JSON file into Actual Budget.

Usage:
    uv run python import_json.py [json_file] [account_id]

Defaults:
    json_file   mytestrevolut.json
    account_id  taken from json_file["account_id"] if present, otherwise required
"""
import json
import sys
from pathlib import Path

import bank_connector  # applies patch_actualpy on import
from bank_connector.actual_patches import fix_rule_note_casing, patch_payee_name_rules
from bank_connector.parsing import parse_own_names, parse_transaction
from bank_connector.settings import ACTUAL_DATA_DIR, CONFIG_FILE
from bank_connector.storage import ConfigRepository

ROOT = Path(__file__).resolve().parent


def main() -> None:
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "mytestrevolut.json"
    if not json_path.exists():
        sys.exit(f"ERROR: file not found: {json_path}")

    data = json.loads(json_path.read_text())
    raw_transactions = data.get("transactions", [])

    if len(sys.argv) > 2:
        account_id = int(sys.argv[2])
    elif "account_id" in data:
        account_id = int(data["account_id"])
    else:
        sys.exit("ERROR: account_id not in JSON and not passed as argument")

    cfg = ConfigRepository(CONFIG_FILE).load()
    actual_cfg = cfg["actual"]

    account = next((a for a in cfg.get("accounts", []) if a["id"] == account_id), None)
    if account is None:
        sys.exit(f"ERROR: no account with id={account_id} in accounts.json")

    actual_name = account["actual_account"]
    own_names = parse_own_names(cfg.get("account_holder_name", ""))

    print(f"Source : {json_path.name}  ({len(raw_transactions)} transactions)")
    print(f"Target : Actual account '{actual_name}'")
    print()

    from actual import Actual
    from actual.queries import (
        create_transaction,
        get_or_create_account,
        reconcile_transaction,
    )

    added = skipped = errors = 0
    new_txns = []

    with Actual(
        base_url=actual_cfg["url"],
        password=actual_cfg["password"],
        encryption_password=actual_cfg.get("encryption_password") or None,
        file=actual_cfg["sync_id"],
        data_dir=str(ACTUAL_DATA_DIR),
    ) as actual:
        account_obj = get_or_create_account(actual.session, actual_name)
        # actualpy treats already_matched as an EXCLUSION list for fuzzy
        # matching: it must start empty and grow per matched/created txn.
        # Seeding it with existing transactions would re-create ref-less
        # transactions as duplicates on a re-import.
        already_matched: list = []

        for raw in raw_transactions:
            try:
                parsed = parse_transaction(raw, own_names)
            except Exception as e:
                print(f"  SKIP  parse error: {e}")
                errors += 1
                continue

            cleared = parsed.status == "BOOK"
            try:
                t = reconcile_transaction(
                    actual.session,
                    parsed.date,
                    account_obj,
                    parsed.payee,
                    parsed.notes,
                    None,
                    parsed.amount,
                    imported_id=parsed.ref or None,
                    cleared=cleared,
                    imported_payee=parsed.payee,
                    already_matched=already_matched,
                )
            except Exception:
                t = create_transaction(
                    actual.session,
                    parsed.date,
                    account_obj,
                    parsed.payee,
                    parsed.notes,
                    amount=parsed.amount,
                    cleared=cleared,
                    imported_payee=parsed.payee,
                )

            already_matched.append(t)
            if t.changed():
                added += 1
                new_txns.append(t)
                status = "PDNG" if not cleared else "    "
                print(f"  + {parsed.date}  {parsed.amount:>10.2f}  {status}  {parsed.payee}")
            else:
                skipped += 1

        try:
            patch_payee_name_rules(actual.session)
            actual.run_rules(new_txns)
            fix_rule_note_casing(actual.session, new_txns)
        except Exception as e:
            print(f"\n  Warning: rule application error: {e}")

        actual.commit()

    print()
    print(f"Done.  {added} imported,  {skipped} already present,  {errors} parse errors.")


if __name__ == "__main__":
    main()
