"""SQLite connection, schema and seed data."""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3

# Version 1: the original schema below. Later versions are applied in order from
# MIGRATIONS so existing databases are upgraded in place.
SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE COLLATE NOCASE,
    address             TEXT,
    is_rental           INTEGER NOT NULL DEFAULT 0 CHECK (is_rental IN (0, 1)),
    monthly_rent_cents  INTEGER CHECK (monthly_rent_cents IS NULL OR monthly_rent_cents >= 0),
    tenant_name         TEXT,
    lease_start         TEXT,
    lease_end           TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Rental categories map to IRS Schedule E lines; personal ones have no line.
CREATE TABLE IF NOT EXISTS categories (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    applies_to       TEXT NOT NULL CHECK (applies_to IN ('rental', 'personal')),
    schedule_e_line  INTEGER,
    UNIQUE (name, applies_to)
);

-- Negative amounts are refunds/returns.
CREATE TABLE IF NOT EXISTS transactions (
    id                      INTEGER PRIMARY KEY,
    property_id             INTEGER NOT NULL REFERENCES properties(id),
    date                    TEXT NOT NULL,
    vendor                  TEXT,
    amount_cents            INTEGER NOT NULL,
    tax_cents               INTEGER,
    category_id             INTEGER REFERENCES categories(id),
    is_capital_improvement  INTEGER NOT NULL DEFAULT 0 CHECK (is_capital_improvement IN (0, 1)),
    source                  TEXT NOT NULL DEFAULT 'manual'
                            CHECK (source IN ('manual', 'receipt', 'import')),
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_transactions_property_date ON transactions(property_id, date);

CREATE TABLE IF NOT EXISTS receipts (
    id              INTEGER PRIMARY KEY,
    property_id     INTEGER REFERENCES properties(id),
    transaction_id  INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    image_path      TEXT NOT NULL,
    ocr_text        TEXT,
    ocr_confidence  REAL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'reviewed')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS line_items (
    id                INTEGER PRIMARY KEY,
    transaction_id    INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    description       TEXT NOT NULL,
    quantity          REAL,
    unit_price_cents  INTEGER,
    total_cents       INTEGER
);

CREATE TABLE IF NOT EXISTS rent_payments (
    id            INTEGER PRIMARY KEY,
    property_id   INTEGER NOT NULL REFERENCES properties(id),
    date          TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL CHECK (amount_cents >= 0),
    tenant_name   TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rent_property_date ON rent_payments(property_id, date);
"""

MIGRATIONS = {
    # What the reader extracted (store, date, totals, items, warnings) as JSON, for the review screen.
    2: "ALTER TABLE receipts ADD COLUMN parsed_json TEXT;",
    # Rentals can have several units (apartments), each with its own rent, tenant and lease.
    # Existing rental details move into a unit "1" of their property.
    3: """
        CREATE TABLE units (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,  -- never reuse ids of removed units
            property_id         INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            label               TEXT NOT NULL COLLATE NOCASE,
            monthly_rent_cents  INTEGER CHECK (monthly_rent_cents IS NULL OR monthly_rent_cents >= 0),
            tenant_name         TEXT,
            lease_start         TEXT,
            lease_end           TEXT,
            position            INTEGER NOT NULL DEFAULT 0,
            UNIQUE (property_id, label)
        );
        INSERT INTO units (property_id, label, monthly_rent_cents, tenant_name, lease_start, lease_end)
            SELECT id, '1', monthly_rent_cents, tenant_name, lease_start, lease_end
            FROM properties WHERE is_rental = 1;
        ALTER TABLE properties DROP COLUMN monthly_rent_cents;
        ALTER TABLE properties DROP COLUMN tenant_name;
        ALTER TABLE properties DROP COLUMN lease_start;
        ALTER TABLE properties DROP COLUMN lease_end;
        ALTER TABLE rent_payments ADD COLUMN unit_id INTEGER REFERENCES units(id);
    """,
}

# (name, Schedule E line). Capital improvements are depreciated over years
# rather than deducted, so they have no single line of their own.
RENTAL_CATEGORIES = [
    ("Advertising", 5),
    ("Auto and travel", 6),
    ("Cleaning and maintenance", 7),
    ("Commissions", 8),
    ("Insurance", 9),
    ("Legal and professional fees", 10),
    ("Management fees", 11),
    ("Mortgage interest", 12),
    ("Other interest", 13),
    ("Repairs", 14),
    ("Supplies", 15),
    ("Taxes", 16),
    ("Utilities", 17),
    ("Other", 19),
    ("Capital improvements", None),
]

PERSONAL_CATEGORIES = [
    "Mortgage interest",
    "Property tax",
    "Insurance",
    "Utilities",
    "Maintenance and repairs",
    "Home improvements",
    "HOA fees",
    "Landscaping",
    "Supplies",
    "Other",
]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables, seed categories and apply migrations. Safe to call on every start."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if version < 1:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO categories (name, applies_to, schedule_e_line) VALUES (?, 'rental', ?)",
                RENTAL_CATEGORIES,
            )
            conn.executemany(
                "INSERT OR IGNORE INTO categories (name, applies_to) VALUES (?, 'personal')",
                [(name,) for name in PERSONAL_CATEGORIES],
            )
            conn.execute("PRAGMA user_version = 1")
        version = 1
    for target in range(version + 1, SCHEMA_VERSION + 1):
        conn.executescript(MIGRATIONS[target])
        conn.execute(f"PRAGMA user_version = {target}")
        conn.commit()
