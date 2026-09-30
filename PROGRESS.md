# Progress

Personal finance tracker for rental properties and a personal home, with receipt
scanning. Runs locally on a Windows PC; used from a browser on the PC or an iPhone
on the same Wi-Fi. No cloud services; receipt photos are read offline with Tesseract.

_Last updated: 2026-09-29_

## Done

### Phase 1, steps 1–3: foundation
- Python 3.12 package (`proptrack`) with a Typer command line and pytest suite.
- SQLite database kept outside OneDrive (`C:\Users\vince\PropertyFinanceData`) with
  versioned, automatic migrations (currently schema v3).
- Money stored as integer cents throughout.
- Expense categories: IRS Schedule E lines for rentals; home categories for personal.

### Properties and units
- Add, edit, delete properties; "This is a rental property" switch.
- Rentals have 1–50 units, each with apartment/unit number, monthly rent, tenant and
  lease dates; total monthly rent shown per property.
- Confirmation before anything that loses details (switching type, removing units).
- Delete only allowed while a property has no records (for fixing mistakes).

### Web app (step 5, started early)
- FastAPI + server-rendered pages; phone-first layout with dark mode.
- `start.bat` launches it and opens http://localhost:8000.
- Pages: properties, property detail (units, recent expenses), receipts, categories.

### Receipt scanning (step 4)
- Take or choose a photo (JPEG, PNG or iPhone HEIC).
- OpenCV cleanup: find and flatten the receipt (or crop the white paper away from a
  background such as a wooden table when its corners are cut off), scale, boost contrast.
- Tesseract reads it; several cleanup/layout combinations are tried and the reading
  whose numbers add up wins (stops early when one checks out).
- Rules extract store, date, subtotal, tax, total and line items, with checks that
  subtotal + tax = total and items add up; warnings when they don't.
- Review screen: photo beside editable fields, then "Save expense" creates the expense
  with line items. Photos saved under `receipts/YYYY/MM/`.
- Learns: stores you've saved are recognized later; category you chose for a store is
  suggested next time. Hints when an item looks like a capital improvement.
- Tuned on a first real receipt (Home Depot, photographed on a wooden table): now reads
  store, date, subtotal, tax, total and all items correctly. "Read well" only when the
  numbers cross-check.

### Reliability
- Clear message instead of "Internal Server Error" if the app window is left running
  across an update, and a friendly page for any unexpected error.

### Tests
- 134 automated tests: database and migrations, properties and units, expenses,
  receipt parsing, end-to-end OCR on generated receipt photos (including a wooden-table
  background), CLI and web pages. Real receipt photos in `tests/images/` are used when
  present on the PC and are never committed.

## Next
1. **Phone access:** allow the iPhone on home Wi-Fi (Windows Firewall rule) and add a
   simple PIN so others on the network can't open it.
2. **Backups:** copy the database and receipt photos into OneDrive on a schedule.
3. **Rent income:** record rent payments per unit; monthly cash flow per rental.
4. **Reports:** spending by month / category / property; Schedule E summary; export.
5. **Keep tuning the reader** with more real receipts (other stores, faded or crumpled).

## Known limitations
- Receipt reading has been tuned on one real receipt so far; other stores' layouts
  will likely need more rules.
- All-caps OCR noise at the edge of a line can end up in item descriptions.
- No login yet; only reachable from this PC until phone access is set up.
- Units can be edited in the web app only, not the command line.
