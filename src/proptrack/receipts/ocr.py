"""Run Tesseract on a receipt photo."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytesseract
from pytesseract import Output


class OcrError(RuntimeError):
    pass


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float  # Tesseract's average word confidence, 0-100


# Page segmentation modes to try: 6 = one uniform block of text, 4 = one column of
# variable-size text. Neither wins on every receipt (4 sometimes drops digits
# separated by wide gaps), so both are tried and the reader keeps the better result.
PAGE_MODES = ("6", "4")


def run_tesseract(image: np.ndarray, tesseract_cmd: Path, page_mode: str = "6") -> OcrResult:
    if not tesseract_cmd.exists():
        raise OcrError(f"Tesseract isn't installed at {tesseract_cmd}. Check config.toml.")
    pytesseract.pytesseract.tesseract_cmd = str(tesseract_cmd)
    try:
        data = pytesseract.image_to_data(image, config=f"--oem 1 --psm {page_mode}", output_type=Output.DICT)
    except pytesseract.TesseractError as exc:
        raise OcrError(f"Tesseract failed: {exc}") from None

    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences = []
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
        confidence = float(data["conf"][i])
        if confidence >= 0:
            confidences.append(confidence)
    text = "\n".join(" ".join(words) for words in lines.values())
    return OcrResult(text=text, confidence=sum(confidences) / len(confidences) if confidences else 0.0)
