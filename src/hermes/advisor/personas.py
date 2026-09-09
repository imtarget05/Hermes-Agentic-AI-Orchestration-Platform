"""Default advisor personas — mirror specialist-agent pattern of procurement."""
from __future__ import annotations

from .schemas import AdvisorPersona

DEFAULT_PERSONAS: list[AdvisorPersona] = [
    AdvisorPersona(
        name="operations",
        domain="business_operations",
        focus_areas=["quy trình vận hành", "hiệu suất team", "backlog", "inbox"],
        system_prompt=(
            "Cố vấn vận hành: đánh giá quy trình, khâu tắc nghẽn, "
            "ưu tiên việc cần xử lý trước."
        ),
        framework_refs=["priority_matrix", "workflow-optimization"],
    ),
    AdvisorPersona(
        name="finance",
        domain="finance",
        focus_areas=["dòng tiền", "chi phí", "ngân sách", "hoá đơn", "khả năng thanh toán"],
        system_prompt=(
            "Cố vấn tài chính: phân tích chi phí, dòng tiền, rủi ro; "
            "không bịa số liệu; chỉ nhắc số có nguồn."
        ),
        framework_refs=["cash-flow", "budget-review"],
    ),
    AdvisorPersona(
        name="risk",
        domain="risk_and_compliance",
        focus_areas=["rủi ro", "tuân thủ", "hợp đồng", "pháp lý", "bảo hiểm"],
        system_prompt=(
            "Cố vấn rủi ro & tuân thủ: nhấn mạnh bảo hiểm, hợp đồng, "
            "hạn chế trách nhiệm và kiểm soát."
        ),
        framework_refs=["risk-register", "contract-review"],
    ),
    AdvisorPersona(
        name="market",
        domain="market_competition",
        focus_areas=["đối thủ", "thị trường", "positioning", "pricing"],
        system_prompt=(
            "Cố vấn thị trường: so sánh đối thủ, xu hướng, cơ hội; "
            "chỉ nêu dữ kiện có nguồn."
        ),
        framework_refs=["competitive-analysis", "market-positioning"],
    ),
]


def persona_by_name(name: str) -> AdvisorPersona | None:
    for p in DEFAULT_PERSONAS:
        if p.name == name:
            return p
    return None


def get_personas(names: list[str] | None) -> list[AdvisorPersona]:
    if not names:
        return list(DEFAULT_PERSONAS)
    out = [persona_by_name(n) for n in names]
    return [p for p in out if p is not None]