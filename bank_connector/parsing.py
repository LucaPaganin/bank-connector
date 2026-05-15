"""Convert raw Enable Banking transaction dicts into typed `ParsedTransaction`s.

Pure functions — no I/O, no state. `own_names` is passed in so the parser
doesn't need to know about config.
"""
import datetime
import decimal
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enable Banking raw transaction schema
# ---------------------------------------------------------------------------

class _BankTransactionCode(BaseModel):
    model_config = {"extra": "ignore"}
    code: str | None = None
    description: str | None = None
    sub_code: str | None = None


class _Party(BaseModel):
    model_config = {"extra": "ignore"}
    name: str | None = None


class _Account(BaseModel):
    model_config = {"extra": "ignore"}
    iban: str | None = None


class _CardId(BaseModel):
    model_config = {"extra": "ignore"}
    identification: str | None = None  # last 4 digits
    issuer: str | None = None          # VISA, MASTERCARD, …
    scheme_name: str | None = None


class _TransactionAmount(BaseModel):
    model_config = {"extra": "ignore"}
    amount: str = "0"
    currency: str = "EUR"


class EnableBankingTransaction(BaseModel):
    """Pydantic schema for a single Enable Banking transaction payload."""

    model_config = {"extra": "ignore"}

    booking_date: str | None = None
    value_date: str | None = None
    transaction_date: str | None = None
    status: str = "BOOK"
    credit_debit_indicator: str | None = None
    transaction_amount: _TransactionAmount = _TransactionAmount()
    entry_reference: str | None = None
    transaction_id: str | None = None
    bank_transaction_code: _BankTransactionCode | None = None
    creditor: _Party | None = None
    creditor_account: _Account | None = None
    debtor: _Party | None = None
    debtor_account: _Account | None = None
    debtor_account_additional_identification: list[_CardId] | None = None
    remittance_information: list[str] | str | None = None
    remittance_information_unstructured: str | None = None
    note: str | None = None
    reference_number: str | None = None
    merchant_category_code: str | None = None


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

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
        """Date|amount key used for pending -> booked dedup."""
        return f"{self.date}|{self.amount}"


def parse_own_names(raw: str) -> frozenset[str]:
    return frozenset(n.strip().lower() for n in (raw or "").split(",") if n.strip())


def parse_transaction(t: dict, own_names: frozenset[str]) -> ParsedTransaction:
    txn = EnableBankingTransaction.model_validate(t)
    payee = _parse_payee(txn, own_names)
    notes = _build_notes(txn, payee)
    return ParsedTransaction(
        date=_parse_date(txn),
        amount=_parse_amount(txn),
        payee=payee,
        notes=notes,
        ref=txn.entry_reference or txn.transaction_id or "",
        status=txn.status,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_date(txn: EnableBankingTransaction) -> datetime.date:
    raw = txn.booking_date or txn.value_date or txn.transaction_date
    if not raw:
        raise ValueError("No date in transaction")
    return datetime.date.fromisoformat(raw[:10])


def _parse_amount(txn: EnableBankingTransaction) -> decimal.Decimal:
    amt = decimal.Decimal(txn.transaction_amount.amount)
    indic = (txn.credit_debit_indicator or "").upper()
    return -abs(amt) if indic == "DBIT" else abs(amt)


def _remittance_text(txn: EnableBankingTransaction) -> str:
    if txn.remittance_information_unstructured:
        return txn.remittance_information_unstructured
    ri = txn.remittance_information
    if isinstance(ri, list):
        return " ".join(ri)
    return ri or ""


def _parse_payee(txn: EnableBankingTransaction, own_names: frozenset[str]) -> str:
    indic = (txn.credit_debit_indicator or "").upper()
    if indic == "DBIT":
        name = txn.creditor.name if txn.creditor else None
        if not name:
            name = _remittance_text(txn)
    else:
        name = txn.debtor.name if txn.debtor else None
        if not name or (own_names and name.lower() in own_names):
            name = _remittance_text(txn)
    return name or "Unknown"


def _build_notes(txn: EnableBankingTransaction, payee: str) -> str:
    parts: list[str] = []

    # Primary description from remittance info — skip if identical to payee
    ri = _remittance_text(txn)
    if ri and ri.strip().lower() != payee.strip().lower():
        parts.append(ri)

    # Free-text note field
    if txn.note:
        parts.append(txn.note)

    # Transaction type code (TRANSFER, CARD_PAYMENT, ATM, TOPUP, …)
    if txn.bank_transaction_code and txn.bank_transaction_code.code:
        parts.append(txn.bank_transaction_code.code)

    # Card used: issuer + last-4 digits
    if txn.debtor_account_additional_identification:
        card = txn.debtor_account_additional_identification[0]
        if card.issuer and card.identification:
            parts.append(f"{card.issuer} {card.identification}")
        elif card.identification:
            parts.append(f"Card {card.identification}")

    # Counterparty IBAN
    indic = (txn.credit_debit_indicator or "").upper()
    if indic == "DBIT" and txn.creditor_account and txn.creditor_account.iban:
        parts.append(f"IBAN: {txn.creditor_account.iban}")
    elif indic == "CRDT" and txn.debtor_account and txn.debtor_account.iban:
        parts.append(f"IBAN: {txn.debtor_account.iban}")

    # Reference number
    if txn.reference_number:
        parts.append(f"Ref: {txn.reference_number}")

    # Merchant category code
    if txn.merchant_category_code:
        parts.append(f"MCC: {txn.merchant_category_code}")

    # Non-EUR currency
    if txn.transaction_amount.currency != "EUR":
        parts.append(f"Currency: {txn.transaction_amount.currency}")

    return " | ".join(parts)
