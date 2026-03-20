import requests
from datetime import datetime, timezone
from backend.skills.base import BaseSkill
from backend import db


class HealthCheckSkill(BaseSkill):
    name = "health_check"

    def execute(self, agent) -> None:
        cfg = agent.config
        agent.log("debug", self.name, "Checking connection...")

        try:
            resp = agent.http_get("/api/v3/system/status")
            version = resp.get("version", "unknown")
            agent.log(
                "debug",
                self.name,
                f"Online — {cfg['type'].upper()} v{version}",
            )
            db.instances.update_status(
                cfg["id"],
                "online",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            )
            agent.state["connection_status"] = "online"

        except requests.exceptions.ConnectionError:
            agent.log("warn", self.name, f"Cannot connect to {cfg['url']}")
            db.instances.update_status(cfg["id"], "offline")
            agent.state["connection_status"] = "offline"

        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response else 0
            if status_code in (401, 403):
                agent.log("error", self.name, "Invalid API key")
                db.instances.update_status(cfg["id"], "error")
                agent.state["connection_status"] = "error"
            else:
                agent.log("warn", self.name, f"HTTP {status_code} from *arr API")
                db.instances.update_status(cfg["id"], "offline")
                agent.state["connection_status"] = "offline"

        except Exception as exc:
            agent.log("error", self.name, f"Unexpected error: {exc}")
            db.instances.update_status(cfg["id"], "error")
            agent.state["connection_status"] = "error"
