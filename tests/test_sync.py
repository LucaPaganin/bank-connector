"""Dedup core (no actualpy) + SyncService orchestration."""
import datetime
import decimal
from unittest.mock import MagicMock

import pytest

from bank_connector.parsing import ParsedTransaction
from bank_connector.sync import AccountSyncer, SyncService
from helpers import FakeTxn, RecordingImporter


def _parsed(status="BOOK", ref="ref-1", amount="-10.00"):
    return ParsedTransaction(
        date=datetime.date(2026, 5, 1),
        amount=decimal.Decimal(amount),
        payee="Shop",
        notes="n",
        ref=ref,
        status=status,
    )


@pytest.fixture
def syncer(tmp_path):
    return AccountSyncer(eb_client=MagicMock(), actual_data_dir=tmp_path)


def _import(syncer, parsed, **kw):
    defaults = dict(
        parsed=parsed,
        skip_pending=False,
        pending_map={},
        imported_refs=set(),
        existing=[],
        already_matched=[],
        actual_session=MagicMock(),
        account_obj=MagicMock(),
        reconcile=RecordingImporter(),
        create=RecordingImporter(),
    )
    defaults.update(kw)
    return defaults, syncer._import_one(**defaults)


# --- _import_one routing & dedup -------------------------------------------

def test_booked_skipped_when_ref_already_imported(syncer):
    rec = RecordingImporter()
    _, out = _import(
        syncer, _parsed(ref="seen"), imported_refs={"seen"}, reconcile=rec
    )
    assert out.skipped == 1 and out.added == 0
    assert rec.calls == []


def test_pending_skipped_when_skip_pending(syncer):
    _, out = _import(syncer, _parsed(status="PDNG"), skip_pending=True)
    assert out.skipped == 1


def test_new_pending_records_pending_map(syncer):
    p = _parsed(status="PDNG")
    pmap = {}
    _, out = _import(syncer, p, pending_map=pmap, reconcile=RecordingImporter(changed=True))
    assert out.added == 1
    assert pmap[p.key]  # txn id recorded


def test_duplicate_pending_key_skipped(syncer):
    p = _parsed(status="PDNG")
    rec = RecordingImporter()
    _, out = _import(syncer, p, pending_map={p.key: "old"}, reconcile=rec)
    assert out.skipped == 1
    assert rec.calls == []


def test_pending_to_booked_clears_existing(syncer):
    p = _parsed(status="BOOK", ref="r9")
    existing_txn = FakeTxn(txn_id="T1")
    pmap = {p.key: "T1"}
    refs = set()
    _, out = _import(
        syncer, p, pending_map=pmap, existing=[existing_txn], imported_refs=refs
    )
    assert out.updated == 1
    assert existing_txn.cleared is True
    assert p.key not in pmap
    assert "r9" in refs


def test_new_booked_added_and_ref_tracked(syncer):
    p = _parsed(status="BOOK", ref="r-new")
    refs = set()
    _, out = _import(
        syncer, p, imported_refs=refs, reconcile=RecordingImporter(changed=True)
    )
    assert out.added == 1
    assert "r-new" in refs


def test_reconcile_failure_falls_back_to_create(syncer):
    p = _parsed(status="BOOK", ref="rf")
    create = RecordingImporter(changed=True)
    _, out = _import(
        syncer,
        p,
        reconcile=RecordingImporter(raise_on_call=True),
        create=create,
    )
    assert out.added == 1
    assert len(create.calls) == 1


# --- SyncService orchestration ---------------------------------------------

def _service(monkeypatch, accounts, recorded):
    cfg_repo = MagicMock()
    cfg_repo.load.return_value = {
        "account_holder_name": "Luca Paganin",
        "actual": {"url": "u", "password": "p", "sync_id": "s"},
        "accounts": accounts,
    }
    state_repo = MagicMock()
    state_repo.load.return_value = {}
    svc = SyncService(
        eb_client=MagicMock(),
        config_repo=cfg_repo,
        state_repo=state_repo,
        actual_data_dir="/tmp",
        interval_hours=24,
    )

    def fake_sync(account, state, actual_cfg, own_names):
        recorded.append((account["id"], own_names))
        return account["id"]  # use id as the "added" count

    monkeypatch.setattr(svc._syncer, "sync", fake_sync)
    return svc, state_repo


def test_run_iterates_accounts_and_sums(monkeypatch):
    recorded = []
    svc, state_repo = _service(
        monkeypatch, [{"id": 2}, {"id": 3}], recorded
    )
    monkeypatch.setattr("bank_connector.sync.time.sleep", lambda *_: None)
    total = svc.run()
    assert total == 5
    assert [r[0] for r in recorded] == [2, 3]
    # own_names threaded through, parsed from account_holder_name.
    assert recorded[0][1] == frozenset({"luca paganin"})
    saved = state_repo.save.call_args[0][0]
    assert "last_run" in saved


def test_run_is_non_blocking_when_locked(monkeypatch):
    recorded = []
    svc, _ = _service(monkeypatch, [{"id": 1}], recorded)
    svc._lock.acquire()
    try:
        assert svc.run() == 0
        assert recorded == []
    finally:
        svc._lock.release()
