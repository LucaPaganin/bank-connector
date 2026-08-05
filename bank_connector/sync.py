"""Sync service.

`AccountSyncer` handles one account end-to-end: fetch -> parse -> reconcile into
Actual Budget, applying the two-layer dedup rules (booked refs + pending map).

`SyncService` runs the orchestrator across all configured accounts on a
non-blocking lock + a 24 h cadence by default. The lock is non-blocking on
purpose — manual `/sync` calls during a scheduled run no-op rather than queue.
"""
import datetime
import logging
import threading
import time
from pathlib import Path

from bank_connector.actual_patches import (
    fix_rule_note_casing,
    patch_payee_name_rules,
)
from bank_connector.enable_banking import ConsentExpiredError, EnableBankingClient
from bank_connector.parsing import (
    ParsedTransaction,
    parse_own_names,
    parse_transaction,
)
from bank_connector.settings import SYNC_INTERVAL_HOURS
from bank_connector.storage import ConfigRepository, StateRepository

log = logging.getLogger("connector")


class AccountSyncer:
    """Sync one Enable Banking account into one Actual Budget account."""

    def __init__(
        self, *, eb_client: EnableBankingClient, actual_data_dir: Path
    ) -> None:
        self._eb = eb_client
        self._data_dir = Path(actual_data_dir)

    def sync(
        self,
        account: dict,
        state: dict,
        actual_cfg: dict,
        own_names: frozenset[str],
    ) -> int:
        from actual import Actual
        from actual.queries import (
            create_transaction,
            get_or_create_account,
            get_transactions,
            reconcile_transaction,
        )

        account_id = str(account["id"])
        account_uid = account["account_uid"]
        actual_name = account["actual_account"]
        label = f"{account.get('bank_name', 'Bank')} -> {actual_name}"

        accounts_state = state.setdefault("accounts", {})
        acct_state = accounts_state.get(account_id, {})
        cursor_date = self._sync_window_start(account, state)
        date_from = cursor_date

        pending_map = acct_state.get("pending_map", {})
        if pending_map:
            earliest = min(
                datetime.date.fromisoformat(k.split("|")[0]) for k in pending_map
            )
            date_from = min(date_from, earliest)

        raw = self._eb.fetch_transactions(account_uid, date_from)
        if not raw:
            log.info("sync account=%s result=ok imported=0", account_id)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            acct_state["last_sync_succeeded_at"] = now
            acct_state["status"] = "ok"
            acct_state.pop("error", None)
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
            data_dir=str(self._data_dir),
        ) as actual:
            account_obj = get_or_create_account(actual.session, actual_name)
            existing = list(get_transactions(actual.session, account=account_obj))
            # Restore ID-based deduplication from Actual itself. This keeps a
            # replaced/lost state.json from causing duplicate imports.
            imported_refs.update(
                txn.financial_id
                for txn in existing
                if getattr(txn, "financial_id", None)
            )
            # actualpy treats already_matched as an EXCLUSION list for fuzzy
            # matching. It must start empty and grow per matched/created txn
            # during this run; seeding it with `existing` would hide every
            # prior transaction from fuzzy matching and re-create ref-less
            # booked transactions as duplicates on each sync.
            already_matched: list = []
            new_txn = []
            processed_transactions: list[ParsedTransaction] = []

            bank_name = account.get("bank_name", "")
            for raw_txn in raw:
                try:
                    parsed = parse_transaction(raw_txn, own_names, bank_name)
                except Exception as e:
                    log.warning("Skipping txn (%s)", e)
                    continue

                try:
                    outcome = self._import_one(
                        parsed=parsed,
                        skip_pending=skip_pending,
                        pending_map=pending_map,
                        imported_refs=imported_refs,
                        existing=existing,
                        already_matched=already_matched,
                        actual_session=actual.session,
                        account_obj=account_obj,
                        reconcile=reconcile_transaction,
                        create=create_transaction,
                    )
                except Exception as e:
                    log.warning("Skipping txn (%s)", e)
                    continue

                processed_transactions.append(parsed)
                if outcome.added_txn is not None:
                    new_txn.append(outcome.added_txn)
                added += outcome.added
                updated += outcome.updated
                skipped += outcome.skipped

            try:
                patch_payee_name_rules(actual.session)
                actual.run_rules(new_txn)
                fix_rule_note_casing(actual.session, new_txn)
            except Exception as e:
                log.error("Rule application error: %s", e)

            actual.commit()

        latest_booked_date = self._latest_booked_date(processed_transactions)
        if latest_booked_date:
            latest_booked_date = max(cursor_date, latest_booked_date)
            acct_state["latest_booked_date"] = latest_booked_date.isoformat()
            # Retain the historical key for external consumers of state.json.
            acct_state["last_sync_date"] = latest_booked_date.isoformat()

        log.info(
            "sync account=%s result=ok added=%d confirmed=%d skipped=%d",
            account_id, added, updated, skipped,
        )
        acct_state["last_sync_date"] = datetime.date.today().isoformat()
        acct_state["last_sync_succeeded_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        acct_state["status"] = "ok"
        acct_state.pop("error", None)
        acct_state["pending_map"] = pending_map
        acct_state["imported_refs"] = list(imported_refs)
        accounts_state[account_id] = acct_state
        return added

    def _sync_window_start(self, account: dict, state: dict) -> datetime.date:
        """Return the inclusive cursor for one bank account.

        The cursor is the booking date of the newest successfully processed
        booked transaction, not the date of the last execution. Re-fetching
        that date is deliberate: it catches multiple same-day bookings while
        identifier-based deduplication prevents duplicate imports.
        """
        account_id = str(account["id"])
        acct_state = state.get("accounts", {}).get(account_id, {})
        cursor = acct_state.get("latest_booked_date") or account.get(
            "start_sync_date"
        )
        if cursor:
            return datetime.date.fromisoformat(cursor)
        return datetime.date.today() - datetime.timedelta(days=30)

    @staticmethod
    def _latest_booked_date(
        transactions: list[ParsedTransaction],
    ) -> datetime.date | None:
        booked_dates = [txn.date for txn in transactions if txn.status == "BOOK"]
        return max(booked_dates) if booked_dates else None

    def _import_one(
        self,
        *,
        parsed: ParsedTransaction,
        skip_pending: bool,
        pending_map: dict,
        imported_refs: set,
        existing: list,
        already_matched: list,
        actual_session,
        account_obj,
        reconcile,
        create,
    ) -> "_ImportOutcome":
        if parsed.status == "PDNG" and skip_pending:
            return _ImportOutcome(skipped=1)

        if parsed.status == "PDNG":
            return self._import_pending(
                parsed,
                pending_map,
                already_matched,
                actual_session,
                account_obj,
                reconcile,
                create,
            )

        return self._import_booked(
            parsed,
            pending_map,
            imported_refs,
            existing,
            already_matched,
            actual_session,
            account_obj,
            reconcile,
            create,
        )

    def _import_pending(
        self,
        parsed,
        pending_map,
        already_matched,
        actual_session,
        account_obj,
        reconcile,
        create,
    ) -> "_ImportOutcome":
        if parsed.key in pending_map:
            return _ImportOutcome(skipped=1)
        try:
            t = reconcile(
                actual_session,
                parsed.date,
                account_obj,
                parsed.payee,
                parsed.notes,
                None,
                parsed.amount,
                imported_id=parsed.ref or None,
                cleared=False,
                imported_payee=parsed.payee,
                already_matched=already_matched,
            )
        except Exception:
            t = create(
                actual_session,
                parsed.date,
                account_obj,
                parsed.payee,
                parsed.notes,
                amount=parsed.amount,
                cleared=False,
                imported_payee=parsed.payee,
            )
        already_matched.append(t)
        if t.changed():
            pending_map[parsed.key] = str(t.id)
            return _ImportOutcome(added=1, added_txn=t)
        return _ImportOutcome(skipped=1)

    def _import_booked(
        self,
        parsed,
        pending_map,
        imported_refs,
        existing,
        already_matched,
        actual_session,
        account_obj,
        reconcile,
        create,
    ) -> "_ImportOutcome":
        identifiers = parsed.identifiers or frozenset(
            {parsed.ref} if parsed.ref else ()
        )
        if identifiers & imported_refs:
            return _ImportOutcome(skipped=1)

        # Pending -> booked: mark cleared, drop from pending_map.
        if parsed.key in pending_map:
            txn_id = pending_map[parsed.key]
            existing_txn = next((t for t in existing if str(t.id) == txn_id), None)
            outcome = _ImportOutcome()
            if existing_txn:
                existing_txn.cleared = True
                outcome.updated = 1
            del pending_map[parsed.key]
            imported_refs.update(identifiers)
            return outcome

        try:
            t = reconcile(
                actual_session,
                parsed.date,
                account_obj,
                parsed.payee,
                parsed.notes,
                None,
                parsed.amount,
                imported_id=parsed.ref or None,
                cleared=True,
                imported_payee=parsed.payee,
                already_matched=already_matched,
            )
        except Exception:
            t = create(
                actual_session,
                parsed.date,
                account_obj,
                parsed.payee,
                parsed.notes,
                amount=parsed.amount,
                cleared=True,
                imported_payee=parsed.payee,
            )
        already_matched.append(t)
        # Record identifiers even if Actual matched an existing transaction.
        # This makes the next run fast and prevents duplicate imports should
        # the provider replay the same inclusive cursor date.
        imported_refs.update(identifiers)
        if t.changed():
            return _ImportOutcome(added=1, added_txn=t)
        return _ImportOutcome(skipped=1)


class _ImportOutcome:
    """Counters returned per imported raw txn — added / updated / skipped."""

    def __init__(
        self, *, added: int = 0, updated: int = 0, skipped: int = 0, added_txn=None
    ) -> None:
        self.added = added
        self.updated = updated
        self.skipped = skipped
        self.added_txn = added_txn


class SyncService:
    """Orchestrates per-account syncs. Holds the non-blocking lock + scheduler."""

    def __init__(
        self,
        *,
        eb_client: EnableBankingClient,
        config_repo: ConfigRepository,
        state_repo: StateRepository,
        actual_data_dir: Path,
        interval_hours: int = SYNC_INTERVAL_HOURS,
        actual_overrides: dict | None = None,
    ) -> None:
        self._cfg_repo = config_repo
        self._state_repo = state_repo
        self._syncer = AccountSyncer(
            eb_client=eb_client, actual_data_dir=actual_data_dir
        )
        self._interval_hours = interval_hours
        self._actual_overrides = actual_overrides or {}
        self._lock = threading.Lock()

    @property
    def interval_hours(self) -> int:
        return self._interval_hours

    def run(self) -> int:
        if not self._lock.acquire(blocking=False):
            log.info("Sync already running, skipping")
            return 0
        try:
            log.info("Sync starting")
            cfg = self._cfg_repo.load()
            state = self._state_repo.load()
            own_names = parse_own_names(cfg.get("account_holder_name", ""))
            total = 0
            for i, acct in enumerate(cfg.get("accounts", [])):
                if i > 0:
                    time.sleep(2)
                try:
                    actual_cfg = {**cfg["actual"], **self._actual_overrides}
                    total += self._syncer.sync(acct, state, actual_cfg, own_names)
                except ConsentExpiredError as e:
                    account_id = str(acct.get("id"))
                    state.setdefault("accounts", {}).setdefault(account_id, {})
                    state["accounts"][account_id].update(
                        {"status": "reauthorization_required", "error": str(e)}
                    )
                    log.error("sync account=%s result=reauthorization_required", account_id)
                except Exception as e:
                    account_id = str(acct.get("id"))
                    state.setdefault("accounts", {}).setdefault(account_id, {})
                    state["accounts"][account_id].update(
                        {"status": "error", "error": str(e)[:500]}
                    )
                    log.error("sync account=%s result=error error=%s", account_id, e)
            state["last_run"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            self._state_repo.save(state)
            log.info("Sync finished. Total imported: %d", total)
            return total
        finally:
            self._lock.release()

    def scheduler_loop(self) -> None:
        while True:
            try:
                self.run()
            except Exception as e:
                log.exception("Sync loop error: %s", e)
            time.sleep(self._interval_hours * 3600)
