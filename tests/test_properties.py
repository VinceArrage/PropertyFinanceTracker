import pytest

from proptrack.properties import (
    PropertyError,
    UnitInput,
    add_property,
    delete_property,
    get_property,
    list_properties,
    mismatched_category_count,
    update_property,
)


def labels(prop):
    return [unit.label for unit in prop.units]


def test_add_rental_with_units(conn):
    prop = add_property(
        conn,
        "  Test Triplex ",
        address="1 Fake St",
        is_rental=True,
        units=[
            UnitInput("1A", 150000, "Tenant A", "2026-01-01", "2026-12-31"),
            UnitInput("1B", 125000),
            UnitInput(),  # blank apartment number gets its position
        ],
    )
    assert prop.name == "Test Triplex"
    assert prop.kind == "rental"
    assert labels(prop) == ["1A", "1B", "3"]
    assert prop.units[0].tenant_name == "Tenant A"
    assert prop.total_rent_cents == 275000


def test_rental_without_units_gets_one(conn):
    prop = add_property(conn, "House", is_rental=True)
    assert labels(prop) == ["1"]
    assert prop.total_rent_cents is None


def test_add_personal(conn):
    prop = add_property(conn, "Home")
    assert prop.kind == "personal"
    assert prop.units == ()


def test_personal_rejects_units(conn):
    with pytest.raises(PropertyError, match="Only rental properties have units"):
        add_property(conn, "Home", units=[UnitInput("1")])


@pytest.mark.parametrize(
    ("units", "message"),
    [
        ([UnitInput("A", -1)], "Unit A: monthly rent can't be negative"),
        ([UnitInput("A", lease_start="01/02/2026")], "Unit A: lease start"),
        ([UnitInput("A", lease_start="2026-06-01", lease_end="2026-01-01")], "Unit A: lease end can't be before"),
        ([UnitInput("2B"), UnitInput("2b")], "both numbered"),
        ([UnitInput("x" * 31)], "too long"),
        ([UnitInput() for _ in range(51)], "at most 50"),
    ],
)
def test_unit_validation(conn, units, message):
    with pytest.raises(PropertyError, match=message):
        add_property(conn, "X", is_rental=True, units=units)


def test_name_required(conn):
    with pytest.raises(PropertyError, match="name is required"):
        add_property(conn, "  ")


def test_names_are_unique_ignoring_case(conn):
    add_property(conn, "Home")
    with pytest.raises(PropertyError, match="already exists"):
        add_property(conn, "HOME")


def test_lookup_by_name_or_id(conn):
    prop = add_property(conn, "Duplex", is_rental=True, units=[UnitInput("1"), UnitInput("2")])
    assert get_property(conn, "duplex") == prop
    assert get_property(conn, prop.id) == prop
    assert get_property(conn, "nope") is None


def test_list_sorted_by_name_with_units(conn):
    add_property(conn, "Zeta", is_rental=True, units=[UnitInput("1"), UnitInput("2")])
    add_property(conn, "Alpha")
    props = list_properties(conn)
    assert [p.name for p in props] == ["Alpha", "Zeta"]
    assert labels(props[1]) == ["1", "2"]


def test_edit_units_update_add_remove_and_reorder(conn):
    prop = add_property(conn, "Triplex", is_rental=True, units=[UnitInput("1"), UnitInput("2"), UnitInput("3")])
    first, second, third = prop.units
    updated = update_property(
        conn,
        prop.id,
        units=[
            UnitInput("2", 90000, id=second.id),  # moved to the top
            UnitInput("1", 80000, "New tenant", id=first.id),
            UnitInput("4"),  # new; unit 3 is removed
        ],
    )
    assert labels(updated) == ["2", "1", "4"]
    assert updated.units[0].id == second.id
    assert updated.units[1].tenant_name == "New tenant"
    assert third.id not in {u.id for u in updated.units}


def test_swapping_apartment_numbers(conn):
    prop = add_property(conn, "Duplex", is_rental=True, units=[UnitInput("A"), UnitInput("B")])
    a, b = prop.units
    updated = update_property(conn, prop.id, units=[UnitInput("B", id=a.id), UnitInput("A", id=b.id)])
    assert labels(updated) == ["B", "A"]


def test_editing_other_fields_keeps_units(conn):
    prop = add_property(conn, "Duplex", is_rental=True, units=[UnitInput("1", 100000), UnitInput("2", 110000)])
    updated = update_property(conn, prop.id, address="2 Fake St")
    assert updated.units == prop.units


def test_unit_ids_must_belong_to_property(conn):
    other = add_property(conn, "Other", is_rental=True)
    prop = add_property(conn, "Duplex", is_rental=True)
    with pytest.raises(PropertyError, match="doesn't belong"):
        update_property(conn, prop.id, units=[UnitInput("1", id=other.units[0].id)])


def test_unit_with_rent_payments_cannot_be_removed(conn):
    prop = add_property(conn, "Duplex", is_rental=True, units=[UnitInput("1"), UnitInput("2")])
    with conn:
        conn.execute(
            "INSERT INTO rent_payments (property_id, unit_id, date, amount_cents) VALUES (?, ?, '2026-09-01', 100)",
            (prop.id, prop.units[1].id),
        )
    with pytest.raises(PropertyError, match="Unit 2 has 1 rent payment"):
        update_property(conn, prop.id, units=[UnitInput("1", id=prop.units[0].id)])


def test_switch_personal_to_rental_creates_a_unit(conn):
    prop = add_property(conn, "Home")
    updated = update_property(conn, prop.id, is_rental=True)
    assert updated.is_rental
    assert labels(updated) == ["1"]


def test_switch_rental_to_personal_removes_units(conn):
    prop = add_property(conn, "Duplex", is_rental=True, units=[UnitInput("1", 100000, "T"), UnitInput("2")])
    updated = update_property(conn, prop.id, is_rental=False)
    assert not updated.is_rental
    assert updated.units == ()
    assert conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 0


def test_rename_conflict(conn):
    add_property(conn, "A")
    b = add_property(conn, "B")
    with pytest.raises(PropertyError, match="already exists"):
        update_property(conn, b.id, name="a")


def test_update_rejects_unknown_field(conn):
    prop = add_property(conn, "A")
    with pytest.raises(PropertyError, match="Can't edit"):
        update_property(conn, prop.id, created_at="2000-01-01")


def _add_expense(conn, prop_id, category_name, applies_to):
    category_id = conn.execute(
        "SELECT id FROM categories WHERE name = ? AND applies_to = ?", (category_name, applies_to)
    ).fetchone()[0]
    with conn:
        conn.execute(
            "INSERT INTO transactions (property_id, date, amount_cents, category_id) VALUES (?, '2026-01-01', 100, ?)",
            (prop_id, category_id),
        )


def test_mismatched_categories_after_switch(conn):
    prop = add_property(conn, "Duplex", is_rental=True)
    _add_expense(conn, prop.id, "Repairs", "rental")
    assert mismatched_category_count(conn, prop.id, is_rental=True) == 0
    assert mismatched_category_count(conn, prop.id, is_rental=False) == 1


def test_delete_only_when_empty(conn):
    prop = add_property(conn, "Mistake", is_rental=True, units=[UnitInput("1"), UnitInput("2")])
    delete_property(conn, prop.id)
    assert get_property(conn, prop.id) is None
    assert conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 0  # units go with it

    kept = add_property(conn, "Kept", is_rental=True)
    _add_expense(conn, kept.id, "Repairs", "rental")
    with pytest.raises(PropertyError, match="1 transactions"):
        delete_property(conn, kept.id)
