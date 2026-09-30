"""Loading, normalizing and storing receipt photos."""

import io
import uuid
from datetime import date
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()  # iPhone photos are often HEIC

MAX_STORED_EDGE = 2400  # plenty for re-reading later, keeps files around 0.5-1 MB


class ImageError(ValueError):
    pass


def load_image(data: bytes) -> Image.Image:
    """Open a JPEG/PNG/HEIC upload, rotated upright according to its EXIF data."""
    try:
        image = Image.open(io.BytesIO(data))
        return ImageOps.exif_transpose(image).convert("RGB")
    except Exception:  # Pillow and pillow-heif raise a variety of types for bad files
        raise ImageError("That file isn't a photo the app can read. Try a JPEG, PNG or HEIC image.") from None


def save_receipt_image(image: Image.Image, receipts_dir: Path, today: date | None = None) -> str:
    """Save as JPEG under receipts/YYYY/MM/ and return the path relative to receipts_dir."""
    today = today or date.today()
    folder = receipts_dir / f"{today:%Y}" / f"{today:%m}"
    folder.mkdir(parents=True, exist_ok=True)
    image = image.copy()
    image.thumbnail((MAX_STORED_EDGE, MAX_STORED_EDGE))
    path = folder / f"{today:%Y%m%d}-{uuid.uuid4().hex[:8]}.jpg"
    image.save(path, "JPEG", quality=88)
    return path.relative_to(receipts_dir).as_posix()


def resolve_receipt_path(receipts_dir: Path, relative: str) -> Path:
    """Turn a stored relative path back into a file path, refusing anything outside receipts_dir."""
    path = (receipts_dir / relative).resolve()
    if not path.is_relative_to(receipts_dir.resolve()):
        raise ImageError("Invalid receipt path.")
    return path
