"""Rule-based intent for 1:1 chat (token-free, testable).

Intents: help | new | inbox_list | inbox_detail | approval_text |
price_question | procurement | procurement_followup | chitchat.
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
    t = (text or "").strip()
    low = t.lower()
    if not t:
        return "help"
    if low in ("/start", "/help", "help", "bắt đầu", "bat dau", "hi", "hello",
               "xin chào", "xin chao"):
        return "help"
    if low in ("/new", "mới", "moi", "reset", "/reset"):
        return "new"
    if low.startswith("/tasks") or low.startswith("tasks") \
            or "inbox" in low or "danh sách" in low or "danh sach" in low:
        return "inbox_list"
    if low.startswith("/task ") or low.startswith("task "):
        return "inbox_detail"
    approved, _ = parse_approval_text(t)
    if approved is not None:
        return "approval_text"
    # Grounded-price policy: market-price questions are answered from dated
    # quotes only — never raw LLM (prevents invented prices). Takes priority
    # over procurement_followup: asking a price is not answering a spec prompt.
    if is_price_question(t):
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


HELP_TEXT = (
    "⚡ Hermes 1:1 chat (local-first)\n"
    "• Gửi yêu cầu mua sắm: `mua 50 laptop` (kèm spec/quotes nếu có)\n"
    "• Gửi PDF báo giá trực tiếp vào chat\n"
    "• `/tasks [N]` xem inbox, `/task <id>` xem chi tiết\n"
    "• Duyệt: bấm ✅/❌ hoặc nhắn `approve <id>` / `reject <id>`\n"
    "• Hỏi giá thị trường: mình chỉ trả lời từ báo giá có ngày, không bịa giá\n"
    "• `/new` bắt đầu hội thoại mới"
)
