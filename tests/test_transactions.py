import pytest

from proptrack.properties import add_property
from proptrack.transactions import (
    NewLineItem,
    TransactionError,
    get_line_items,
    get_transaction,
    known_vendors,
    list_transactions,
    save_transaction,
    suggest_category_id,
)


def category_id(conn, name, applies_to):
    return conn.execute(
        "SELECT id FROM categories WHERE name = ? AND applies_to = ?", (name, applies_to)
    ).fetchone()[0]


@pytest.fixture
def rental(conn):
    return add_property(conn, "Duplex", is_rental=True)


def test_save_and_read_back(conn, rental):
    txn_id = save_transaction(
        conn, property_id=rental.id, date="2026-09-28", vendor=" Home Depot ", amount_cents=5922, tax_cents=439,
        category_id=category_id(conn, "Repairs", "rental"), source="receipt",
        items=[NewLineItem("PAINT", 1498), NewLineItem("SCREWS", 997, 1, 997)],
    )
    txn = get_transaction(conn, txn_id)
    assert txn.vendor == "Home Depot"
    assert txn.category_name == "Repairs"
    assert [i.description for i in get_line_items(conn, txn_id)] == ["PAINT", "SCREWS"]
    assert list_transactions(conn, rental.id)[0].id == txn_id


def test_update_replaces_line_items(conn, rental):
    txn_id = save_transaction(conn, property_id=rental.id, date="2026-09-28", amount_cents=100, items=[NewLineItem("A", 100)])
    save_transaction(
        conn, property_id=rental.id, date="2026-09-28", amount_cents=200,
        items=[NewLineItem("B", 200)], transaction_id=txn_id,
    )
    assert get_transaction(conn, txn_id).amount_cents == 200
    assert [i.description for i in get_line_items(conn, txn_id)] == ["B"]


def test_category_must_match_property_type(conn, rental):
    with pytest.raises(TransactionError, match="for personal properties"):
        save_transaction(
            conn, property_id=rental.id, date="2026-09-28", amount_cents=100,
            category_id=category_id(conn, "HOA fees", "personal"),
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"date": "09/28/2026"}, "YYYY-MM-DD"), ({"amount_cents": None}, "total amount"), ({"property_id": 999}, "Pick a property")],
)
def test_validation(conn, rental, kwargs, message):
    args = {"property_id": rental.id, "date": "2026-09-28", "amount_cents": 100} | kwargs
    with pytest.raises(TransactionError, match=message):
        save_transaction(conn, **args)


def test_category_suggestions(conn, rental):
    home = add_property(conn, "Home")
    assert suggest_category_id(conn, "Home Depot", True) == category_id(conn, "Repairs", "rental")
    assert suggest_category_id(conn, "Home Depot", False) == category_id(conn, "Maintenance and repairs", "personal")
    assert suggest_category_id(conn, "Unknown Shop", True) is None

    # Your own past choice for a store wins over the built-in guess.
    supplies = category_id(conn, "Supplies", "rental")
    save_transaction(conn, property_id=rental.id, date="2026-09-01", vendor="Home Depot", amount_cents=1, category_id=supplies)
    assert suggest_category_id(conn, "home depot", True) == supplies
    assert suggest_category_id(conn, "Home Depot", False) == category_id(conn, "Maintenance and repairs", "personal")
    assert home.kind == "personal"


def test_known_vendors_most_used_first(conn, rental):
    for vendor in ["A", "B", "B"]:
        save_transaction(conn, property_id=rental.id, date="2026-09-01", vendor=vendor, amount_cents=1)
    assert known_vendors(conn) == ["B", "A"]
