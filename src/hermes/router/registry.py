"""Project → channel → thread routing registry (§6 of spec)."""
from __future__ import annotations

import json
from pathlib import Path
from pydantic import BaseModel


class Route(BaseModel):
    channel: str
    thread_id: int = 0
    description: str = ""


class RoutingRegistry:
    def __init__(self, path: str):
        self.path = path
        self.routes: dict[str, Route] = {}
        self.load()

    def load(self) -> None:
        p = Path(self.path)
        if not p.exists():
            self.routes = {"default": Route(channel="@hermes_default")}
            return
        data = json.loads(p.read_text())
        projs = data.get("projects", {})
        self.routes = {k: Route(**v) for k, v in projs.items()}
        if "default" not in self.routes:
            self.routes["default"] = Route(channel="@hermes_default")

    def resolve(self, project: str) -> Route:
        return self.routes.get(project, self.routes["default"])

    def projects(self) -> list[str]:
        return sorted(self.routes.keys())
