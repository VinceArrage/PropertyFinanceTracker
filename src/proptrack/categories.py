"""Expense categories, split by property type."""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    applies_to: str
    schedule_e_line: int | None


def list_categories(conn: sqlite3.Connection, is_rental: bool | None = None) -> list[Category]:
    """All categories, or only those for rentals (True) or personal homes (False)."""
    query = "SELECT id, name, applies_to, schedule_e_line FROM categories"
    params: tuple = ()
    if is_rental is not None:
        query += " WHERE applies_to = ?"
        params = ("rental" if is_rental else "personal",)
    query += " ORDER BY applies_to, schedule_e_line IS NULL, schedule_e_line, name"
    return [Category(**dict(row)) for row in conn.execute(query, params)]
