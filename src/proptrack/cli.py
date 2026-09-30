"""Command-line interface: `proptrack --help`."""

import dataclasses
import sqlite3
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from proptrack.categories import list_categories
from proptrack.config import load_config
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

app = typer.Typer(help="Track expenses, receipts and rent for your properties.", no_args_is_help=True)
property_app = typer.Typer(help="Add and manage properties.", no_args_is_help=True)
app.add_typer(property_app, name="property")

console = Console()


def _open_db() -> sqlite3.Connection:
    conn = connect(load_config().db_path)
    init_db(conn)
    return conn


def _fail(message: str) -> None:
    console.print(f"[red]Error:[/red] {escape(message)}")
    raise typer.Exit(1)


def _parse_rent(text: str | None) -> int | None:
    if text is None or not text.strip():
        return None
    try:
        return parse_money(text)
    except ValueError as exc:
        _fail(str(exc))


def _require_property(conn: sqlite3.Connection, name: str) -> Property:
    prop = get_property(conn, name)
    if prop is None:
        _fail(f"No property named {name!r}. Run 'proptrack property list' to see them.")
    return prop


def _lease_text(prop: Property) -> str:
    if not (prop.lease_start or prop.lease_end):
        return ""
    return f"{prop.lease_start or '?'} to {prop.lease_end or '?'}"


@app.command()
def init() -> None:
    """Create the database and show where data is stored."""
    config = load_config()
    conn = connect(config.db_path)
    init_db(conn)
    config.receipts_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"Database:  {config.db_path}")
    console.print(f"Receipts:  {config.receipts_dir}")
    if config.tesseract_cmd.exists():
        console.print(f"Tesseract: {config.tesseract_cmd}")
    else:
        console.print(f"[yellow]Tesseract not found at {config.tesseract_cmd}[/yellow] (update config.toml)")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Use 0.0.0.0 to allow phones on your Wi-Fi.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port number.")] = 8000,
    open_browser: Annotated[bool, typer.Option("--open", help="Open the app in your browser.")] = False,
    data_dir: Annotated[Optional[Path], typer.Option(help="Use a different data folder (for trying things out).")] = None,
) -> None:
    """Start the web app."""
    import threading
    import webbrowser

    import uvicorn

    from proptrack.web.app import create_app

    config = load_config()
    if data_dir is not None:
        config = dataclasses.replace(config, data_dir=data_dir)
    url = f"http://localhost:{port}"
    console.print(f"Property Tracker is running at {url}")
    console.print(f"Data: {config.data_dir}")
    console.print("Keep this window open while you use the app. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")


@property_app.command("add")
def property_add(
    name: Annotated[Optional[str], typer.Argument(help="Short name, e.g. 'Elm St duplex'.")] = None,
    address: Annotated[Optional[str], typer.Option(help="Street address.")] = None,
    rental: Annotated[
        Optional[bool], typer.Option("--rental/--personal", help="Is this a rental property?")
    ] = None,
    rent: Annotated[Optional[str], typer.Option(help="Monthly rent, e.g. 1800 (rentals only).")] = None,
    tenant: Annotated[Optional[str], typer.Option(help="Tenant name (rentals only).")] = None,
    lease_start: Annotated[Optional[str], typer.Option(help="YYYY-MM-DD (rentals only).")] = None,
    lease_end: Annotated[Optional[str], typer.Option(help="YYYY-MM-DD (rentals only).")] = None,
    notes: Annotated[Optional[str], typer.Option(help="Anything else worth remembering.")] = None,
) -> None:
    """Add a property. Run with no arguments to be asked step by step."""
    interactive = name is None
    if interactive:
        name = typer.prompt("Property name (e.g. 'Elm St duplex')")
        address = address or typer.prompt("Address", default="", show_default=False)
    if rental is None:
        rental = typer.confirm("Is this a rental property?", default=False)
    if interactive and rental:
        rent = rent or typer.prompt("Monthly rent (blank to skip)", default="", show_default=False)
        tenant = tenant or typer.prompt("Tenant name (blank to skip)", default="", show_default=False)
        lease_start = lease_start or typer.prompt(
            "Lease start YYYY-MM-DD (blank to skip)", default="", show_default=False
        )
        lease_end = lease_end or typer.prompt(
            "Lease end YYYY-MM-DD (blank to skip)", default="", show_default=False
        )

    conn = _open_db()
    try:
        prop = add_property(
            conn,
            name,
            address=address,
            is_rental=rental,
            monthly_rent_cents=_parse_rent(rent),
            tenant_name=tenant or None,
            lease_start=lease_start or None,
            lease_end=lease_end or None,
            notes=notes,
        )
    except PropertyError as exc:
        _fail(str(exc))
    console.print(f"Added [bold]{escape(prop.name)}[/bold] ({prop.kind}).")


@property_app.command("list")
def property_list() -> None:
    """Show all properties."""
    props = list_properties(_open_db())
    if not props:
        console.print("No properties yet. Add one with 'proptrack property add'.")
        return
    table = Table("Name", "Type", "Address", "Monthly rent", "Tenant", "Lease")
    for prop in props:
        table.add_row(
            escape(prop.name),
            prop.kind,
            escape(prop.address or ""),
            format_cents(prop.monthly_rent_cents) if prop.monthly_rent_cents is not None else "",
            escape(prop.tenant_name or ""),
            _lease_text(prop),
        )
    console.print(table)


@property_app.command("show")
def property_show(name: Annotated[str, typer.Argument(help="Property name.")]) -> None:
    """Show one property's details and its expense categories."""
    conn = _open_db()
    prop = _require_property(conn, name)
    console.print(f"[bold]{escape(prop.name)}[/bold] ({prop.kind})")
    console.print(f"  Address: {escape(prop.address or '-')}")
    if prop.is_rental:
        rent = format_cents(prop.monthly_rent_cents) if prop.monthly_rent_cents is not None else "-"
        console.print(f"  Monthly rent: {rent}")
        console.print(f"  Tenant: {escape(prop.tenant_name or '-')}")
        console.print(f"  Lease: {_lease_text(prop) or '-'}")
    if prop.notes:
        console.print(f"  Notes: {escape(prop.notes)}")
    counts = linked_record_counts(conn, prop.id)
    console.print(
        f"  Records: {counts['transactions']} expenses, {counts['receipts']} receipts, "
        f"{counts['rent_payments']} rent payments"
    )
    categories = ", ".join(c.name for c in list_categories(conn, prop.is_rental))
    console.print(f"  Expense categories: {categories}")


@property_app.command("edit")
def property_edit(
    name: Annotated[str, typer.Argument(help="Current property name.")],
    new_name: Annotated[Optional[str], typer.Option("--name", help="Rename the property.")] = None,
    address: Annotated[Optional[str], typer.Option(help="Street address.")] = None,
    rental: Annotated[
        Optional[bool], typer.Option("--rental/--personal", help="Switch between rental and personal.")
    ] = None,
    rent: Annotated[Optional[str], typer.Option(help="Monthly rent (rentals only).")] = None,
    tenant: Annotated[Optional[str], typer.Option(help="Tenant name (rentals only).")] = None,
    lease_start: Annotated[Optional[str], typer.Option(help="YYYY-MM-DD (rentals only).")] = None,
    lease_end: Annotated[Optional[str], typer.Option(help="YYYY-MM-DD (rentals only).")] = None,
    notes: Annotated[Optional[str], typer.Option(help="Notes.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Change a property's details, or switch it between rental and personal."""
    conn = _open_db()
    prop = _require_property(conn, name)
    changes = {
        "name": new_name,
        "address": address,
        "is_rental": rental,
        "monthly_rent_cents": _parse_rent(rent),
        "tenant_name": tenant,
        "lease_start": lease_start,
        "lease_end": lease_end,
        "notes": notes,
    }
    changes = {key: value for key, value in changes.items() if value is not None}
    if not changes:
        _fail("Nothing to change. See 'proptrack property edit --help'.")

    if rental is not None and rental != prop.is_rental:
        target = "rental" if rental else "personal"
        mismatched = mismatched_category_count(conn, prop.id, rental)
        if mismatched:
            console.print(
                f"[yellow]{mismatched} expense(s) use categories that don't apply to a {target} "
                "property. You'll need to recategorize them.[/yellow]"
            )
        if not rental and any(getattr(prop, key) is not None for key in RENTAL_ONLY_FIELDS):
            console.print("[yellow]The rent, tenant and lease details will be cleared.[/yellow]")
        if not yes and not typer.confirm(f"Switch {prop.name!r} to {target}?", default=False):
            raise typer.Exit(0)

    try:
        updated = update_property(conn, prop.id, **changes)
    except PropertyError as exc:
        _fail(str(exc))
    console.print(f"Updated [bold]{escape(updated.name)}[/bold] ({updated.kind}).")


@property_app.command("delete")
def property_delete(
    name: Annotated[str, typer.Argument(help="Property name.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Delete a property. Only allowed while it has no expenses, receipts or rent."""
    conn = _open_db()
    prop = _require_property(conn, name)
    if not yes and not typer.confirm(f"Delete {prop.name!r}?", default=False):
        raise typer.Exit(0)
    try:
        delete_property(conn, prop.id)
    except PropertyError as exc:
        _fail(str(exc))
    console.print(f"Deleted {escape(prop.name)}.")


@app.command()
def categories(
    property_name: Annotated[
        Optional[str], typer.Option("--property", "-p", help="Only categories for this property.")
    ] = None,
) -> None:
    """List expense categories (Schedule E lines for rentals)."""
    conn = _open_db()
    is_rental = _require_property(conn, property_name).is_rental if property_name else None
    table = Table("Category", "For", "Schedule E line")
    for category in list_categories(conn, is_rental):
        line = str(category.schedule_e_line) if category.schedule_e_line else ""
        if category.applies_to == "rental" and category.name == "Capital improvements":
            line = "depreciated"
        table.add_row(category.name, category.applies_to, line)
    console.print(table)
