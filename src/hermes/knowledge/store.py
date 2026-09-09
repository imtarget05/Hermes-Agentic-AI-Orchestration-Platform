"""Knowledge store — SQLite + FTS5, multi-tenant with scope/owner filtering.

Separate from the task store on purpose: these are persisted documents, not
task lifecycle events. Kept dependency-free (stdlib sqlite3), matching the repo's
local-first posture.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .schemas import KnowledgeChunk, KnowledgeDoc

_DDL = """
CREATE TABLE IF NOT EXISTS kb_docs (
    doc_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    source_uri TEXT,
    content TEXT,
    owner_user_id TEXT,
    shared_with TEXT,
    ingested_at TEXT,
    ttl_days INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
    title, content, source_uri, content='kb_docs', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS kb_ai AFTER INSERT ON kb_docs BEGIN
    INSERT INTO kb_fts(rowid, title, content, source_uri)
    VALUES (new.rowid, new.title, new.content, new.source_uri);
END;
CREATE TRIGGER IF NOT EXISTS kb_ad AFTER DELETE ON kb_docs BEGIN
    INSERT INTO kb_fts(kb_fts, rowid, title, content, source_uri)
    VALUES ('delete', old.rowid, old.title, old.content, old.source_uri);
END;
CREATE TRIGGER IF NOT EXISTS kb_au AFTER UPDATE ON kb_docs BEGIN
    INSERT INTO kb_fts(kb_fts, rowid, title, content, source_uri)
    VALUES ('delete', old.rowid, old.title, old.content, old.source_uri);
    INSERT INTO kb_fts(rowid, title, content, source_uri)
    VALUES (new.rowid, new.title, new.content, new.source_uri);
END;
"""


def _fts_query(text: str) -> str:
    tokens = [t.strip().strip('"') for t in (text or "").split()]
    tokens = [t for t in tokens if t]
    return " OR ".join(f'"{t}"' for t in tokens) if tokens else ""


class KnowledgeStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30.0)
        con.row_factory = sqlite3.Row
        return con

    def _init(self) -> None:
        con = self._connect()
        con.executescript(_DDL)
        con.commit()
        con.close()

    def put(self, doc: KnowledgeDoc) -> str:
        con = self._connect()
        con.execute(
            "INSERT INTO kb_docs(doc_id, tenant_id, scope, category, title, "
            "source_uri, content, owner_user_id, shared_with, ingested_at, ttl_days) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(doc_id) DO UPDATE SET "
            "tenant_id=excluded.tenant_id, scope=excluded.scope, "
            "category=excluded.category, title=excluded.title, "
            "source_uri=excluded.source_uri, content=excluded.content, "
            "owner_user_id=excluded.owner_user_id, shared_with=excluded.shared_with, "
            "ingested_at=excluded.ingested_at, ttl_days=excluded.ttl_days",
            (doc.doc_id, doc.tenant_id, doc.scope, doc.category, doc.title,
             doc.source_uri, doc.content, doc.owner_user_id,
             _json(doc.shared_with), doc.ingested_at, doc.ttl_days),
        )
        con.commit()
        con.close()
        return doc.doc_id

    def get(self, doc_id: str) -> KnowledgeDoc | None:
        con = self._connect()
        row = con.execute("SELECT * FROM kb_docs WHERE doc_id=?", (doc_id,)).fetchone()
        con.close()
        return self._row_to_doc(row) if row else None

    def delete(self, doc_id: str) -> bool:
        con = self._connect()
        cur = con.execute("DELETE FROM kb_docs WHERE doc_id=?", (doc_id,))
        con.commit()
        con.close()
        return cur.rowcount > 0

    def search(self, query: str, *, tenant_id: str, scope: str,
               user_id: str | None,
               limit: int = 5) -> list[KnowledgeChunk]:
        """FTS5 search restricted by tenant + visibility scope.

        For `personal` scope we only return docs the requesting user owns or is
        explicitly shared with.
        """
        fts = _fts_query(query)
        if not fts:
            return []
        sql = (
            "SELECT d.doc_id, d.title, d.source_uri, d.content, "
            "d.owner_user_id, d.shared_with, bm25(kb_fts) AS score "
            "FROM kb_fts JOIN kb_docs d ON kb_fts.rowid = d.rowid "
            "WHERE kb_fts MATCH ? AND d.tenant_id = ? AND d.scope = ?"
            " ORDER BY score LIMIT ?"
        )
        params: list = [fts, tenant_id, scope, limit]
        con = self._connect()
        rows = con.execute(sql, params).fetchall()
        con.close()
        chunks = []
        for r in rows:
            if scope == "personal" and not _personal_visible(
                    r["owner_user_id"], r["shared_with"], user_id):
                continue
            chunks.append(self._chunk_from_row(r))
        return chunks[:limit]

    def recent(self, *, tenant_id: str, scope: str, user_id: str | None,
               limit: int = 20) -> list[KnowledgeDoc]:
        con = self._connect()
        sql = "SELECT * FROM kb_docs WHERE tenant_id=? AND scope=?"
        params: list = [tenant_id, scope]
        if scope == "personal":
            sql += " AND (owner_user_id=? OR shared_with LIKE ?)"
            params += [user_id or "", f"%:{user_id}:%"]
        sql += " ORDER BY ingested_at DESC LIMIT ?"
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
        con.close()
        return [self._row_to_doc(r) for r in rows]

    def _chunk_from_row(self, r) -> KnowledgeChunk:
        return KnowledgeChunk(
            doc_id=r["doc_id"], title=r["title"],
            source_uri=r["source_uri"] or "",
            text=(r["content"] or "")[:400], score=0.0)

    @staticmethod
    def _row_to_doc(row) -> KnowledgeDoc:
        d = dict(row)
        d["shared_with"] = _unjson(d.get("shared_with") or "")
        return KnowledgeDoc(**d)


def _json(items: list[str]) -> str:
    return ":" + ":".join(items) + ":" if items else ""


def _unjson(s: str | None) -> list[str]:
    s = s or ""
    return [x for x in s.strip(":").split(":") if x] if s else []


def _personal_visible(owner: str | None, shared: str | None, user_id: str | None) -> bool:
    if owner and owner == user_id:
        return True
    if shared and user_id:
        return user_id in _unjson(shared)
    return False