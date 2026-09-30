from contextlib import contextmanager

from fastapi.testclient import TestClient

from proptrack.auth import set_pin
from proptrack.config import Config
from proptrack.db import connect
from proptrack.web.app import create_app

TEST_PIN = "246810"


@contextmanager
def app_client(
    config: Config,
    *,
    app=None,
    pin: str | None = TEST_PIN,
    sign_in: bool = True,
    client_host: str = "127.0.0.1",
    raise_server_exceptions: bool = True,
):
    """A test client for the web app, with a PIN set and (by default) signed in."""
    app = app or create_app(config)
    if pin is not None:
        conn = connect(config.db_path)
        set_pin(conn, pin)
        conn.close()
    with TestClient(app, client=(client_host, 50000), raise_server_exceptions=raise_server_exceptions) as client:
        if pin is not None and sign_in:
            response = client.post("/login", data={"pin": pin}, follow_redirects=False)
            assert response.status_code == 303, response.text
        yield client
