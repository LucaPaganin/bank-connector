"""CSV export configuration and serialization for Enable Banking transactions.

Column mapping follows the Actual Budget CSV import format:
  Date, Payee, Notes, Amount  (Actual Budget required columns)
plus optional Ref and Status columns for traceability.

To customise the export, adjust COLUMN_DEFS or the DEFAULT_COLUMNS /
EXTENDED_COLUMNS key lists.
"""
import csv
import datetime
import io
from dataclasses import dataclass, field
from typing import Callable, Sequence

from bank_connector.parsing import ParsedTransaction


@dataclass(frozen=True)
class ColumnDef:
    """Specification for one CSV output column."""

    header: str
    attr: str  # attribute name on ParsedTransaction
    formatter: Callable[[object], str] = field(default=str)


def _fmt_date(v: object) -> str:
    return v.isoformat() if isinstance(v, datetime.date) else str(v)


# All available column definitions, keyed by a logical name.
COLUMN_DEFS: dict[str, ColumnDef] = {
    "date":   ColumnDef(header="Date",   attr="date",   formatter=_fmt_date),
    "payee":  ColumnDef(header="Payee",  attr="payee"),
    "notes":  ColumnDef(header="Notes",  attr="notes"),
    "amount": ColumnDef(header="Amount", attr="amount"),
    "ref":    ColumnDef(header="Ref",    attr="ref"),
    "status": ColumnDef(header="Status", attr="status"),
}

# Actual Budget import format (minimum required columns).
DEFAULT_COLUMNS: list[str] = ["date", "payee", "notes", "amount"]

# Default export — Actual Budget columns plus traceability extras.
EXTENDED_COLUMNS: list[str] = ["date", "payee", "notes", "amount", "ref", "status"]


def transactions_to_csv(
    transactions: Sequence[ParsedTransaction],
    *,
    columns: list[str] | None = None,
) -> str:
    """Serialize parsed transactions to a CSV string.

    Args:
        transactions: Parsed transactions to export (any order).
        columns: Logical column keys from COLUMN_DEFS to include, in order.
                 Defaults to EXTENDED_COLUMNS.

    Returns:
        UTF-8 CSV string with a header row followed by one row per transaction.
    """
    col_keys = columns if columns is not None else EXTENDED_COLUMNS
    defs = [COLUMN_DEFS[k] for k in col_keys]
    headers = [d.header for d in defs]

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=headers)
    writer.writeheader()
    for txn in transactions:
        writer.writerow({d.header: d.formatter(getattr(txn, d.attr)) for d in defs})
    return out.getvalue()
