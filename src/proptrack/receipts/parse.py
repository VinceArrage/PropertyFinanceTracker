"""Find store, date, totals and line items in OCR text using rules.

Receipts vary a lot, so every result is shown on a review screen before saving.
The warnings list explains anything that looked off.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from difflib import get_close_matches

# A price at the end of a line: "4.98", "$1,234.56", "5.00-", "-2.00", "12.99 T", "84 . 12".
_AMOUNT = re.compile(
    r"(?:(?<=\s)|(?<=\$)|^)(?P<neg>-)?\$?\s?(?P<dollars>\d{1,3}(?:,\d{3})+|\d{1,5})\s?[.,]\s?(?P<cents>\d{2})"
    r"(?P<trail>-)?(?:\s*[A-Za-z*]{1,2})?\s*$"
)
_QTY = re.compile(r"(?P<qty>\d+(?:\.\d+)?)\s*(?:@|[xX]\s)\s*\$?(?P<price>\d+[.,]\d{2})")

_TOTAL = re.compile(r"\b(grand\s*total|total\s*due|amount\s*due|balance\s*due|total)\b", re.I)
_NOT_TOTAL = re.compile(
    r"sub\s*-?\s*total|total\s*(savings|saved|discount|items?|qty|quantity|tax|points|number)"
    r"|items?\s*total|you\s*saved|#\s*items",
    re.I,
)
_SUBTOTAL = re.compile(r"sub\s*-?\s*total", re.I)
_TAX = re.compile(r"\b(sales\s*)?tax\b|\bhst\b|\bgst\b|\bpst\b|\bvat\b", re.I)
_NOT_TAX = re.compile(r"pre-?\s*tax|tax\s*exempt|non-?\s*tax|before\s*tax|taxable", re.I)
_PAYMENT = re.compile(
    r"\b(cash|change|visa|master\s*card|mastercard|amex|american\s*express|discover|debit|credit|tender(ed)?"
    r"|card|payment|paid|auth|approval|balance|chip|contactless|ref|account|acct)\b",
    re.I,
)
_NOT_ITEM = re.compile(r"\b(savings|saved|points|rewards|items?\s*sold|coupon\s*total|change\s*due)\b", re.I)

_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_MONTH_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?"
_DATE_PATTERNS = [
    ("ymd", re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")),
    ("mdy", re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b")),
    ("Mdy", re.compile(rf"\b{_MONTH_RE}\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I)),
    ("dMy", re.compile(rf"\b(\d{{1,2}})\s+{_MONTH_RE},?\s+(\d{{4}})\b", re.I)),
]

_NOT_VENDOR = re.compile(
    r"welcome|thank|receipt|store\s*#|store\s*no|www\.|\.com|tel\b|phone|cashier|register|trans(action)?\b"
    r"|^\d|\d{3}[-.\s)]\d{3}[-.\s]\d{4}",
    re.I,
)

# Well-known stores, matched anywhere in the text (the name is often in a logo up top).
KNOWN_STORES = {
    r"home\s*depot": "Home Depot",
    r"lowe'?s": "Lowe's",
    r"menards": "Menards",
    r"ace\s*hardware": "Ace Hardware",
    r"true\s*value": "True Value",
    r"harbor\s*freight": "Harbor Freight",
    r"sherwin[-\s]*williams": "Sherwin-Williams",
    r"walmart|wal[-\s]*mart": "Walmart",
    r"\btarget\b": "Target",
    r"costco": "Costco",
    r"sam'?s\s*club": "Sam's Club",
    r"best\s*buy": "Best Buy",
    r"staples": "Staples",
    r"ikea": "IKEA",
    r"amazon": "Amazon",
    r"canadian\s*tire": "Canadian Tire",
    r"rona\b": "RONA",
}

# Big-ticket items that suggest a capital improvement (depreciated) rather than a repair.
# Kept to specific phrases: plain "deck", "washer" or "window" match screws, hardware
# and cleaner far more often than new decks, appliances or windows.
IMPROVEMENT_WORDS = re.compile(
    r"water\s*heater|furnace|\bhvac\b|air\s*condition(er|ing)|heat\s*pump|roofing|shingles|refrigerator"
    r"|dishwasher|washing\s*machine|clothes\s*dryer|\bcabinets?\b|countertop|flooring|hardwood|vinyl\s*plank"
    r"|replacement\s*window|\bsiding\b|remodel",
    re.I,
)


@dataclass
class LineItem:
    description: str
    total_cents: int
    quantity: float | None = None
    unit_price_cents: int | None = None


@dataclass
class ParsedReceipt:
    vendor: str | None = None
    date: str | None = None
    total_cents: int | None = None
    subtotal_cents: int | None = None
    tax_cents: int | None = None
    items: list[LineItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: str = "low"  # low / medium / high
    improvement_hint: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ParsedReceipt":
        data = dict(data)
        data["items"] = [LineItem(**item) for item in data.get("items", [])]
        return cls(**data)


def parse_amount(line: str) -> tuple[int, str] | None:
    """Return (cents, text before the amount) when a line ends with a price."""
    match = _AMOUNT.search(line)
    if not match:
        return None
    cents = int(match["dollars"].replace(",", "")) * 100 + int(match["cents"])
    if match["neg"] or match["trail"]:
        cents = -cents
    return cents, line[: match.start()].strip()


def _amount_near(lines: list[str], index: int) -> int | None:
    """Amount on a label's line, or on the next line when OCR split them."""
    found = parse_amount(lines[index])
    if found:
        return found[0]
    if index + 1 < len(lines):
        found = parse_amount(lines[index + 1])
        if found and not re.search(r"[A-Za-z]{3,}", found[1]):
            return found[0]
    return None


def find_date(text: str, today: date | None = None) -> str | None:
    today = today or date.today()
    earliest = date(2000, 1, 1)
    for kind, pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            try:
                if kind == "ymd":
                    year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                elif kind == "mdy":
                    month, day, year = int(groups[0]), int(groups[1]), int(groups[2])
                    if month > 12 >= day:  # day-first receipt
                        month, day = day, month
                elif kind == "Mdy":
                    month, day, year = _MONTHS.index(groups[0][:3].lower()) + 1, int(groups[1]), int(groups[2])
                else:
                    day, month, year = int(groups[0]), _MONTHS.index(groups[1][:3].lower()) + 1, int(groups[2])
                if year < 100:
                    year += 2000
                found = date(year, month, day)
            except ValueError:
                continue
            if earliest <= found <= today + timedelta(days=1):
                return found.isoformat()
    return None


def find_vendor(lines: list[str], known_vendors: list[str] = ()) -> tuple[str | None, bool]:
    """Return (vendor, confident). Known and previously used store names win."""
    text = "\n".join(lines)
    for pattern, name in KNOWN_STORES.items():
        if re.search(pattern, text, re.I):
            return name, True

    head = [line for line in lines[:8] if line.strip()]
    if known_vendors:
        by_lower = {vendor.lower(): vendor for vendor in known_vendors}
        for line in head[:5]:
            lowered = line.lower().strip()
            for key, vendor in by_lower.items():
                if len(key) >= 4 and key in lowered:
                    return vendor, True
            match = get_close_matches(lowered, by_lower, n=1, cutoff=0.8)
            if match:
                return by_lower[match[0]], True

    for line in head:
        letters = sum(ch.isalpha() for ch in line)
        if letters >= 3 and letters / max(len(line.replace(" ", "")), 1) >= 0.6 and not _NOT_VENDOR.search(line):
            return " ".join(word.capitalize() if word.isupper() else word for word in line.split()), False
    return None, False


def _is_summary_line(line: str) -> bool:
    return bool(_TOTAL.search(line) or _SUBTOTAL.search(line) or (_TAX.search(line) and not _NOT_TAX.search(line)))


def find_items(lines: list[str]) -> list[LineItem]:
    """Priced lines above the subtotal/total that look like products."""
    end = next((i for i, line in enumerate(lines) if _is_summary_line(line)), len(lines))
    items: list[LineItem] = []
    for line in lines[:end]:
        qty_match = _QTY.search(line)
        found = parse_amount(line)
        if qty_match and (not found or not re.search(r"[A-Za-z]{2,}", _QTY.sub("", found[1]))):
            # A "2 @ 4.98" line on its own belongs to the item above it.
            if items:
                items[-1].quantity = float(qty_match["qty"])
                items[-1].unit_price_cents = parse_amount(qty_match["price"])[0]
            continue
        if not found:
            continue
        cents, description = found
        description = re.sub(r"\b\d{5,}\b", "", description)  # SKU / UPC numbers
        if qty_match:
            description = _QTY.sub("", description)
        description = " ".join(description.split()).strip(" -*#:")
        if sum(ch.isalpha() for ch in description) < 2 or _PAYMENT.search(description):
            continue
        # "INSTANT SAVINGS 10.00-" is a discount on an item; "YOU SAVED 10.00" is just a summary.
        if _NOT_ITEM.search(description) and cents >= 0:
            continue
        item = LineItem(description=description, total_cents=cents)
        if qty_match:
            item.quantity = float(qty_match["qty"])
            item.unit_price_cents = parse_amount(qty_match["price"])[0]
        items.append(item)
    return items


def _money(cents: int) -> str:
    dollars, remainder = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}${dollars:,}.{remainder:02d}"


def parse_receipt_text(text: str, known_vendors: list[str] = (), today: date | None = None) -> ParsedReceipt:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    result = ParsedReceipt()

    result.vendor, vendor_confident = find_vendor(lines, known_vendors)
    result.date = find_date(text, today)

    total_candidates, subtotals, taxes, tax_totals = [], [], [], []
    for i, line in enumerate(lines):
        if _SUBTOTAL.search(line):
            amount = _amount_near(lines, i)
            if amount and amount > 0:
                subtotals.append(amount)
        elif _TOTAL.search(line) and not _NOT_TOTAL.search(line):
            amount = _amount_near(lines, i)
            if amount and amount > 0:
                total_candidates.append(amount)
        if _TAX.search(line) and not _NOT_TAX.search(line) and not _SUBTOTAL.search(line):
            is_tax_total = bool(re.search(r"total\s*tax|tax\s*total", line, re.I))
            if is_tax_total or not _TOTAL.search(line):
                amount = _amount_near(lines, i)
                if amount is not None and amount >= 0:
                    (tax_totals if is_tax_total else taxes).append(amount)

    result.subtotal_cents = subtotals[0] if subtotals else None
    # Several tax lines (state + county) add up, unless the receipt also prints their total.
    if tax_totals:
        result.tax_cents = max(tax_totals)
    elif taxes:
        result.tax_cents = sum(taxes)
    total_from_label = bool(total_candidates)
    if total_candidates:
        result.total_cents = max(total_candidates)
    elif result.subtotal_cents is not None and result.tax_cents is not None:
        result.total_cents = result.subtotal_cents + result.tax_cents
        result.warnings.append("No TOTAL line found; the total was worked out as subtotal + tax.")
    else:
        amounts = [found[0] for line in lines if (found := parse_amount(line)) and found[0] > 0]
        if amounts:
            result.total_cents = max(amounts)
            result.warnings.append("No TOTAL line found; guessed the largest amount on the receipt.")

    result.items = find_items(lines)

    totals_check = None
    if result.subtotal_cents is not None and result.tax_cents is not None and result.total_cents is not None:
        totals_check = abs(result.subtotal_cents + result.tax_cents - result.total_cents) <= 1
        if not totals_check:
            result.warnings.append(
                f"Subtotal {_money(result.subtotal_cents)} + tax {_money(result.tax_cents)} "
                f"doesn't equal the total {_money(result.total_cents)}. One of them may be misread."
            )
    if result.items:
        items_sum = sum(item.total_cents for item in result.items)
        target = result.subtotal_cents
        if target is None and result.total_cents is not None:
            target = result.total_cents - (result.tax_cents or 0)
        if target is not None and items_sum != target:
            result.warnings.append(
                f"The items add up to {_money(items_sum)} but should total {_money(target)}. "
                "Some items may be missing or misread."
            )

    if result.vendor is None:
        result.warnings.append("Couldn't find the store name.")
    if result.date is None:
        result.warnings.append("Couldn't find the date.")
    if result.total_cents is None:
        result.warnings.append("Couldn't find the total.")

    if result.total_cents and result.date and result.vendor and total_from_label and totals_check is not False:
        result.confidence = "high" if vendor_confident or totals_check else "medium"
    elif result.total_cents and (result.date or result.vendor):
        result.confidence = "medium"

    hint = IMPROVEMENT_WORDS.search(" ".join(item.description for item in result.items))
    result.improvement_hint = hint.group(0) if hint else None
    return result
