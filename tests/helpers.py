"""Shared test helpers: raw-transaction builder and actualpy doubles."""
from __future__ import annotations

import itertools
from typing import Any


def raw_txn(**overrides: Any) -> dict:
    """Build a minimal Enable Banking transaction dict, overridable per field."""
    base: dict[str, Any] = {
        "booking_date": "2026-05-10",
        "status": "BOOK",
        "credit_debit_indicator": "DBIT",
        "transaction_amount": {"amount": "12.34", "currency": "EUR"},
        "entry_reference": "ref-1",
        "creditor": {"name": "Some Shop"},
    }
    base.update(overrides)
    return base


class FakeTxn:
    """Stand-in for an actualpy transaction object used by AccountSyncer."""

    _ids = itertools.count(1)

    def __init__(self, *, changed: bool = True, txn_id: str | None = None) -> None:
        self.id = txn_id if txn_id is not None else f"txn-{next(self._ids)}"
        self.cleared = False
        self._changed = changed

    def changed(self) -> bool:
        return self._changed


class RecordingImporter:
    """Callable double for actualpy's reconcile/create.

    Records every call and returns a fresh ``FakeTxn``. Set ``changed`` to
    control whether the returned txn reports a change (drives added/skipped).
    Set ``raise_on_call`` to force the reconcile->create fallback path.
    """

    def __init__(self, *, changed: bool = True, raise_on_call: bool = False) -> None:
        self.changed = changed
        self.raise_on_call = raise_on_call
        self.calls: list[dict] = []

    def __call__(self, *args: Any, **kwargs: Any) -> FakeTxn:
        self.calls.append({"args": args, "kwargs": kwargs})
        if self.raise_on_call:
            raise RuntimeError("forced fallback")
        return FakeTxn(changed=self.changed)
