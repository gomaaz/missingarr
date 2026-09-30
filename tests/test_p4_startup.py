import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.config import settings

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", str(tmp_path / "missingarr.db"))
    monkeypatch.setattr(settings, "auth_password", PASSWORD)
    monkeypatch.setattr(settings, "secret_key", "")
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(database, "_cached_secret_key", None)
    from backend import crypto, main
    crypto._reset_cache()
    main.app.middleware_stack = None
    with TestClient(main.app, base_url=f"http://{HOST}") as test_client:
        yield test_client
    crypto._reset_cache()
    main.app.middleware_stack = None


def login(client, remember=False, next_path="/"):
    data = {"username": "admin", "password": PASSWORD, "next": next_path}
    if remember:
        data["remember"] = "true"
    return client.post("/login", data=data, follow_redirects=False)


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
            "api_key": "SUPERSECRETKEY1234567890", "enabled": False}
    data.update(fields)
    return db.instances.create(data)


def test_importing_main_does_not_touch_any_database(tmp_path):
    target = tmp_path / "never.db"
    env = {**os.environ, "DATABASE_URL": str(target)}
    subprocess.run([sys.executable, "-c", "import backend.main"], cwd=REPO, env=env, check=True)
    assert not target.exists()


def test_login_works_with_the_lazily_read_session_key(client):
    assert login(client).status_code == 302
    assert client.get("/api/instances").status_code == 200
