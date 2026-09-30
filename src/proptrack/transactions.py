"""Expenses (transactions) and their line items."""

import re
import sqlite3
from dataclasses import dataclass
from datetime import date

from proptrack.properties import get_property

SOURCES = ("manual", "receipt", "import")

# First-guess categories for a vendor, before the app has learned from your own choices.
# Each entry: (pattern, rental category, personal category).
VENDOR_CATEGORY_HINTS = [
    (r"home\s*depot|lowe'?s|menards|ace\s*hardware|true\s*value|harbor\s*freight|sherwin|rona|canadian\s*tire",
     "Repairs", "Maintenance and repairs"),
    (r"insurance|state\s*farm|allstate|geico|progressive|farmers", "Insurance", "Insurance"),
    (r"electric|energy|power|water|sewer|gas\s*co|utility|utilities|hydro", "Utilities", "Utilities"),
    (r"landscap|lawn|garden|nursery", "Cleaning and maintenance", "Landscaping"),
    (r"clean|maid|janitor", "Cleaning and maintenance", "Maintenance and repairs"),
    (r"plumb|electrician|hvac|roofing|contractor|handyman", "Repairs", "Maintenance and repairs"),
    (r"zillow|apartments\.com|craigslist|realtor", "Advertising", "Other"),
    (r"property\s*management|management", "Management fees", "Other"),
    (r"attorney|law\s*office|cpa|accounting|legal", "Legal and professional fees", "Other"),
    (r"walmart|target|costco|staples|amazon|ikea", "Supplies", "Supplies"),
]


class TransactionError(ValueError):
    pass


@dataclass(frozen=True)
class Transaction:
    id: int
    property_id: int
    date: str
    vendor: str | None
    amount_cents: int
    tax_cents: int | None
    category_id: int | None
    category_name: str | None
    is_capital_improvement: bool
    source: str
    notes: str | None


@dataclass(frozen=True)
class NewLineItem:
    description: str
    total_cents: int
    quantity: float | None = None
    unit_price_cents: int | None = None


def _validate(conn, property_id, txn_date, amount_cents, category_id, source) -> None:
    prop = get_property(conn, property_id)
    if prop is None:
        raise TransactionError("Pick a property.")
    try:
        date.fromisoformat(txn_date or "")
    except ValueError:
        raise TransactionError("Enter the date as YYYY-MM-DD.") from None
    if amount_cents is None:
        raise TransactionError("Enter the total amount.")
    if source not in SOURCES:
        raise TransactionError(f"Unknown source {source!r}.")
    if category_id is not None:
        row = conn.execute("SELECT applies_to FROM categories WHERE id = ?", (category_id,)).fetchone()
        if row is None:
            raise TransactionError("Unknown category.")
        if row["applies_to"] != prop.kind:
            raise TransactionError(f"That category is for {row['applies_to']} properties, not {prop.kind} ones.")


def save_transaction(
    conn: sqlite3.Connection,
    *,
    property_id: int,
    date: str,
    amount_cents: int,
    vendor: str | None = None,
    tax_cents: int | None = None,
    category_id: int | None = None,
    is_capital_improvement: bool = False,
    source: str = "manual",
    notes: str | None = None,
    items: list[NewLineItem] = (),
    transaction_id: int | None = None,
) -> int:
    """Create a transaction, or replace an existing one (including its line items). Returns its id."""
    _validate(conn, property_id, date, amount_cents, category_id, source)
    fields = {
        "property_id": property_id,
        "date": date,
        "vendor": (vendor or "").strip() or None,
        "amount_cents": amount_cents,
        "tax_cents": tax_cents,
        "category_id": category_id,
        "is_capital_improvement": int(bool(is_capital_improvement)),
        "source": source,
        "notes": (notes or "").strip() or None,
    }
    with conn:
        if transaction_id is None:
            columns = ", ".join(fields)
            placeholders = ", ".join(f":{key}" for key in fields)
            transaction_id = conn.execute(
                f"INSERT INTO transactions ({columns}) VALUES ({placeholders})", fields
            ).lastrowid
        else:
            assignments = ", ".join(f"{key} = :{key}" for key in fields)
            conn.execute(f"UPDATE transactions SET {assignments} WHERE id = :id", fields | {"id": transaction_id})
            conn.execute("DELETE FROM line_items WHERE transaction_id = ?", (transaction_id,))
        conn.executemany(
            "INSERT INTO line_items (transaction_id, description, quantity, unit_price_cents, total_cents) "
            "VALUES (?, ?, ?, ?, ?)",
            [(transaction_id, i.description, i.quantity, i.unit_price_cents, i.total_cents) for i in items],
        )
    return transaction_id


_SELECT = """
    SELECT t.id, t.property_id, t.date, t.vendor, t.amount_cents, t.tax_cents, t.category_id,
           c.name AS category_name, t.is_capital_improvement, t.source, t.notes
    FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
"""


def _from_row(row: sqlite3.Row) -> Transaction:
    return Transaction(**{**dict(row), "is_capital_improvement": bool(row["is_capital_improvement"])})


def get_transaction(conn: sqlite3.Connection, transaction_id: int) -> Transaction | None:
    row = conn.execute(_SELECT + " WHERE t.id = ?", (transaction_id,)).fetchone()
    return _from_row(row) if row else None


def list_transactions(conn: sqlite3.Connection, property_id: int | None = None, limit: int = 50) -> list[Transaction]:
    query, params = _SELECT, []
    if property_id is not None:
        query += " WHERE t.property_id = ?"
        params.append(property_id)
    query += " ORDER BY t.date DESC, t.id DESC LIMIT ?"
    params.append(limit)
    return [_from_row(row) for row in conn.execute(query, params)]


def get_line_items(conn: sqlite3.Connection, transaction_id: int) -> list[NewLineItem]:
    rows = conn.execute(
        "SELECT description, quantity, unit_price_cents, total_cents FROM line_items WHERE transaction_id = ? ORDER BY id",
        (transaction_id,),
    )
    return [NewLineItem(**dict(row)) for row in rows]


def delete_transaction(conn: sqlite3.Connection, transaction_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))


def known_vendors(conn: sqlite3.Connection) -> list[str]:
    """Store names already confirmed on saved expenses, most used first."""
    rows = conn.execute(
        "SELECT vendor FROM transactions WHERE vendor IS NOT NULL GROUP BY vendor ORDER BY COUNT(*) DESC LIMIT 200"
    )
    return [row["vendor"] for row in rows]


def suggest_category_id(conn: sqlite3.Connection, vendor: str | None, is_rental: bool) -> int | None:
    """The category you last used for this store (on the same property type), else a built-in guess."""
    if not vendor:
        return None
    kind = "rental" if is_rental else "personal"
    row = conn.execute(
        """
        SELECT t.category_id FROM transactions t JOIN categories c ON c.id = t.category_id
        WHERE lower(t.vendor) = lower(?) AND c.applies_to = ?
        ORDER BY t.date DESC, t.id DESC LIMIT 1
        """,
        (vendor, kind),
    ).fetchone()
    if row:
        return row["category_id"]
    for pattern, rental_name, personal_name in VENDOR_CATEGORY_HINTS:
        if re.search(pattern, vendor, re.I):
            name = rental_name if is_rental else personal_name
            found = conn.execute(
                "SELECT id FROM categories WHERE name = ? AND applies_to = ?", (name, kind)
            ).fetchone()
            return found["id"] if found else None
    return None
