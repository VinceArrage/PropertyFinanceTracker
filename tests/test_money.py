import pytest

from proptrack.money import format_cents, parse_money


@pytest.mark.parametrize(
    ("text", "cents"),
    [("1800", 180000), ("$1,800.50", 180050), (" 12.345 ", 1235), ("-4.99", -499), ("0", 0)],
)
def test_parse_money(text, cents):
    assert parse_money(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "12.3.4", "nan", "inf"])
def test_parse_money_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_money(text)


@pytest.mark.parametrize(("cents", "text"), [(180050, "$1,800.50"), (5, "$0.05"), (-499, "-$4.99")])
def test_format_cents(cents, text):
    assert format_cents(cents) == text
