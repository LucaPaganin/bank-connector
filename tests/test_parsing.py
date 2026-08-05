"""Pure parsing logic — the highest-value unit tests."""
import datetime
import decimal

import pytest

from bank_connector.parsing import (
    ParsedTransaction,
    parse_own_names,
    parse_transaction,
)
from helpers import raw_txn


# --- parse_own_names --------------------------------------------------------

def test_parse_own_names_splits_strips_lowercases():
    assert parse_own_names("Luca Paganin, FOO ,, bar") == frozenset(
        {"luca paganin", "foo", "bar"}
    )


def test_parse_own_names_empty():
    assert parse_own_names("") == frozenset()
    assert parse_own_names("   ") == frozenset()


# --- fixtures round-trip ----------------------------------------------------

def test_parse_all_revolut_fixture(revolut_txns):
    parsed = [parse_transaction(t, frozenset(), "Revolut") for t in revolut_txns]
    assert len(parsed) == len(revolut_txns)
    assert all(isinstance(p, ParsedTransaction) for p in parsed)


def test_parse_all_fineco_fixture(fineco_txns):
    parsed = [parse_transaction(t, frozenset(), "FinecoBank") for t in fineco_txns]
    assert len(parsed) == 4


# --- amount sign ------------------------------------------------------------

def test_debit_is_negative():
    p = parse_transaction(raw_txn(credit_debit_indicator="DBIT"), frozenset())
    assert p.amount == decimal.Decimal("-12.34")


def test_credit_is_positive():
    p = parse_transaction(
        raw_txn(credit_debit_indicator="CRDT", creditor=None, debtor={"name": "Boss"}),
        frozenset(),
    )
    assert p.amount == decimal.Decimal("12.34")


# --- date parsing -----------------------------------------------------------

def test_date_precedence_booking_first():
    p = parse_transaction(
        raw_txn(booking_date="2026-01-02", value_date="2026-03-04"), frozenset()
    )
    assert p.date == datetime.date(2026, 1, 2)


def test_date_falls_back_to_value_then_transaction():
    p = parse_transaction(
        raw_txn(booking_date=None, value_date=None, transaction_date="2026-07-08"),
        frozenset(),
    )
    assert p.date == datetime.date(2026, 7, 8)


def test_missing_date_raises():
    with pytest.raises(ValueError):
        parse_transaction(
            raw_txn(booking_date=None, value_date=None, transaction_date=None),
            frozenset(),
        )


# --- payee selection --------------------------------------------------------

def test_debit_payee_is_creditor():
    p = parse_transaction(
        raw_txn(credit_debit_indicator="DBIT", creditor={"name": "Acme"}), frozenset()
    )
    assert p.payee == "Acme"


def test_own_name_falls_back_to_remittance():
    p = parse_transaction(
        raw_txn(
            credit_debit_indicator="CRDT",
            creditor=None,
            debtor={"name": "Luca Paganin"},
            remittance_information=["Salary March"],
        ),
        frozenset({"luca paganin"}),
    )
    assert p.payee == "Salary March"


def test_unknown_payee_default():
    p = parse_transaction(
        raw_txn(credit_debit_indicator="DBIT", creditor=None, remittance_information=None),
        frozenset(),
    )
    assert p.payee == "Unknown"


# --- Fineco-specific payee extraction --------------------------------------

def test_fineco_card_payment(fineco_txns):
    p = parse_transaction(fineco_txns[0], frozenset(), "FinecoBank")
    assert p.payee == "MICROSOFTG156992423 MSBILL.INFO IE"


def test_fineco_sdd(fineco_txns):
    p = parse_transaction(fineco_txns[1], frozenset(), "FinecoBank")
    assert p.payee == "Scalable Capital Bank GmbH"


def test_fineco_incoming_wire_ord_ben(fineco_txns):
    p = parse_transaction(fineco_txns[3], frozenset(), "FinecoBank")
    assert p.payee == "RINA CONSULTING SPA"


def test_fineco_logic_skipped_for_other_banks(fineco_txns):
    # Without the FinecoBank hint the generic path runs instead.
    p = parse_transaction(fineco_txns[0], frozenset(), "Revolut")
    assert "Carta N." in p.payee or p.payee == "Unknown"


# --- notes building ---------------------------------------------------------

def test_notes_include_iban_card_mcc_currency_ref():
    p = parse_transaction(
        raw_txn(
            credit_debit_indicator="DBIT",
            creditor={"name": "Shop"},
            creditor_account={"iban": "IT60X0542811101000000123456"},
            debtor_account_additional_identification=[
                {"identification": "1234", "issuer": "VISA"}
            ],
            merchant_category_code="5411",
            reference_number="RN-9",
            transaction_amount={"amount": "5.00", "currency": "USD"},
            remittance_information=["Groceries"],
        ),
        frozenset(),
    )
    assert "Groceries" in p.notes
    assert "VISA 1234" in p.notes
    assert "IBAN: IT60X0542811101000000123456" in p.notes
    assert "MCC: 5411" in p.notes
    assert "Ref: RN-9" in p.notes
    assert "Currency: USD" in p.notes


def test_notes_skip_remittance_equal_to_payee():
    p = parse_transaction(
        raw_txn(creditor={"name": "Acme"}, remittance_information=["acme"]),
        frozenset(),
    )
    assert "acme" not in p.notes.lower().split(" | ")


# --- ParsedTransaction.key --------------------------------------------------

def test_key_is_date_pipe_amount():
    p = ParsedTransaction(
        date=datetime.date(2026, 5, 1),
        amount=decimal.Decimal("-9.99"),
        payee="x",
        notes="",
        ref="r",
        status="BOOK",
    )
    assert p.key == "2026-05-01|-9.99"


def test_transaction_keeps_all_bank_identifiers_for_deduplication():
    parsed = parse_transaction(
        raw_txn(entry_reference="entry-123", transaction_id="transaction-456"),
        frozenset(),
    )

    assert parsed.ref == "entry-123"
    assert parsed.identifiers == frozenset({"entry-123", "transaction-456"})
