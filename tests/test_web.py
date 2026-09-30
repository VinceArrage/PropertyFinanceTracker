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


def add_duplex(client):
    return add(
        client, name="Test Duplex", address="1 Fake St", is_rental="on", unit_count="2",
        unit_id=["", ""], unit_label=["1A", "1B"], unit_rent=["1,800", "1200.50"],
        unit_tenant=["Tenant A", ""], unit_lease_start=["2026-01-01", ""], unit_lease_end=["2026-12-31", ""],
    )


def test_home_redirects_to_properties(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/properties"


def test_empty_list(client):
    response = client.get("/properties")
    assert response.status_code == 200
    assert "No properties yet" in response.text


def test_new_form_has_one_unit_block(client):
    page = client.get("/properties/new").text
    assert page.count('<fieldset class="unit">') == 2  # one block + the hidden template for new ones
    assert 'name="unit_count"' in page
    assert "Apartment / unit no." in page


def test_add_rental_with_units(client):
    response = add_duplex(client)
    assert response.status_code == 200
    assert "Property added." in response.text
    assert "1A" in response.text and "1B" in response.text
    assert "$3,000.50" in response.text  # total rent
    assert "Tenant A" in response.text
    assert "Management fees" in response.text  # rental categories

    listing = client.get("/properties").text
    assert "2 units" in listing
    assert "$3,000.50" in listing


def test_unit_count_pads_and_trims_rows(client):
    # Three units asked for, only one row filled: the others are created with default numbers.
    add(client, name="Triplex", is_rental="on", unit_count="3", unit_label="A", unit_rent="500")
    page = client.get("/properties/1").text
    assert "<td>A</td>" in page and "<td>2</td>" in page and "<td>3</td>" in page

    # One unit asked for but two rows sent: the extra row is ignored.
    add(client, name="House", is_rental="on", unit_count="1", unit_label=["Main", "Extra"])
    page = client.get("/properties/2").text
    assert "<td>Main</td>" in page
    assert "Extra" not in page


@pytest.mark.parametrize("count", ["0", "51", "abc"])
def test_bad_unit_count(client, count):
    response = add(client, name="X", is_rental="on", unit_count=count)
    assert response.status_code == 400
    assert "Number of units" in response.text


def test_unit_errors_name_the_unit(client):
    response = add(client, name="X", is_rental="on", unit_count="1", unit_label="2B", unit_rent="lots")
    assert response.status_code == 400
    assert "Unit 2B" in response.text
    assert 'value="2B"' in response.text  # form keeps what was typed


def test_add_personal_ignores_hidden_unit_fields(client):
    # The unit fields are hidden (not removed) when the switch is off, so they may still be posted.
    response = add(client, name="Home", unit_count="3", unit_label="1", unit_rent="999")
    assert response.status_code == 200
    assert "Personal" in response.text
    assert "$999" not in response.text
    assert "HOA fees" in response.text


def test_duplicate_name(client):
    add(client, name="Home")
    response = add(client, name="home")
    assert response.status_code == 400
    assert "already exists" in response.text


def test_edit_form_prefilled_with_units(client):
    add_duplex(client)
    page = client.get("/properties/1/edit").text
    assert 'value="1A"' in page and 'value="1B"' in page
    assert 'value="1800.00"' in page
    assert 'name="unit_count"' in page and 'value="2"' in page


def unit_ids(client):
    page = client.get("/properties/1/edit").text
    return [part.split('"')[0] for part in page.split('name="unit_id" value="')[1:] if part.split('"')[0]]


def test_edit_unit_rent(client):
    add_duplex(client)
    ids = unit_ids(client)
    response = client.post(
        "/properties/1/edit",
        data={"name": "Test Duplex", "is_rental": "on", "unit_count": "2", "unit_id": ids,
              "unit_label": ["1A", "1B"], "unit_rent": ["1900", "1200.50"]},
    )
    assert "Changes saved." in response.text
    assert "$3,100.50" in response.text


def test_removing_a_unit_needs_confirmation(client):
    add_duplex(client)
    ids = unit_ids(client)
    data = {"name": "Test Duplex", "is_rental": "on", "unit_count": "1", "unit_id": ids,
            "unit_label": ["1A", "1B"], "unit_rent": ["1800", "1200.50"]}

    first = client.post("/properties/1/edit", data=data)
    assert "Please confirm" in first.text
    assert "Unit(s) 1B" in first.text
    assert "1B" in client.get("/properties/1").text  # nothing saved yet

    confirmed = client.post("/properties/1/edit", data=data | {"confirmed": "1"})
    assert "Changes saved." in confirmed.text
    assert "<td>1B</td>" not in confirmed.text


def test_switch_to_personal_requires_confirmation(client):
    add_duplex(client)
    first = client.post("/properties/1/edit", data={"name": "Test Duplex"})
    assert "Please confirm" in first.text
    assert "2 unit(s)" in first.text
    assert "Rental" in client.get("/properties/1").text

    confirmed = client.post("/properties/1/edit", data={"name": "Test Duplex", "confirmed": "1"})
    assert "Changes saved." in confirmed.text
    assert "Personal" in confirmed.text
    assert "1A" not in confirmed.text


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
