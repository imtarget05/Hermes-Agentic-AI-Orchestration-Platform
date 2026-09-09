"""Evidence mapper — extracts evidence from workflow data."""
from __future__ import annotations

from .models import EvidenceItem


def extract_procurement_evidence(data: dict) -> list[EvidenceItem]:
    """Extract evidence from procurement workflow data."""
    items = []
    evidence_id = 1

    # Quote evidence
    for q in data.get("quotes", []):
        items.append(EvidenceItem(
            id=f"E-{evidence_id:02d}",
            claim=f"Vendor {q.get('vendor', 'N/A')} quote: ${q.get('total', 0):,.0f}",
            source=q.get("source_uri", ""),
            source_uri=q.get("source_uri", ""),
            agent="Price Agent",
            verification="VERIFIED" if q.get("quote_date") else "PENDING",
            confidence=0.9 if q.get("quote_date") else 0.5,
            timestamp=q.get("quote_date", ""),
        ))
        evidence_id += 1

    # Vendor approval evidence
    for q in data.get("quotes", []):
        if q.get("approved"):
            items.append(EvidenceItem(
                id=f"E-{evidence_id:02d}",
                claim=f"Vendor {q.get('vendor', 'N/A')} is approved",
                source="vendors.json",
                agent="Vendor Agent",
                verification="VERIFIED",
                confidence=0.95,
            ))
            evidence_id += 1

    # Spec evaluation evidence
    for vendor, score in data.get("spec_scores", {}).items():
        meets = "meets" if score.get("meets_minimum", False) else "does not meet"
        items.append(EvidenceItem(
            id=f"E-{evidence_id:02d}",
            claim=f"{vendor} {meets} specification requirements",
            source="spec_evaluation",
            agent="Spec Agent",
            verification="VERIFIED" if score.get("score", 0) > 0 else "PENDING",
            confidence=score.get("score", 0) / 100,
        ))
        evidence_id += 1

    return items


def extract_generic_evidence(data: dict) -> list[EvidenceItem]:
    """Extract evidence from generic workflow data."""
    items = []
    for i, e in enumerate(data.get("evidence", []), start=1):
        if isinstance(e, dict):
            items.append(EvidenceItem(
                id=f"E-{i:02d}",
                claim=e.get("claim", ""),
                source=e.get("source", ""),
                source_uri=e.get("source_uri", ""),
                agent=e.get("agent", ""),
                verification=e.get("verification", "PENDING"),
                confidence=e.get("confidence", 0.0),
                timestamp=e.get("timestamp", ""),
            ))
    return items


def map_agent_to_evidence(agent_name: str, finding: str, source: str = "") -> EvidenceItem:
    """Create an evidence item from an agent finding."""
    return EvidenceItem(
        claim=finding,
        source=source,
        agent=agent_name,
        verification="PENDING",
        confidence=0.7,
    )
