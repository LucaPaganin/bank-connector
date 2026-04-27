# bank-connector

A minimal, single-file connector that syncs bank transactions from [Enable Banking](https://enablebanking.com) into a self-hosted [Actual Budget](https://actualbudget.org) instance.

A long-running Flask process handles the bank OAuth flow on demand and runs the sync on a fixed interval in a background thread. State and configuration live in two JSON files. No database, no web UI, no external services besides Enable Banking and your own Actual Budget server.

This is a stripped-down reimplementation of the core sync logic from [bridge-bank](https://github.com/dazaro/bridge-bank), without the licensing, scheduler library, web wizard, email notifications, balance providers, or Docker stack.

---

## Layout

```
bank-connector/
├── connector.py            # everything: Flask, sync loop, parsing, Actual Budget client
├── pyproject.toml          # project metadata and dependencies
├── uv.lock                 # locked dependency graph
├── .python-version         # pinned Python version (3.13)
├── accounts.example.json   # config template
├── accounts.json           # your config (gitignored — create from the template)
├── private.pem             # your Enable Banking RS256 key (gitignored)
├── state.json              # auto-created sync state (gitignored)
└── actual-cache/           # auto-created actualpy local replica
```

---

## Setup

### 1. Install dependencies

```bash
uv sync
```

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

### 2. Get an Enable Banking application

- Sign up at [enablebanking.com](https://enablebanking.com).
- Create an application — you'll receive an Application ID (UUID) and a private key file (`<uuid>.pem`).
- In the application settings, register `http://localhost:3000/callback` as a redirect URL.
- Copy the PEM file into this folder as `private.pem` (or set `pem_path` in `accounts.json`).

### 3. Configure

```bash
cp accounts.example.json accounts.json
```

Edit `accounts.json` and fill in:

| Field | Description |
|---|---|
| `application_id` | The Enable Banking Application UUID |
| `pem_path` | Path to your `.pem` file (default: `./private.pem`) |
| `redirect_url` | OAuth callback URL — must match what you registered |
| `account_holder_name` | Comma-separated own names; used to relabel self-transfers |
| `actual.url` | Base URL of your Actual Budget server (e.g. `http://localhost:5006`) |
| `actual.password` | Actual Budget login password |
| `actual.encryption_password` | E2E encryption password, or `null` if not used |
| `actual.sync_id` | The budget's sync ID (visible in Actual Budget settings) |
| `accounts` | Leave as `[]` — populated automatically when you connect a bank |

### 4. Run

```bash
uv run python connector.py
```

The Flask app starts on `127.0.0.1:3000`. The background sync thread fires immediately, then every 24 hours. Adjust the interval by changing `SYNC_INTERVAL_HOURS` at the top of `connector.py`.

### 5. Connect a bank

Open in your browser:

```
http://localhost:3000/connect?bank_name=Revolut&country=LT&actual_account=Revolut
```

You'll be redirected to your bank to log in. After approval the bank redirects to `/callback`, the connector finalises the OAuth session, and appends the new account(s) to `accounts.json`. The next sync (manual or scheduled) imports them.

To find supported banks: `GET /banks` returns the full list of names and country codes.

---

## Endpoints

| Route | Purpose |
|---|---|
| `GET /` | Status JSON — last run timestamp, configured accounts |
| `GET /banks` | Full list of supported banks `[{name, country}, ...]` |
| `GET /connect?bank_name=&country=&actual_account=[&start_sync_date=][&psu_type=]` | Start OAuth, redirects to the bank login |
| `GET /callback` | Bank's redirect target — completes OAuth, saves accounts |
| `POST /sync` | Trigger a manual sync (returns immediately, runs in background) |

---

## How it works

```
Enable Banking
  │  JWT (RS256, 1-hour exp) → Bearer token on every request
  │
  │  GET /accounts/<uid>/transactions  (paginated, rate-limit aware)
  ▼
parse  (date · amount sign from credit_debit_indicator · payee with self-transfer filter · notes · dedup ref)
  │
  ▼
dedup
  │  imported_refs: set of entry_reference / transaction_id (settled txns)
  │  pending_map:   "<date>|<amount>" → Actual Budget txn UUID (PDNG txns)
  │                 when a booked txn matches a pending key, the existing
  │                 record is marked cleared rather than re-imported
  ▼
actualpy
  │  reconcile_transaction or create_transaction
  │  + run_rules with two patches:
  │      payee_name → description           (otherwise rules fail validation)
  │      restore SET-notes original casing  (actualpy lowercases values)
  ▼
Actual Budget
```

Sessions from Enable Banking last about 180 days. After that the bank account row in `accounts.json` becomes stale and you'll need to re-run `/connect` for it.

---

## State

`state.json` (auto-managed):

```json
{
  "last_run": "2026-04-27T14:32:11",
  "accounts": {
    "1": {
      "last_sync_date": "2026-04-27",
      "imported_refs": ["..."],
      "pending_map": {"2026-04-25|15.50": "actual-txn-uuid"}
    }
  },
  "pending_oauth": {}
}
```

`pending_oauth` holds short-lived metadata between `/connect` and `/callback`, keyed by the OAuth state UUID. It's persisted so a process restart mid-flow doesn't lose the bank's redirect.

To re-import an account from scratch: stop the connector, delete that account's entry under `state.accounts.<id>`, restart.

---

## Running unattended

The simplest option on Linux is a `systemd` user service that runs `python connector.py` and restarts on failure. On Windows, NSSM or a scheduled task that wraps `python connector.py` works the same way.

The process is single-threaded for sync (a `_sync_lock` prevents the manual `/sync` endpoint and the background thread from overlapping), so multiple banks are synced sequentially with a 2-second pause between accounts to be polite to Enable Banking's rate limits.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401`/`403` on `/transactions` | Enable Banking session expired — re-run `/connect` for that bank |
| `429` on every page | Hit the rate limit; the connector backs off automatically, but persistent 429s mean you're syncing too often |
| `actualpy apply_change` SQLite error | The `_patch_actualpy` block at the top of `connector.py` should prevent this on Actual Budget ≥ 26.3.0; check it imported successfully |
| Rules don't apply to imported transactions | Confirm `_patch_payee_name_rules` ran (no error in logs); some rule actions still aren't supported by actualpy |
| `400 Unknown OAuth state` on `/callback` | `state.json` was wiped, or you took longer than the bank's auth window — restart from `/connect` |

---

## Credits

Sync logic adapted from [bridge-bank](https://github.com/dazaro/bridge-bank) by David Alves (MIT + Commons Clause). Personal/private use only — see the bridge-bank repo's `LICENSE` for details.
