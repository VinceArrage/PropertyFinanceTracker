"""Command-line interface: `proptrack --help`."""

import dataclasses
import sqlite3
from pathlib import Path
from typing import Annotated, Optional

import click
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from proptrack.categories import list_categories
from proptrack.config import load_config
from proptrack.db import connect, init_db
from proptrack.money import format_cents, parse_money
from proptrack.properties import (
    MAX_UNITS,
    Property,
    PropertyError,
    Unit,
    UnitInput,
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


def _lease_text(unit: Unit) -> str:
    if not (unit.lease_start or unit.lease_end):
        return ""
    return f"{unit.lease_start or '?'} to {unit.lease_end or '?'}"


def _money_or_blank(cents: int | None) -> str:
    return format_cents(cents) if cents is not None else ""


def _prompt_optional(text: str) -> str:
    return typer.prompt(f"{text} (blank to skip)", default="", show_default=False)


def _prompt_units(count: int) -> list[UnitInput]:
    units = []
    for number in range(1, count + 1):
        if count > 1:
            console.print(f"[bold]Unit {number}[/bold]")
        units.append(
            UnitInput(
                label=typer.prompt("  Apartment / unit no.", default=str(number)),
                monthly_rent_cents=_parse_rent(_prompt_optional("  Monthly rent")),
                tenant_name=_prompt_optional("  Tenant name"),
                lease_start=_prompt_optional("  Lease start YYYY-MM-DD"),
                lease_end=_prompt_optional("  Lease end YYYY-MM-DD"),
            )
        )
    return units


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
    units: Annotated[
        Optional[int], typer.Option(min=1, max=MAX_UNITS, help="Number of units (rentals only).")
    ] = None,
    notes: Annotated[Optional[str], typer.Option(help="Anything else worth remembering.")] = None,
) -> None:
    """Add a property. Run with no arguments to be asked step by step, including each unit."""
    interactive = name is None
    if interactive:
        name = typer.prompt("Property name (e.g. 'Elm St duplex')")
        address = address or typer.prompt("Address", default="", show_default=False)
    if rental is None:
        rental = typer.confirm("Is this a rental property?", default=False)
    if units is not None and not rental:
        _fail("Only rental properties have units.")

    unit_inputs: list[UnitInput] = []
    if rental:
        if interactive:
            count = units or typer.prompt("How many units?", default=1, type=click.IntRange(1, MAX_UNITS))
            unit_inputs = _prompt_units(count)
        else:
            unit_inputs = [UnitInput() for _ in range(units or 1)]

    conn = _open_db()
    try:
        prop = add_property(conn, name, address=address, is_rental=rental, notes=notes, units=unit_inputs)
    except PropertyError as exc:
        _fail(str(exc))
    detail = f", {len(prop.units)} unit{'s' if len(prop.units) != 1 else ''}" if prop.is_rental else ""
    console.print(f"Added [bold]{escape(prop.name)}[/bold] ({prop.kind}{detail}).")


@property_app.command("list")
def property_list() -> None:
    """Show all properties."""
    props = list_properties(_open_db())
    if not props:
        console.print("No properties yet. Add one with 'proptrack property add'.")
        return
    table = Table("Name", "Type", "Address", "Units", "Monthly rent")
    for prop in props:
        table.add_row(
            escape(prop.name),
            prop.kind,
            escape(prop.address or ""),
            str(len(prop.units)) if prop.is_rental else "",
            _money_or_blank(prop.total_rent_cents),
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
        console.print(f"  Monthly rent: {_money_or_blank(prop.total_rent_cents) or '-'}")
        table = Table("Apt / unit", "Tenant", "Lease", "Rent")
        for unit in prop.units:
            table.add_row(
                escape(unit.label), escape(unit.tenant_name or ""), _lease_text(unit),
                _money_or_blank(unit.monthly_rent_cents),
            )
        console.print(table)
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
    notes: Annotated[Optional[str], typer.Option(help="Notes.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Change a property's details, or switch it between rental and personal.

    To change units (apartment numbers, rent, tenants, leases), use the web app.
    """
    conn = _open_db()
    prop = _require_property(conn, name)
    changes = {"name": new_name, "address": address, "is_rental": rental, "notes": notes}
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
        if not rental and prop.units:
            console.print(
                f"[yellow]Its {len(prop.units)} unit(s) and their rent, tenant and lease details "
                "will be removed.[/yellow]"
            )
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
