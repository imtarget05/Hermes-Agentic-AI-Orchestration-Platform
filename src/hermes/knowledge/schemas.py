"""Knowledge domain schemas — Team Knowledge Base (④) + Second Brain (⑤).

A single document model with a `scope` switch:
- scope="team"    → visible to the whole tenant (SOP, decisions, client history)
- scope="personal"→ visible only to `owner_user_id` (contracts, insurance, IDs)
Categories mirror the image briefs (decisions, contracts, insurance, finance,
notes …) plus general-purpose docs.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

DocCategory = Literal[
    "sop", "decision", "client_history", "document", "note",
    "contract", "insurance", "finance", "receipt", "ids", "pdf", "other",
]
DocScope = Literal["team", "personal"]


def now_iso() -> str:
    return date.today().isoformat()


def new_doc_id() -> str:
    return uuid.uuid4().hex[:16]


class KnowledgeDoc(BaseModel):
    doc_id: str = Field(default_factory=new_doc_id)
    tenant_id: str = "default"
    scope: DocScope = "team"
    category: DocCategory = "other"
    title: str = ""
    source_uri: str = ""
    content: str = ""
    owner_user_id: str = ""          # required when scope == "personal"
    shared_with: list[str] = Field(default_factory=list)  # personal docs can add viewers
    ingested_at: str = Field(default_factory=now_iso)
    ttl_days: int = 365

    def is_visible_to(self, user_id: str | None) -> bool:
        if self.scope == "team":
            return True
        if self.owner_user_id and self.owner_user_id == user_id:
            return True
        return bool(self.shared_with) and user_id in self.shared_with


class KnowledgeChunk(BaseModel):
    doc_id: str
    title: str
    source_uri: str
    text: str
    score: float = 0.0


class KnowledgeAnswer(BaseModel):
    query: str = ""
    scope: DocScope = "team"
    answer: str = ""                 # deterministic answer w/ [source=...] citations
    sources: list[KnowledgeChunk] = Field(default_factory=list)
    grounded: bool = True

    def to_text(self) -> str:
        lines = [self.answer] if self.answer else []
        if not self.grounded:
            lines = ["Không tìm thấy tài liệu khớp trong kho kiến thức."]
        lines += ["", "📚 Nguồn:"] if self.sources else []
        for c in self.sources:
            lines.append(f"- {c.title} [source={c.source_uri}]")
        return "\n".join(lines)