"""Money is stored as integer cents to avoid floating-point rounding errors."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def parse_money(text: str) -> int:
    """Parse '1,800', '$1800.50' or '-12.3' into cents."""
    cleaned = text.strip().replace("$", "").replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Not a valid amount: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"Not a valid amount: {text!r}")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_cents(cents: int) -> str:
    dollars, remainder = divmod(abs(cents), 100)
    sign = "-" if cents < 0 else ""
    return f"{sign}${dollars:,}.{remainder:02d}"
