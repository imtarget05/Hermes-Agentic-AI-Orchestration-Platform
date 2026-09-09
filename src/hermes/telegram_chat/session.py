"""Per-chat session store (SQLite, local-first).

State machine: idle → awaiting_spec → running → awaiting_approval → idle.
History: last N turns for chitchat context. Survives uvicorn --reload.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

_DDL = """
CREATE TABLE IF NOT EXISTS telegram_sessions (
    chat_id TEXT PRIMARY KEY,
    username TEXT DEFAULT '',
    state TEXT DEFAULT 'idle',
    pending_text TEXT DEFAULT '',
    pending_spec TEXT DEFAULT '',
    pending_task_id TEXT DEFAULT '',
    pending_approval_id TEXT DEFAULT '',
    history_json TEXT DEFAULT '[]',
    lang TEXT DEFAULT 'vi',
    updated_at TEXT DEFAULT ''
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatSessionStore:
    def __init__(self, db_path: str = "./telegram_sessions.db"):
        self.db_path = db_path or "./telegram_sessions.db"
        con = sqlite3.connect(self.db_path)
        con.executescript(_DDL)
        con.commit()
        con.close()

    def _exec(self, sql: str, params: tuple = (), fetch: str = ""):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(sql, params)
        out = None
        if fetch == "one":
            out = cur.fetchone()
        elif fetch == "all":
            out = cur.fetchall()
        con.commit()
        con.close()
        return out

    def get(self, chat_id: str | int) -> dict:
        cid = str(chat_id)
        row = self._exec(
            "SELECT * FROM telegram_sessions WHERE chat_id=?", (cid,), fetch="one")
        if row:
            d = dict(row)
            try:
                d["history"] = json.loads(d.get("history_json") or "[]")
            except Exception:
                d["history"] = []
            # Ensure lang defaults to 'vi'
            if "lang" not in d or not d["lang"]:
                d["lang"] = "vi"
            return d
        return {
            "chat_id": cid, "username": "", "state": "idle",
            "pending_text": "", "pending_spec": "",
            "pending_task_id": "", "pending_approval_id": "",
            "history": [], "lang": "vi", "updated_at": "",
        }

    def save(self, chat_id: str | int, **fields) -> dict:
        cid = str(chat_id)
        cur = self.get(cid)
        cur.update(fields)
        self._exec(
                "INSERT INTO telegram_sessions "
                "(chat_id, username, state, pending_text, pending_spec, "
                " pending_task_id, pending_approval_id, history_json, lang, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET "
                "username=excluded.username, state=excluded.state, "
                "pending_text=excluded.pending_text, pending_spec=excluded.pending_spec, "
                "pending_task_id=excluded.pending_task_id, "
                "pending_approval_id=excluded.pending_approval_id, "
                "history_json=excluded.history_json, lang=excluded.lang, updated_at=excluded.updated_at",
                (cid, cur.get("username", ""), cur.get("state", "idle"),
                 cur.get("pending_text", ""), cur.get("pending_spec", ""),
                 cur.get("pending_task_id", ""), cur.get("pending_approval_id", ""),
                 json.dumps(cur.get("history", [])), cur.get("lang", "vi"), _now()))
        return self.get(cid)

    def append_history(self, chat_id: str | int, role: str, text: str,
                       max_history: int = 20) -> dict:
        cur = self.get(chat_id)
        hist = cur.get("history", []) + [{"role": role, "text": text[:2000]}]
        hist = hist[-max(2, max_history * 2):]
        return self.save(chat_id, history=hist)

    def clear(self, chat_id: str | int) -> dict:
        return self.save(chat_id, state="idle", pending_text="",
                         pending_spec="", pending_task_id="",
                         pending_approval_id="")
