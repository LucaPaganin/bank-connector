"""Quick smoke-test for the Enable Banking /auth endpoint.

Usage:
    uv run python test_auth.py --bank Revolut --country LT
    uv run python test_auth.py --bank "ING" --country IT --psu business
"""
import argparse
import json
import time
from pathlib import Path

import requests

import bank_connector  # applies patch_actualpy eagerly
from bank_connector.enable_banking import EnableBankingClient
from bank_connector.settings import CONFIG_FILE
from bank_connector.storage import ConfigRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", help="ASPSP name (case-sensitive); omit to list available banks")
    parser.add_argument("--country", help="ISO 3166-1 alpha-2 country code; filters the bank list")
    parser.add_argument("--psu", default="personal", choices=["personal", "business"])
    parser.add_argument("--config", default=str(CONFIG_FILE))
    args = parser.parse_args()

    cfg = ConfigRepository(Path(args.config)).load()
    client = EnableBankingClient(
        application_id=cfg["application_id"],
        pem_path=Path(cfg["pem_path"]),
        redirect_url=cfg.get("redirect_url", "http://localhost:3000/callback"),
    )

    if not args.bank:
        banks = client.list_banks()
        if args.country:
            banks = [b for b in banks if b["country"].upper() == args.country.upper()]
        for b in sorted(banks, key=lambda x: (x["country"], x["name"])):
            print(f"{b['country']}  {b['name']}")
        return

    body = {
        "access": {"valid_until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 179 * 24 * 3600))},
        "aspsp": {"name": args.bank, "country": args.country},
        "state": "test-state-1234",
        "redirect_url": client.redirect_url,
        "psu_type": args.psu,
    }

    print("--- Request body ---")
    print(json.dumps(body, indent=2))
    print(f"\nredirect_url in accounts.json: {client.redirect_url!r}")

    r = requests.post(
        f"{client.api_url}/auth",
        json=body,
        headers=client._headers(),
        timeout=30,
    )

    print(f"\n--- Response: {r.status_code} ---")
    try:
        print(json.dumps(r.json(), indent=2))
    except Exception:
        print(r.text)


if __name__ == "__main__":
    main()
