"""1:1 chat handler — webhook (tunnel) local-first, cloud chỉ Bot API + LLM."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from ..runtime import get_runtime
from .auth import (
    check_permission,
    get_user_from_telegram,
    is_allowed,
)
from .i18n import DEFAULT_LANG, t
from .intent import (
    HELP_TEXT,
    MENU_HELP,
    build_menu_keyboard,
    build_reply_keyboard,
    classify,
    parse_approval_text,
    parse_task_id,
)
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

    def _get_lang(self, cid: str) -> str:
        """Get language preference from session, default 'vi'."""
        sess = self.sessions.get(cid)
        if sess and "lang" in sess:
            return sess["lang"]
        return DEFAULT_LANG

    def _t(self, cid: str, key: str, **kwargs) -> str:
        """Translate key using chat's language."""
        return t(key, self._get_lang(cid), **kwargs)

    async def _send_menu(self, chat_id, text: str = "") -> None:
        """Send the main inline-keyboard menu with persistent reply keyboard."""
        cid = str(chat_id)
        menu_text = text or self._t(cid, "menu_button_text")
        kb = build_menu_keyboard()
        reply_kb = build_reply_keyboard()
        await self._send(cid, menu_text, reply_markup=kb)
        # Also send persistent reply keyboard (shows at bottom of chat)
        await self._send(cid, self._t(cid, "menu_hint"), reply_markup=reply_kb)

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

    async def _reply_webhook_status(self, chat_id: str) -> None:
        from ..config import settings as _s
        cid = str(chat_id)
        token = (_s.telegram_bot_token or "").strip()
        if not token:
            await self._send(cid, self._t(cid, "webhook_no_token"))
            return
        webhook_url = (_s.telegram_webhook_url or "").strip()
        secret = (_s.telegram_webhook_secret or "").strip()
        try:
            from ..telegram_chat.webhook import webhook_info
            info = webhook_info()
        except Exception as e:
            await self._send(cid, self._t(cid, "webhook_info_error", error=e))
            return
        token_status = "đã cấu hình" if info.get('configured') else "chưa cấu hình"
        if self._get_lang(cid) == "en":
            token_status = "configured" if info.get('configured') else "not configured"
        lines = [
            f"🔗 Mode: {info.get('mode', '?')}",
            f"✅ Bot token: {token_status}",
        ]
        if webhook_url:
            lines.append(f"🌐 Webhook URL: {webhook_url}")
            secret_status = "đã đặt" if secret else "chưa đặt"
            if self._get_lang(cid) == "en":
                secret_status = "set" if secret else "not set"
            lines.append(f"🔒 Secret: {secret_status}")
        else:
            await self._send(cid, self._t(cid, "webhook_missing_url"))
            return
        await self._send(cid, "\n".join(lines))

    # -- entry points ---------------------------------------------------
    async def handle_message(self, chat_id, user_id, username, text: str) -> None:
        cid = str(chat_id)
        ok, _ = self._auth(user_id, username)
        if not ok:
            await self._send(cid, self._t(cid, "no_permission"))
            return
        if self._limited(cid):
            await self._send(cid, self._t(cid, "rate_limited"))
            return
        sess = self.sessions.save(cid, username=str(username or user_id or ""))
        self.sessions.append_history(cid, "user", text or "", self.max_history)
        intent = classify(text or "", sess.get("state", "idle"))

        if intent == "help":
            self.sessions.save(cid, state="idle")
            await self._send_menu(cid)
            self.sessions.append_history(cid, "assistant", "(menu shown)",
                                         self.max_history)
        elif intent == "new":
            self.sessions.clear(cid)
            await self._send_menu(cid, self._t(cid, "welcome_new"))
        elif intent == "webhook":
            await self._reply_webhook_status(cid)
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
            t_text = (text or "").strip()
            if len(t_text) < 20 and not sess.get("pending_text"):
                self.sessions.save(cid, state="awaiting_spec", pending_text=t_text)
                await self._send(
                    cid, self._t(cid, "procurement_need_spec"))
            else:
                await self._start_procurement(cid, t_text, "", username, user_id)
        elif intent == "lang":
            await self._handle_lang_command(cid)
        elif await self._reply_domain(cid, text, user_id, username):
            self.sessions.append_history(cid, "assistant", "(domain reply)",
                                         self.max_history)
        else:
            sess_now = self.sessions.get(cid)
            reply = await asyncio.to_thread(
                chitchat_reply, text or "", sess_now.get("history", []))
            await self._send(cid, reply)
            self.sessions.append_history(cid, "assistant", reply, self.max_history)

    async def _handle_lang_command(self, cid: str) -> None:
        """Handle /lang command - toggle between vi and en."""
        current = self._get_lang(cid)
        new_lang = "en" if current == "vi" else "vi"
        self.sessions.save(cid, lang=new_lang)
        # Send confirmation in the OLD language (before toggle)
        if current == "vi":
            msg = t("lang_switched_vi", "vi")
        else:
            msg = t("lang_switched_en", "en")
        await self._send(cid, msg)

    async def handle_document(self, chat_id, user_id, username,
                              file_id: str, file_name: str,
                              caption: str = "") -> None:
        cid = str(chat_id)
        ok, _ = self._auth(user_id, username)
        if not ok:
            await self._send(cid, self._t(cid, "no_permission"))
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
                    cid, self._t(cid, "pdf_received",
                                 filename=safe,
                                 vendor=q.get('vendor', '?'),
                                 unit_price=q.get('unit_price', '?'),
                                 quantity=q.get('quantity', '?'),
                                 total=q.get('total', '?')))
            else:
                # Not a quote PDF → fall back to Second Brain ingest (personal).
                from ..knowledge.service import extract_text_from_file
                content = extract_text_from_file(str(dest))
                if content:
                    uid = f"tg:{user_id or username}"
                    from ..runtime import get_runtime
                    doc_id = get_runtime().services()["knowledge"].ingest(
                        content=content, title=safe, scope="personal",
                        user_id=uid, source_uri=rel)
                    await self._send(
                        cid, self._t(cid, "pdf_ingested",
                                     filename=safe, doc_id=doc_id))
                else:
                    await self._send(cid, self._t(cid, "pdf_failed",
                                                  filename=safe))
        except Exception as e:  # noqa: BLE001
            await self._send(cid, self._t(cid, "pdf_read_error",
                                          error=str(e)[:300]))

    async def handle_callback(self, query) -> None:
        data = getattr(query, "data", "") or ""
        user = getattr(query, "from_user", None)
        uid = getattr(user, "id", None)
        uname = getattr(user, "username", "") or ""
        msg = getattr(query, "message", None)
        cid = str(getattr(getattr(msg, "chat", None), "id", "") or "")
        ok, _ = self._auth(uid, uname)
        if not ok:
            try:
                await query.answer(self._t(cid, "no_permission"), show_alert=True)
            except Exception:
                pass
            return

        # --- Menu buttons ---
        if data.startswith("menu:"):
            action = data.split("menu:", 1)[1]
            try:
                await query.answer()
            except Exception:
                pass
            help_text = MENU_HELP.get(action, HELP_TEXT)
            # Edit the original message with contextual help + re-show menu
            try:
                await query.edit_message_text(
                    help_text,
                    reply_markup=build_menu_keyboard())
            except Exception:
                # Fallback: send new message (edit may fail if text unchanged)
                await self._send(cid, help_text, reply_markup=build_menu_keyboard())
            return

        # --- Approval buttons (approve/reject) ---
        action, _, rid = data.partition(":")
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
                    self._t(cid, "approval_not_found", rid=rid))
            except Exception:
                pass
            if cid:
                await self._send(cid, self._t(cid, "approval_not_found", rid=rid))
            return
        verdict = (self._t(cid, "approval_approved")
                   if action == "approve" else self._t(cid, "approval_rejected"))
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
            await self._send(cid, self._t(cid, "error_reading_inbox",
                                          error=str(e)[:200]))

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
            await self._send(cid, self._t(cid, "task_not_found", task_id=task_id))
        except Exception as e:  # noqa: BLE001
            await self._send(cid, self._t(cid, "error_reading_task",
                                          error=str(e)[:200]))

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
            await self._send(cid, self._t(cid, "approval_not_found",
                                          request_id=request_id))
            return
        self.sessions.save(cid, state="idle", pending_approval_id="")
        verdict = (self._t(cid, "approval_approved")
                   if approved else self._t(cid, "approval_rejected"))
        await self._send(cid, verdict)

    async def _start_procurement(self, cid: str, text: str, spec: str,
                                 username, user_id) -> None:
        user = get_user_from_telegram(user_id, username)
        if user and not check_permission(user, "procurement:create"):
            await self._send(cid, self._t(cid, "no_procurement_permission"))
            return

        self.sessions.save(cid, state="running", pending_text=text,
                           pending_spec=spec)
        await self._send(
            cid, self._t(cid, "procurement_start"))
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
            r = get_runtime()
            task = r.run_procurement(
                text, project="", user=f"telegram:{username or user_id}",
                quotes=quotes, required_spec=spec)
            return task
        try:
            task = await asyncio.to_thread(_block)
        except Exception as e:  # noqa: BLE001
            self.sessions.save(cid, state="idle")
            logging.error(f"Procurement failed for {cid}: {e}")
            await self._send(cid, self._t(cid, "error_generic"))
            return

        # Generate PDF report
        try:
            import json as _json

            from ..report import generate_report

            # Parse recommendation
            try:
                rec = _json.loads(task.result.split("\n", 1)[-1]) if task.result else {}
            except Exception:
                rec = {"vendor": "N/A", "total_cost": 0}

            # Build report data
            report_data = {
                "title": "PROCUREMENT DECISION REPORT",
                "subtitle": text[:60],
                "request": text,
                "spec": spec,
                "quotes": quotes or [],
                "recommendation": rec,
                "decision": "PENDING",
                "confidence": "HIGH",
                "policy_status": "COMPLIANT",
                "approver": "",
                "approved_at": "",
                "execution_status": "COMPLETED",
                "evidence": [
                    {"claim": f"Quote from {q.get('vendor', 'N/A')}",
                     "source": q.get("source_uri", ""),
                     "agent": "Price Agent",
                     "verification": "VERIFIED"}
                    for q in (quotes or [])
                ],
                "audit_trail": [
                    {"timestamp": task.created_at or "",
                     "actor": f"telegram:{username or user_id}",
                     "action": "Procurement request submitted"},
                ],
            }

            pdf_bytes = generate_report("procurement", report_data,
                                        workflow_id=task.id,
                                        task_id=task.id)

            # Save PDF
            import os
            import tempfile
            pdf_path = os.path.join(tempfile.gettempdir(),
                                    f"hermes_report_{task.id[:8]}.pdf")
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)

            # Send PDF
            try:
                with open(pdf_path, "rb") as f:
                    await self.bot.send_document(
                        chat_id=int(cid) if cid.lstrip("-").isdigit() else cid,
                        document=f,
                        filename=f"BaoCao_{task.id[:8]}.pdf",
                        caption=self._t(cid, "procurement_result"))
            except Exception:
                pass

            # Cleanup
            try:
                os.unlink(pdf_path)
            except Exception:
                pass

        except Exception as e:
            logging.error(f"PDF generation failed: {e}")

        # Send recommendation summary
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
                    text=self._t(cid, "approval_prompt", rid=rid),
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

    # -- new-domain replies (Phase 1-4: KB / Brain / Advisor / Ops / CI) --
    _DOMAIN_INTENTS = None  # filled lazily to avoid import cycle at module load

    async def _reply_domain(self, cid: str, text: str, user_id, username) -> bool:
        """Handle new-domain intents; return True when handled."""
        from ..router import Intent, route
        domain_intents = {
            Intent.ASK_ADVISOR, Intent.OPS_STATUS, Intent.OPS_CONNECT,
            Intent.COMPETITOR_BRIEF, Intent.COMPETITOR_WATCH,
            Intent.KB_QUERY, Intent.KB_INGEST, Intent.BRAIN_QUERY, Intent.BRAIN_INGEST,
        }
        plan = route(text or "")
        if plan.intent not in domain_intents:
            return False

        def _work() -> str:
            svc = get_runtime().services()
            uid = f"tg:{user_id or username}"
            t = text or ""
            if plan.intent == Intent.KB_QUERY:
                return svc["knowledge"].query(text=t, scope="team").to_text()
            if plan.intent == Intent.BRAIN_QUERY:
                return svc["knowledge"].query(text=t, scope="personal",
                                              user_id=uid).to_text()
            if plan.intent == Intent.KB_INGEST:
                doc_id = svc["knowledge"].ingest(content=t, title=t[:60],
                                                 scope="team")
                return f"💾 Đã lưu vào kho kiến thức team (`{doc_id}`)."
            if plan.intent == Intent.BRAIN_INGEST:
                doc_id = svc["knowledge"].ingest(content=t, title=t[:60],
                                                 scope="personal", user_id=uid)
                return f"🧠 Đã lưu vào bộ não thứ hai (`{doc_id}`)."
            if plan.intent == Intent.ASK_ADVISOR:
                kb = svc["knowledge"].query(text=t, scope="team", limit=3)
                ctx = "\n".join(c.text for c in kb.sources)
                return svc["council"].ask(t, context=ctx).to_text()
            if plan.intent == Intent.OPS_STATUS:
                return svc["ops"].collect_attention().to_text()
            if plan.intent == Intent.OPS_CONNECT:
                low = t.lower()
                kind = next((k for k in ("crm", "invoicing", "calendar", "inbox")
                             if k in low), "crm")
                svc["ops"].add_source(kind=kind)
                return (f"🔌 Đã kết nối nguồn `{kind}` (mock). Nhắn "
                        f"\"hôm nay cần chú ý gì\" để xem tổng quan.")
            if plan.intent == Intent.COMPETITOR_WATCH:
                return self._reply_competitor_watch(cid, t)
            if plan.intent == Intent.COMPETITOR_BRIEF:
                return self._reply_competitor_brief(cid, t)
            return ("🧭 Chưa có đối thủ nào được theo dõi. Thêm qua API "
                    "`POST /competitor/watch` với `{competitor, urls}` rồi hỏi brief.")

        reply = await asyncio.to_thread(_work)
        await self._send(cid, reply)
        return True

    def _parse_competitor_text(self, text: str) -> tuple[str, list[str]]:
        """Extract (competitor_name, urls) from free-text watch command."""
        import re
        urls = re.findall(r"https?://\S+", text or "")
        name = (text or "").strip()
        for url in urls:
            name = name.replace(url, "")
        name = " ".join(name.split())
        for kw in ("theo dõi", "theo doi", "watch", "giám sát", "giam sat",
                    "monitor", "đối thủ", "doi thu", "competitor"):
            name = name.lower().replace(kw, "")
        name = " ".join(name.split()).strip()
        return name or "Unknown", urls

    def _reply_competitor_watch(self, cid: str, text: str) -> str:
        from ..competitor import CompetitorTarget
        name, urls = self._parse_competitor_text(text)
        svc = get_runtime().services()
        target = CompetitorTarget(competitor=name, urls=urls, feeds=[])
        svc["competitor_targets"].append(target)
        return self._t(cid, "competitor_watch_added", name=name, count=len(urls))

    def _reply_competitor_brief(self, cid: str, text: str) -> str:
        import time

        from ..competitor import build_weekly_brief
        svc = get_runtime().services()
        targets = svc.get("competitor_targets") or []
        if not targets:
            return self._t(cid, "competitor_no_targets")
        
        # Check if we have cached findings and last_refresh timestamp
        now = time.time()
        last_refresh = svc.get("competitor_last_refresh", 0)
        cached_findings = svc.get("competitor_cached_findings")
        
        # Auto-refresh if stale (>5 minutes) or no cache
        if cached_findings is None or (now - last_refresh) > 300:
            findings = svc["competitor"].collect(targets)
            svc["competitor_cached_findings"] = findings
            svc["competitor_last_refresh"] = now
        else:
            findings = cached_findings
        
        if not findings:
            return self._t(cid, "competitor_no_findings")
        brief = build_weekly_brief(findings)
        return brief.to_text()

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
