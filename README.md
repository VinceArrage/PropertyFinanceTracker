# Property Finance Tracker (`proptrack`)

Tracks expenses, receipts and rent for rental properties and a personal home.
Everything runs locally; nothing is sent to any online service.

## Where things live
- **Code:** this folder (synced by OneDrive).
- **Data:** `C:\Users\vince\PropertyFinanceData` (database + receipt photos), kept
  outside OneDrive so syncing can't corrupt a database mid-write. Set in `config.toml`.

## Setup (already done on this PC)
```powershell
py -3.12 -m venv .venv            # or the full path to python.exe
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\proptrack.exe init
```

## Usage
```powershell
.\.venv\Scripts\proptrack.exe property add            # asks step by step, incl. "Is this a rental?"
.\.venv\Scripts\proptrack.exe property list
.\.venv\Scripts\proptrack.exe property show "Elm St"
.\.venv\Scripts\proptrack.exe property edit "Elm St" --rent 1850
.\.venv\Scripts\proptrack.exe property edit "Elm St" --personal   # switch type
.\.venv\Scripts\proptrack.exe categories --property "Elm St"
```
Rentals track rent, tenant and lease, and use IRS Schedule E expense categories.
Personal homes use home categories (maintenance, HOA, improvements, ...).

## Tests
```powershell
.\.venv\Scripts\python.exe -m pytest
```
Tests use temporary databases and never touch the real data folder.
