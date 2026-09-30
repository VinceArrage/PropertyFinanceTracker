from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proptrack.config import Config
from proptrack.web.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(Config(data_dir=tmp_path / "data", tesseract_cmd=Path("missing.exe")))
    with TestClient(app) as test_client:
        yield test_client


def add(client, **form):
    return client.post("/properties/new", data=form)


def test_home_redirects_to_properties(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/properties"


def test_empty_list(client):
    response = client.get("/properties")
    assert response.status_code == 200
    assert "No properties yet" in response.text


def test_add_rental(client):
    response = add(
        client, name="Test Duplex", address="1 Fake St", is_rental="on",
        monthly_rent="1,800", tenant_name="Tenant A", lease_start="2026-01-01", lease_end="2026-12-31",
    )
    assert response.status_code == 200
    assert "Property added." in response.text
    assert "$1,800.00" in response.text
    assert "Management fees" in response.text  # rental categories

    listing = client.get("/properties").text
    assert "Test Duplex" in listing
    assert "Rental" in listing


def test_add_personal_ignores_hidden_rental_fields(client):
    # The rental fields are hidden (not removed) when the switch is off, so they may still be posted.
    response = add(client, name="Home", monthly_rent="999", tenant_name="Nobody")
    assert response.status_code == 200
    assert "Personal" in response.text
    assert "$999" not in response.text
    assert "HOA fees" in response.text


def test_add_validation_error_keeps_input(client):
    response = add(client, name="Duplex", address="1 Fake St", is_rental="on", monthly_rent="lots")
    assert response.status_code == 400
    assert "Not a valid amount" in response.text
    assert 'value="1 Fake St"' in response.text


def test_duplicate_name(client):
    add(client, name="Home")
    response = add(client, name="home")
    assert response.status_code == 400
    assert "already exists" in response.text


def test_edit(client):
    add(client, name="Duplex", is_rental="on", monthly_rent="1000")
    response = client.post("/properties/1/edit", data={"name": "Duplex", "is_rental": "on", "monthly_rent": "1250.50"})
    assert "Changes saved." in response.text
    assert "$1,250.50" in response.text


def test_edit_form_prefilled(client):
    add(client, name="Duplex", is_rental="on", monthly_rent="1000")
    response = client.get("/properties/1/edit")
    assert 'value="1000.00"' in response.text
    assert "checked" in response.text


def test_switch_to_personal_requires_confirmation(client):
    add(client, name="Duplex", is_rental="on", monthly_rent="1000")

    first = client.post("/properties/1/edit", data={"name": "Duplex"})
    assert "Please confirm" in first.text
    assert "will be cleared" in first.text
    assert "Rental" in client.get("/properties/1").text  # nothing saved yet

    confirmed = client.post("/properties/1/edit", data={"name": "Duplex", "confirm_switch": "1"})
    assert "Changes saved." in confirmed.text
    assert "Personal" in confirmed.text
    assert "$1,000" not in confirmed.text


def test_delete(client):
    add(client, name="Mistake")
    assert "Delete property" in client.get("/properties/1/delete").text
    response = client.post("/properties/1/delete")
    assert "Property deleted." in response.text
    assert "No properties yet" in response.text


def test_unknown_property_is_404(client):
    response = client.get("/properties/42")
    assert response.status_code == 404
    assert "Back to properties" in response.text


def test_categories_page(client):
    response = client.get("/categories")
    assert response.status_code == 200
    assert "Schedule E" in response.text
    assert "Depreciated" in response.text


def test_text_is_escaped(client):
    response = add(client, name="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
