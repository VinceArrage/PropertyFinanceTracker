from pathlib import Path

import pytest
from typer.testing import CliRunner

from proptrack.auth import LoginThrottle, PinError, check_pin, pin_is_set, pin_version, session_secret, set_pin
from proptrack.cli import app as cli_app
from proptrack.config import Config
from proptrack.db import connect, init_db
from proptrack.network import qr_svg
from tests.helpers import TEST_PIN, app_client

PHONE = "192.168.1.50"


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=tmp_path / "data", tesseract_cmd=Path("missing.exe"), host="0.0.0.0")


# ---- PIN storage -----------------------------------------------------------------


def test_pin_is_hashed_and_checked(conn):
    assert not pin_is_set(conn)
    set_pin(conn, "123456")
    stored = conn.execute("SELECT value FROM settings WHERE key = 'pin_hash'").fetchone()[0]
    assert "123456" not in stored
    assert check_pin(conn, "123456")
    assert not check_pin(conn, "654321")
    assert not check_pin(conn, "")


@pytest.mark.parametrize("pin", ["123", "1234567890123", "12a456", ""])
def test_pin_rules(conn, pin):
    with pytest.raises(PinError, match="4 to 12 digits"):
        set_pin(conn, pin)


def test_changing_pin_bumps_version(conn):
    set_pin(conn, "1111")
    first = pin_version(conn)
    set_pin(conn, "2222")
    assert pin_version(conn) == first + 1
    assert not check_pin(conn, "1111")


def test_session_secret_is_stable(conn):
    assert session_secret(conn) == session_secret(conn)
    assert len(session_secret(conn)) == 64


def test_throttle_locks_and_backs_off():
    now = [0.0]
    throttle = LoginThrottle(clock=lambda: now[0])
    for _ in range(4):
        throttle.failed("phone")
    assert throttle.seconds_locked("phone") == 0
    throttle.failed("phone")  # 5th wrong PIN
    assert throttle.seconds_locked("phone") == 60
    assert throttle.seconds_locked("laptop") == 0  # other devices unaffected
    now[0] = 61
    throttle.failed("phone")  # 6th: lock doubles
    assert throttle.seconds_locked("phone") == 120
    throttle.succeeded("phone")
    assert throttle.seconds_locked("phone") == 0


# ---- Web sign-in -------------------------------------------------------------------


def test_first_run_sends_to_setup(config):
    with app_client(config, pin=None) as client:
        response = client.get("/properties", follow_redirects=False)
        assert response.headers["location"] == "/setup"


def test_setup_only_from_this_pc(config):
    with app_client(config, pin=None, client_host=PHONE) as phone:
        page = phone.get("/setup")
        assert "can only be set up on the PC" in page.text
        response = phone.post("/setup", data={"pin": "123456", "confirm": "123456"})
        assert response.status_code == 403
    conn = connect(config.db_path)
    assert not pin_is_set(conn)


def test_setup_from_pc_signs_in(config):
    with app_client(config, pin=None) as pc:
        mismatch = pc.post("/setup", data={"pin": "123456", "confirm": "123457"})
        assert "don&#39;t match" in mismatch.text
        response = pc.post("/setup", data={"pin": "123456", "confirm": "123456"})
        assert "PIN saved" in response.text
        assert pc.get("/properties").status_code == 200
        assert pc.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_pages_need_sign_in_but_static_files_dont(config):
    with app_client(config, sign_in=False, client_host=PHONE) as phone:
        response = phone.get("/receipts/new?property=2", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/receipts/new%3Fproperty%3D2"
        assert phone.get("/static/style.css").status_code == 200
        assert phone.get("/receipts/1/image", follow_redirects=False).status_code == 303


def test_sign_in_returns_to_page(config):
    with app_client(config, sign_in=False, client_host=PHONE) as phone:
        response = phone.post("/login", data={"pin": TEST_PIN, "next": "/categories"}, follow_redirects=False)
        assert response.headers["location"] == "/categories"
        assert phone.get("/categories").status_code == 200


@pytest.mark.parametrize("target", ["https://evil.example", "//evil.example", "/\\evil.example"])
def test_sign_in_never_redirects_off_site(config, target):
    with app_client(config, sign_in=False) as client:
        response = client.post("/login", data={"pin": TEST_PIN, "next": target}, follow_redirects=False)
        assert response.headers["location"] == "/"


def test_wrong_pin_then_lockout(config):
    with app_client(config, sign_in=False, client_host=PHONE) as phone:
        for _ in range(4):
            response = phone.post("/login", data={"pin": "000000"})
            assert response.status_code == 401
            assert "Wrong PIN." in response.text
        fifth = phone.post("/login", data={"pin": "000000"})
        assert "Try again in 1 minute" in fifth.text
        # Even the right PIN is refused while locked out.
        locked = phone.post("/login", data={"pin": TEST_PIN})
        assert locked.status_code == 429
        assert phone.get("/properties", follow_redirects=False).status_code == 303


def test_sign_out(config):
    with app_client(config) as client:
        client.post("/logout")
        assert client.get("/properties", follow_redirects=False).status_code == 303


def test_changing_pin_signs_out_other_devices(config):
    with app_client(config) as pc, app_client(config, pin=None, client_host=PHONE) as phone:
        phone.post("/login", data={"pin": TEST_PIN})
        assert phone.get("/properties").status_code == 200

        wrong = pc.post("/settings/pin", data={"current": "999999", "pin": "135790", "confirm": "135790"})
        assert "current PIN is wrong" in wrong.text
        changed = pc.post("/settings/pin", data={"current": TEST_PIN, "pin": "135790", "confirm": "135790"})
        assert "PIN changed" in changed.text

        assert pc.get("/properties").status_code == 200  # this device stays signed in
        assert phone.get("/properties", follow_redirects=False).status_code == 303  # others signed out


def test_cookie_is_http_only_and_same_site(config):
    with app_client(config, sign_in=False) as client:
        response = client.post("/login", data={"pin": TEST_PIN}, follow_redirects=False)
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie


def test_settings_shows_phone_address_and_qr(config, monkeypatch):
    monkeypatch.setattr("proptrack.web.app.phone_urls", lambda port: [f"http://192.168.1.20:{port}"])
    with app_client(config) as client:
        page = client.get("/settings").text
        assert "http://192.168.1.20:8000" in page
        assert "<svg" in page


def test_settings_when_phone_access_off(tmp_path):
    config = Config(data_dir=tmp_path / "data", tesseract_cmd=Path("missing.exe"), host="127.0.0.1")
    with app_client(config) as client:
        assert "Phone access is off" in client.get("/settings").text


def test_qr_code_is_svg():
    svg = qr_svg("http://192.168.1.20:8000")
    assert svg.startswith("<svg")


def test_set_pin_command(data_dir):
    runner = CliRunner()
    result = runner.invoke(cli_app, ["set-pin"], input="4321\n4321\n")
    assert result.exit_code == 0, result.output
    conn = connect(data_dir / "tracker.db")
    init_db(conn)
    assert check_pin(conn, "4321")

    mismatch = runner.invoke(cli_app, ["set-pin"], input="4321\n1234\n")
    assert mismatch.exit_code == 1
    assert "don't match" in mismatch.output
