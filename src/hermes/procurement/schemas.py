"""Enterprise Procurement Case Agent — domain schemas.

One procurement case: 3+ quotes → 4 parallel analyses → recommendation.
All agent outputs are JSON strings grounded in evidence (quote URIs).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


def now_iso() -> str:
    """Current date in ISO format (YYYY-MM-DD)."""
    return date.today().isoformat()


class Quote(BaseModel):
    vendor: str = ""
    unit_price: float = 0.0
    quantity: int = 0
    total: float = 0.0
    source_uri: str = ""
    raw_text: str = ""
    # Freshness: ISO date (YYYY-MM-DD) the quote was collected, or "DEMO".
    # Grounded-price policy: undated price claims must not pass verification.
    quote_date: str = ""
    is_demo: bool = False
    # Evidence trust fields (P0-2)
    valid_until: str | None = None  # ISO date
    retrieved_at: str = Field(default_factory=now_iso)
    legal_entity: str | None = None
    tax_included: bool = True
    currency: str = "USD"
    incoterms: str | None = None
    status: Literal["VALID", "EXPIRED", "UNKNOWN"] = "UNKNOWN"
    source_hash: str | None = None  # for tamper detection


class VendorStatus(BaseModel):
    vendor: str = ""
    approved: bool = False
    note: str = ""


class ContractTerms(BaseModel):
    vendor: str = ""
    payment: str = ""
    warranty_years: float = 0.0
    sla_hours: float = 0.0
    source_uri: str = ""


class SpecScore(BaseModel):
    vendor: str = ""
    score: float = 0.0
    meets_minimum: bool = True
    notes: str = ""


class RecommendationReason(BaseModel):
    claim: str = ""
    evidence_ref: str = ""


class Recommendation(BaseModel):
    vendor: str = ""
    total_cost: float = 0.0
    reasons: list[RecommendationReason] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    status: str = "DRAFT"  # DRAFT | PENDING_APPROVAL | APPROVED | REJECTED
    # Freshness label: ISO date, date range, or "DEMO" for sample data.
    data_as_of: str = ""

    def to_text(self) -> str:
        lines = [f"Recommended vendor: {self.vendor}", f"Total cost: {self.total_cost}", "Reasons:"]
        for r in self.reasons:
            lines.append(f"- {r.claim} [evidence: {r.evidence_ref}]")
        return "\n".join(lines)

    @classmethod
    def from_text(cls, text: str) -> "Recommendation":
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "vendor" in data:
                return cls(**data)
        except Exception:
            pass
        # fallback: plain-text recommendation, treated as unverified
        return cls(vendor="", total_cost=0.0, reasons=[], evidence_refs=[], status="DRAFT")


class ProcurementCase(BaseModel):
    item: str = "laptop"
    quantity: int = 50
    request_text: str = ""
    quote_uris: list[str] = Field(default_factory=list)
    required_spec: str = ""
