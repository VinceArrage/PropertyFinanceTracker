# Property Finance Tracker (`proptrack`)

> **🚧 Still in progress.** This is a personal project under active development, so features and structure may still change.

Tracks expenses, receipts and rent for rental properties and a personal home.
Everything runs locally on this PC; nothing is sent to any online service.

## Start the app
Double-click **`start.bat`**. It starts the app and opens http://localhost:8000 in your
browser. Keep the black window open while you use the app; close it to stop.

The first time, it asks you to choose a PIN (only possible on this PC). Every device
then enters the PIN once and stays signed in for 30 days. Forgot it? Run
`.\.venv\Scripts\proptrack.exe set-pin`.

**On your iPhone:** open **Settings** in the app and scan the QR code (same Wi-Fi), then
Share → Add to Home Screen. This needs your Wi-Fi set to *Private* in Windows and
Python allowed through Windows Firewall on private networks. To keep the app to this PC
only, set `host = "127.0.0.1"` in `config.toml`.

What you can do there:
- **Properties:** add, edit, delete, and switch between rental and personal.
- **Receipts:** take or choose a photo, let the app read store/date/total/items,
  check the result next to the photo, and save it as an expense.
- **Categories:** the expense categories (IRS Schedule E lines for rentals).

## How receipt reading works
Photos are read on this PC with Tesseract (free, offline). The app flattens and cleans
the photo, reads the text, then uses rules to find the store, date, subtotal, tax, total
and line items. It checks that subtotal + tax = total and that the items add up, and
tells you when something doesn't match. You always confirm before anything is saved.
Tips: lay the receipt flat on a dark surface, fill the frame, avoid shadows and glare.

It learns as you go: stores you've saved are recognized on later receipts, and the
category you pick for a store is suggested next time.

## Where things live
- **Code:** this folder (synced by OneDrive, pushed to GitHub).
- **Data:** `C:\Users\vince\PropertyFinanceData` (database + receipt photos), kept outside
  OneDrive so syncing can't corrupt a database mid-write. Set in `config.toml`.

## Command line
```powershell
.\.venv\Scripts\proptrack.exe --help
.\.venv\Scripts\proptrack.exe serve --open          # same as start.bat
.\.venv\Scripts\proptrack.exe property list
```

## Development
```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```
Tests use temporary databases and generated receipt images; they never touch real data.
