"""Web interface. Start it with `proptrack serve`, then open http://localhost:8000."""

from itertools import zip_longest
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from proptrack.categories import list_categories
from proptrack.config import Config, load_config
from proptrack.db import SCHEMA_VERSION, connect, init_db
from proptrack.money import format_cents, parse_money
from proptrack.properties import (
    MAX_UNITS,
    Property,
    PropertyError,
    UnitInput,
    add_property,
    delete_property,
    get_property,
    linked_record_counts,
    list_properties,
    mismatched_category_count,
    update_property,
)
from proptrack.receipts.images import ImageError, resolve_receipt_path
from proptrack.receipts.store import (
    ReceiptError,
    delete_receipt,
    get_receipt,
    list_receipts,
    mark_reviewed,
    scan_receipt,
)
from proptrack.transactions import (
    NewLineItem,
    TransactionError,
    get_line_items,
    get_transaction,
    list_transactions,
    save_transaction,
    suggest_category_id,
)

WEB_DIR = Path(__file__).parent
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
BLANK_ITEM_ROWS = 2
SAVED_MESSAGES = {"added": "Property added.", "updated": "Changes saved."}
# Form field name -> key in a unit's form values. Each unit block posts one of each.
UNIT_FORM_FIELDS = {
    "unit_id": "id",
    "unit_label": "label",
    "unit_rent": "monthly_rent",
    "unit_tenant": "tenant_name",
    "unit_lease_start": "lease_start",
    "unit_lease_end": "lease_end",
}


def _cents_to_input(cents: int | None) -> str:
    if cents is None:
        return ""
    dollars, remainder = divmod(cents, 100)
    return f"{dollars}.{remainder:02d}"


def _blank_unit() -> dict:
    return {key: "" for key in UNIT_FORM_FIELDS.values()}


def _empty_values() -> dict:
    return {"name": "", "address": "", "notes": "", "is_rental": False, "unit_count": "1", "units": [_blank_unit()]}


def _values_from_property(prop: Property) -> dict:
    units = [
        {
            "id": str(unit.id),
            "label": unit.label,
            "monthly_rent": _cents_to_input(unit.monthly_rent_cents),
            "tenant_name": unit.tenant_name or "",
            "lease_start": unit.lease_start or "",
            "lease_end": unit.lease_end or "",
        }
        for unit in prop.units
    ] or [_blank_unit()]
    return {
        "name": prop.name,
        "address": prop.address or "",
        "notes": prop.notes or "",
        "is_rental": prop.is_rental,
        "unit_count": str(len(units)),
        "units": units,
    }


def _values_from_form(form) -> dict:
    columns = [form.getlist(field) for field in UNIT_FORM_FIELDS]
    units = [
        {key: str(value).strip() for key, value in zip(UNIT_FORM_FIELDS.values(), row)}
        for row in zip_longest(*columns, fillvalue="")
    ]
    return {
        "name": str(form.get("name") or "").strip(),
        "address": str(form.get("address") or "").strip(),
        "notes": str(form.get("notes") or "").strip(),
        "is_rental": form.get("is_rental") == "on",
        "unit_count": str(form.get("unit_count") or "1").strip(),
        "units": units or [_blank_unit()],
    }


def _fields_from_values(values: dict) -> tuple[dict, list[UnitInput]]:
    """Turn form strings into property fields and units. Units are ignored for personal homes.

    The unit list is cut or padded to the "Number of units" value, so lowering the
    number removes the last units.
    """
    fields = {key: values[key] for key in ("name", "address", "notes", "is_rental")}
    if not values["is_rental"]:
        return fields, []

    count = values["unit_count"]
    if not count.isdigit() or not 1 <= int(count) <= MAX_UNITS:
        raise PropertyError(f"Number of units must be a whole number from 1 to {MAX_UNITS}.")
    rows = (values["units"] + [_blank_unit() for _ in range(int(count))])[: int(count)]

    units = []
    for number, row in enumerate(rows, start=1):
        rent = None
        if row["monthly_rent"]:
            try:
                rent = parse_money(row["monthly_rent"])
            except ValueError as exc:
                raise PropertyError(f"Unit {row['label'] or number}: {exc}") from None
        units.append(
            UnitInput(
                label=row["label"],
                monthly_rent_cents=rent,
                tenant_name=row["tenant_name"],
                lease_start=row["lease_start"],
                lease_end=row["lease_end"],
                id=int(row["id"]) if row["id"].isdigit() else None,
            )
        )
    return fields, units


def _parse_optional_money(text: str, label: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    try:
        return parse_money(text)
    except ValueError:
        raise TransactionError(f"{label}: {text!r} isn't a valid amount.") from None


def _review_values_from_receipt(receipt, transaction, items) -> dict:
    """Prefill the review form from the saved expense, or from what the reader found."""
    if transaction is not None:
        return {
            "property_id": transaction.property_id,
            "vendor": transaction.vendor or "",
            "date": transaction.date,
            "total": _cents_to_input(transaction.amount_cents),
            "tax": _cents_to_input(transaction.tax_cents),
            "category_id": transaction.category_id,
            "is_capital_improvement": transaction.is_capital_improvement,
            "notes": transaction.notes or "",
            "items": [
                {
                    "description": item.description,
                    "quantity": "" if item.quantity is None else f"{item.quantity:g}",
                    "amount": _cents_to_input(item.total_cents),
                }
                for item in items
            ],
        }
    parsed = receipt.parsed
    return {
        "property_id": receipt.property_id,
        "vendor": parsed.vendor or "",
        "date": parsed.date or "",
        "total": _cents_to_input(parsed.total_cents),
        "tax": _cents_to_input(parsed.tax_cents),
        "category_id": None,
        "is_capital_improvement": False,
        "notes": "",
        "items": [
            {
                "description": item.description,
                "quantity": "" if item.quantity is None else f"{item.quantity:g}",
                "amount": _cents_to_input(item.total_cents),
            }
            for item in parsed.items
        ],
    }


def _review_values_from_form(form) -> dict:
    rows = zip_longest(
        form.getlist("item_description"), form.getlist("item_quantity"), form.getlist("item_amount"), fillvalue=""
    )
    category = str(form.get("category_id") or "")
    prop = str(form.get("property_id") or "")
    return {
        "property_id": int(prop) if prop.isdigit() else None,
        "vendor": str(form.get("vendor") or "").strip(),
        "date": str(form.get("date") or "").strip(),
        "total": str(form.get("total") or "").strip(),
        "tax": str(form.get("tax") or "").strip(),
        "category_id": int(category) if category.isdigit() else None,
        "is_capital_improvement": form.get("is_capital_improvement") == "on",
        "notes": str(form.get("notes") or "").strip(),
        "items": [
            {"description": str(d).strip(), "quantity": str(q).strip(), "amount": str(a).strip()}
            for d, q, a in rows
            if str(d).strip() or str(a).strip()
        ],
    }


def _line_items_from_values(values: dict) -> list[NewLineItem]:
    items = []
    for number, row in enumerate(values["items"], start=1):
        if not row["description"]:
            raise TransactionError(f"Item {number} needs a description.")
        amount = _parse_optional_money(row["amount"], f"Item {number}")
        if amount is None:
            raise TransactionError(f"Item {number} needs an amount.")
        quantity = None
        if row["quantity"]:
            try:
                quantity = float(row["quantity"])
            except ValueError:
                raise TransactionError(f"Item {number}: quantity must be a number.") from None
            if quantity <= 0:
                raise TransactionError(f"Item {number}: quantity must be more than zero.")
        unit_price = round(amount / quantity) if quantity else None
        items.append(NewLineItem(row["description"], amount, quantity, unit_price))
    return items


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    setup_conn = connect(config.db_path)
    init_db(setup_conn)
    setup_conn.close()

    app = FastAPI(title="Property Tracker", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    templates.env.filters["money"] = format_cents

    # Handlers and this dependency are all async so each request's SQLite
    # connection stays on the event-loop thread that created it.
    async def get_conn():
        conn = connect(config.db_path)
        try:
            # If the database was upgraded by a newer copy of the app while this one kept
            # running, saving would fail on changed tables. Say so instead of crashing.
            if conn.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
                raise HTTPException(
                    status_code=503,
                    detail="The app was updated while this window was open. Close the black "
                    "Property Tracker window and double-click start.bat again.",
                )
            yield conn
        finally:
            conn.close()

    def render(request: Request, template: str, status_code: int = 200, **context):
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    def load_property(conn, prop_id: int) -> Property:
        prop = get_property(conn, prop_id)
        if prop is None:
            raise HTTPException(status_code=404, detail="That property doesn't exist.")
        return prop

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return render(request, "error.html", status_code=exc.status_code, status=exc.status_code, message=exc.detail)

    # The server still prints the full error details in its window after this page is sent.
    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return render(
            request, "error.html", status_code=500, status=500,
            message="Something went wrong and nothing was saved. The details are in the black Property "
            "Tracker window; copy them to Claude to get it fixed.",
        )

    @app.get("/")
    async def home():
        return RedirectResponse("/properties", status_code=303)

    @app.get("/properties")
    async def properties_page(request: Request, deleted: bool = False, conn=Depends(get_conn)):
        return render(
            request, "properties.html", active="properties", properties=list_properties(conn), deleted=deleted
        )

    def property_form(request, *, heading, submit_label, cancel_url, values, error=None, warnings=(), status=200):
        return render(
            request,
            "property_form.html",
            status_code=status,
            active="properties",
            heading=heading,
            submit_label=submit_label,
            cancel_url=cancel_url,
            values=values,
            error=error,
            warnings=list(warnings),
        )

    @app.get("/properties/new")
    async def new_property_page(request: Request):
        return property_form(
            request, heading="Add property", submit_label="Add property", cancel_url="/properties",
            values=_empty_values(),
        )

    @app.post("/properties/new")
    async def create_property(request: Request, conn=Depends(get_conn)):
        values = _values_from_form(await request.form())
        try:
            fields, units = _fields_from_values(values)
            prop = add_property(conn, units=units, **fields)
        except PropertyError as exc:
            return property_form(
                request, heading="Add property", submit_label="Add property", cancel_url="/properties",
                values=values, error=str(exc), status=400,
            )
        return RedirectResponse(f"/properties/{prop.id}?saved=added", status_code=303)

    @app.get("/properties/{prop_id}")
    async def property_page(request: Request, prop_id: int, saved: str = "", conn=Depends(get_conn)):
        prop = load_property(conn, prop_id)
        return render(
            request,
            "property_detail.html",
            active="properties",
            prop=prop,
            counts=linked_record_counts(conn, prop.id),
            categories=list_categories(conn, prop.is_rental),
            expenses=list_transactions(conn, prop.id, limit=20),
            notice=SAVED_MESSAGES.get(saved),
        )

    @app.get("/properties/{prop_id}/edit")
    async def edit_property_page(request: Request, prop_id: int, conn=Depends(get_conn)):
        prop = load_property(conn, prop_id)
        return property_form(
            request, heading=f"Edit {prop.name}", submit_label="Save changes",
            cancel_url=f"/properties/{prop.id}", values=_values_from_property(prop),
        )

    @app.post("/properties/{prop_id}/edit")
    async def save_property(request: Request, prop_id: int, conn=Depends(get_conn)):
        prop = load_property(conn, prop_id)
        form = await request.form()
        values = _values_from_form(form)
        form_args = dict(
            heading=f"Edit {prop.name}", submit_label="Save changes",
            cancel_url=f"/properties/{prop.id}", values=values,
        )
        try:
            fields, units = _fields_from_values(values)
        except PropertyError as exc:
            return property_form(request, **form_args, error=str(exc), status=400)

        # Switching type or removing units loses details, so it needs an explicit confirmation.
        warnings = []
        if fields["is_rental"] != prop.is_rental:
            target = "rental" if fields["is_rental"] else "personal"
            warnings.append(f"This will switch {prop.name} to a {target} property.")
            mismatched = mismatched_category_count(conn, prop.id, fields["is_rental"])
            if mismatched:
                warnings.append(
                    f"{mismatched} expense(s) use categories that don't apply to a {target} "
                    "property and will need recategorizing."
                )
            if not fields["is_rental"] and prop.units:
                warnings.append(
                    f"Its {len(prop.units)} unit(s) and their rent, tenant and lease details will be removed."
                )
        elif prop.is_rental:
            kept = {unit.id for unit in units}
            removed = [unit.label for unit in prop.units if unit.id not in kept]
            if removed:
                warnings.append(
                    f"Unit(s) {', '.join(removed)} and their rent, tenant and lease details will be removed."
                )
        if warnings and form.get("confirmed") != "1":
            return property_form(request, **form_args, warnings=warnings)

        try:
            update_property(conn, prop.id, units=units, **fields)
        except PropertyError as exc:
            return property_form(request, **form_args, error=str(exc), status=400)
        return RedirectResponse(f"/properties/{prop.id}?saved=updated", status_code=303)

    @app.get("/properties/{prop_id}/delete")
    async def delete_property_page(request: Request, prop_id: int, conn=Depends(get_conn)):
        prop = load_property(conn, prop_id)
        return render(
            request, "delete.html", active="properties", prop=prop, counts=linked_record_counts(conn, prop.id)
        )

    @app.post("/properties/{prop_id}/delete")
    async def remove_property(request: Request, prop_id: int, conn=Depends(get_conn)):
        prop = load_property(conn, prop_id)
        try:
            delete_property(conn, prop.id)
        except PropertyError as exc:
            return render(
                request, "delete.html", status_code=400, active="properties", prop=prop,
                counts=linked_record_counts(conn, prop.id), error=str(exc),
            )
        return RedirectResponse("/properties?deleted=true", status_code=303)

    # ---- Receipts -------------------------------------------------------------

    def load_receipt(conn, receipt_id: int):
        receipt = get_receipt(conn, receipt_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="That receipt doesn't exist.")
        return receipt

    @app.get("/receipts")
    async def receipts_page(request: Request, saved: bool = False, deleted: bool = False, conn=Depends(get_conn)):
        return render(
            request, "receipts.html", active="receipts", receipts=list_receipts(conn),
            has_properties=bool(list_properties(conn)), saved=saved, deleted=deleted,
        )

    def scan_form(request, conn, *, property_id=None, error=None, status=200):
        return render(
            request, "receipt_new.html", status_code=status, active="receipts",
            properties=list_properties(conn), property_id=property_id, error=error,
        )

    @app.get("/receipts/new")
    async def new_receipt_page(request: Request, property: int | None = None, conn=Depends(get_conn)):
        last = request.cookies.get("last_property", "")
        property_id = property or (int(last) if last.isdigit() else None)
        return scan_form(request, conn, property_id=property_id)

    @app.post("/receipts/new")
    async def upload_receipt(request: Request, conn=Depends(get_conn)):
        form = await request.form()
        prop = str(form.get("property_id") or "")
        property_id = int(prop) if prop.isdigit() else None
        upload = form.get("photo")
        if property_id is None or get_property(conn, property_id) is None:
            return scan_form(request, conn, error="Pick which property this receipt is for.", status=400)
        if upload is None or not hasattr(upload, "read"):
            return scan_form(request, conn, property_id=property_id, error="Take or choose a photo.", status=400)
        photo = await upload.read(MAX_UPLOAD_BYTES + 1)
        if not photo:
            return scan_form(request, conn, property_id=property_id, error="Take or choose a photo.", status=400)
        if len(photo) > MAX_UPLOAD_BYTES:
            return scan_form(request, conn, property_id=property_id, error="That photo is too large (25 MB max).", status=400)

        # Reading a receipt takes a few seconds of CPU, so it runs on a worker thread
        # with its own database connection instead of blocking the server.
        def scan() -> int:
            worker_conn = connect(config.db_path)
            try:
                return scan_receipt(
                    worker_conn, property_id=property_id, photo=photo,
                    receipts_dir=config.receipts_dir, tesseract_cmd=config.tesseract_cmd,
                ).id
            finally:
                worker_conn.close()

        try:
            receipt_id = await run_in_threadpool(scan)
        except (ImageError, ReceiptError) as exc:
            return scan_form(request, conn, property_id=property_id, error=str(exc), status=400)
        response = RedirectResponse(f"/receipts/{receipt_id}", status_code=303)
        response.set_cookie("last_property", str(property_id), max_age=365 * 24 * 3600, samesite="lax")
        return response

    def review_page(request, conn, receipt, values, *, error=None, status=200):
        properties = list_properties(conn)
        selected = next((p for p in properties if p.id == values["property_id"]), None)
        if values["category_id"] is None and selected is not None and not error:
            values["category_id"] = suggest_category_id(conn, values["vendor"], selected.is_rental)
        return render(
            request, "receipt_review.html", status_code=status, active="receipts",
            receipt=receipt, values=values, properties=properties, selected=selected,
            rental_categories=list_categories(conn, is_rental=True),
            personal_categories=list_categories(conn, is_rental=False),
            blank_rows=BLANK_ITEM_ROWS, error=error,
        )

    @app.get("/receipts/{receipt_id}")
    async def receipt_page(request: Request, receipt_id: int, conn=Depends(get_conn)):
        receipt = load_receipt(conn, receipt_id)
        transaction = get_transaction(conn, receipt.transaction_id) if receipt.transaction_id else None
        items = get_line_items(conn, transaction.id) if transaction else []
        return review_page(request, conn, receipt, _review_values_from_receipt(receipt, transaction, items))

    @app.post("/receipts/{receipt_id}")
    async def save_receipt(request: Request, receipt_id: int, conn=Depends(get_conn)):
        receipt = load_receipt(conn, receipt_id)
        values = _review_values_from_form(await request.form())
        try:
            if values["property_id"] is None:
                raise TransactionError("Pick a property.")
            total = _parse_optional_money(values["total"], "Total")
            transaction_id = save_transaction(
                conn,
                property_id=values["property_id"],
                date=values["date"],
                vendor=values["vendor"],
                amount_cents=total,
                tax_cents=_parse_optional_money(values["tax"], "Tax"),
                category_id=values["category_id"],
                is_capital_improvement=values["is_capital_improvement"],
                source="receipt",
                notes=values["notes"],
                items=_line_items_from_values(values),
                transaction_id=receipt.transaction_id,
            )
        except TransactionError as exc:
            return review_page(request, conn, receipt, values, error=str(exc), status=400)
        mark_reviewed(conn, receipt.id, property_id=values["property_id"], transaction_id=transaction_id)
        return RedirectResponse("/receipts?saved=true", status_code=303)

    @app.get("/receipts/{receipt_id}/image")
    async def receipt_image(receipt_id: int, conn=Depends(get_conn)):
        receipt = load_receipt(conn, receipt_id)
        path = resolve_receipt_path(config.receipts_dir, receipt.image_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="The photo for this receipt is missing.")
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/receipts/{receipt_id}/delete")
    async def remove_receipt(receipt_id: int, conn=Depends(get_conn)):
        load_receipt(conn, receipt_id)
        delete_receipt(conn, receipt_id, config.receipts_dir)
        return RedirectResponse("/receipts?deleted=true", status_code=303)

    @app.get("/categories")
    async def categories_page(request: Request, conn=Depends(get_conn)):
        return render(
            request,
            "categories.html",
            active="categories",
            rental=list_categories(conn, is_rental=True),
            personal=list_categories(conn, is_rental=False),
        )

    return app
