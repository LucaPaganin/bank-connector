"""Flask app factory.

`create_app()` builds an app with all dependencies injected — no module-level
state, no implicit config reads. The factory returns a fully wired Flask app
the caller can `.run()` or hand to a WSGI server.
"""
import datetime
import logging
import threading

from flask import Flask, abort, jsonify, redirect, request

from bank_connector.enable_banking import EnableBankingClient
from bank_connector.storage import ConfigRepository, StateRepository
from bank_connector.sync import SyncService

log = logging.getLogger("connector")


def create_app(
    *,
    config_repo: ConfigRepository,
    state_repo: StateRepository,
    eb_client: EnableBankingClient,
    sync_service: SyncService,
) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def status():
        cfg = config_repo.load()
        state = state_repo.load()
        return jsonify(
            {
                "last_run": state.get("last_run"),
                "accounts": [
                    {
                        "id": a["id"],
                        "bank_name": a.get("bank_name"),
                        "actual_account": a["actual_account"],
                        "session_expiry": a.get("session_expiry"),
                    }
                    for a in cfg.get("accounts", [])
                ],
            }
        )

    @app.get("/banks")
    def list_banks():
        return jsonify(eb_client.list_banks())

    @app.get("/connect")
    def connect():
        """Start the OAuth flow.

        Visit in browser:
        /connect?bank_name=Revolut&country=LT&actual_account=Revolut[&start_sync_date=YYYY-MM-DD]
        """
        bank_name = request.args.get("bank_name")
        country = request.args.get("country")
        actual_account = request.args.get("actual_account")
        if not (bank_name and country and actual_account):
            abort(400, "Missing bank_name, country, or actual_account")
        start_date = (
            request.args.get("start_sync_date") or datetime.date.today().isoformat()
        )
        psu_type = request.args.get("psu_type", "personal")

        state_val, valid_until, auth_url = eb_client.start_auth(
            bank_name, country, psu_type
        )
        state = state_repo.load()
        state.setdefault("pending_oauth", {})[state_val] = {
            "bank_name": bank_name,
            "country": country,
            "actual_account": actual_account,
            "start_sync_date": start_date,
            "valid_until": valid_until,
        }
        state_repo.save(state)
        return redirect(auth_url)

    @app.get("/callback")
    def callback():
        code = request.args.get("code")
        state_val = request.args.get("state")
        if not code or not state_val:
            abort(400, "Missing code or state")
        state = state_repo.load()
        pending = state.get("pending_oauth", {}).pop(state_val, None)
        if not pending:
            abort(400, "Unknown OAuth state")
        state_repo.save(state)

        result = eb_client.complete_auth(code, state_val)
        session_id = result["session_id"]
        bank_accounts = result.get("accounts", [])
        if not bank_accounts:
            return "Bank returned no accounts.", 400

        cfg = config_repo.load()
        cfg.setdefault("accounts", [])
        next_id = max([a.get("id", 0) for a in cfg["accounts"]], default=0) + 1
        added = []
        for ba in bank_accounts:
            cfg["accounts"].append(
                {
                    "id": next_id,
                    "session_id": session_id,
                    "account_uid": ba["uid"],
                    "bank_name": pending["bank_name"],
                    "country": pending["country"],
                    "actual_account": pending["actual_account"],
                    "start_sync_date": pending["start_sync_date"],
                    "session_expiry": pending["valid_until"],
                    "skip_pending": False,
                }
            )
            added.append(
                {
                    "id": next_id,
                    "uid": ba["uid"],
                    "iban": (ba.get("account_id") or {}).get("iban", ""),
                    "name": ba.get("name", ""),
                }
            )
            next_id += 1
        config_repo.save(cfg)
        return jsonify(
            {
                "connected": added,
                "note": "Edit accounts.json if you need to remap any of these to a different Actual Budget account.",
            }
        )

    @app.post("/sync")
    def manual_sync():
        threading.Thread(target=sync_service.run, daemon=True).start()
        return jsonify({"started": True})

    return app
