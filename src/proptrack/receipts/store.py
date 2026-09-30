"""Receipt records: photo in, extracted fields out, then reviewed into an expense."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from proptrack.properties import get_property
from proptrack.receipts.images import load_image, resolve_receipt_path, save_receipt_image
from proptrack.receipts.ocr import OcrError
from proptrack.receipts.parse import ParsedReceipt
from proptrack.receipts.reader import read_receipt
from proptrack.transactions import delete_transaction, known_vendors


class ReceiptError(ValueError):
    pass


@dataclass(frozen=True)
class Receipt:
    id: int
    property_id: int | None
    property_name: str | None
    transaction_id: int | None
    image_path: str
    ocr_text: str | None
    ocr_confidence: float | None
    status: str
    created_at: str
    parsed: ParsedReceipt


_SELECT = """
    SELECT r.*, p.name AS property_name FROM receipts r
    LEFT JOIN properties p ON p.id = r.property_id
"""


def _from_row(row: sqlite3.Row) -> Receipt:
    data = dict(row)
    parsed_json = data.pop("parsed_json")
    parsed = ParsedReceipt.from_dict(json.loads(parsed_json)) if parsed_json else ParsedReceipt()
    return Receipt(**data, parsed=parsed)


def scan_receipt(
    conn: sqlite3.Connection, *, property_id: int, photo: bytes, receipts_dir: Path, tesseract_cmd: Path
) -> Receipt:
    """Store the photo, read it, and create a receipt waiting for review.

    If text recognition fails the photo is still kept, so the details can be typed in by hand.
    """
    if get_property(conn, property_id) is None:
        raise ReceiptError("Pick a property for this receipt.")
    image = load_image(photo)
    image_path = save_receipt_image(image, receipts_dir)

    try:
        reading = read_receipt(image, tesseract_cmd, known_vendors(conn))
        parsed, text, confidence = reading.parsed, reading.ocr.text, reading.ocr.confidence
    except OcrError as exc:
        parsed = ParsedReceipt(warnings=[f"The receipt couldn't be read automatically ({exc}). Enter the details below."])
        text, confidence = None, None

    with conn:
        receipt_id = conn.execute(
            "INSERT INTO receipts (property_id, image_path, ocr_text, ocr_confidence, parsed_json) VALUES (?, ?, ?, ?, ?)",
            (property_id, image_path, text, confidence, json.dumps(parsed.to_dict())),
        ).lastrowid
    return get_receipt(conn, receipt_id)


def get_receipt(conn: sqlite3.Connection, receipt_id: int) -> Receipt | None:
    row = conn.execute(_SELECT + " WHERE r.id = ?", (receipt_id,)).fetchone()
    return _from_row(row) if row else None


def list_receipts(conn: sqlite3.Connection, limit: int = 100) -> list[Receipt]:
    rows = conn.execute(_SELECT + " ORDER BY r.status = 'reviewed', r.created_at DESC, r.id DESC LIMIT ?", (limit,))
    return [_from_row(row) for row in rows]


def mark_reviewed(conn: sqlite3.Connection, receipt_id: int, *, property_id: int, transaction_id: int) -> None:
    with conn:
        conn.execute(
            "UPDATE receipts SET status = 'reviewed', property_id = ?, transaction_id = ? WHERE id = ?",
            (property_id, transaction_id, receipt_id),
        )


def delete_receipt(conn: sqlite3.Connection, receipt_id: int, receipts_dir: Path) -> None:
    """Delete a receipt, the expense created from it, and its photo."""
    receipt = get_receipt(conn, receipt_id)
    if receipt is None:
        return
    with conn:
        conn.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))
    if receipt.transaction_id is not None:
        delete_transaction(conn, receipt.transaction_id)
    path = resolve_receipt_path(receipts_dir, receipt.image_path)
    path.unlink(missing_ok=True)
