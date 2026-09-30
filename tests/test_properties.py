import pytest

from proptrack.properties import (
    PropertyError,
    add_property,
    delete_property,
    get_property,
    list_properties,
    mismatched_category_count,
    update_property,
)


def test_add_rental(conn):
    prop = add_property(
        conn,
        "  Test Duplex ",
        address="1 Fake St",
        is_rental=True,
        monthly_rent_cents=150000,
        tenant_name="Tenant A",
        lease_start="2026-01-01",
        lease_end="2026-12-31",
    )
    assert prop.name == "Test Duplex"
    assert prop.kind == "rental"
    assert prop.monthly_rent_cents == 150000


def test_add_personal(conn):
    prop = add_property(conn, "Home", is_rental=False)
    assert prop.kind == "personal"
    assert prop.monthly_rent_cents is None


def test_personal_rejects_rental_fields(conn):
    with pytest.raises(PropertyError, match="only apply to rentals"):
        add_property(conn, "Home", monthly_rent_cents=1000)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "  "}, "name is required"),
        ({"name": "X", "is_rental": True, "monthly_rent_cents": -1}, "negative"),
        ({"name": "X", "is_rental": True, "lease_start": "01/02/2026"}, "Lease start"),
        (
            {"name": "X", "is_rental": True, "lease_start": "2026-06-01", "lease_end": "2026-01-01"},
            "before lease start",
        ),
    ],
)
def test_validation(conn, kwargs, message):
    with pytest.raises(PropertyError, match=message):
        add_property(conn, **kwargs)


def test_names_are_unique_ignoring_case(conn):
    add_property(conn, "Home")
    with pytest.raises(PropertyError, match="already exists"):
        add_property(conn, "HOME")


def test_lookup_by_name_or_id(conn):
    prop = add_property(conn, "Home")
    assert get_property(conn, "home") == prop
    assert get_property(conn, prop.id) == prop
    assert get_property(conn, "nope") is None


def test_list_sorted_by_name(conn):
    add_property(conn, "Zeta")
    add_property(conn, "Alpha")
    assert [p.name for p in list_properties(conn)] == ["Alpha", "Zeta"]


def test_switch_personal_to_rental(conn):
    prop = add_property(conn, "Home")
    updated = update_property(conn, prop.id, is_rental=True, monthly_rent_cents=200000)
    assert updated.is_rental
    assert updated.monthly_rent_cents == 200000


def test_switch_rental_to_personal_clears_rental_fields(conn):
    prop = add_property(conn, "Duplex", is_rental=True, monthly_rent_cents=100000, tenant_name="T")
    updated = update_property(conn, prop.id, is_rental=False)
    assert not updated.is_rental
    assert updated.monthly_rent_cents is None
    assert updated.tenant_name is None


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
    prop = add_property(conn, "Mistake")
    delete_property(conn, prop.id)
    assert get_property(conn, prop.id) is None

    kept = add_property(conn, "Kept", is_rental=True)
    _add_expense(conn, kept.id, "Repairs", "rental")
    with pytest.raises(PropertyError, match="1 transactions"):
        delete_property(conn, kept.id)
