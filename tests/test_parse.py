from datetime import date

import pytest

from proptrack.receipts.parse import find_date, parse_amount, parse_receipt_text

TODAY = date(2026, 9, 29)

HARDWARE = """
THE HOME DEPOT
123 FAKE STREET
0612 00041 23456 09/28/26 02:14 PM
2X4 STUD 8FT 29.88
12 @ 2.49
DECK SCREWS 1LB 9.97
PAINT ROLLER KIT 14.98
SUBTOTAL 54.83
SALES TAX 4.39
TOTAL $59.22
VISA XXXX1234 USD$ 59.22
"""


def test_hardware_receipt():
    result = parse_receipt_text(HARDWARE, today=TODAY)
    assert result.vendor == "Home Depot"
    assert result.date == "2026-09-28"
    assert result.subtotal_cents == 5483
    assert result.tax_cents == 439
    assert result.total_cents == 5922
    assert [i.description for i in result.items] == ["2X4 STUD 8FT", "DECK SCREWS 1LB", "PAINT ROLLER KIT"]
    assert result.items[0].quantity == 12
    assert result.items[0].unit_price_cents == 249
    assert result.warnings == []
    assert result.confidence == "high"


def test_unknown_store_uses_first_name_like_line():
    text = "JOE'S PLUMBING SUPPLY\n42 Main St\nSep 3, 2026\nPIPE WRENCH 24.00\nTOTAL 24.00\n"
    result = parse_receipt_text(text, today=TODAY)
    assert result.vendor == "Joe's Plumbing Supply"
    assert result.date == "2026-09-03"
    assert result.total_cents == 2400


def test_known_vendor_from_history():
    text = "JOES PLUMBING SUPPLY\n09/01/2026\nTOTAL 10.00\n"
    result = parse_receipt_text(text, known_vendors=["Joe's Plumbing Supply"], today=TODAY)
    assert result.vendor == "Joe's Plumbing Supply"


def test_total_on_next_line():
    text = "CORNER STORE\n2026-09-10\nBATTERIES 8.00\nTOTAL\n8.00\nCASH 10.00\nCHANGE 2.00\n"
    result = parse_receipt_text(text, today=TODAY)
    assert result.total_cents == 800


def test_total_savings_and_item_count_are_not_the_total():
    text = "SHOP\n09/10/2026\nTAPE 5.00\nTOTAL 5.00\nTOTAL SAVINGS 20.00\nTOTAL ITEMS 1\n"
    assert parse_receipt_text(text, today=TODAY).total_cents == 500


def test_tax_total_line_is_not_double_counted():
    text = "SHOP\n09/10/2026\nITEM 10.00\nSUBTOTAL 10.00\nSTATE TAX 0.60\nCOUNTY TAX 0.20\nTOTAL TAX 0.80\nTOTAL 10.80\n"
    result = parse_receipt_text(text, today=TODAY)
    assert result.tax_cents == 80
    assert result.warnings == []


def test_missing_total_line_falls_back_to_subtotal_plus_tax():
    text = "SHOP\n09/10/2026\nITEM 10.00\nSUBTOTAL 10.00\nTAX 0.80\n"
    result = parse_receipt_text(text, today=TODAY)
    assert result.total_cents == 1080
    assert any("subtotal + tax" in w for w in result.warnings)


def test_mismatched_totals_warn_and_lower_confidence():
    text = "HOME DEPOT\n09/10/2026\nITEM 10.00\nSUBTOTAL 10.00\nTAX 0.80\nTOTAL 18.00\n"
    result = parse_receipt_text(text, today=TODAY)
    assert any("doesn't equal the total" in w for w in result.warnings)
    assert result.confidence != "high"


def test_discount_lines_count_as_negative_items():
    text = "SHOP\n09/10/2026\nDRILL 99.00\nINSTANT SAVINGS 10.00-\nSUBTOTAL 89.00\nTAX 0.00\nTOTAL 89.00\n"
    result = parse_receipt_text(text, today=TODAY)
    assert [i.total_cents for i in result.items] == [9900, -1000]


def test_nothing_found():
    result = parse_receipt_text("", today=TODAY)
    assert result.total_cents is None
    assert result.confidence == "low"
    assert len(result.warnings) == 3


def test_improvement_hint():
    text = "LOWES\n09/10/2026\n50 GAL WATER HEATER 649.00\nTOTAL 649.00\n"
    assert parse_receipt_text(text, today=TODAY).improvement_hint.lower() == "water heater"


def test_no_improvement_hint_for_small_hardware():
    text = "LOWES\n09/10/2026\nDECK SCREWS 9.97\nFLAT WASHER 10PK 2.49\nWINDOW CLEANER 4.99\nTOTAL 17.45\n"
    assert parse_receipt_text(text, today=TODAY).improvement_hint is None


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("TOTAL 12.34", (1234, "TOTAL")),
        ("PAINT $1,234.56", (123456, "PAINT")),
        ("PAINT 12.99 T", (1299, "PAINT")),
        ("COUPON 5.00-", (-500, "COUPON")),
        ("TOTAL 84 . 12", (8412, "TOTAL")),
        ("SKU 1234567894.98", None),  # digits run together: not a believable price
        ("NO PRICE HERE", None),
    ],
)
def test_parse_amount(line, expected):
    assert parse_amount(line) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("09/28/2026", "2026-09-28"),
        ("9-28-26", "2026-09-28"),
        ("2026-09-28", "2026-09-28"),
        ("28/09/2026", "2026-09-28"),  # day-first
        ("Sept 28, 2026", "2026-09-28"),
        ("28 Sep 2026", "2026-09-28"),
        ("12/31/2030", None),  # in the future
        ("PHONE 555-123-4567", None),
    ],
)
def test_find_date(text, expected):
    assert find_date(text, today=TODAY) == expected
