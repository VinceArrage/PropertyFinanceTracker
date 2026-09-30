"""End-to-end reading of generated receipt photos with the real Tesseract."""

from datetime import date

import pytest

from proptrack.config import DEFAULT_TESSERACT
from proptrack.receipts.images import load_image
from proptrack.receipts.ocr import OcrError
from proptrack.receipts.reader import read_receipt
from tests.fake_receipts import make_receipt_photo

needs_tesseract = pytest.mark.skipif(not DEFAULT_TESSERACT.exists(), reason="Tesseract not installed")


@needs_tesseract
@pytest.mark.parametrize("rotate", [0, 4, -7])
def test_reads_generated_receipt(rotate):
    image = load_image(make_receipt_photo(rotate=rotate))
    result = read_receipt(image, DEFAULT_TESSERACT, today=date(2026, 9, 29)).parsed
    assert result.vendor == "Home Depot"
    assert result.date == "2026-09-28"
    assert result.total_cents == 5922
    assert result.tax_cents == 439
    assert result.confidence == "high"


def test_missing_tesseract_is_reported(tmp_path):
    image = load_image(make_receipt_photo())
    with pytest.raises(OcrError, match="isn't installed"):
        read_receipt(image, tmp_path / "nope.exe")
