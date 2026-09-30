import pytest

from proptrack.db import connect, init_db


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the CLI at a throwaway data folder and ignore the real config.toml."""
    monkeypatch.setenv("PROPTRACK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROPTRACK_CONFIG", str(tmp_path / "missing.toml"))
    return tmp_path / "data"
