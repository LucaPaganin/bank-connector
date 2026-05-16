"""CSV serialization of parsed transactions."""
import csv
import datetime
import decimal
import io

from bank_connector.csv_export import (
    DEFAULT_COLUMNS,
    transactions_to_csv,
)
from bank_connector.parsing import ParsedTransaction


def _txn(**kw):
    base = dict(
        date=datetime.date(2026, 5, 1),
        amount=decimal.Decimal("-1.50"),
        payee="Shop",
        notes="note",
        ref="r1",
        status="BOOK",
    )
    base.update(kw)
    return ParsedTransaction(**base)


def test_extended_header_and_row():
    csv_text = transactions_to_csv([_txn()])
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0] == ["Date", "Payee", "Notes", "Amount", "Ref", "Status"]
    assert rows[1] == ["2026-05-01", "Shop", "note", "-1.50", "r1", "BOOK"]


def test_custom_columns_subset():
    csv_text = transactions_to_csv([_txn()], columns=DEFAULT_COLUMNS)
    header = next(csv.reader(io.StringIO(csv_text)))
    assert header == ["Date", "Payee", "Notes", "Amount"]


def test_empty_input_is_header_only():
    csv_text = transactions_to_csv([])
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert len(rows) == 1
