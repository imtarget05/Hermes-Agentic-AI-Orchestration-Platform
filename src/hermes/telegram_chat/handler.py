"""1:1 chat handler — webhook (tunnel) local-first, cloud chỉ Bot API + LLM."""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from .auth import (
    check_permission,
    get_user_from_telegram,
    is_allowed,
)
from .intent import HELP_TEXT, classify, parse_approval_text, parse_task_id
from .service import (
    chitchat_reply,
    format_recommendation,
    format_task_detail,
    format_task_list,
    price_question_reply,
)
from .session import ChatSessionStore

_RATE_LIMIT = 30  # messages / minute / chat
_RATE_WINDOW = 60.0


def _safe_name(name: str) -> str:
    base = (name or "quote.pdf").split("/")[-1].split("\\")[-1]
    base = re.sub(r"[^A-Za-z0-9._\-À-ỹà-ỹ ]", "_", base).strip() or "quote.pdf"
    return base[:120]


class ChatHandler:
    def __init__(self, bot, sessions: ChatSessionStore | None = None,
                 session_db: str = "", max_history: int = 20):
        self.bot = bot
        if sessions is not None:
            self.sessions = sessions
        else:
            from ..config import settings as _s
            self.sessions = ChatSessionStore(session_db or _s.telegram_session_db)
        self.max_history = max_history or 20
        self._rate: dict[str, list[float]] = {}

    # -- low-level ------------------------------------------------------
    async def _send(self, chat_id, text: str, reply_markup=None) -> None:
        from ..messaging import chunk_text
        for chunk in chunk_text(text or ""):
            await self.bot.send_message(chat_id=chat_id, text=chunk,
                                        reply_markup=reply_markup)

    def _limited(self, chat_id: str) -> bool:
        now = time.time()
        hits = [t for t in self._rate.get(chat_id, []) if now - t < _RATE_WINDOW]
        hits.append(now)
        self._rate[chat_id] = hits[-_RATE_LIMIT:]
        return len(hits) > _RATE_LIMIT

    def _auth(self, user_id, username) -> tuple[bool, list[str]]:
        from ..config import settings as _s
        allowed = _s.allowed_users
        return is_allowed(user_id, username, allowed), allowed

    def _chat_quotes_path(self, chat_id: str) -> Path:
        from ..config import settings as _s
        base = Path(_s.hermes_sandbox_dir).resolve()
        d = base / f"telegram_{chat_id}"
        d.mkdir(parents=True, exist_ok=True)
        return d / "quotes.json"

    def _load_chat_quotes(self, chat_id: str) -> list[dict] | None:
        p = self._chat_quotes_path(chat_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
            return data if isinstance(data, list) and data else None
        except Exception:
            return None

    # -- entry points ---------------------------------------------------
    async def handle_message(self, chat_id, user_id, username, text: str) -> None:
        cid = str(chat_id)
        ok, _ = self._auth(user_id, username)
        if not ok:
            await self._send(cid, "⛔ Bạn chưa được cấp quyền chat với Hermes.")
            return
        if self._limited(cid):
            await self._send(cid, "⏳ Bạn nhắn quá nhanh, thử lại sau 1 phút.")
            return
        sess = self.sessions.save(cid, username=str(username or user_id or ""))
        self.sessions.append_history(cid, "user", text or "", self.max_history)
        intent = classify(text or "", sess.get("state", "idle"))

        if intent == "help":
            self.sessions.save(cid, state="idle")
            await self._send(cid, HELP_TEXT)
            self.sessions.append_history(cid, "assistant", HELP_TEXT, self.max_history)
        elif intent == "new":
            self.sessions.clear(cid)
            await self._send(cid, "🆕 Hội thoại mới. Gửi `mua 50 laptop` để bắt đầu.")
        elif intent == "inbox_list":
            await self._reply_inbox(cid, text or "")
        elif intent == "inbox_detail":
            await self._reply_task_detail(cid, parse_task_id(text or ""))
        elif intent == "approval_text":
            approved, rid = parse_approval_text(text or "")
            await self._resolve_approval_dm(cid, rid or "", bool(approved),
                                            username, user_id)
        elif intent == "price_question":
            await self._reply_price_question(cid)
        elif intent == "procurement_followup":
            spec = (text or "").strip()
            pending = sess.get("pending_text", "")
            self.sessions.save(cid, pending_spec=spec)
            await self._start_procurement(cid, pending or "mua 50 laptop",
                                          spec, username, user_id)
        elif intent == "procurement":
            t = (text or "").strip()
            if len(t) < 20 and not sess.get("pending_text"):
                self.sessions.save(cid, state="awaiting_spec", pending_text=t)
                await self._send(
                    cid, "📝 Bạn cần mua gì cụ thể? (VD: 50 laptop RAM 16GB, "
                         "bảo hành 3 năm). Nhắn spec để mình chạy DAG.")
            else:
                await self._start_procurement(cid, t, "", username, user_id)
        else:
            sess_now = self.sessions.get(cid)
            reply = await asyncio.to_thread(
                chitchat_reply, text or "", sess_now.get("history", []))
            await self._send(cid, reply)
            self.sessions.append_history(cid, "assistant", reply, self.max_history)

    async def handle_document(self, chat_id, user_id, username,
                              file_id: str, file_name: str,
                              caption: str = "") -> None:
        cid = str(chat_id)
        ok, _ = self._auth(user_id, username)
        if not ok:
            await self._send(cid, "⛔ Bạn chưa được cấp quyền chat với Hermes.")
            return
        from ..config import settings as _s
        try:
            tg_file = await self.bot.get_file(file_id)
            safe = _safe_name(file_name)
            dest_dir = Path(_s.hermes_sandbox_dir).resolve() / f"telegram_{cid}"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / safe
            await tg_file.download_to_drive(str(dest))
            rel = f"telegram_{cid}/{safe}"
            from ..runtime import parse_quote_files
            quotes = parse_quote_files([rel], sandbox=str(
                Path(_s.hermes_sandbox_dir).resolve()))
            if quotes:
                qp = self._chat_quotes_path(cid)
                existing: list = []
                try:
                    existing = json.loads(qp.read_text()) if qp.exists() else []
                except Exception:
                    existing = []
                existing += quotes
                qp.write_text(json.dumps(existing))
                q = quotes[0]
                await self._send(
                    cid, f"📄 Đã nhận {safe}: {q.get('vendor', '?')} "
                         f"${q.get('unit_price', '?')} x {q.get('quantity', '?')} "
                         f"= ${q.get('total', '?')}. "
                         f"Gửi yêu cầu (VD: `mua 50 laptop`) để chạy.")
            else:
                await self._send(cid, f"⚠️ Đã lưu {safe} nhưng không parse được.")
        except Exception as e:  # noqa: BLE001
            await self._send(cid, f"⚠️ Không đọc được PDF: {str(e)[:300]}")

    async def handle_callback(self, query) -> None:
        data = getattr(query, "data", "") or ""
        action, _, rid = data.partition(":")
        user = getattr(query, "from_user", None)
        uid = getattr(user, "id", None)
        uname = getattr(user, "username", "") or ""
        msg = getattr(query, "message", None)
        cid = str(getattr(getattr(msg, "chat", None), "id", "") or "")
        ok, _ = self._auth(uid, uname)
        if not ok:
            try:
                await query.answer("⛔ No permission", show_alert=True)
            except Exception:
                pass
            return
        if action not in ("approve", "reject"):
            try:
                await query.answer()
            except Exception:
                pass
            return
        try:
            await query.answer()
        except Exception:
            pass
        rec = await asyncio.to_thread(
            self._resolve_blocking, rid, action == "approve",
            f"telegram:{uname or uid}")
        if rec is None:
            try:
                await query.edit_message_text(
                    f"⚠️ Approval {rid} không tồn tại / đã xử lý.")
            except Exception:
                pass
            if cid:
                await self._send(cid, f"⚠️ Approval {rid} không tồn tại.")
            return
        verdict = ("✅ APPROVED — purchase request may proceed."
                   if action == "approve" else "❌ REJECTED.")
        try:
            await query.edit_message_text(f"🛒 Purchase approval [{rid}]\n{verdict}")
        except Exception:
            pass
        if cid:
            self.sessions.save(cid, state="idle", pending_approval_id="")
            await self._send(cid, verdict)

    # -- internals ------------------------------------------------------
    async def _reply_price_question(self, cid: str) -> None:
        quotes = self._load_chat_quotes(cid)
        if quotes is None:
            from ..runtime import default_demo_quotes
            quotes = default_demo_quotes()
        reply = await asyncio.to_thread(price_question_reply, quotes)
        await self._send(cid, reply)
        self.sessions.append_history(cid, "assistant", reply, self.max_history)

    async def _reply_inbox(self, cid: str, text: str) -> None:
        m = re.search(r"(\d+)", text or "")
        limit = max(1, min(int(m.group(1)) if m else 10, 30))
        def _block():
            from ..config import settings as _s
            from ..tasks import TaskStore
            s = TaskStore(_s.hermes_db_path,
                          dsn=_s.hermes_database_url or None)
            return s.list_tasks(limit)
        try:
            rows = await asyncio.to_thread(_block)
            await self._send(cid, format_task_list(rows, limit))
        except Exception as e:  # noqa: BLE001
            await self._send(cid, f"⚠️ Không đọc được inbox: {str(e)[:200]}")

    async def _reply_task_detail(self, cid: str, task_id: str) -> None:
        if not task_id:
            await self._send(cid, "Dùng: `/task <id>`")
            return
        def _block():
            from ..config import settings as _s
            from ..tasks import TaskStore
            s = TaskStore(_s.hermes_db_path,
                          dsn=_s.hermes_database_url or None)
            t = s.get(task_id)
            return t.model_dump(), s.events(task_id)
        try:
            task, events = await asyncio.to_thread(_block)
            await self._send(cid, format_task_detail(task, events))
        except KeyError:
            await self._send(cid, f"⚠️ Task {task_id} không tồn tại.")
        except Exception as e:  # noqa: BLE001
            await self._send(cid, f"⚠️ Lỗi đọc task: {str(e)[:200]}")

    def _resolve_blocking(self, request_id: str, approved: bool,
                          resolver: str) -> dict | None:
        from ..config import settings as _s
        from ..messaging.approval_bot import resolve_approval
        from ..procurement.pipeline import default_procurement_db
        proc_db = default_procurement_db(_s.hermes_db_path)
        return resolve_approval(request_id, approved, resolver=resolver,
                                proc_db=proc_db, sync_db=_s.hermes_db_path)

    async def _resolve_approval_dm(self, cid: str, request_id: str,
                                   approved: bool, username, user_id) -> None:
        rec = await asyncio.to_thread(
            self._resolve_blocking, request_id, approved,
            f"telegram:{username or user_id}")
        if rec is None:
            await self._send(cid, f"⚠️ Approval {request_id} không tồn tại.")
            return
        self.sessions.save(cid, state="idle", pending_approval_id="")
        await self._send(
            cid, "✅ APPROVED — purchase request may proceed."
            if approved else "❌ REJECTED.")

    async def _start_procurement(self, cid: str, text: str, spec: str,
                                 username, user_id) -> None:
        user = get_user_from_telegram(user_id, username)
        if user and not check_permission(user, "procurement:create"):
            await self._send(cid, "⛔ Bạn không có quyền tạo yêu cầu mua hàng.")
            return
        
        self.sessions.save(cid, state="running", pending_text=text,
                           pending_spec=spec)
        await self._send(
            cid, "⏳ Đang chạy DAG (price‖vendor‖contract‖spec → analysis → "
                 "verification, 10-60s)…")
        try:
            try:
                await self.bot.send_chat_action(chat_id=int(cid), action="typing")
            except Exception:
                pass
        except Exception:
            pass
        asyncio.create_task(
            self._procurement_bg(cid, text, spec, username, user_id))

    async def _procurement_bg(self, cid: str, text: str, spec: str,
                              username, user_id) -> None:
        quotes = self._load_chat_quotes(cid)

        def _block():
            from ..runtime import HermesRuntime
            r = HermesRuntime()
            task = r.run_procurement(
                text, project="", user=f"telegram:{username or user_id}",
                quotes=quotes, required_spec=spec)
            return task
        try:
            task = await asyncio.to_thread(_block)
        except Exception as e:  # noqa: BLE001
            self.sessions.save(cid, state="idle")
            await self._send(cid, f"❌ Task failed: {str(e)[:500]}")
            return
        body = format_recommendation(task.result)
        rid = self._latest_pending_for(task.id)
        if rid:
            from ..messaging import approval_keyboard
            self.sessions.save(cid, state="awaiting_approval",
                               pending_task_id=task.id,
                               pending_approval_id=rid)
            await self._send(cid, f"{body}\nTask: `{task.id}`")
            try:
                await self.bot.send_message(
                    chat_id=int(cid) if cid.lstrip("-").isdigit() else cid,
                    text=f"🛒 Duyệt mua [{rid}]?",
                    reply_markup=approval_keyboard(rid))
            except Exception:
                await self._send(cid, f"Approval: {rid} "
                    f"(hoặc nhắn `approve {rid}`)")
        else:
            self.sessions.save(cid, state="idle", pending_task_id=task.id,
                               pending_approval_id="")
            await self._send(cid, f"{body}\nTask: `{task.id}`")
        self.sessions.append_history(cid, "assistant", body[:1500],
                                     self.max_history)

    def _latest_pending_for(self, task_id: str) -> str:
        try:
            import json as _json

            from ..async_engine.loops.hitl import ApprovalStore
            from ..config import settings as _s
            from ..procurement.pipeline import default_procurement_db
            store = ApprovalStore(default_procurement_db(_s.hermes_db_path))
            for rec in reversed(store.pending(limit=50)):
                try:
                    args = _json.loads(rec.get("args") or "{}")
                except Exception:
                    args = {}
                if args.get("sync_task_id") == task_id or \
                        rec.get("task_id") == task_id:
                    return rec.get("request_id", "")
        except Exception:
            pass
        return ""
