# CLAUDE.md — bank-connector

## Project

Single-file Flask app that syncs Enable Banking transactions into a self-hosted Actual Budget instance. No license server, no DB, no UI — just one Python file plus two JSON files for config and state.

Sync logic was extracted and stripped down from `../bridge-bank/` (MIT + Commons Clause). For deeper reference on the full version see `../bridge-bank/docs/`.

## Stack

- Python 3.9+
- `flask` for the OAuth callback + manual sync trigger
- `actualpy` for Actual Budget client
- `PyJWT[crypto]` + `cryptography` for the RS256 JWT used to authenticate to Enable Banking
- `requests` for everything HTTP

No package manager configured — plain `pip install -r requirements.txt`.

## Entry point

```bash
python connector.py
```

Starts a Flask server on `127.0.0.1:3000` and a daemon thread running `scheduler_loop()` (24 h cadence by default).

## Architecture in one line

`Flask /connect` → `eb_start_auth` → bank OAuth → `Flask /callback` → `eb_complete_auth` → append to `accounts.json` → background loop → `run_sync` → `sync_account` (per account: `eb_fetch_transactions` → parse → dedup via `imported_refs` + `pending_map` → `actualpy reconcile_transaction` / `create_transaction` → `run_rules`).

## File map

| File | Role |
|---|---|
| `connector.py` | Everything — Flask app, JWT auth, transaction fetch, parsing, sync, scheduler |
| `accounts.json` | Config: Application ID, PEM path, Actual Budget creds, list of connected accounts. Created from `accounts.example.json`. |
| `state.json` | Per-account `last_sync_date` / `imported_refs` / `pending_map`, plus transient `pending_oauth` keyed by OAuth state UUID |
| `private.pem` | Enable Banking RS256 private key (user-supplied, gitignored) |
| `actual-cache/` | actualpy local replica — auto-managed, safe to delete |

## Function map (all in `connector.py`)

| Section | Functions |
|---|---|
| actualpy patch | `_patch_actualpy` (Actual Budget ≥ 26.3.0 SQLite ON-CONFLICT fix) |
| Config / state I/O | `load_config`, `save_config`, `load_state`, `save_state`, `_redirect_url` |
| Enable Banking | `_eb_headers`, `eb_start_auth`, `eb_complete_auth`, `eb_list_banks`, `eb_fetch_transactions` |
| Transaction parsing | `_parse_date`, `_parse_amount`, `_parse_payee`, `_parse_notes`, `_entry_ref`, `_own_names` |
| Actual Budget rule patches | `_patch_payee_name_rules`, `_fix_rule_note_casing` |
| Sync | `sync_account`, `run_sync`, `scheduler_loop` |
| Flask routes | `status`, `list_banks`, `connect`, `callback`, `manual_sync` |

## Non-obvious things to know before changing code

- **Two-layer dedup is intentional.** `imported_refs` skips already-seen settled transactions by Enable Banking's `entry_reference` / `transaction_id`. `pending_map` (keyed by `"<date>|<amount>"`) handles the case where a transaction first appears as `PDNG` and later as `BOOK` — the existing pending record is marked `cleared` instead of a duplicate booked record being created. Don't simplify either side without breaking duplicate handling.
- **Three actualpy compatibility shims** that all silently fail-soft if actualpy changes:
  - `_patch_actualpy` rewrites `actual.database.apply_change` to coerce SQLAlchemy `Column` keys to plain strings in the ON CONFLICT SET clause. Required on Actual Budget ≥ 26.3.0.
  - `_patch_payee_name_rules` remaps `payee_name` → `description` and `imported_payee` → `imported_description` in rule action JSON before `run_rules`. Without this, Pydantic validation fails inside actualpy and no rules apply.
  - `_fix_rule_note_casing` re-uppers transaction notes after `run_rules` because actualpy normalises SET-action values to lowercase via `get_normalized_string()`.
- **The OAuth `state` UUID is the dedup key for `pending_oauth`.** It's persisted to `state.json` so a restart between `/connect` and `/callback` doesn't lose the in-flight request. Don't move that to in-memory state.
- **Multi-account banks.** A single bank OAuth can return several accounts. The current behavior maps all of them to the same `actual_account` from the `/connect` request — the `/callback` response tells the user to edit `accounts.json` afterwards if they want different mappings. Don't add a picker UI; the design choice is to keep the surface tiny.
- **`account_holder_name`** is comma-separated and used in `_parse_payee` to relabel incoming transfers where the debtor is the user themselves — falls back to remittance info to avoid every credit transaction being labelled "self".
- **`_sync_lock`** is non-blocking: if a manual `POST /sync` arrives while the scheduled run is in progress, the manual call returns silently. Don't change to blocking — that would let the manual endpoint stack up sync requests.
- **Sessions expire ~180 days.** There's no proactive warning system (that was email-notification clutter from bridge-bank); the user just hits a 401/403 on `/transactions` and re-runs `/connect`. Don't add an email notifier without asking — the deliberate trade-off is "no clutter".
- **Redirect URL must match Enable Banking app settings.** Default is `http://localhost:3000/callback`. If `redirect_url` in `accounts.json` changes, it also has to be re-registered in the Enable Banking dashboard.

## Common tasks

| Task | Where to look |
|---|---|
| Change sync cadence | `SYNC_INTERVAL_HOURS` constant at the top of `connector.py` |
| Change listening port | `HOST` / `PORT` constants (also update `redirect_url` and re-register in Enable Banking) |
| Reset an account's history | Stop the connector, remove `state.accounts.<id>` from `state.json`, restart — next sync re-fetches from `start_sync_date` |
| Disconnect a bank | Remove its entry from `accounts.json` and (optionally) the matching `state.accounts.<id>` |
| Inspect what was imported | `actual-cache/` is the SQLite replica — readable with any SQLite tool, but Actual Budget is the source of truth |

## What this project deliberately does NOT have

If asked to add any of these back, push back first — they were removed on purpose:

- License key validation, machine fingerprinting, remote license server
- Multi-step setup wizard / web UI / status dashboard
- Email notifications (success / failure / session-expiry warnings)
- Balance-only providers (Binance, Coinbase, eToro)
- Encrypted credential storage (Fernet) — there are no per-account credentials to encrypt anymore
- SQLite database — state is two JSON files
- Docker, docker-compose, CI/CD, self-update logic
- `schedule` library — replaced with a `time.sleep` loop in a daemon thread

## Reference for deeper questions

The original full implementation lives at `../bridge-bank/`. Its docs (`../bridge-bank/docs/`) explain the same concepts in much more detail — useful when investigating an Enable Banking field, an edge case in transaction parsing, or an Actual Budget compatibility issue.
