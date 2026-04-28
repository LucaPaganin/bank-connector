"""Convert raw Enable Banking transaction dicts into typed `ParsedTransaction`s.

Pure functions — no I/O, no state. `own_names` is passed in so the parser
doesn't need to know about config.
"""
import datetime
import decimal
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedTransaction:
    date: datetime.date
    amount: decimal.Decimal
    payee: str
    notes: str
    ref: str
    status: str  # "BOOK" or "PDNG"

    @property
    def key(self) -> str:
        """Date|amount key used for pending → booked dedup."""
        return f"{self.date}|{self.amount}"


def parse_own_names(raw: str) -> frozenset[str]:
    return frozenset(n.strip().lower() for n in (raw or "").split(",") if n.strip())


def parse_transaction(t: dict, own_names: frozenset[str]) -> ParsedTransaction:
    payee = _parse_payee(t, own_names)
    notes = _parse_notes(t)
    if notes and notes.strip().lower() == payee.strip().lower():
        notes = ""
    return ParsedTransaction(
        date=_parse_date(t),
        amount=_parse_amount(t),
        payee=payee,
        notes=notes,
        ref=_entry_ref(t),
        status=t.get("status", "BOOK"),
    )


def _parse_date(t: dict) -> datetime.date:
    raw = t.get("booking_date") or t.get("value_date") or t.get("transaction_date")
    if not raw:
        raise ValueError("No date in transaction")
    return datetime.date.fromisoformat(raw[:10])


def _parse_amount(t: dict) -> decimal.Decimal:
    amt = decimal.Decimal(str((t.get("transaction_amount") or {}).get("amount", "0")))
    indic = t.get("credit_debit_indicator") or t.get("credit_debit_indic", "")
    return -abs(amt) if indic.upper() == "DBIT" else abs(amt)


def _parse_payee(t: dict, own_names: frozenset[str]) -> str:
    indic = (t.get("credit_debit_indicator") or t.get("credit_debit_indic", "")).upper()
    if indic == "DBIT":
        name = (t.get("creditor") or {}).get("name") or t.get("creditor_name")
        if not name:
            ri = t.get("remittance_information")
            name = ri[0] if isinstance(ri, list) else ri
    else:
        name = (t.get("debtor") or {}).get("name") or t.get("debtor_name")
        if not name or (own_names and name.lower() in own_names):
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
