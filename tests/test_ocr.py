"""End-to-end reading of receipt photos with the real Tesseract."""

from datetime import date
from pathlib import Path

import cv2
import numpy as np
import pytest

from proptrack.config import DEFAULT_TESSERACT
from proptrack.receipts.images import load_image
from proptrack.receipts.ocr import OcrError
from proptrack.receipts.preprocess import find_paper_corners, find_receipt_corners
from proptrack.receipts.reader import read_receipt
from tests.fake_receipts import make_receipt_photo

needs_tesseract = pytest.mark.skipif(not DEFAULT_TESSERACT.exists(), reason="Tesseract not installed")

# Real receipt photos kept on this PC only (the folder is git-ignored). Tests using
# them are skipped wherever the photos aren't present.
REAL_RECEIPTS = Path(__file__).parent / "images"


@needs_tesseract
@pytest.mark.parametrize(
    "photo",
    [
        {"rotate": 0},
        {"rotate": 4},
        {"rotate": -7},
        {"background": "wood", "bleed": True},  # receipt runs off the photo on a wooden table
        {"background": "wood", "bleed": True, "rotate": 3},
    ],
    ids=["straight", "tilted", "tilted-other-way", "wood-bleed", "wood-bleed-tilted"],
)
def test_reads_generated_receipt(photo):
    image = load_image(make_receipt_photo(**photo))
    result = read_receipt(image, DEFAULT_TESSERACT, today=date(2026, 9, 29)).parsed
    assert result.vendor == "Home Depot"
    assert result.date == "2026-09-28"
    assert result.total_cents == 5922
    assert result.tax_cents == 439
    assert result.confidence == "high"


def test_paper_found_when_corners_are_cut_off():
    image = load_image(make_receipt_photo(background="wood", bleed=True))
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    assert find_receipt_corners(gray) is None
    corners = find_paper_corners(image)
    assert corners is not None
    left, right = corners[:, 0].min(), corners[:, 0].max()
    assert 100 <= left <= 140 and 860 <= right <= 900  # the slip sits at x=120..880


def test_no_paper_crop_on_plain_background():
    # A photo that's all paper (or a white table) is left alone rather than cropped wrongly.
    blank = load_image(make_receipt_photo(background="slate"))
    assert find_paper_corners(blank.crop((150, 150, 800, 900))) is None


def test_missing_tesseract_is_reported(tmp_path):
    image = load_image(make_receipt_photo())
    with pytest.raises(OcrError, match="isn't installed"):
        read_receipt(image, tmp_path / "nope.exe")


@needs_tesseract
@pytest.mark.skipif(not (REAL_RECEIPTS / "hopedepotrecipt.jpg").exists(), reason="real receipt photo not on this PC")
def test_real_home_depot_receipt_on_wood():
    image = load_image((REAL_RECEIPTS / "hopedepotrecipt.jpg").read_bytes())
    result = read_receipt(image, DEFAULT_TESSERACT, today=date(2026, 9, 29)).parsed
    assert result.vendor == "Home Depot"
    assert result.date == "2019-09-10"
    assert (result.subtotal_cents, result.tax_cents, result.total_cents) == (74114, 5003, 79117)
    assert [item.total_cents for item in result.items] == [1514, 57800, 14800]
    assert result.confidence == "high"
