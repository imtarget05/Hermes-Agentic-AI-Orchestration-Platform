"""Telegram 1:1 chat tests — token-free (session/auth/intent/webhook)."""
from __future__ import annotations

import pytest


def test_auth_allowlist():
    from hermes.telegram_chat.auth import allowed_from_string, is_allowed
    assert is_allowed(1, "bob", []) is True  # empty → open (local dev)
    allowed = allowed_from_string("123, @Alice")
    assert is_allowed(123, "any", allowed) is True
    assert is_allowed(999, "alice", allowed) is True
    assert is_allowed(999, "ALICE", allowed) is True
    assert is_allowed(999, "bob", allowed) is False


def test_session_roundtrip(tmp_path):
    from hermes.telegram_chat.session import ChatSessionStore
    s = ChatSessionStore(str(tmp_path / "s.db"))
    assert s.get("1")["state"] == "idle"
    s.save("1", state="awaiting_spec", pending_text="mua laptop")
    assert s.get("1")["pending_text"] == "mua laptop"
    s.append_history("1", "user", "hi", 4)
    assert len(s.get("1")["history"]) == 1
    s.clear("1")
    assert s.get("1")["state"] == "idle"


def test_intent_matrix():
    from hermes.telegram_chat.intent import classify, parse_approval_text
    assert classify("/start") == "help"
    assert classify("/new") == "new"
    assert classify("/webhook") == "webhook"
    assert classify("/lang") == "lang"
    assert classify("/tasks 5") == "inbox_list"
    assert classify("/task abc") == "inbox_detail"
    assert classify("approve abc123") == "approval_text"
    assert classify("mua 50 laptop") == "procurement"
    assert classify("gửi báo giá Dell", "idle") == "procurement"
    assert classify("RAM 16GB bảo hành 3 năm", "awaiting_spec") == "procurement_followup"
    # spec constraint mentioning price is NOT a price question (no question marker)
    assert classify("RAM 16GB, giá dưới 30 triệu", "awaiting_spec") == "procurement_followup"
    assert classify("bot có khỏe không", "idle") == "chitchat"
    ok, rid = parse_approval_text("reject abc123")
    assert ok is False and rid == "abc123"


def test_price_question_intent():
    from hermes.telegram_chat.intent import classify, is_price_question
    # incident case: must never reach raw-LLM chitchat
    assert classify("Bạn đã check lại giá thị trường hay dùng data cũ") == "price_question"
    assert classify("MacBook hiện giờ giá bao nhiêu?") == "price_question"
    assert classify("mua 50 laptop giá bao nhiêu") == "price_question"
    assert classify("giá Lenovo mới nhất") == "price_question"
    assert classify("Sai giá") == "chitchat"
    assert is_price_question("RAM 16GB, giá dưới 30 triệu") is False


def test_price_reply_grounded_no_data():
    import re

    from hermes.telegram_chat.service import price_question_reply
    reply = price_question_reply([])
    assert "chưa có dữ liệu giá" in reply
    assert "PDF" in reply
    # must not invent any price figure
    assert not re.search(r"\$\s?[\d,]+|\d[\d.,]*\s*(VND|₫|đồng)", reply)


def test_price_reply_demo_label():
    from hermes.runtime import default_demo_quotes
    from hermes.telegram_chat.service import price_question_reply
    reply = price_question_reply(default_demo_quotes())
    assert "DEMO" in reply
    assert "không phải giá thị trường" in reply


def test_price_reply_dated_and_stale():
    from hermes.telegram_chat.service import price_question_reply
    fresh = [{"vendor": "ACME", "unit_price": 10, "quantity": 5, "total": 50,
              "source_uri": "tg/acme.pdf", "quote_date": "2099-01-01"}]
    assert "ACME" in price_question_reply(fresh)
    assert "đã cũ" not in price_question_reply(fresh)
    old = [dict(fresh[0], quote_date="2020-01-01")]
    assert "đã cũ" in price_question_reply(old)


def test_chitchat_prompt_has_no_fabrication_rule():
    from hermes.telegram_chat.service import (
        NO_FABRICATION_RULE,
        build_chitchat_prompt,
    )
    p = build_chitchat_prompt("MacBook giá bao nhiêu?", [])
    assert "KHÔNG BAO GIỜ" in p
    assert NO_FABRICATION_RULE in p


def test_verification_rejects_undated_price_claim():
    from hermes.agents import VERIFICATION
    good = ('{"vendor": "Lenovo", "total_cost": 54000, "reasons": '
            '[{"claim": "lowest total cost $54,000", "evidence_ref": "demo/lenovo.pdf"}], '
            '"evidence_refs": ["demo/lenovo.pdf"]}')
    assert VERIFICATION.think("t", good).startswith("VERIFICATION PASSED")
    bad = ('{"vendor": "Lenovo", "total_cost": 54000, "reasons": '
           '[{"claim": "lowest total cost $54,000", "evidence_ref": "quotes"}], '
           '"evidence_refs": ["quotes"]}')
    out = VERIFICATION.think("t", bad)
    assert out.startswith("VERIFICATION FAILED")
    assert "dated quote" in out


def test_demo_quotes_labeled_and_recommendation_dated():
    import json

    from hermes.agents import ANALYSIS
    from hermes.runtime import default_demo_quotes
    quotes = default_demo_quotes()
    assert all(q["is_demo"] and q["quote_date"] == "DEMO" for q in quotes)
    ctx = "\n".join(json.dumps(q) for q in quotes)
    rec = json.loads(ANALYSIS.think("mua 50 laptop", ctx))
    assert rec["vendor"] == "Lenovo" and rec["data_as_of"] == "DEMO"


def test_dispatch_price_question():
    pytest.importorskip("telegram")
    import asyncio

    from hermes.telegram_chat.handler import ChatHandler
    from hermes.telegram_chat.session import ChatSessionStore
    from hermes.telegram_chat.webhook import dispatch_update

    class FakeBot:
        def __init__(self):
            self.sent: list = []

        async def send_message(self, chat_id=None, text=None, **kw):
            self.sent.append((str(chat_id), text or ""))

    async def _go(tmp_path):
        bot = FakeBot()
        h = ChatHandler(bot, sessions=ChatSessionStore(str(tmp_path / "s.db")))
        data = {"update_id": 2, "message": {
            "message_id": 2, "date": 1,
            "chat": {"id": 43, "type": "private"},
            "from": {"id": 43, "is_bot": False, "first_name": "T",
                     "username": "tester"},
            "text": "Bạn check giá thị trường chưa hay dùng data cũ?"}}
        assert await dispatch_update(data, handler=h, bot=bot) == "message"
        assert bot.sent and "DEMO" in bot.sent[0][1]

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        asyncio.run(_go(Path(td)))


def test_chunk_and_keyboard():
    pytest.importorskip("telegram")
    from hermes.messaging import approval_keyboard, chunk_text
    assert len(chunk_text("a" * 5000)[0]) == 4000
    kb = approval_keyboard("r1")
    data = kb.inline_keyboard[0][0].callback_data
    assert data == "approve:r1"


def test_webhook_info_and_secret(monkeypatch):
    from fastapi.testclient import TestClient

    from hermes import api
    from hermes.config import settings
    from hermes.runtime import reset_runtime
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cr3t")
    reset_runtime()
    c = TestClient(api.app)
    assert c.get("/telegram/webhook").status_code == 200
    r = c.post("/telegram/webhook", json={"update_id": 1},
               headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert r.status_code == 403


def test_dispatch_message_help():
    pytest.importorskip("telegram")
    import asyncio

    from hermes.telegram_chat.handler import ChatHandler
    from hermes.telegram_chat.session import ChatSessionStore
    from hermes.telegram_chat.webhook import dispatch_update

    class FakeBot:
        def __init__(self):
            self.sent: list = []

        async def send_message(self, chat_id=None, text=None, **kw):
            self.sent.append((str(chat_id), text or ""))

    async def _go(tmp_path):
        bot = FakeBot()
        h = ChatHandler(bot, sessions=ChatSessionStore(str(tmp_path / "s.db")))
        data = {"update_id": 1, "message": {
            "message_id": 1, "date": 1,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "T",
                     "username": "tester"},
            "text": "/start"}}
        # allow-all (no allowlist in test env)
        action = await dispatch_update(data, handler=h, bot=bot)
        assert action == "message"
        assert bot.sent and "Hermes" in bot.sent[0][1]

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        asyncio.run(_go(Path(td)))


def test_lang_command_toggles_language():
    """Test /lang command toggles between vi and en."""
    pytest.importorskip("telegram")
    import asyncio

    from hermes.telegram_chat.handler import ChatHandler
    from hermes.telegram_chat.session import ChatSessionStore
    from hermes.telegram_chat.webhook import dispatch_update

    class FakeBot:
        def __init__(self):
            self.sent: list = []

        async def send_message(self, chat_id=None, text=None, **kw):
            self.sent.append((str(chat_id), text or ""))

    async def _go(tmp_path):
        bot = FakeBot()
        h = ChatHandler(bot, sessions=ChatSessionStore(str(tmp_path / "s.db")))
        
        # Initial state should be Vietnamese (default)
        data = {"update_id": 1, "message": {
            "message_id": 1, "date": 1,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "T",
                     "username": "tester"},
            "text": "/lang"}}
        action = await dispatch_update(data, handler=h, bot=bot)
        assert action == "message"
        assert bot.sent and "Tiếng Việt" in bot.sent[0][1]
        
        # Second /lang should switch to English
        bot.sent.clear()
        data["update_id"] = 2
        data["message"]["message_id"] = 2
        data["message"]["text"] = "/lang"
        action = await dispatch_update(data, handler=h, bot=bot)
        assert action == "message"
        assert bot.sent and "English" in bot.sent[0][1]
        
        # Third /lang should switch back to Vietnamese
        bot.sent.clear()
        data["update_id"] = 3
        data["message"]["message_id"] = 3
        data["message"]["text"] = "/lang"
        action = await dispatch_update(data, handler=h, bot=bot)
        assert action == "message"
        assert bot.sent and "Tiếng Việt" in bot.sent[0][1]

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        asyncio.run(_go(Path(td)))


def test_i18n_translations_exist_both_languages():
    """Test that all translation keys exist in both Vietnamese and English."""
    from hermes.telegram_chat.i18n import TRANSLATIONS, DEFAULT_LANG, get_available_languages
    
    # Both languages should be available
    langs = get_available_languages()
    assert "vi" in langs
    assert "en" in langs
    assert DEFAULT_LANG == "vi"
    
    # All keys should exist in both languages
    vi_keys = set(TRANSLATIONS["vi"].keys())
    en_keys = set(TRANSLATIONS["en"].keys())
    
    assert vi_keys == en_keys, f"Key mismatch: vi has {vi_keys - en_keys}, en has {en_keys - vi_keys}"
    
    # Check essential keys exist
    essential_keys = {
        "help_text", "menu_procure", "menu_price", "menu_kb", "menu_tasks",
        "menu_advisor", "menu_ops", "menu_competitor", "full_help_text",
        "webhook_status", "webhook_missing_url", "webhook_no_token",
        "competitor_watch_added", "competitor_no_targets", "competitor_no_findings",
        "competitor_brief_header", "procurement_start", "procurement_need_spec",
        "procurement_result", "pdf_received", "pdf_ingested", "pdf_failed",
        "approval_prompt", "approval_approved", "approval_rejected",
        "welcome_new", "rate_limited", "no_permission", "no_procurement_permission",
        "error_generic", "webhook_info_error", "approval_not_found",
        "task_not_found", "error_reading_inbox", "error_reading_task",
        "pdf_read_error", "lang_switched_vi", "lang_switched_en",
        "lang_current", "menu_button_text"
    }
    
    for key in essential_keys:
        assert key in vi_keys, f"Missing key in vi: {key}"
        assert key in en_keys, f"Missing key in en: {key}"
        # Both should have non-empty values
        assert TRANSLATIONS["vi"][key].strip(), f"Empty value for {key} in vi"
        assert TRANSLATIONS["en"][key].strip(), f"Empty value for {key} in en"


def test_i18n_t_function_formatting():
    """Test t() function with formatting kwargs."""
    from hermes.telegram_chat.i18n import t
    
    # Test with formatting
    result_vi = t("competitor_watch_added", "vi", name="Dell", count=2)
    assert "Dell" in result_vi
    assert "2" in result_vi
    
    result_en = t("competitor_watch_added", "en", name="Dell", count=2)
    assert "Dell" in result_en
    assert "2" in result_en
    
    # Test fallback to default lang
    result = t("nonexistent_key", "vi")
    assert result == "nonexistent_key"
    
    result = t("help_text", "fr")  # fr not supported, should fallback to vi
    assert "Hermes" in result
