"""Web interface. Start it with `proptrack serve`, then open http://localhost:8000."""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from proptrack.categories import list_categories
from proptrack.config import Config, load_config
from proptrack.db import connect, init_db
from proptrack.money import format_cents, parse_money
from proptrack.properties import (
    RENTAL_ONLY_FIELDS,
    Property,
    PropertyError,
    add_property,
    delete_property,
    get_property,
    linked_record_counts,
    list_properties,
    mismatched_category_count,
    update_property,
)

WEB_DIR = Path(__file__).parent
TEXT_FIELDS = ("name", "address", "monthly_rent", "tenant_name", "lease_start", "lease_end", "notes")
SAVED_MESSAGES = {"added": "Property added.", "updated": "Changes saved."}


def _cents_to_input(cents: int | None) -> str:
    if cents is None:
        return ""
    dollars, remainder = divmod(cents, 100)
    return f"{dollars}.{remainder:02d}"


def _empty_values() -> dict:
    return {key: "" for key in TEXT_FIELDS} | {"is_rental": False}


def _values_from_property(prop: Property) -> dict:
    return {
        "name": prop.name,
        "address": prop.address or "",
        "monthly_rent": _cents_to_input(prop.monthly_rent_cents),
        "tenant_name": prop.tenant_name or "",
        "lease_start": prop.lease_start or "",
        "lease_end": prop.lease_end or "",
        "notes": prop.notes or "",
        "is_rental": prop.is_rental,
    }


def _values_from_form(form) -> dict:
    return {key: str(form.get(key) or "").strip() for key in TEXT_FIELDS} | {
        "is_rental": form.get("is_rental") == "on"
    }


def _fields_from_values(values: dict) -> dict:
    """Turn form strings into property fields. Rental details are dropped for personal homes."""
    is_rental = values["is_rental"]
    rent = None
    if is_rental and values["monthly_rent"]:
        try:
            rent = parse_money(values["monthly_rent"])
        except ValueError as exc:
            raise PropertyError(str(exc)) from None
    return {
        "name": values["name"],
        "address": values["address"],
        "notes": values["notes"],
        "is_rental": is_rental,
        "monthly_rent_cents": rent,
        "tenant_name": values["tenant_name"] if is_rental else None,
        "lease_start": values["lease_start"] if is_rental else None,
        "lease_end": values["lease_end"] if is_rental else None,
    }


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
        return render(request, "error.html", status_code=exc.status_code, message=exc.detail)

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
            prop = add_property(conn, **_fields_from_values(values))
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
            fields = _fields_from_values(values)
        except PropertyError as exc:
            return property_form(request, **form_args, error=str(exc), status=400)

        # Switching between rental and personal needs an explicit confirmation.
        if fields["is_rental"] != prop.is_rental and form.get("confirm_switch") != "1":
            target = "rental" if fields["is_rental"] else "personal"
            warnings = [f"This will switch {prop.name} to a {target} property."]
            mismatched = mismatched_category_count(conn, prop.id, fields["is_rental"])
            if mismatched:
                warnings.append(
                    f"{mismatched} expense(s) use categories that don't apply to a {target} "
                    "property and will need recategorizing."
                )
            if not fields["is_rental"] and any(getattr(prop, key) is not None for key in RENTAL_ONLY_FIELDS):
                warnings.append("The rent, tenant and lease details will be cleared.")
            return property_form(request, **form_args, warnings=warnings)

        try:
            update_property(conn, prop.id, **fields)
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
