"""FastAPI webhook for Telegram 1:1 (local tunnel → same port as hermes.api)."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(tags=["telegram"])

_handler = None


def get_chat_handler():
    """Singleton ChatHandler sharing one Bot (local reload-safe)."""
    global _handler
    if _handler is not None:
        return _handler
    from ..config import settings as _s
    from .handler import ChatHandler
    from .session import ChatSessionStore
    token = _s.telegram_bot_token
    bot = None
    if token:
        from telegram import Bot
        bot = Bot(token)
    _handler = ChatHandler(
        bot, sessions=ChatSessionStore(_s.telegram_session_db),
        max_history=_s.telegram_max_history)
    return _handler


def _check_secret(secret_header: str | None) -> None:
    from ..config import settings as _s
    expected = _s.telegram_webhook_secret
    if expected and secret_header != expected:
        raise HTTPException(403, "bad webhook secret")


@router.get("/telegram/webhook")
def webhook_info():
    from ..config import settings as _s
    return {"status": "ok", "mode": _s.telegram_mode,
            "configured": bool(_s.telegram_bot_token)}


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    _check_secret(x_telegram_bot_api_secret_token)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(422, "invalid json")
    handler = get_chat_handler()
    if handler.bot is None:
        return {"status": "ok", "mock": True}
    await dispatch_update(data, handler)
    return {"status": "ok"}


async def dispatch_update(data: dict, handler=None, bot=None) -> str:
    """Route one Telegram Update → handler. Returns action label (testable)."""
    handler = handler or get_chat_handler()
    b = bot or handler.bot
    from telegram import Update
    try:
        update = Update.de_json(data, b)
    except Exception:
        return "bad_update"
    cq = getattr(update, "callback_query", None)
    if cq is not None:
        await handler.handle_callback(cq)
        return "callback"
    msg = getattr(update, "message", None) or getattr(update, "edited_message", None)
    if msg is None:
        return "ignored"
    chat = getattr(msg, "chat", None)
    user = getattr(msg, "from_user", None)
    chat_id = getattr(chat, "id", "")
    uid = getattr(user, "id", None)
    uname = getattr(user, "username", "") or ""
    doc = getattr(msg, "document", None)
    text = (getattr(msg, "text", "") or "") + (
        f"\n{(getattr(msg, 'caption', '') or '')}" if doc else "")
    if doc is not None:
        fid = getattr(doc, "file_id", "")
        fname = getattr(doc, "file_name", "") or "quote.pdf"
        await handler.handle_document(chat_id, uid, uname, fid, fname,
                                      getattr(msg, "caption", "") or "")
        return "document"
    if not (text or "").strip() and getattr(msg, "photo", None):
        await handler._send(str(chat_id), "📷 Mình chưa đọc ảnh. Gửi PDF báo giá nhé.")
        return "photo"
    if not (text or "").strip():
        return "ignored"
    await handler.handle_message(chat_id, uid, uname, text)
    return "message"


@router.post("/telegram/webhook/set")
async def webhook_set(body: dict | None = None):
    from ..config import settings as _s
    if not _s.telegram_bot_token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN not set")
    url = ((body or {}).get("url") or _s.telegram_webhook_url or "").strip()
    if not url:
        raise HTTPException(422, "missing url (body.url or TELEGRAM_WEBHOOK_URL)")
    from telegram import Bot
    bot = Bot(_s.telegram_bot_token)
    secret = _s.telegram_webhook_secret or None
    ok = await bot.set_webhook(url, secret_token=secret,
                               allowed_updates=["message", "callback_query"])
    return {"status": "ok" if ok else "failed", "url": url}


@router.delete("/telegram/webhook")
async def webhook_delete():
    from ..config import settings as _s
    if not _s.telegram_bot_token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN not set")
    from telegram import Bot
    ok = await Bot(_s.telegram_bot_token).delete_webhook()
    return {"status": "ok" if ok else "failed"}
