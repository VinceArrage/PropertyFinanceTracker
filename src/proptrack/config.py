"""Loads settings from config.toml, with environment variable overrides."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
DEFAULT_TESSERACT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    tesseract_cmd: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tracker.db"

    @property
    def receipts_dir(self) -> Path:
        return self.data_dir / "receipts"


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path(os.environ.get("PROPTRACK_CONFIG", DEFAULT_CONFIG_PATH))
    raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    data_dir = (
        os.environ.get("PROPTRACK_DATA_DIR")
        or raw.get("paths", {}).get("data_dir")
        or Path.home() / "PropertyFinanceData"
    )
    tesseract = raw.get("ocr", {}).get("tesseract_cmd") or DEFAULT_TESSERACT
    return Config(data_dir=Path(data_dir).expanduser(), tesseract_cmd=Path(tesseract))
