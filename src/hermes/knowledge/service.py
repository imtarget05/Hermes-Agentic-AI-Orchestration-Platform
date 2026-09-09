"""KnowledgeService — ingest + deterministic grounded query answers.

Reuses the IngestionPipeline spirit (raw content → searchable text) while keeping
retrieval entirely local via `KnowledgeStore` (FTS5). Answers are real excerpts
with `[source=...]` citations — never fabricated, mirroring the repo's
grounded-price policy.
"""
from __future__ import annotations

from pathlib import Path

from .schemas import (
    DocCategory,
    DocScope,
    KnowledgeAnswer,
    KnowledgeChunk,
    KnowledgeDoc,
)
from .store import KnowledgeStore

_ANSWERING_CAP = 1200  # max characters of answer before truncation


class KnowledgeService:
    """Multi-tenant KB for both team and personal scopes."""

    def __init__(self, db_path: str):
        self.store = KnowledgeStore(db_path)

    # ---- ingest ---------------------------------------------------------

    def ingest(self, *, content: str, title: str = "", scope: DocScope = "team",
               tenant_id: str = "default", user_id: str = "",
               category: DocCategory = "other",
               source_uri: str = "") -> str:
        """Persist a document and return its doc_id.

        `user_id` must be provided for personal-scope docs (owner).
        """
        if scope == "personal" and not user_id:
            raise ValueError("personal-scope documents require owner user_id")
        doc = KnowledgeDoc(
            tenant_id=tenant_id, scope=scope, category=category,
            title=title or "unnamed", source_uri=source_uri or f"kb/{scope}/{title or 'doc'}",
            content=content, owner_user_id=user_id if scope == "personal" else "",
            ttl_days=365,
        )
        return self.store.put(doc)

    # ---- query ----------------------------------------------------------

    def query(self, *, text: str, scope: DocScope = "team",
              tenant_id: str = "default", user_id: str = "",
              limit: int = 5) -> KnowledgeAnswer:
        chunks = self.store.search(
            text, tenant_id=tenant_id, scope=scope, user_id=user_id or None,
            limit=limit)
        if not chunks:
            return KnowledgeAnswer(query=text, scope=scope, grounded=False)
        answer = self._compose_answer(text, chunks)
        return KnowledgeAnswer(query=text, scope=scope, answer=answer,
                               sources=chunks, grounded=True)

    def _compose_answer(self, query: str, chunks: list[KnowledgeChunk]) -> str:
        lines: list[str] = []
        for c in chunks:
            snippet = (c.text or "").strip()
            if not snippet:
                continue
            lines.append(f"- {snippet} [source={c.source_uri}]")
            if sum(len(l) for l in lines) >= _ANSWERING_CAP:
                break
        lines.append("")
        lines.append("(Đây là trích đoạn gốc từ tài liệu; để xem đầy đủ hãy hỏi với từ khóa cụ thể hơn.)")
        return "\n".join(lines)

    def recent(self, *, scope: DocScope, tenant_id: str = "default",
               user_id: str = "", limit: int = 10) -> list[KnowledgeDoc]:
        return self.store.recent(tenant_id=tenant_id, scope=scope,
                                 user_id=user_id or None, limit=limit)

    def states(self) -> dict:
        return {
            "db": self.store.db_path,
            "categories": list(DocCategory.__args__),
            "scopes": ["team", "personal"],
        }


def scope_id(scope: str) -> str:
    return "team" if scope == "team" else "personal"


# ---- text extraction from user-attached files (PDFs, .txt) --------------

def extract_text_from_file(path: str) -> str:
    """Extract text from a local .txt/.pdf path (no network)."""
    p = Path(path)
    if p.suffix.lower() == ".txt":
        return p.read_text(encoding="utf-8", errors="ignore")
    if p.suffix.lower() == ".pdf":
        return _extract_pdf_text(path)
    return ""


def _extract_pdf_text(path: str) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:  # noqa: BLE001
        return ""