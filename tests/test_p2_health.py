import pytest
import requests

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.skills.health_check import HealthCheckSkill


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def make_instance(**fields):
    data = {
        "name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32,
        "seconds_between_actions": 0, "hours_after_release": 0, "rate_cap": 1000,
        "search_order": "random", "missing_mode": "episode", "missing_per_run": 5,
    }
    data.update(fields)
    return db.instances.create(data)


class StatusAgent(BaseAgent):
    def __init__(self, config, status):
        super().__init__(config)
        self.status = status

    def build_skills(self):
        return []

    def http_get(self, path, params=None):
        response = requests.Response()
        response.status_code = self.status
        raise requests.exceptions.HTTPError(f"{self.status} error", response=response)


def messages():
    return [r["message"] for r in db.activity.query(include_debug=True, limit=50)]


@pytest.mark.parametrize("status,expected,text", [
    (401, "error", "Invalid API key"),
    (403, "error", "Invalid API key"),
    (500, "offline", "HTTP 500"),
    (302, "offline", "redirects are not followed"),
])
def test_http_errors_keep_their_status_code(db_path, status, expected, text):
    inst = make_instance()
    agent = StatusAgent(db.instances.get_by_id(inst["id"]), status)
    HealthCheckSkill().execute(agent)
    assert agent.state["connection_status"] == expected
    assert db.instances.get_by_id(inst["id"])["connection_status"] == expected
    assert any(text in m for m in messages())
