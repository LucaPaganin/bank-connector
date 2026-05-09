"""Flask app factory.

`create_app()` builds an app with all dependencies injected — no module-level
state, no implicit config reads. The factory returns a fully wired Flask app
the caller can `.run()` or hand to a WSGI server.
"""
import datetime
import logging
import threading

from flask import Flask, Response, abort, jsonify, redirect, render_template, request

from bank_connector.csv_export import transactions_to_csv
from bank_connector.enable_banking import EnableBankingClient
from bank_connector.parsing import parse_own_names, parse_transaction
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
        accounts = [
            {
                "id": a["id"],
                "bank_name": a.get("bank_name"),
                "actual_account": a["actual_account"],
                "session_expiry": a.get("session_expiry"),
            }
            for a in cfg.get("accounts", [])
        ]
        if "text/html" in request.headers.get("Accept", ""):
            today = datetime.date.today()
            warn_threshold = today + datetime.timedelta(days=30)
            for a in accounts:
                exp = a.get("session_expiry")
                a["expiring_soon"] = bool(
                    exp and exp[:10] <= warn_threshold.isoformat()
                )
            return render_template(
                "index.html",
                last_run=state.get("last_run"),
                accounts=accounts,
            )
        return jsonify({"last_run": state.get("last_run"), "accounts": accounts})

    @app.get("/banks")
    def list_banks():
        return jsonify(eb_client.list_banks())

    @app.get("/connect")
    def connect():
        bank_name = request.args.get("bank_name")
        country = request.args.get("country")
        actual_account = request.args.get("actual_account")
        if not (bank_name and country and actual_account):
            return render_template(
                "connect.html",
                today=datetime.date.today().isoformat(),
            )
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
        if "text/html" in request.headers.get("Accept", ""):
            return render_template("callback.html", accounts=added)
        return jsonify(
            {
                "connected": added,
                "note": "Edit accounts.json if you need to remap any of these to a different Actual Budget account.",
            }
        )

    @app.get("/transactions/<int:account_id>")
    def list_transactions(account_id: int):
        """Return raw transactions for a connected account.

        Optional query params:
          date_from=YYYY-MM-DD  (defaults to 30 days ago)
        """
        cfg = config_repo.load()
        account = next((a for a in cfg.get("accounts", []) if a["id"] == account_id), None)
        if not account:
            abort(404, f"No account with id={account_id}")
        date_from_str = request.args.get("date_from")
        if date_from_str:
            try:
                date_from = datetime.date.fromisoformat(date_from_str)
            except ValueError:
                abort(400, "date_from must be YYYY-MM-DD")
        else:
            date_from = datetime.date.today() - datetime.timedelta(days=30)
        txns = eb_client.fetch_transactions(account["account_uid"], date_from)
        return jsonify({"account_id": account_id, "date_from": date_from.isoformat(), "count": len(txns), "transactions": txns})

    @app.post("/sync")
    def manual_sync():
        threading.Thread(target=sync_service.run, daemon=True).start()
        return jsonify({"started": True})

    @app.get("/export")
    def export_form():
        cfg = config_repo.load()
        accounts = [
            {
                "id": a["id"],
                "bank_name": a.get("bank_name", ""),
                "actual_account": a["actual_account"],
            }
            for a in cfg.get("accounts", [])
        ]
        today = datetime.date.today()
        return render_template(
            "export.html",
            accounts=accounts,
            today=today.isoformat(),
            default_from=(today - datetime.timedelta(days=90)).isoformat(),
        )

    @app.get("/export/<int:account_id>")
    def export_csv(account_id: int):
        cfg = config_repo.load()
        account = next(
            (a for a in cfg.get("accounts", []) if a["id"] == account_id), None
        )
        if not account:
            abort(404, f"No account with id={account_id}")

        date_from_str = request.args.get("date_from")
        date_to_str = request.args.get("date_to")
        try:
            date_from = (
                datetime.date.fromisoformat(date_from_str)
                if date_from_str
                else datetime.date.today() - datetime.timedelta(days=90)
            )
        except ValueError:
            abort(400, "date_from must be YYYY-MM-DD")
        try:
            date_to = (
                datetime.date.fromisoformat(date_to_str)
                if date_to_str
                else datetime.date.today()
            )
        except ValueError:
            abort(400, "date_to must be YYYY-MM-DD")

        own_names = parse_own_names(cfg.get("account_holder_name", ""))
        raw_txns = eb_client.fetch_transactions(
            account["account_uid"], date_from, date_to=date_to
        )
        parsed = sorted(
            (parse_transaction(t, own_names) for t in raw_txns),
            key=lambda t: t.date,
            reverse=True,
        )
        csv_text = transactions_to_csv(parsed)
        filename = f"transactions_{account_id}_{date_from}_{date_to}.csv"
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app
