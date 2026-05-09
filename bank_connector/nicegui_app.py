"""NiceGUI interface for Bank Connector.

All UI event handlers call Python objects directly — no HTTP round-trip
between browser and backend. The single /callback route is a FastAPI
endpoint (NiceGUI's underlying ASGI framework) needed for the OAuth redirect.
"""
import asyncio
import datetime
import logging
import threading

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from nicegui import app, ui

from bank_connector.csv_export import transactions_to_csv
from bank_connector.enable_banking import EnableBankingClient
from bank_connector.parsing import parse_own_names, parse_transaction
from bank_connector.storage import ConfigRepository, StateRepository
from bank_connector.sync import SyncService

log = logging.getLogger("connector")


def create_nicegui_ui(
    *,
    config_repo: ConfigRepository,
    state_repo: StateRepository,
    eb_client: EnableBankingClient,
    sync_service: SyncService,
) -> None:
    """Register all NiceGUI pages and the OAuth callback route."""

    # ── OAuth callback (bank redirects here after user authorises) ────────────
    @app.get("/callback")
    async def oauth_callback(request: Request):
        code = request.query_params.get("code")
        state_val = request.query_params.get("state")
        if not code or not state_val:
            return Response("Missing code or state", status_code=400)

        state = state_repo.load()
        pending = state.get("pending_oauth", {}).pop(state_val, None)
        if not pending:
            return Response("Unknown OAuth state", status_code=400)
        state_repo.save(state)

        try:
            result = await asyncio.to_thread(eb_client.complete_auth, code, state_val)
        except Exception as exc:
            log.error("complete_auth failed: %s", exc)
            return Response(f"Auth error: {exc}", status_code=502)

        session_id = result["session_id"]
        bank_accounts = result.get("accounts", [])
        if not bank_accounts:
            return Response("Bank returned no accounts.", status_code=400)

        cfg = config_repo.load()
        cfg.setdefault("accounts", [])
        next_id = max((a.get("id", 0) for a in cfg["accounts"]), default=0) + 1
        added_ids: list[int] = []
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
            added_ids.append(next_id)
            next_id += 1
        config_repo.save(cfg)
        return RedirectResponse(
            url=f"/connected?ids={','.join(str(i) for i in added_ids)}"
        )

    # ── Status dashboard ──────────────────────────────────────────────────────
    @ui.page("/")
    def index():
        cfg = config_repo.load()
        state = state_repo.load()
        accounts = cfg.get("accounts", [])
        today = datetime.date.today()
        warn_threshold = today + datetime.timedelta(days=30)

        with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-4"):
            ui.label("Bank Connector").classes("text-3xl font-bold")
            ui.label(
                f"Last sync: {state.get('last_run', 'never')}"
            ).classes("text-sm text-gray-500")

            with ui.row().classes("gap-2 items-center"):

                async def do_sync():
                    sync_btn.disable()
                    ui.notify("Sync started")
                    threading.Thread(target=sync_service.run, daemon=True).start()
                    sync_btn.enable()

                sync_btn = ui.button("Sync Now", on_click=do_sync, icon="sync")
                ui.button(
                    "Connect Bank",
                    icon="add_link",
                    on_click=lambda: ui.navigate.to("/connect"),
                ).props("outline")

            ui.separator()
            ui.label("Connected Accounts").classes("text-lg font-semibold mt-2")

            if not accounts:
                ui.label(
                    "No accounts connected. Connect your first bank."
                ).classes("text-gray-400 text-sm")
            else:
                for a in accounts:
                    exp = a.get("session_expiry", "")
                    expiring_soon = bool(
                        exp and exp[:10] <= warn_threshold.isoformat()
                    )
                    with ui.card().classes("w-full"):
                        with ui.row().classes(
                            "items-center justify-between w-full gap-4"
                        ):
                            with ui.column().classes("gap-0 flex-1"):
                                ui.label(
                                    f"{a.get('bank_name', '—')} → {a['actual_account']}"
                                ).classes("font-medium")
                                if exp:
                                    color = (
                                        "text-yellow-600"
                                        if expiring_soon
                                        else "text-green-600"
                                    )
                                    suffix = " ⚠" if expiring_soon else ""
                                    ui.label(
                                        f"Session expires: {exp[:10]}{suffix}"
                                    ).classes(f"text-sm {color}")
                            with ui.row().classes("gap-1 shrink-0"):
                                ui.button(
                                    "Export CSV",
                                    icon="download",
                                    on_click=lambda a=a: _export_dialog(
                                        a, config_repo, eb_client
                                    ),
                                ).props("flat dense")
                                ui.button(
                                    "Re-connect",
                                    icon="refresh",
                                    on_click=lambda a=a: ui.navigate.to(
                                        f"/connect?bank_name={a.get('bank_name', '')}"
                                        f"&country={a.get('country', '')}"
                                    ),
                                ).props("flat dense")

    # ── Connect bank form ─────────────────────────────────────────────────────
    @ui.page("/connect")
    async def connect_page(bank_name: str = "", country: str = ""):
        with ui.column().classes("w-full max-w-xl mx-auto p-6 gap-4"):
            with ui.row().classes("items-center gap-2"):
                ui.button(
                    icon="arrow_back", on_click=lambda: ui.navigate.to("/")
                ).props("flat round")
                ui.label("Connect Bank").classes("text-2xl font-bold")

            with ui.row().classes("items-center gap-2") as loading_row:
                ui.spinner(size="sm")
                ui.label("Loading banks...").classes("text-sm text-gray-500")

            try:
                banks = await asyncio.to_thread(eb_client.list_banks)
            except Exception as exc:
                ui.notify(f"Could not load banks: {exc}", color="negative")
                banks = []

            loading_row.set_visibility(False)

            bank_options = [f"{b['name']} ({b['country']})" for b in banks]
            bank_map = {f"{b['name']} ({b['country']})": b for b in banks}

            pre_select = None
            if bank_name and country:
                key = f"{bank_name} ({country})"
                if key in bank_map:
                    pre_select = key

            with ui.column().classes("w-full gap-3"):
                bank_sel = ui.select(
                    bank_options,
                    label="Bank",
                    value=pre_select,
                    with_input=True,
                ).classes("w-full")
                actual_input = ui.input(
                    "Actual Budget account name"
                ).classes("w-full")
                today_str = datetime.date.today().isoformat()
                date_input = ui.input(
                    "Start sync date", value=today_str
                ).props("type=date").classes("w-full")
                psu_input = ui.select(
                    ["personal", "business"],
                    label="Account type",
                    value="personal",
                ).classes("w-full")

                async def do_connect():
                    if not bank_sel.value:
                        ui.notify("Select a bank", color="negative")
                        return
                    if not actual_input.value.strip():
                        ui.notify(
                            "Enter the Actual Budget account name",
                            color="negative",
                        )
                        return

                    bank = bank_map[bank_sel.value]
                    connect_btn.disable()
                    try:
                        state_val, valid_until, auth_url = await asyncio.to_thread(
                            eb_client.start_auth,
                            bank["name"],
                            bank["country"],
                            psu_input.value,
                        )
                    except Exception as exc:
                        ui.notify(f"Error: {exc}", color="negative")
                        connect_btn.enable()
                        return

                    st = state_repo.load()
                    st.setdefault("pending_oauth", {})[state_val] = {
                        "bank_name": bank["name"],
                        "country": bank["country"],
                        "actual_account": actual_input.value.strip(),
                        "start_sync_date": date_input.value or today_str,
                        "valid_until": valid_until,
                    }
                    state_repo.save(st)
                    ui.navigate.to(auth_url)

                connect_btn = ui.button(
                    "Authorise with bank", icon="open_in_new", on_click=do_connect
                )

    # ── Post-OAuth success ────────────────────────────────────────────────────
    @ui.page("/connected")
    def connected_page(ids: str = ""):
        cfg = config_repo.load()
        id_list = (
            [int(i) for i in ids.split(",") if i.isdigit()] if ids else []
        )
        added = [a for a in cfg.get("accounts", []) if a["id"] in id_list]

        with ui.column().classes("w-full max-w-xl mx-auto p-6 gap-4"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("check_circle", color="positive", size="2.5rem")
                ui.label("Bank connected!").classes("text-2xl font-bold")

            if added:
                ui.label(
                    "The following accounts were added:"
                ).classes("text-gray-600 text-sm")
                for a in added:
                    with ui.card().classes("w-full"):
                        ui.label(
                            f"{a.get('bank_name', '—')} → {a['actual_account']}"
                        ).classes("font-medium")
                        ui.label(a["account_uid"]).classes(
                            "text-xs text-gray-400 font-mono"
                        )

                ui.label(
                    "If multiple accounts were returned, edit accounts.json "
                    "to remap them to different Actual Budget accounts."
                ).classes("text-xs text-gray-500 mt-1")

            ui.button(
                "Back to dashboard",
                icon="home",
                on_click=lambda: ui.navigate.to("/"),
            ).classes("mt-4")


def _export_dialog(
    account: dict,
    config_repo: ConfigRepository,
    eb_client: EnableBankingClient,
) -> None:
    """Open a date-range picker dialog and download the resulting CSV."""
    today = datetime.date.today()
    default_from = (today - datetime.timedelta(days=90)).isoformat()

    with ui.dialog() as dialog, ui.card().style("min-width: 26rem"):
        ui.label(
            f"Export — {account.get('bank_name', '')} → {account['actual_account']}"
        ).classes("font-semibold text-base")

        date_from_in = (
            ui.input("From", value=default_from).props("type=date").classes("w-full")
        )
        date_to_in = (
            ui.input("To", value=today.isoformat()).props("type=date").classes("w-full")
        )

        async def do_export():
            try:
                df = datetime.date.fromisoformat(date_from_in.value)
                dt = datetime.date.fromisoformat(date_to_in.value)
            except ValueError:
                ui.notify("Invalid date format", color="negative")
                return

            dl_btn.disable()
            try:
                cfg = config_repo.load()
                own_names = parse_own_names(cfg.get("account_holder_name", ""))
                raw = await asyncio.to_thread(
                    eb_client.fetch_transactions,
                    account["account_uid"],
                    df,
                    date_to=dt,
                )
                parsed = sorted(
                    (parse_transaction(t, own_names) for t in raw),
                    key=lambda t: t.date,
                    reverse=True,
                )
                csv_text = transactions_to_csv(parsed)
                filename = f"transactions_{account['id']}_{df}_{dt}.csv"
                ui.download(csv_text.encode(), filename)
                dialog.close()
            except Exception as exc:
                ui.notify(f"Export failed: {exc}", color="negative")
            finally:
                dl_btn.enable()

        with ui.row().classes("justify-end gap-2 w-full mt-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            dl_btn = ui.button("Download", icon="download", on_click=do_export)

    dialog.open()
