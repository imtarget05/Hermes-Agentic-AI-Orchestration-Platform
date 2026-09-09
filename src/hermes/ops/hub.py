"""OpsHub — aggregate attention items across connected business sources (②)."""
from __future__ import annotations

import uuid

from .connectors import build_connector
from .schemas import OpsAttentionItem, OpsReport, OpsSource, sort_by_severity


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class OpsHub:
    """Registers OpsSources and aggregates their attention items.

    Sources live in-memory for this phase (offline-first); a persisted version
    can mirror `KnowledgeStore` later.
    """

    def __init__(self, sources: list[OpsSource] | None = None):
        self._sources: dict[str, OpsSource] = {}
        for s in sources or []:
            if not s.source_id:
                s.source_id = _new_id()
            self._sources[s.source_id] = s

    def add_source(self, *, kind: str, tenant_id: str = "default",
                   name: str = "", enabled: bool = True,
                   config: dict | None = None) -> OpsSource:
        src = OpsSource(source_id=_new_id(), tenant_id=tenant_id,
                        kind=kind, name=name, enabled=enabled,
                        config=config or {})
        self._sources[src.source_id] = src
        return src

    def remove_source(self, source_id: str) -> bool:
        return self._sources.pop(source_id, None) is not None

    def list_sources(self, tenant_id: str = "default") -> list[OpsSource]:
        return [s for s in self._sources.values() if s.tenant_id == tenant_id]

    def collect_attention(self, tenant_id: str = "default") -> OpsReport:
        items: list[OpsAttentionItem] = []
        for src in self.list_sources(tenant_id):
            if not src.enabled:
                continue
            try:
                conn = build_connector(src.kind, source=src)
                items.extend(conn.collect(src))
            except Exception:  # noqa: BLE001 (one bad source must not kill the hub)
                continue
        items = sort_by_severity(items)
        return OpsReport(tenant_id=tenant_id, items=items)

    def sources_json(self, tenant_id: str = "default") -> list[dict]:
        return [s.model_dump() for s in self.list_sources(tenant_id)]