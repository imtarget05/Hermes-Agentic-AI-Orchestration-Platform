"""Formatting + LLM helpers for 1:1 chat (sync, token-free testable)."""
from __future__ import annotations

import json
from datetime import date

# Quotes older than this are flagged stale in price answers.
PRICE_FRESH_DAYS = 30

# Hard anti-fabrication rule injected into every user-facing LLM prompt.
# Incident: bot invented MacBook VND prices + wrong specs from memory.
NO_FABRICATION_RULE = (
    "GROUND TRUTH RULE (ưu tiên cao nhất):\n"
    "- KHÔNG BAO GIỜ tự đưa ra con số giá, cấu hình, đời máy cụ thể từ trí nhớ.\n"
    "- Chỉ nêu giá/spec khi dữ liệu được cung cấp trong prompt (báo giá có ngày).\n"
    "- Nếu không có dữ liệu tươi: nói rõ mình không có dữ liệu, nêu dữ liệu "
    "mới nhất mình có + ngày của nó, và hướng dẫn user gửi PDF báo giá.\n"
    "- Không bao giờ xin user đọc giá hộ để mình học — hãy hướng dẫn gửi báo giá."
)


def format_task_list(tasks: list[dict], limit: int = 10) -> str:
    if not tasks:
        return "📥 Inbox trống."
    lines = [f"📥 Inbox ({len(tasks)}):"]
    for t in tasks[:limit]:
        tid = t.get("id", "?")
        st = t.get("status", "?")
        txt = (t.get("text") or "")[:70]
        lines.append(f"• `{tid}` [{st}] {txt}")
    lines.append("\nXem chi tiết: `/task <id>`")
    return "\n".join(lines)


def format_task_detail(task: dict, events: list[dict] | None = None) -> str:
    tid = task.get("id", "?")
    out = [f"📋 `{tid}` [{task.get('status', '?')}]",
           f"Text: {(task.get('text') or '')[:500]}",
           f"Project: {task.get('project', '')}"]
    result = task.get("result") or ""
    if result:
        try:
            body = json.loads(result.split("\n", 1)[-1])
            vendor = body.get("vendor", "")
            total = body.get("total_cost", body.get("total", ""))
            status = body.get("status", "")
            if vendor or total or status:
                out.append(f"Recommendation: {vendor} total={total} status={status}")
            else:
                out.append(f"Result: {result[:1200]}")
        except Exception:
            out.append(f"Result: {result[:1200]}")
    if events:
        out.append("Events:")
        for e in events[-8:]:
            out.append(f"  [{e.get('from')}→{e.get('to')}] {e.get('actor')}: "
                       f"{(e.get('note') or '')[:120]}")
    return "\n".join(out)


def format_recommendation(task_result: str) -> str:
    try:
        body = json.loads((task_result or "").split("\n", 1)[-1])
        vendor = body.get("vendor", "?")
        total = body.get("total_cost", body.get("total", "?"))
        status = body.get("status", "")
        reasons = body.get("reasons", []) or []
        lines = [f"✅ Hoàn tất: {vendor} — total {total} ({status})"]
        for r in reasons[:4]:
            lines.append(f"• {r}")
        fresh = freshness_line(body)
        if fresh:
            lines.append(fresh)
        return "\n".join(lines)
    except Exception:
        return f"✅ Hoàn tất:\n{(task_result or '')[:1500]}"


def freshness_line(rec: dict) -> str:
    """One-line data-freshness label for a recommendation dict."""
    as_of = (rec or {}).get("data_as_of", "")
    if not as_of:
        return ""
    if as_of == "DEMO":
        return "⚠️ Dựa trên số liệu DEMO mẫu — không phải giá thị trường."
    return f"📅 Dựa trên báo giá ngày {as_of}."


def _parse_iso_date(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return date.fromisoformat(d.strip())
    except ValueError:
        return None


def quote_effective_date(quote: dict) -> date | None:
    """Get the effective date for staleness check: valid_until preferred, quote_date as fallback."""
    # Primary: valid_until (expiry date)
    vu = _parse_iso_date(quote.get("valid_until"))
    if vu:
        return vu
    # Fallback: quote_date (collection date)
    return _parse_iso_date(quote.get("quote_date"))


def quote_age_days(quote: dict) -> int | None:
    """Compute age in days from effective date to today."""
    eff = quote_effective_date(quote)
    if eff is None:
        return None
    return (date.today() - eff).days


def price_question_reply(quotes: list[dict] | None) -> str:
    """Deterministic grounded answer to market-price questions (no LLM).

    3 parts: (a) what dated data the bot has, (b) staleness verdict,
    (c) how to get fresh numbers. Never invents a price figure.
    """
    quotes = [q for q in (quotes or []) if q.get("vendor")]
    if not quotes:
        return (
            "🔍 Mình chưa có dữ liệu giá nào trong hệ thống nên không báo giá được.\n"
            "• Gửi PDF báo giá vào chat này (mình parse + gắn ngày tự động), hoặc\n"
            "• Nhắn `mua 50 laptop` để chạy case phân tích (mặc định dùng số liệu DEMO mẫu)."
        )
    if all(q.get("is_demo") for q in quotes):
        return (
            "🔍 Mình chỉ có số liệu DEMO mẫu (Dell/Lenovo/HP) — "
            "không phải giá thị trường, nên không báo giá cụ thể được.\n"
            "• Gửi PDF báo giá thật vào chat này để mình phân tích giá tươi có ngày."
        )
    real = [q for q in quotes if not q.get("is_demo")]
    lines = ["🔍 Giá mình có (từ báo giá đã gửi, có ngày):"]
    stale_any = False
    for q in real[:6]:
        eff_date = quote_effective_date(q)
        qd = eff_date.isoformat() if eff_date else "không rõ ngày"
        age = quote_age_days(q)
        flag = ""
        if age is not None and age > PRICE_FRESH_DAYS:
            flag = f" — ⚠️ đã cũ ({age} ngày)"
            stale_any = True
        lines.append(
            f"• {q.get('vendor', '?')}: {q.get('unit_price', '?')} x "
            f"{q.get('quantity', '?')} = {q.get('total', '?')} (ngày {qd}){flag}")
    if stale_any:
        lines.append(
            f"Báo giá quá {PRICE_FRESH_DAYS} ngày được coi là cũ — "
            "nên gửi PDF báo giá mới để mình cập nhật.")
    lines.append("Muốn so sánh chi tiết: nhắn `mua ...` để chạy DAG phân tích.")
    return "\n".join(lines)


def build_chitchat_prompt(prompt: str, history: list[dict] | None = None) -> str:
    hist = ""
    for h in (history or [])[-6:]:
        hist += f"{h.get('role', 'user')}: {(h.get('text') or '')[:300]}\n"
    return (
        "Bạn là Hermes, trợ lý mua sắm 1:1, trả lời ngắn gọn tiếng Việt.\n"
        f"{NO_FABRICATION_RULE}\n"
        f"{hist}user: {prompt}\nassistant:"
    )


def chitchat_reply(prompt: str, history: list[dict] | None = None) -> str:
    """LLM reply with stub fallback (no creds → deterministic, tests pass)."""
    from ..config import settings
    from ..llm import build_llm
    llm = build_llm(
        settings.llm_provider,
        settings.cloudflare_model or settings.llm_model,
        settings.cloudflare_account_id,
        settings.cloudflare_api_token,
        settings.cloudflare_timeout)
    if llm is None:
        return ("Hermes stub (chưa có LLM creds): mình đã nhận "
                f"“{(prompt or '')[:200]}”. Gửi `mua 50 laptop` để chạy case, "
                "`/tasks` xem inbox, `/help` xem lệnh.")
    try:
        return llm.complete(build_chitchat_prompt(prompt, history))
    except Exception as e:  # noqa: BLE001
        return f"Hermes LLM lỗi (fallback stub): {str(e)[:300]}"
