"""Photo -> best OCR text -> extracted fields.

Several cleanup variants and Tesseract modes are tried. Each reading is parsed and
scored on whether its numbers are consistent (subtotal + tax = total, items add
up), which says more about accuracy than Tesseract's own confidence. Reading stops
early once a result checks out.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image

from proptrack.receipts.ocr import PAGE_MODES, OcrResult, run_tesseract
from proptrack.receipts.parse import ParsedReceipt, parse_receipt_text
from proptrack.receipts.preprocess import prepare_variants

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class Reading:
    ocr: OcrResult
    parsed: ParsedReceipt

    @property
    def score(self) -> tuple:
        return (_CONFIDENCE_RANK[self.parsed.confidence], -len(self.parsed.warnings), self.ocr.confidence)


def read_receipt(
    image: Image.Image, tesseract_cmd: Path, known_vendors: list[str] = (), today: date | None = None
) -> Reading:
    best: Reading | None = None
    for variant in prepare_variants(image):
        for page_mode in PAGE_MODES:
            ocr = run_tesseract(variant, tesseract_cmd, page_mode)
            reading = Reading(ocr, parse_receipt_text(ocr.text, known_vendors, today))
            if best is None or reading.score > best.score:
                best = reading
            if reading.parsed.confidence == "high" and not reading.parsed.warnings:
                return reading
    return best
