# CLAUDE.md — bank-connector

## Project

Small Flask app that syncs Enable Banking transactions into a self-hosted Actual Budget instance. No license server, no DB, no UI — a thin entry shim plus a small `bank_connector/` package, plus two JSON files for config and state.

Sync logic was extracted and stripped down from `../bridge-bank/` (MIT + Commons Clause). For deeper reference on the full version see `../bridge-bank/docs/`.

## Stack

- Python 3.13+
- `uv` for package management (`uv sync` to install, `uv run python connector.py` to run)
- `flask` for the OAuth callback + manual sync trigger
- `actualpy` for Actual Budget client
- `PyJWT[crypto]` + `cryptography` for the RS256 JWT used to authenticate to Enable Banking
- `requests` for everything HTTP

## Entry point

```bash
uv run python connector.py
```

Starts a Flask server on `127.0.0.1:3000` and a daemon thread running `scheduler_loop()` (24 h cadence by default).

## Architecture in one line

`Flask /connect` -> `EnableBankingClient.start_auth` -> bank OAuth -> `Flask /callback` -> `EnableBankingClient.complete_auth` -> append to `accounts.json` -> `SyncService.scheduler_loop` -> `SyncService.run` -> `AccountSyncer.sync` (per account: `fetch_transactions` -> `parse_transaction` -> dedup via `imported_refs` + `pending_map` -> `actualpy reconcile_transaction` / `create_transaction` -> `run_rules`).

## File map

| File | Role |
|---|---|
| `connector.py` | Entry shim — calls `bank_connector.cli.main()` |
| `bank_connector/__init__.py` | Package init — applies `patch_actualpy` eagerly + sets up logging |
| `bank_connector/settings.py` | Constants and paths (`ROOT`, `CONFIG_FILE`, `STATE_FILE`, `EB_API`, `HOST`, `PORT`, `SYNC_INTERVAL_HOURS`) |
| `bank_connector/storage.py` | `ConfigRepository` and `StateRepository` — JSON load/save |
| `bank_connector/enable_banking.py` | `EnableBankingClient` — JWT auth + HTTP calls (`start_auth`, `complete_auth`, `list_banks`, `fetch_transactions`) |
| `bank_connector/parsing.py` | `ParsedTransaction` dataclass + `parse_transaction` / `parse_own_names` (pure functions) |
| `bank_connector/actual_patches.py` | `patch_actualpy`, `patch_payee_name_rules`, `fix_rule_note_casing` |
| `bank_connector/sync.py` | `AccountSyncer` (per-account end-to-end sync) + `SyncService` (orchestrator + non-blocking lock + `scheduler_loop`) |
| `bank_connector/web.py` | `create_app(...)` Flask app factory with all deps injected |
| `bank_connector/cli.py` | `main()` — composition root: builds repos, client, service, app and starts the server |
| `pyproject.toml` | Project metadata and dependencies |
| `uv.lock` | Locked dependency graph |
| `.python-version` | Pinned Python version (3.13) |
| `accounts.json` | Config: Application ID, PEM path, Actual Budget creds, list of connected accounts. Created from `accounts.example.json`. |
| `state.json` | Per-account `last_sync_date` / `imported_refs` / `pending_map`, plus transient `pending_oauth` keyed by OAuth state UUID |
| `private.pem` | Enable Banking RS256 private key (user-supplied, gitignored) |
| `actual-cache/` | actualpy local replica — auto-managed, safe to delete |

## Module map

| Module | Public surface |
|---|---|
| `settings` | `ROOT`, `CONFIG_FILE`, `STATE_FILE`, `PEM_DEFAULT`, `ACTUAL_DATA_DIR`, `EB_API`, `HOST`, `PORT`, `SYNC_INTERVAL_HOURS`, `default_redirect_url()` |
| `storage` | `ConfigRepository(path)`, `StateRepository(path)` — both with `.load()` / `.save()` |
| `enable_banking` | `EnableBankingClient(application_id, pem_path, redirect_url, ...)` |
| `parsing` | `ParsedTransaction`, `parse_transaction(t, own_names)`, `parse_own_names(raw)` |
| `actual_patches` | `patch_actualpy()`, `patch_payee_name_rules(session)`, `fix_rule_note_casing(session, txns)` |
| `sync` | `AccountSyncer(eb_client, actual_data_dir)`, `SyncService(eb_client, config_repo, state_repo, actual_data_dir, interval_hours)` |
| `web` | `create_app(config_repo, state_repo, eb_client, sync_service)` |
| `cli` | `main()` |

## Non-obvious things to know before changing code

- **Two-layer dedup is intentional.** `imported_refs` skips already-seen settled transactions by Enable Banking's `entry_reference` / `transaction_id`. `pending_map` (keyed by `"<date>|<amount>"` via `ParsedTransaction.key`) handles the case where a transaction first appears as `PDNG` and later as `BOOK` — the existing pending record is marked `cleared` instead of a duplicate booked record being created. The two import paths live in `AccountSyncer._import_pending` / `_import_booked`. Don't simplify either side without breaking duplicate handling.
- **Three actualpy compatibility shims** in `actual_patches.py`, all silently fail-soft if actualpy changes:
  - `patch_actualpy()` rewrites `actual.database.apply_change` to coerce SQLAlchemy `Column` keys to plain strings in the ON CONFLICT SET clause. Required on Actual Budget ≥ 26.3.0. Called eagerly from `bank_connector/__init__.py` on package import.
  - `patch_payee_name_rules(session)` remaps `payee_name` -> `description` and `imported_payee` -> `imported_description` in rule action JSON before `run_rules`. Without this, Pydantic validation fails inside actualpy and no rules apply.
  - `fix_rule_note_casing(session, transactions)` re-uppers transaction notes after `run_rules` because actualpy normalises SET-action values to lowercase via `get_normalized_string()`.
- **The OAuth `state` UUID is the dedup key for `pending_oauth`.** It's persisted to `state.json` so a restart between `/connect` and `/callback` doesn't lose the in-flight request. Don't move that to in-memory state.
- **Multi-account banks.** A single bank OAuth can return several accounts. The current behavior maps all of them to the same `actual_account` from the `/connect` request — the `/callback` response tells the user to edit `accounts.json` afterwards if they want different mappings. Don't add a picker UI; the design choice is to keep the surface tiny.
- **`account_holder_name`** is comma-separated and used in `parsing._parse_payee` to relabel incoming transfers where the debtor is the user themselves — falls back to remittance info to avoid every credit transaction being labelled "self". The set is computed once per `SyncService.run()` and threaded down through `AccountSyncer.sync(..., own_names)`.
- **`SyncService._lock`** is non-blocking: if a manual `POST /sync` arrives while the scheduled run is in progress, the manual call returns silently. Don't change to blocking — that would let the manual endpoint stack up sync requests.
- **`EnableBankingClient` reads `application_id`, `pem_path`, `redirect_url` once at construction.** Editing those values in `accounts.json` requires a restart. The PEM private key is loaded once and cached on the instance. (The `accounts` list itself is re-read every sync cycle, so connecting a new bank does not need a restart.)
- **Sessions expire ~180 days.** There's no proactive warning system (that was email-notification clutter from bridge-bank); the user just hits a 401/403 on `/transactions` and re-runs `/connect`. Don't add an email notifier without asking — the deliberate trade-off is "no clutter".
- **Redirect URL must match Enable Banking app settings.** Default is `http://localhost:3000/callback`. If `redirect_url` in `accounts.json` changes, it also has to be re-registered in the Enable Banking dashboard.

## Common tasks

| Task | Where to look |
|---|---|
| Change sync cadence | `SYNC_INTERVAL_HOURS` in `bank_connector/settings.py` (or pass `interval_hours=` to `SyncService` in `cli.main`) |
| Change listening port | `HOST` / `PORT` in `bank_connector/settings.py` (also update `redirect_url` and re-register in Enable Banking) |
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
