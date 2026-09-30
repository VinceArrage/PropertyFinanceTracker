"""Properties and their units.

Rentals have one or more units (apartments), each with its own number, rent, tenant
and lease, and use Schedule E categories. Personal homes have no units.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date

EDITABLE_FIELDS = ("name", "address", "is_rental", "notes")
MAX_UNITS = 50
MAX_LABEL_LENGTH = 30


class PropertyError(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    id: int
    property_id: int
    label: str
    monthly_rent_cents: int | None
    tenant_name: str | None
    lease_start: str | None
    lease_end: str | None
    position: int


@dataclass(frozen=True)
class UnitInput:
    """A unit as entered on a form. `id` is set when editing an existing unit."""

    label: str | None = None
    monthly_rent_cents: int | None = None
    tenant_name: str | None = None
    lease_start: str | None = None
    lease_end: str | None = None
    id: int | None = None


@dataclass(frozen=True)
class Property:
    id: int
    name: str
    address: str | None
    is_rental: bool
    notes: str | None
    created_at: str
    units: tuple[Unit, ...] = ()

    @property
    def kind(self) -> str:
        return "rental" if self.is_rental else "personal"

    @property
    def total_rent_cents(self) -> int | None:
        """Sum of the units' monthly rents, or None when no rent has been entered."""
        rents = [unit.monthly_rent_cents for unit in self.units if unit.monthly_rent_cents is not None]
        return sum(rents) if rents else None


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


def _validate_property(fields: dict) -> dict:
    fields = dict(fields)
    fields["name"] = _clean_text(fields.get("name"))
    if not fields["name"]:
        raise PropertyError("Property name is required.")
    fields["address"] = _clean_text(fields.get("address"))
    fields["notes"] = _clean_text(fields.get("notes"))
    fields["is_rental"] = bool(fields.get("is_rental"))
    return fields


def _validate_units(units: list[UnitInput], is_rental: bool) -> list[UnitInput]:
    if not is_rental:
        if units:
            raise PropertyError("Only rental properties have units.")
        return []
    if not units:
        units = [UnitInput()]
    if len(units) > MAX_UNITS:
        raise PropertyError(f"A property can have at most {MAX_UNITS} units.")

    cleaned, seen = [], set()
    for number, unit in enumerate(units, start=1):
        label = _clean_text(unit.label) or str(number)
        name = f"Unit {label}"
        if len(label) > MAX_LABEL_LENGTH:
            raise PropertyError(f"{name}: the apartment number is too long.")
        if label.casefold() in seen:
            raise PropertyError(f"Two units are both numbered {label!r}. Each apartment number must be different.")
        seen.add(label.casefold())
        if unit.monthly_rent_cents is not None and unit.monthly_rent_cents < 0:
            raise PropertyError(f"{name}: monthly rent can't be negative.")
        lease_start = _clean_date(unit.lease_start, f"{name}: lease start")
        lease_end = _clean_date(unit.lease_end, f"{name}: lease end")
        if lease_start and lease_end and lease_end < lease_start:
            raise PropertyError(f"{name}: lease end can't be before lease start.")
        cleaned.append(
            UnitInput(
                label=label,
                monthly_rent_cents=unit.monthly_rent_cents,
                tenant_name=_clean_text(unit.tenant_name),
                lease_start=lease_start,
                lease_end=lease_end,
                id=unit.id,
            )
        )
    return cleaned


def _write_units(conn: sqlite3.Connection, prop_id: int, units: list[UnitInput]) -> None:
    """Make the property's units match `units`: update, add and remove. Call inside a transaction."""
    existing = {row["id"] for row in conn.execute("SELECT id FROM units WHERE property_id = ?", (prop_id,))}
    unknown = {unit.id for unit in units if unit.id is not None} - existing
    if unknown:
        raise PropertyError("One of the units doesn't belong to this property.")

    removed = existing - {unit.id for unit in units}
    for unit_id in removed:
        payments = conn.execute("SELECT COUNT(*) FROM rent_payments WHERE unit_id = ?", (unit_id,)).fetchone()[0]
        if payments:
            label = conn.execute("SELECT label FROM units WHERE id = ?", (unit_id,)).fetchone()[0]
            raise PropertyError(f"Unit {label} has {payments} rent payment(s) recorded, so it can't be removed.")
    conn.executemany("DELETE FROM units WHERE id = ?", [(unit_id,) for unit_id in removed])

    # Give kept units temporary labels first so swapping two apartment numbers doesn't clash.
    conn.executemany(
        "UPDATE units SET label = ? WHERE id = ?",
        [(f"__renumbering_{unit.id}__", unit.id) for unit in units if unit.id is not None],
    )
    for position, unit in enumerate(units):
        values = (unit.label, unit.monthly_rent_cents, unit.tenant_name, unit.lease_start, unit.lease_end, position)
        if unit.id is None:
            conn.execute(
                "INSERT INTO units (label, monthly_rent_cents, tenant_name, lease_start, lease_end, position, property_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*values, prop_id),
            )
        else:
            conn.execute(
                "UPDATE units SET label = ?, monthly_rent_cents = ?, tenant_name = ?, lease_start = ?, lease_end = ?, "
                "position = ? WHERE id = ?",
                (*values, unit.id),
            )


def _units_by_property(conn: sqlite3.Connection, prop_id: int | None = None) -> dict[int, tuple[Unit, ...]]:
    query = "SELECT * FROM units"
    params: tuple = ()
    if prop_id is not None:
        query += " WHERE property_id = ?"
        params = (prop_id,)
    grouped: dict[int, list[Unit]] = {}
    for row in conn.execute(query + " ORDER BY position, id", params):
        grouped.setdefault(row["property_id"], []).append(Unit(**dict(row)))
    return {key: tuple(value) for key, value in grouped.items()}


def _from_row(row: sqlite3.Row, units: tuple[Unit, ...]) -> Property:
    return Property(**{**dict(row), "is_rental": bool(row["is_rental"]), "units": units})


def add_property(
    conn: sqlite3.Connection,
    name: str,
    *,
    address: str | None = None,
    is_rental: bool = False,
    notes: str | None = None,
    units: list[UnitInput] = (),
) -> Property:
    """Add a property. A rental with no units given gets one unit."""
    fields = _validate_property({"name": name, "address": address, "is_rental": is_rental, "notes": notes})
    cleaned_units = _validate_units(list(units), fields["is_rental"])
    try:
        with conn:
            prop_id = conn.execute(
                "INSERT INTO properties (name, address, is_rental, notes) VALUES (:name, :address, :is_rental, :notes)",
                fields,
            ).lastrowid
            _write_units(conn, prop_id, cleaned_units)
    except sqlite3.IntegrityError as exc:
        raise _integrity_error(exc, fields["name"]) from None
    return get_property(conn, prop_id)


def get_property(conn: sqlite3.Connection, key: int | str) -> Property | None:
    """Look up a property by id or by name (case-insensitive)."""
    column = "id" if isinstance(key, int) else "name"
    row = conn.execute(f"SELECT * FROM properties WHERE {column} = ?", (key,)).fetchone()
    if row is None:
        return None
    return _from_row(row, _units_by_property(conn, row["id"]).get(row["id"], ()))


def list_properties(conn: sqlite3.Connection) -> list[Property]:
    units = _units_by_property(conn)
    rows = conn.execute("SELECT * FROM properties ORDER BY name").fetchall()
    return [_from_row(row, units.get(row["id"], ())) for row in rows]


def update_property(
    conn: sqlite3.Connection, prop_id: int, *, units: list[UnitInput] | None = None, **changes
) -> Property:
    """Update fields and, when `units` is given, replace the unit list.

    Switching a rental to personal removes its units; switching a personal home to
    a rental without giving units creates one.
    """
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise PropertyError(f"Can't edit: {', '.join(sorted(unknown))}.")
    current = get_property(conn, prop_id)
    if current is None:
        raise PropertyError(f"No property with id {prop_id}.")

    fields = _validate_property({key: getattr(current, key) for key in EDITABLE_FIELDS} | changes)
    if not fields["is_rental"]:
        new_units = [] if units is None else list(units)
    elif units is not None:
        new_units = list(units)
    elif current.is_rental:
        new_units = [UnitInput(u.label, u.monthly_rent_cents, u.tenant_name, u.lease_start, u.lease_end, u.id)
                     for u in current.units]
    else:
        new_units = []
    cleaned_units = _validate_units(new_units, fields["is_rental"])

    try:
        with conn:
            conn.execute(
                "UPDATE properties SET name = :name, address = :address, is_rental = :is_rental, notes = :notes "
                "WHERE id = :id",
                fields | {"id": prop_id},
            )
            _write_units(conn, prop_id, cleaned_units)
    except sqlite3.IntegrityError as exc:
        raise _integrity_error(exc, fields["name"]) from None
    return get_property(conn, prop_id)


def _integrity_error(exc: sqlite3.IntegrityError, name: str) -> PropertyError:
    if "properties.name" in str(exc):
        return PropertyError(f"A property named {name!r} already exists.")
    return PropertyError(f"Couldn't save the property: {exc}")


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
    """Delete a property that has no records yet (for fixing mistakes). Its units go with it."""
    counts = linked_record_counts(conn, prop_id)
    if any(counts.values()):
        summary = ", ".join(f"{n} {table.replace('_', ' ')}" for table, n in counts.items() if n)
        raise PropertyError(f"Can't delete a property that has records ({summary}).")
    with conn:
        conn.execute("DELETE FROM properties WHERE id = ?", (prop_id,))
