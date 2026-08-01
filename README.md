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

## Server deployment (Docker)

The Compose stack runs the connector as a non-root container on an `internal` Docker network shared only with Actual. The connector publishes port 3000 on loopback only; expose it to Tailscale using the NAS/Tailscale setup, never with port forwarding. Copy `accounts.example.json` to `accounts.json`, then set the required values in `.env` (`BANK_CONN_SYNC_TOKEN`, `ACTUAL_PASSWORD`, `ENABLE_BANKING_APPLICATION_ID`, `BANK_CONN_BASE_URL`, and `BANK_CONN_REDIRECT_URL`). The private Enable Banking key is mounted read-only and Actual is reached as `http://actual-server:5006`.

The scheduler uses APScheduler and runs every 6 hours by default in Compose. `POST /sync/refresh` and `GET /sync/status` require `X-Sync-Token`; `/sync` is retained as a backwards-compatible alias and has the same protection. Status is persisted in the state volume per account, including `last_sync_succeeded_at`, `ok`, `error`, or `reauthorization_required`.

```sh
cp accounts.example.json accounts.json
export BANK_CONN_SYNC_TOKEN="$(openssl rand -hex 32)"
export ACTUAL_PASSWORD='...'
export ENABLE_BANKING_APPLICATION_ID='...'
export BANK_CONN_BASE_URL='https://your-tailnet-host:8443'
export BANK_CONN_REDIRECT_URL="$BANK_CONN_BASE_URL/callback"
docker compose up -d --build
curl -i -X POST http://127.0.0.1:3000/sync/refresh -H "X-Sync-Token: $BANK_CONN_SYNC_TOKEN"
curl -s http://127.0.0.1:3000/sync/status -H "X-Sync-Token: $BANK_CONN_SYNC_TOKEN"
```

Run the refresh twice and verify in Actual that the second run reports no additions and does not create duplicates. A 401/403 from Enable Banking is reported as `reauthorization_required`; re-run the interactive `/connect` flow rather than retrying aggressively.

## Test MCP con Actual Budget sul NAS

Il servizio `actual-mcp` incluso nel Compose usa l'immagine `sstefanov/actual-mcp`, si collega a `http://192.168.1.148:5006` e ascolta SSE solamente su `127.0.0.1:3001`. È protetto da bearer token; non viene esposto su Internet o sulla rete Docker interna del connector.

Impostare anche questi valori nel file `.env` (non committarlo):

```dotenv
ACTUAL_PASSWORD=...
ACTUAL_BUDGET_SYNC_ID=...
ACTUAL_MCP_TOKEN=...
```

Avvio e verifica:

```sh
docker compose up -d actual-mcp
docker compose logs -f actual-mcp
curl -i http://127.0.0.1:3001/ -H "Authorization: Bearer $ACTUAL_MCP_TOKEN"
```

Per collegare un client MCP compatibile usare l'endpoint SSE `http://127.0.0.1:3001` con il bearer token. Ad esempio, per Codex:

```toml
[mcp_servers.actual-budget]
url = "http://127.0.0.1:3001"
# configurare il token Authorization secondo il client utilizzato
```

Il server espone strumenti di sola lettura e scrittura (la Compose abilita `--enable-write` solo se lo si aggiunge esplicitamente al comando). Prima del primo test di scrittura verificare budget e sync ID con una query di lettura.

Per usare questo MCP direttamente con Pi è stata aggiunta l'estensione `.pi/extensions/actual-mcp/index.ts`. Dopo aver avviato il container, riavviare Pi nel repository oppure usare `/reload` dopo aver approvato il progetto (`pi --approve`). L'estensione legge `ACTUAL_MCP_URL` e `ACTUAL_MCP_TOKEN` dall'ambiente o dal file locale `.env`, scopre gli strumenti MCP e li rende disponibili al modello tramite il tool `actual_budget`. Se la connessione fallisce il tool mostra l'errore senza esporre il token.

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
  │  JWT (RS256, 1-hour exp) -> Bearer token on every request
  │
  │  GET /accounts/<uid>/transactions  (paginated, rate-limit aware)
  ▼
parse  (date · amount sign from credit_debit_indicator · payee with self-transfer filter · notes · dedup ref)
  │
  ▼
dedup
  │  imported_refs: set of entry_reference / transaction_id (settled txns)
  │  pending_map:   "<date>|<amount>" -> Actual Budget txn UUID (PDNG txns)
  │                 when a booked txn matches a pending key, the existing
  │                 record is marked cleared rather than re-imported
  ▼
actualpy
  │  reconcile_transaction or create_transaction
  │  + run_rules with two patches:
  │      payee_name -> description           (otherwise rules fail validation)
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
