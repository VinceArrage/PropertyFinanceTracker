import sqlite3

import pytest

from proptrack.categories import list_categories
from proptrack.db import PERSONAL_CATEGORIES, RENTAL_CATEGORIES, SCHEMA_VERSION, init_db


def test_tables_created(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"properties", "categories", "transactions", "receipts", "line_items", "rent_payments"} <= tables


def test_categories_seeded_once(conn):
    conn.execute("PRAGMA user_version = 0")
    init_db(conn)  # re-running must not duplicate categories
    assert len(list_categories(conn, is_rental=True)) == len(RENTAL_CATEGORIES)
    assert len(list_categories(conn, is_rental=False)) == len(PERSONAL_CATEGORIES)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_rental_categories_have_schedule_e_lines(conn):
    lines = {c.name: c.schedule_e_line for c in list_categories(conn, is_rental=True)}
    assert lines["Repairs"] == 14
    assert lines["Mortgage interest"] == 12
    assert lines["Capital improvements"] is None


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("INSERT INTO rent_payments (property_id, date, amount_cents) VALUES (999, '2026-01-01', 100)")
