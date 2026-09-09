"""Decision Card renderer — standard component at end of every report."""
from __future__ import annotations

from .models import DecisionCard


def render_decision_card_text(card: DecisionCard) -> str:
    """Render decision card as plain text (for Telegram messages)."""
    lines = [
        "┌───────────────────────────────────────────────┐",
        "│ HERMES DECISION                               │",
        "├───────────────────────────────────────────────┤",
        f"│ Recommendation: {card.recommendation:<28}│",
        f"│ Decision: {card.decision:<33}│",
    ]

    if card.confidence:
        lines.append(f"│ Confidence: {card.confidence:.0%}{' ' * (31 - len(f'{card.confidence:.0%}'))}│")
    else:
        lines.append("│ Confidence: N/A                               │")

    lines.extend([
        f"│ Policy: {card.policy_status:<35}│",
        f"│ Evidence: {card.evidence_count} sources ({card.verified_count} verified){' ' * (24 - len(str(card.evidence_count)) - len(str(card.verified_count)))}│",
        f"│ Risks: {card.risk_count:<36}│",
    ])

    if card.approver:
        approver_line = f"│ Human Approval: {card.approver} — {card.approved_at}"
        lines.append(f"{approver_line:<48}│")
    else:
        lines.append("│ Human Approval: Pending                       │")

    lines.extend([
        f"│ Execution: {card.execution_status:<31}│",
        "└───────────────────────────────────────────────┘",
    ])

    return "\n".join(lines)


def render_decision_card_markdown(card: DecisionCard) -> str:
    """Render decision card as markdown (for documents)."""
    lines = [
        "## HERMES DECISION",
        "",
        f"- **Recommendation:** {card.recommendation}",
        f"- **Decision:** {card.decision}",
    ]

    if card.confidence:
        lines.append(f"- **Confidence:** {card.confidence:.0%}")

    lines.extend([
        f"- **Policy:** {card.policy_status}",
        f"- **Evidence:** {card.evidence_count} sources ({card.verified_count} verified)",
        f"- **Risks:** {card.risk_count}",
    ])

    if card.approver:
        lines.append(f"- **Approved by:** {card.approver} on {card.approved_at}")
    else:
        lines.append("- **Approved by:** Pending")

    lines.append(f"- **Execution:** {card.execution_status}")

    return "\n".join(lines)
