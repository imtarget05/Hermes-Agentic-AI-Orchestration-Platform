"""Rule-based intent for 1:1 chat (token-free, testable).

Intents: help | new | inbox_list | inbox_detail | approval_text |
price_question | procurement | procurement_followup | chitchat | lang.
Procurement multi-turn uses session.state.

Grounded-price policy: market-price questions NEVER go to raw-LLM chitchat
(past incident: bot invented MacBook VND prices). They go to price_question,
answered deterministically from dated quotes only.

Now uses the new Router-First Architecture (P0-1) for intent classification
and routing plan generation.
"""
from __future__ import annotations

import re

from ..router import (
    RoutingPlan,
    route,
)
from .i18n import DEFAULT_LANG, t

# Updated regex with full Vietnamese character support (matches router)
_APPROVE_RE = re.compile(
    r"^\s*(approve|duy[eêệ]t|reject|t[uừ]\s*ch[oôố]i)\s*[:\-]?\s*([A-Za-z0-9_\-]{4,64})",
    re.IGNORECASE)


_PRICE_KW = ("giá", "gia", "price", "pricing", "bảng giá", "bang gia",
             "giá cả", "gia ca", "thị trường", "thi truong", "market",
             "chi phí", "chi phi", "cost", "đắt", "dat", "rẻ")
# question markers — distinguish "what is the price?" from spec text like
# "RAM 16GB, giá dưới 30 triệu" (a constraint, not a question)
_PRICE_QMARK = ("bao nhiêu", "bao nhieu", "thế nào", "the nao", "không?",
                "khong?", "?", "hiện tại", "hien tai", "hiện nay", "hien nay",
                "mới nhất", "moi nhat", "check", "cập nhật", "cap nhat",
                "update", "giá thị trường", "gia thi truong")

_PROCURE_KW = ("mua", "laptop", "báo giá", "bao gia", "quote",
               "procurement", "mua sắm", "mua sam", "vendor",
               "dell", "lenovo", "hp")


def is_price_question(text: str) -> bool:
    """True if the user asks for market/current prices (needs dated quotes).

    Requires a price keyword + a question marker so spec constraints
    ("RAM 16GB, giá dưới 30 triệu") are NOT misclassified.
    """
    low = (text or "").lower()
    if not any(k in low for k in _PRICE_KW):
        return False
    return any(m in low for m in _PRICE_QMARK)


def parse_approval_text(text: str) -> tuple[bool | None, str | None]:
    """Return (approved, request_id) or (None, None) if not an approval cmd."""
    m = _APPROVE_RE.match(text or "")
    if not m:
        return None, None
    verb = m.group(1).lower()
    approved = verb.startswith("approve") or verb.startswith("duy")
    return approved, m.group(2)


def classify(text: str, session_state: str = "idle") -> str:
    """Classify text into legacy intent string (backward compatible).

    Uses the new router internally but returns legacy intent strings
    for backward compatibility with existing handler code.
    """
    t_text = (text or "").strip()
    low = t_text.lower()
    if not t_text:
        return "help"
    if low in ("/start", "/help", "help", "bắt đầu", "bat dau", "hi", "hello",
               "xin chào", "xin chao", "menu", "mở menu", "mo menu"):
        return "help"
    if low in ("/new", "mới", "moi", "reset", "/reset"):
        return "new"
    if low.startswith("/webhook"):
        return "webhook"
    if low.startswith("/lang"):
        return "lang"
    if low.startswith("/tasks") or low.startswith("tasks") \
            or "inbox" in low or "danh sách" in low or "danh sach" in low:
        return "inbox_list"
    if low.startswith("/task ") or low.startswith("task "):
        return "inbox_detail"
    approved, _ = parse_approval_text(t_text)
    if approved is not None:
        return "approval_text"
    # Grounded-price policy: market-price questions are answered from dated
    # quotes only — never raw LLM (prevents invented prices). Takes priority
    # over procurement_followup: asking a price is not answering a spec prompt.
    if is_price_question(t_text):
        return "price_question"
    if session_state == "awaiting_spec":
        return "procurement_followup"
    if any(k in low for k in _PROCURE_KW):
        return "procurement"
    # short follow-up while a procurement was pending → treat as spec
    if session_state in ("awaiting_approval", "running"):
        return "chitchat"
    return "chitchat"


def classify_with_routing(
    text: str,
    session_state: str = "idle",
    quotes: list[dict] | None = None,
) -> RoutingPlan:
    """New router-first classification returning full RoutingPlan.

    This is the primary entry point for P0-1 router-first architecture.
    """
    return route(text, session_state, quotes)


def parse_task_id(text: str) -> str:
    parts = (text or "").strip().split()
    return parts[1] if len(parts) > 1 else ""


# HELP_TEXT and MENU_HELP now reference i18n translations
# These are kept for backward compatibility but use i18n internally
HELP_TEXT = t("help_text", DEFAULT_LANG)

# Contextual help texts shown when a menu button is pressed.
# These are kept for backward compatibility but use i18n internally
MENU_HELP = {
    "procure": t("menu_procure", DEFAULT_LANG),
    "price": t("menu_price", DEFAULT_LANG),
    "kb": t("menu_kb", DEFAULT_LANG),
    "tasks": t("menu_tasks", DEFAULT_LANG),
    "advisor": t("menu_advisor", DEFAULT_LANG),
    "ops": t("menu_ops", DEFAULT_LANG),
    "competitor": t("menu_competitor", DEFAULT_LANG),
    "help": HELP_TEXT,
}


def build_menu_keyboard():
    """Return InlineKeyboardMarkup for the main menu."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for row in _MENU_ROWS:
        rows.append([
            InlineKeyboardButton(text=label, callback_data=data)
            for label, data in row
        ])
    return InlineKeyboardMarkup(rows)


def build_reply_keyboard():
    """Return ReplyKeyboardMarkup with persistent '☰ Mở menu' button."""
    from telegram import KeyboardButton, ReplyKeyboardMarkup
    return ReplyKeyboardMarkup(
        [[KeyboardButton(text="☰ Mở menu")]],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=True
    )


# --- Menu keyboard layout ---------------------------------------------------
# Callback data: "menu:<action>"
_MENU_ROWS = [
    [("🛒 Mua sắm", "menu:procure"), ("💰 Hỏi giá", "menu:price")],
    [("🧠 Second Brain", "menu:kb"), ("📋 Tasks", "menu:tasks")],
    [("📊 Advisor", "menu:advisor"), ("🔌 Ops Hub", "menu:ops")],
    [("🧭 Competitor", "menu:competitor"), ("ℹ️ Help", "menu:help")],
]
