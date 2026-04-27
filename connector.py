#!/usr/bin/env python3
"""Minimal Enable Banking → Actual Budget connector.

Single-process Flask app:
  • Background thread runs sync every SYNC_INTERVAL_HOURS.
  • HTTP endpoints handle the bank OAuth flow and manual sync triggers.

Files used (all in this directory):
  accounts.json   - config (created from accounts.example.json)
  state.json      - sync state (auto-created)
  private.pem     - Enable Banking RS256 private key (you supply)
"""
import os
import sys
import json
import time
import uuid
import decimal
import datetime
import logging
import threading
import unicodedata
from pathlib import Path

import requests
import jwt as pyjwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from flask import Flask, request, redirect, jsonify, abort

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
EB_API = "https://api.enablebanking.com"
ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "accounts.json"
STATE_FILE = ROOT / "state.json"
HOST = "127.0.0.1"
PORT = 3000
SYNC_INTERVAL_HOURS = 24

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("connector")

# ----------------------------------------------------------------------------
# actualpy patch - required for Actual Budget >= 26.3.0
# Bug: apply_change passes Column objects in ON CONFLICT SET, producing
# table-qualified names that SQLite rejects. Convert keys to plain strings.
# ----------------------------------------------------------------------------
def _patch_actualpy():
    try:
        import actual.database as _adb
        import actual as _actual_mod
        from sqlalchemy import Column
        from sqlalchemy.dialects.sqlite import insert

        def _patched(session, table, table_id, values):
            set_dict = {(c.name if isinstance(c, Column) else c): v for c, v in values.items()}
            stmt = (
                insert(table)
                .values({"id": table_id, **values})
                .on_conflict_do_update(index_elements=["id"], set_=set_dict)
            )
            session.exec(stmt)

        _adb.apply_change = _patched
        if hasattr(_actual_mod, "apply_change"):
            _actual_mod.apply_change = _patched
    except Exception as e:
        log.warning("Failed to patch actualpy: %s", e)

_patch_actualpy()

# ----------------------------------------------------------------------------
# Config + state
# ----------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"accounts": {}, "pending_oauth": {}}

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def _redirect_url() -> str:
    cfg = load_config()
    return cfg.get("redirect_url") or f"http://localhost:{PORT}/callback"

# ----------------------------------------------------------------------------
# Enable Banking - JWT auth + endpoints
# ----------------------------------------------------------------------------
def _eb_headers() -> dict:
    cfg = load_config()
    app_id = cfg["application_id"]
    pem_path = Path(cfg.get("pem_path") or (ROOT / "private.pem"))
    key = load_pem_private_key(pem_path.read_bytes(), password=None)
    now = int(time.time())
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": now,
        "exp": now + 3600,
        "jti": str(uuid.uuid4()),
        "sub": app_id,
    }
    token = pyjwt.encode(payload, key, algorithm="RS256", headers={"kid": app_id})
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def eb_start_auth(bank_name: str, country: str, psu_type: str = "personal"):
    state_val = str(uuid.uuid4())
    valid_until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 180 * 24 * 3600))
    body = {
        "access": {"valid_until": valid_until},
        "aspsp": {"name": bank_name, "country": country},
        "state": state_val,
        "redirect_url": _redirect_url(),
        "psu_type": psu_type,
    }
    r = requests.post(f"{EB_API}/auth", json=body, headers=_eb_headers(), timeout=30)
    r.raise_for_status()
    return state_val, valid_until, r.json()["url"]

def eb_complete_auth(code: str, state: str) -> dict:
    r = requests.post(f"{EB_API}/sessions", json={"code": code, "state": state},
                      headers=_eb_headers(), timeout=30)
    r.raise_for_status()
    return r.json()

def eb_list_banks() -> list:
    r = requests.get(f"{EB_API}/aspsps", headers=_eb_headers(), timeout=30)
    r.raise_for_status()
    return [{"name": b["name"], "country": b["country"]} for b in r.json().get("aspsps", [])]

def eb_fetch_transactions(account_uid: str, date_from: datetime.date) -> list:
    headers = _eb_headers()
    params = {"date_from": date_from.isoformat(), "date_to": datetime.date.today().isoformat()}
    txns = []
    url = f"{EB_API}/accounts/{account_uid}/transactions"
    page = 0
    while url:
        if page > 0:
            time.sleep(1)
        for attempt in range(4):
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code == 429:
                wait = min(2 ** attempt * 5, 60)
                log.warning("Rate limited (429), retrying in %ds", wait)
                time.sleep(wait)
                continue
            break
        r.raise_for_status()
        data = r.json()
        txns.extend(data.get("transactions", []))
        ck = data.get("continuation_key")
        url = f"{EB_API}/accounts/{account_uid}/transactions" if ck else None
        params = {"continuation_key": ck} if ck else {}
        page += 1
    log.info("Fetched %d transactions for %s", len(txns), account_uid)
    return txns

# ----------------------------------------------------------------------------
# Transaction parsing
# ----------------------------------------------------------------------------
def _own_names() -> set:
    cfg = load_config()
    val = cfg.get("account_holder_name") or ""
    return {n.strip().lower() for n in val.split(",") if n.strip()}

def _parse_date(t: dict) -> datetime.date:
    raw = t.get("booking_date") or t.get("value_date") or t.get("transaction_date")
    if not raw:
        raise ValueError("No date in transaction")
    return datetime.date.fromisoformat(raw[:10])

def _parse_amount(t: dict) -> decimal.Decimal:
    amt = decimal.Decimal(str((t.get("transaction_amount") or {}).get("amount", "0")))
    indic = t.get("credit_debit_indicator") or t.get("credit_debit_indic", "")
    return -abs(amt) if indic.upper() == "DBIT" else abs(amt)

def _parse_payee(t: dict) -> str:
    own = _own_names()
    indic = (t.get("credit_debit_indicator") or t.get("credit_debit_indic", "")).upper()
    if indic == "DBIT":
        name = (t.get("creditor") or {}).get("name") or t.get("creditor_name")
        if not name:
            ri = t.get("remittance_information")
            name = ri[0] if isinstance(ri, list) else ri
    else:
        name = (t.get("debtor") or {}).get("name") or t.get("debtor_name")
        if not name or (own and name.lower() in own):
            ri = t.get("remittance_information")
            name = ri[0] if isinstance(ri, list) else ri
    return name or "Unknown"

def _parse_notes(t: dict) -> str:
    ref = t.get("remittance_information_unstructured")
    if ref:
        return ref
    ri = t.get("remittance_information")
    if ri and isinstance(ri, list):
        return " ".join(ri)
    return ""

def _entry_ref(t: dict) -> str:
    return t.get("entry_reference") or t.get("transaction_id") or ""

# ----------------------------------------------------------------------------
# Actual Budget rule patches
# ----------------------------------------------------------------------------
def _patch_payee_name_rules(session) -> None:
    """Remap payee_name → description in rule actions so actualpy accepts them."""
    from actual.queries import get_rules
    field_map = {"payee_name": "description", "imported_payee": "imported_description"}
    for rule in get_rules(session):
        for attr in ("conditions", "actions"):
            raw = getattr(rule, attr, None)
            if not raw:
                continue
            try:
                items = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            patched = False
            for item in items:
                if item.get("field") in field_map:
                    item["field"] = field_map[item["field"]]
                    patched = True
            if patched:
                setattr(rule, attr, json.dumps(items))

def _fix_rule_note_casing(session, transactions) -> None:
    """Restore original case for notes set by rules (actualpy lowercases values)."""
    from actual.queries import get_rules
    note_rules = []
    for rule in get_rules(session):
        try:
            actions = json.loads(rule.actions)
        except (json.JSONDecodeError, TypeError):
            continue
        for action in actions:
            if action.get("field") == "notes" and action.get("op") == "set" and action.get("value"):
                original = action["value"]
                lowered = unicodedata.normalize("NFD", original.lower())
                note_rules.append((lowered, original))
    if not note_rules:
        return
    for txn in transactions:
        if not txn.notes:
            continue
        for lowered, original in note_rules:
            if txn.notes == lowered:
                txn.notes = original
                break

# ----------------------------------------------------------------------------
# Per-account sync (transactions)
# ----------------------------------------------------------------------------
def sync_account(account: dict, state: dict, actual_cfg: dict) -> int:
    from actual import Actual
    from actual.queries import (
        get_or_create_account, reconcile_transaction,
        get_transactions, create_transaction,
    )

    account_id = str(account["id"])
    account_uid = account["account_uid"]
    actual_name = account["actual_account"]
    label = f"{account.get('bank_name', 'Bank')} → {actual_name}"

    accounts_state = state.setdefault("accounts", {})
    acct_state = accounts_state.get(account_id, {})

    last = acct_state.get("last_sync_date") or account.get("start_sync_date")
    date_from = (
        datetime.date.fromisoformat(last) if last
        else (datetime.date.today() - datetime.timedelta(days=30))
    )

    pending_map = acct_state.get("pending_map", {})
    if pending_map:
        earliest = min(datetime.date.fromisoformat(k.split("|")[0]) for k in pending_map)
        date_from = min(date_from, earliest)

    raw = eb_fetch_transactions(account_uid, date_from)
    if not raw:
        log.info("%s: no new transactions", label)
        acct_state["last_sync_date"] = datetime.date.today().isoformat()
        accounts_state[account_id] = acct_state
        return 0

    imported_refs = set(acct_state.get("imported_refs", []))
    skip_pending = bool(account.get("skip_pending"))
    added = updated = skipped = 0

    with Actual(
        base_url=actual_cfg["url"],
        password=actual_cfg["password"],
        encryption_password=actual_cfg.get("encryption_password") or None,
        file=actual_cfg["sync_id"],
        data_dir=str(ROOT / "actual-cache"),
    ) as actual:
        account_obj = get_or_create_account(actual.session, actual_name)
        existing = list(get_transactions(actual.session, account=account_obj))
        already_matched = existing[:]
        new_txn = []

        for txn in raw:
            try:
                status = txn.get("status", "BOOK")
                if status == "PDNG" and skip_pending:
                    skipped += 1
                    continue
                date = _parse_date(txn)
                amount = _parse_amount(txn)
                payee = _parse_payee(txn)
                notes = _parse_notes(txn)
                if notes and notes.strip().lower() == payee.strip().lower():
                    notes = ""
                ref = _entry_ref(txn)
                key = f"{date}|{amount}"

                if status == "PDNG":
                    if key in pending_map:
                        skipped += 1
                        continue
                    try:
                        t = reconcile_transaction(
                            actual.session, date, account_obj, payee, notes, None,
                            amount, imported_id=ref or None, cleared=False,
                            imported_payee=payee, already_matched=already_matched,
                        )
                    except Exception:
                        t = create_transaction(
                            actual.session, date, account_obj, payee, notes,
                            amount=amount, cleared=False, imported_payee=payee,
                        )
                    already_matched.append(t)
                    if t.changed():
                        pending_map[key] = str(t.id)
                        new_txn.append(t)
                        added += 1
                    else:
                        skipped += 1
                else:
                    if ref and ref in imported_refs:
                        skipped += 1
                        continue
                    if key in pending_map:
                        # Pending → booked: mark cleared, drop from pending_map
                        txn_id = pending_map[key]
                        existing_txn = next((t for t in existing if str(t.id) == txn_id), None)
                        if existing_txn:
                            existing_txn.cleared = True
                            updated += 1
                        del pending_map[key]
                        if ref:
                            imported_refs.add(ref)
                        continue
                    try:
                        t = reconcile_transaction(
                            actual.session, date, account_obj, payee, notes, None,
                            amount, imported_id=ref or None, cleared=True,
                            imported_payee=payee, already_matched=already_matched,
                        )
                    except Exception:
                        t = create_transaction(
                            actual.session, date, account_obj, payee, notes,
                            amount=amount, cleared=True, imported_payee=payee,
                        )
                    already_matched.append(t)
                    if t.changed():
                        if ref:
                            imported_refs.add(ref)
                        new_txn.append(t)
                        added += 1
                    else:
                        skipped += 1
            except Exception as e:
                log.warning("Skipping txn (%s)", e)

        try:
            _patch_payee_name_rules(actual.session)
            actual.run_rules(new_txn)
            _fix_rule_note_casing(actual.session, new_txn)
        except Exception as e:
            log.error("Rule application error: %s", e)

        actual.commit()

    log.info("%s: %d added, %d confirmed, %d skipped", label, added, updated, skipped)
    acct_state["last_sync_date"] = datetime.date.today().isoformat()
    acct_state["pending_map"] = pending_map
    acct_state["imported_refs"] = list(imported_refs)
    accounts_state[account_id] = acct_state
    return added

# ----------------------------------------------------------------------------
# Sync orchestrator
# ----------------------------------------------------------------------------
_sync_lock = threading.Lock()

def run_sync() -> int:
    if not _sync_lock.acquire(blocking=False):
        log.info("Sync already running, skipping")
        return 0
    try:
        log.info("Sync starting")
        cfg = load_config()
        state = load_state()
        total = 0
        for i, acct in enumerate(cfg.get("accounts", [])):
            if i > 0:
                time.sleep(2)
            try:
                total += sync_account(acct, state, cfg["actual"])
            except Exception as e:
                log.error("Sync failed for %s: %s", acct.get("bank_name", "?"), e)
        state["last_run"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_state(state)
        log.info("Sync finished. Total imported: %d", total)
        return total
    finally:
        _sync_lock.release()

def scheduler_loop() -> None:
    while True:
        try:
            run_sync()
        except Exception as e:
            log.exception("Sync loop error: %s", e)
        time.sleep(SYNC_INTERVAL_HOURS * 3600)

# ----------------------------------------------------------------------------
# Flask app
# ----------------------------------------------------------------------------
app = Flask(__name__)

@app.get("/")
def status():
    cfg = load_config()
    state = load_state()
    return jsonify({
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
    })

@app.get("/banks")
def list_banks():
    return jsonify(eb_list_banks())

@app.get("/connect")
def connect():
    """Start the OAuth flow. Visit in browser:
    /connect?bank_name=Revolut&country=LT&actual_account=Revolut[&start_sync_date=YYYY-MM-DD]
    """
    bank_name = request.args.get("bank_name")
    country = request.args.get("country")
    actual_account = request.args.get("actual_account")
    if not (bank_name and country and actual_account):
        abort(400, "Missing bank_name, country, or actual_account")
    start_date = request.args.get("start_sync_date") or datetime.date.today().isoformat()
    psu_type = request.args.get("psu_type", "personal")

    state_val, valid_until, auth_url = eb_start_auth(bank_name, country, psu_type)
    state = load_state()
    state.setdefault("pending_oauth", {})[state_val] = {
        "bank_name": bank_name,
        "country": country,
        "actual_account": actual_account,
        "start_sync_date": start_date,
        "valid_until": valid_until,
    }
    save_state(state)
    return redirect(auth_url)

@app.get("/callback")
def callback():
    code = request.args.get("code")
    state_val = request.args.get("state")
    if not code or not state_val:
        abort(400, "Missing code or state")
    state = load_state()
    pending = state.get("pending_oauth", {}).pop(state_val, None)
    if not pending:
        abort(400, "Unknown OAuth state")
    save_state(state)

    result = eb_complete_auth(code, state_val)
    session_id = result["session_id"]
    bank_accounts = result.get("accounts", [])
    if not bank_accounts:
        return "Bank returned no accounts.", 400

    cfg = load_config()
    cfg.setdefault("accounts", [])
    next_id = max([a.get("id", 0) for a in cfg["accounts"]], default=0) + 1
    added = []
    for ba in bank_accounts:
        cfg["accounts"].append({
            "id": next_id,
            "session_id": session_id,
            "account_uid": ba["uid"],
            "bank_name": pending["bank_name"],
            "country": pending["country"],
            "actual_account": pending["actual_account"],
            "start_sync_date": pending["start_sync_date"],
            "session_expiry": pending["valid_until"],
            "skip_pending": False,
        })
        added.append({
            "id": next_id,
            "uid": ba["uid"],
            "iban": (ba.get("account_id") or {}).get("iban", ""),
            "name": ba.get("name", ""),
        })
        next_id += 1
    save_config(cfg)
    return jsonify({
        "connected": added,
        "note": "Edit accounts.json if you need to remap any of these to a different Actual Budget account.",
    })

@app.post("/sync")
def manual_sync():
    threading.Thread(target=run_sync, daemon=True).start()
    return jsonify({"started": True})

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    if not CONFIG_FILE.exists():
        log.error("Missing %s - copy accounts.example.json and fill in your details.", CONFIG_FILE)
        sys.exit(1)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    app.run(host=HOST, port=PORT)
