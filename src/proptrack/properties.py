"""Properties: rentals track rent and use Schedule E categories; personal homes don't."""

import sqlite3
from dataclasses import dataclass
from datetime import date

RENTAL_ONLY_FIELDS = ("monthly_rent_cents", "tenant_name", "lease_start", "lease_end")
EDITABLE_FIELDS = ("name", "address", "is_rental", "notes", *RENTAL_ONLY_FIELDS)


class PropertyError(ValueError):
    pass


@dataclass(frozen=True)
class Property:
    id: int
    name: str
    address: str | None
    is_rental: bool
    monthly_rent_cents: int | None
    tenant_name: str | None
    lease_start: str | None
    lease_end: str | None
    notes: str | None
    created_at: str

    @property
    def kind(self) -> str:
        return "rental" if self.is_rental else "personal"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Property":
        return cls(**{**dict(row), "is_rental": bool(row["is_rental"])})


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _clean_date(value: str | None, label: str) -> str | None:
    value = _clean_text(value)
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise PropertyError(f"{label} must be a date like 2026-01-31, got {value!r}.") from None


def _validate(fields: dict) -> dict:
    fields = dict(fields)
    fields["name"] = _clean_text(fields.get("name"))
    if not fields["name"]:
        raise PropertyError("Property name is required.")
    for key in ("address", "tenant_name", "notes"):
        fields[key] = _clean_text(fields.get(key))
    fields["lease_start"] = _clean_date(fields.get("lease_start"), "Lease start")
    fields["lease_end"] = _clean_date(fields.get("lease_end"), "Lease end")
    fields["is_rental"] = bool(fields.get("is_rental"))

    rent = fields.get("monthly_rent_cents")
    if rent is not None and rent < 0:
        raise PropertyError("Monthly rent can't be negative.")
    if not fields["is_rental"]:
        given = [key for key in RENTAL_ONLY_FIELDS if fields.get(key) is not None]
        if given:
            raise PropertyError(
                "Rent, tenant and lease details only apply to rentals "
                f"(got {', '.join(given)} for a personal property)."
            )
    if fields["lease_start"] and fields["lease_end"] and fields["lease_end"] < fields["lease_start"]:
        raise PropertyError("Lease end can't be before lease start.")
    return fields


def add_property(
    conn: sqlite3.Connection,
    name: str,
    *,
    address: str | None = None,
    is_rental: bool = False,
    monthly_rent_cents: int | None = None,
    tenant_name: str | None = None,
    lease_start: str | None = None,
    lease_end: str | None = None,
    notes: str | None = None,
) -> Property:
    fields = _validate(
        {
            "name": name,
            "address": address,
            "is_rental": is_rental,
            "monthly_rent_cents": monthly_rent_cents,
            "tenant_name": tenant_name,
            "lease_start": lease_start,
            "lease_end": lease_end,
            "notes": notes,
        }
    )
    columns = ", ".join(fields)
    placeholders = ", ".join(f":{key}" for key in fields)
    try:
        with conn:
            cur = conn.execute(f"INSERT INTO properties ({columns}) VALUES ({placeholders})", fields)
    except sqlite3.IntegrityError:
        raise PropertyError(f"A property named {fields['name']!r} already exists.") from None
    return get_property(conn, cur.lastrowid)


def get_property(conn: sqlite3.Connection, key: int | str) -> Property | None:
    """Look up a property by id or by name (case-insensitive)."""
    column = "id" if isinstance(key, int) else "name"
    row = conn.execute(f"SELECT * FROM properties WHERE {column} = ?", (key,)).fetchone()
    return Property.from_row(row) if row else None


def list_properties(conn: sqlite3.Connection) -> list[Property]:
    rows = conn.execute("SELECT * FROM properties ORDER BY name").fetchall()
    return [Property.from_row(row) for row in rows]


def update_property(conn: sqlite3.Connection, prop_id: int, **changes) -> Property:
    """Update fields. Switching a rental to personal clears its rent/tenant/lease details."""
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise PropertyError(f"Can't edit: {', '.join(sorted(unknown))}.")
    current = get_property(conn, prop_id)
    if current is None:
        raise PropertyError(f"No property with id {prop_id}.")

    merged = {key: getattr(current, key) for key in EDITABLE_FIELDS} | changes
    if current.is_rental and changes.get("is_rental") is False:
        for key in RENTAL_ONLY_FIELDS:
            if changes.get(key) is None:
                merged[key] = None
    fields = _validate(merged)

    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    try:
        with conn:
            conn.execute(f"UPDATE properties SET {assignments} WHERE id = :id", fields | {"id": prop_id})
    except sqlite3.IntegrityError:
        raise PropertyError(f"A property named {fields['name']!r} already exists.") from None
    return get_property(conn, prop_id)


def linked_record_counts(conn: sqlite3.Connection, prop_id: int) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE property_id = ?", (prop_id,)).fetchone()[0]
        for table in ("transactions", "receipts", "rent_payments")
    }


def mismatched_category_count(conn: sqlite3.Connection, prop_id: int, is_rental: bool) -> int:
    """Transactions whose category belongs to the other property type.

    Used to warn when a property is switched between rental and personal.
    """
    kind = "rental" if is_rental else "personal"
    return conn.execute(
        """
        SELECT COUNT(*) FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE t.property_id = ? AND c.applies_to != ?
        """,
        (prop_id, kind),
    ).fetchone()[0]


def delete_property(conn: sqlite3.Connection, prop_id: int) -> None:
    """Delete a property that has no records yet (for fixing mistakes)."""
    counts = linked_record_counts(conn, prop_id)
    if any(counts.values()):
        summary = ", ".join(f"{n} {table.replace('_', ' ')}" for table, n in counts.items() if n)
        raise PropertyError(f"Can't delete a property that has records ({summary}).")
    with conn:
        conn.execute("DELETE FROM properties WHERE id = ?", (prop_id,))
