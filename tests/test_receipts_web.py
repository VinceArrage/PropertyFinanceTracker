"""Scan -> review -> save, through the web app, with generated receipt photos."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proptrack.config import DEFAULT_TESSERACT, Config
from proptrack.web.app import create_app
from tests.fake_receipts import make_receipt_photo

needs_tesseract = pytest.mark.skipif(not DEFAULT_TESSERACT.exists(), reason="Tesseract not installed")


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=tmp_path / "data", tesseract_cmd=DEFAULT_TESSERACT)


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as test_client:
        test_client.post("/properties/new", data={"name": "Duplex", "is_rental": "on"})
        test_client.post("/properties/new", data={"name": "Home"})
        yield test_client


def upload(client, photo: bytes, property_id="1", filename="receipt.jpg"):
    return client.post(
        "/receipts/new", data={"property_id": property_id}, files={"photo": (filename, photo, "image/jpeg")}
    )


def category_option_id(page: str, name: str, group: str) -> str:
    section = page.split(f'label="{group}"')[1].split("</optgroup>")[0]
    return re.search(rf'<option value="(\d+)"[^>]*>{re.escape(name)}</option>', section).group(1)


def test_receipts_page_empty(client):
    assert "No receipts yet" in client.get("/receipts").text


def test_scan_page_remembers_last_property(client):
    page = client.get("/receipts/new?property=2").text
    assert '<option value="2" selected>' in page


@needs_tesseract
def test_scan_review_and_save(client, config):
    review = upload(client, make_receipt_photo())
    assert review.status_code == 200
    assert "Check and save" in review.text
    assert 'value="Home Depot"' in review.text
    assert 'value="2026-09-28"' in review.text
    assert 'value="59.22"' in review.text
    assert client.cookies.get("last_property") == "1"

    # The suggested category for a hardware store at a rental is Repairs.
    repairs = category_option_id(review.text, "Repairs", "Rental categories")
    assert f'<option value="{repairs}" selected>' in review.text

    saved = client.post(
        "/receipts/1",
        data={
            "property_id": "1", "vendor": "Home Depot", "date": "2026-09-28", "total": "59.22", "tax": "4.39",
            "category_id": repairs, "item_description": ["2X4 STUD 8FT", ""], "item_quantity": ["12", ""],
            "item_amount": ["29.88", ""],
        },
    )
    assert "Expense saved." in saved.text
    assert "Saved" in saved.text

    property_page = client.get("/properties/1").text
    assert "Home Depot" in property_page
    assert "$59.22" in property_page

    photos = list((config.receipts_dir).rglob("*.jpg"))
    assert len(photos) == 1

    # Re-opening shows the saved expense for editing; deleting removes photo and expense.
    assert "Edit expense" in client.get("/receipts/1").text
    client.post("/receipts/1/delete")
    assert not list(config.receipts_dir.rglob("*.jpg"))
    assert "No expenses yet" in client.get("/properties/1").text


def test_png_upload_without_tesseract_still_saves_photo(tmp_path):
    config = Config(data_dir=tmp_path / "data", tesseract_cmd=tmp_path / "missing.exe")
    with TestClient(create_app(config)) as client:
        client.post("/properties/new", data={"name": "Home"})
        review = upload(client, make_receipt_photo(fmt="PNG"), filename="r.png")
        assert review.status_code == 200
        assert "couldn&#39;t be read automatically" in review.text
        assert client.get("/receipts/1/image").headers["content-type"] == "image/jpeg"


def test_rejects_non_image(client):
    response = upload(client, b"definitely not a photo")
    assert response.status_code == 400
    assert "isn&#39;t a photo" in response.text


def test_requires_property(client):
    response = upload(client, make_receipt_photo(), property_id="")
    assert response.status_code == 400
    assert "Pick which property" in response.text


def test_save_rejects_mismatched_category(tmp_path):
    config = Config(data_dir=tmp_path / "data", tesseract_cmd=tmp_path / "missing.exe")
    with TestClient(create_app(config)) as client:
        client.post("/properties/new", data={"name": "Home"})
        review = upload(client, make_receipt_photo())
        repairs = category_option_id(review.text, "Repairs", "Rental categories")
        response = client.post(
            "/receipts/1", data={"property_id": "1", "date": "2026-09-28", "total": "10", "category_id": repairs}
        )
        assert response.status_code == 400
        assert "for rental properties" in response.text


def test_save_rejects_bad_item(tmp_path):
    config = Config(data_dir=tmp_path / "data", tesseract_cmd=tmp_path / "missing.exe")
    with TestClient(create_app(config)) as client:
        client.post("/properties/new", data={"name": "Home"})
        upload(client, make_receipt_photo())
        response = client.post(
            "/receipts/1",
            data={"property_id": "1", "date": "2026-09-28", "total": "10", "item_description": "Nails", "item_amount": "abc"},
        )
        assert response.status_code == 400
        assert "Item 1" in response.text
        assert 'value="Nails"' in response.text  # the form keeps what was typed


def test_unknown_receipt_is_404(client):
    assert client.get("/receipts/99").status_code == 404
