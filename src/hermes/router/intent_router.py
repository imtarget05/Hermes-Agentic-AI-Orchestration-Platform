"""Intent Router — deterministic, rule-based classification (P0-1).

Classifies user text into Intent and produces a RoutingPlan with:
- required_agents (subset of 6 procurement agents)
- estimated_complexity (low/medium/high)
- estimated_latency_ms (budget for orchestration)

No LLM calls — purely keyword/heuristic based for determinism and testability.
"""
from __future__ import annotations

import re
from typing import Any

from .routing_plan import (
    AGENT_CONTRACT,
    COMPARE_AGENTS,
    FULL_PROCUREMENT_AGENTS,
    SIMPLE_AGENTS,
    Complexity,
    Intent,
    RoutingPlan,
)

# ---- Keyword sets ---------------------------------------------------------

# Simple task keywords (single agent) - EXACT PHRASES to avoid false positives
_SIMPLE_QUOTE_VALIDITY = (
    "hiệu lực", "còn hiệu lực", "valid", "check quote", "quote date",
    "quote valid", "quote validity", "expired", "hết hạn", "expire",
)
_SIMPLE_VENDOR_CHECK = (
    "check vendor", "vendor check", "is vendor approved",
    "vendor status", "duyệt vendor",
)
_SIMPLE_PRICE_CHECK = (
    "price check", "check price", "giá check", "check giá",
)
_SIMPLE_SPEC_CHECK = (
    "spec check", "check spec", "đủ spec", "meets spec",
    "spec score", "đánh giá spec",
)

# Compare task keywords (2-3 agents) - CHECKED BEFORE simple tasks
_COMPARE_KW = (
    "so sánh", "compare", "giá nào rẻ", "which vendor", "vendor comparison",
    "vendor compare", "so sanh", "bảng so sánh", "compare vendors",
    "price comparison", "giá thấp nhất", "cheapest", "rẻ nhất",
    "so sánh giá", "so sanh gia",
)

# Full procurement keywords (6 agents) - broader terms
_FULL_PROCURE_KW = (
    "mua", "procurement", "báo giá", "quote", "mua sắm", "mua sam",
    "laptop", "máy tính", "may tinh", "device", "thiết bị", "thiet bi",
    "dell", "lenovo", "hp", "asus", "acer", "msi",
)

# Spec detail indicators (pushes toward full procurement)
_SPEC_DETAIL_KW = (
    "ram", "cpu", "ssd", "hdd", "bảo hành", "warranty",
    "screen", "màn hình", "man hinh", "gpu", "card màn hình",
    "processor", "xử lý", "xu ly", "core", "thread", "generation",
    "thế hệ", "the he", "inch", "weight", "trọng lượng", "trong luong",
    "battery", "pin", "port", "thunderbolt", "hdmi", "usb-c", "usbc",
    "wifi", "bluetooth", "os", "hệ điều hành", "he dieu hanh", "windows",
    "linux", "ubuntu", "docker", "kubernetes", "k8s", "devops", "ml",
    "ai", "training", "inference", "gaming", "render", "video edit",
)

# Help keywords
_HELP_KW = (
    "help", "hỗ trợ", "huong dan", "hướng dẫn", "/help", "/start",
    "bắt đầu", "bat dau", "xin chào", "xin chao", "hello", "hi",
)

# Inbox keywords
_INBOX_KW = (
    "inbox", "danh sách", "danh sach", "tasks", "/tasks", "task list",
    "danh sách task", "danh sach task",
)

# Task detail keywords
_TASK_DETAIL_KW = (
    "/task", "task ", "chi tiết", "chi tiet", "detail",
)

# Approval keywords (more specific pattern) - includes Vietnamese ệ (U+1EC7), ừ (U+1EEB), ố (U+1ED1)
_APPROVAL_RE = re.compile(
    r"^\s*(approve|duy[eêệ]t|reject|t[uừ]\s*ch[oôố]i)\s*[:\-]?\s*([A-Za-z0-9_\-]{4,64})",
    re.IGNORECASE
)

# Price question keywords (market price queries)
_PRICE_KW = (
    "giá", "gia", "price", "pricing", "bảng giá", "bang gia",
    "giá cả", "gia ca", "thị trường", "thi truong", "market",
    "chi phí", "chi phi", "cost", "đắt", "dat", "rẻ", "re",
)
_PRICE_QMARK = (
    "bao nhiêu", "bao nhieu", "thế nào", "the nao", "không?", "khong?",
    "?", "hiện tại", "hien tai", "hiện nay", "hien nay",
    "mới nhất", "moi nhat", "cập nhật", "cap nhat",
    "update", "giá thị trường", "gia thi truong",
)


def _count_quotes(text: str, quotes: list[dict[str, Any]] | None = None) -> int:
    """Count quotes from explicit mention or provided list."""
    if quotes:
        return len(quotes)
    # Heuristic: look for quote-like patterns in text
    count = 0
    low = text.lower()
    # Count vendor mentions as proxy for quotes
    for vendor in ("dell", "lenovo", "hp", "asus", "acer", "msi"):
        if vendor in low:
            count += 1
    # Count explicit "quote" / "báo giá" mentions
    count += low.count("quote")
    count += low.count("báo giá")
    count += low.count("bao gia")
    return max(count, 0)


def _has_spec_details(text: str) -> bool:
    """Check if text contains detailed spec requirements."""
    low = text.lower()
    return any(kw in low for kw in _SPEC_DETAIL_KW)


def _is_simple_task(text: str) -> tuple[bool, list[str] | None]:
    """Check for simple single-agent tasks. Returns (matched, agents)."""
    low = text.lower()
    if any(kw in low for kw in _SIMPLE_QUOTE_VALIDITY):
        return True, SIMPLE_AGENTS["quote_validity"]
    if any(kw in low for kw in _SIMPLE_VENDOR_CHECK):
        return True, SIMPLE_AGENTS["vendor_check"]
    if any(kw in low for kw in _SIMPLE_PRICE_CHECK):
        return True, SIMPLE_AGENTS["price_check"]
    if any(kw in low for kw in _SIMPLE_SPEC_CHECK):
        return True, SIMPLE_AGENTS["spec_check"]
    return False, None


def _is_compare_task(text: str) -> bool:
    """Check for comparison tasks (2-3 agents)."""
    low = text.lower()
    return any(kw in low for kw in _COMPARE_KW)


def _is_full_procurement(text: str, quote_count: int, has_spec: bool) -> bool:
    """Check for full procurement decision (6 agents)."""
    low = text.lower()
    has_procure_kw = any(kw in low for kw in _FULL_PROCURE_KW)
    # Heuristic: procurement intent + (multiple quotes OR detailed specs)
    return has_procure_kw and (quote_count >= 2 or has_spec or quote_count == 0)


def _is_price_question(text: str) -> bool:
    """Check for market price questions (grounded-price policy)."""
    low = text.lower()
    if not any(kw in low for kw in _PRICE_KW):
        return False
    return any(m in low for m in _PRICE_QMARK)


def _is_help(text: str) -> bool:
    low = text.lower().strip()
    return low in _HELP_KW or any(low.startswith(kw) for kw in ("/help", "/start"))


def _is_inbox(text: str) -> bool:
    low = text.lower().strip()
    return any(kw in low for kw in _INBOX_KW)


def _is_task_detail(text: str) -> bool:
    low = text.lower().strip()
    return any(low.startswith(kw) for kw in _TASK_DETAIL_KW)


def _is_approval(text: str) -> bool:
    """Check for approval commands (specific pattern: verb + request_id)."""
    return _APPROVAL_RE.match(text or "") is not None


# ---- Public API -----------------------------------------------------------

def classify_intent(
    text: str,
    session_state: str = "idle",
    quotes: list[dict[str, Any]] | None = None,
) -> Intent:
    """Classify user text into Intent (deterministic, no LLM)."""
    t = (text or "").strip()
    low = t.lower()

    if not t:
        return Intent.HELP

    # Priority order: explicit commands first
    if _is_help(t):
        return Intent.HELP

    if _is_inbox(t):
        return Intent.INBOX

    if _is_task_detail(t):
        return Intent.INBOX  # treated as inbox detail

    if _is_approval(t):
        return Intent.APPROVAL

    # Grounded-price policy: market-price questions take priority
    # over procurement followup (asking price ≠ answering spec prompt)
    if _is_price_question(t):
        return Intent.PRICE_QUESTION

    # Session-state dependent
    if session_state == "awaiting_spec":
        return Intent.PROCUREMENT_DECISION  # will be treated as followup

    quote_count = _count_quotes(t, quotes)
    has_spec = _has_spec_details(t)

    # Compare task check (2-3 agents) - BEFORE simple task
    if _is_compare_task(t):
        return Intent.COMPARE_TASK

    # Simple task check (single agent)
    simple_matched, simple_agents = _is_simple_task(t)
    if simple_matched:
        return Intent.SIMPLE_TASK

    # Full procurement check (6 agents)
    if _is_full_procurement(t, quote_count, has_spec):
        return Intent.PROCUREMENT_DECISION

    # Short procurement-like but no spec yet → will prompt for spec
    if any(kw in low for kw in _FULL_PROCURE_KW):
        return Intent.PROCUREMENT_DECISION

    # Session carry-over
    if session_state in ("awaiting_approval", "running"):
        return Intent.CHITCHAT

    return Intent.CHITCHAT


def build_routing_plan(
    intent: Intent,
    text: str = "",
    quotes: list[dict[str, Any]] | None = None,
) -> RoutingPlan:
    """Build RoutingPlan from classified intent + context."""
    quote_count = _count_quotes(text, quotes)
    has_spec = _has_spec_details(text)
    reasoning_parts: list[str] = []

    if intent == Intent.SIMPLE_TASK:
        simple_matched, agents = _is_simple_task(text)
        if simple_matched and agents:
            reasoning_parts.append(f"simple_task:{agents[0]}")
            return RoutingPlan(
                intent=intent,
                required_agents=agents,
                estimated_complexity=Complexity.LOW,
                estimated_latency_ms=2000,
                quote_count=quote_count,
                has_spec_details=has_spec,
                reasoning="; ".join(reasoning_parts),
            )
        # Fallback: contract agent for general simple queries
        return RoutingPlan(
            intent=intent,
            required_agents=[AGENT_CONTRACT],
            estimated_complexity=Complexity.LOW,
            estimated_latency_ms=2000,
            quote_count=quote_count,
            has_spec_details=has_spec,
            reasoning="simple_task:default_contract",
        )

    if intent == Intent.COMPARE_TASK:
        reasoning_parts.append(f"compare:quotes={quote_count},spec={has_spec}")
        return RoutingPlan(
            intent=intent,
            required_agents=COMPARE_AGENTS,
            estimated_complexity=Complexity.MEDIUM,
            estimated_latency_ms=8000,
            quote_count=quote_count,
            has_spec_details=has_spec,
            reasoning="; ".join(reasoning_parts),
        )

    if intent == Intent.PROCUREMENT_DECISION:
        reasoning_parts.append(f"full_procurement:quotes={quote_count},spec={has_spec}")
        return RoutingPlan(
            intent=intent,
            required_agents=FULL_PROCUREMENT_AGENTS,
            estimated_complexity=Complexity.HIGH,
            estimated_latency_ms=30000,
            quote_count=quote_count,
            has_spec_details=has_spec,
            reasoning="; ".join(reasoning_parts),
        )

    # Non-procurement intents: no agents needed
    return RoutingPlan(
        intent=intent,
        required_agents=[],
        estimated_complexity=Complexity.LOW,
        estimated_latency_ms=500,
        quote_count=quote_count,
        has_spec_details=has_spec,
        reasoning=f"intent:{intent.value}",
    )


def route(
    text: str,
    session_state: str = "idle",
    quotes: list[dict[str, Any]] | None = None,
) -> RoutingPlan:
    """Main entry: classify intent and build routing plan in one call."""
    intent = classify_intent(text, session_state, quotes)
    return build_routing_plan(intent, text, quotes)
