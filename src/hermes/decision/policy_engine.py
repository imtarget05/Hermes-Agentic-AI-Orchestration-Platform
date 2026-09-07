"""Deterministic Decision Policy Engine.

Config-driven policy engine replacing LLM-based scoring.
Loads policy from YAML, scores vendors deterministically,
applies hard constraints, selects best vendor.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ..procurement.schemas import (
    ContractTerms,
    Quote,
    Recommendation,
    RecommendationReason,
    SpecScore,
    VendorStatus,
)


class WeightConfig(BaseModel):
    price: float = 0.40
    warranty_years: float = 0.20
    sla_hours: float = 0.15
    vendor_score: float = 0.15
    payment_terms: float = 0.10


class ConstraintsConfig(BaseModel):
    min_warranty_years: float = 3
    max_sla_hours: float = 4
    required_payment_terms: list[str] = Field(default_factory=lambda: ["Net 30", "Net 45"])
    min_vendor_score: float = 70


class VendorTiersConfig(BaseModel):
    TIER_1: list[str] = Field(default_factory=lambda: ["Dell", "Lenovo", "HP"])
    TIER_2: list[str] = Field(default_factory=list)


class VendorTierBonusConfig(BaseModel):
    TIER_1: float = 0.05
    TIER_2: float = 0.0


class DisqualifierConfig(BaseModel):
    field: str
    operator: str  # lt, gt, lte, gte, eq, ne, in, not_in
    value: Any


class DecisionPolicy(BaseModel):
    version: str = "1.0"
    weights: WeightConfig = Field(default_factory=WeightConfig)
    constraints: ConstraintsConfig = Field(default_factory=ConstraintsConfig)
    vendor_tiers: VendorTiersConfig = Field(default_factory=VendorTiersConfig)
    vendor_tier_bonus: VendorTierBonusConfig = Field(default_factory=VendorTierBonusConfig)
    disqualifiers: list[DisqualifierConfig] = Field(default_factory=list)


@dataclass
class ScoredVendor:
    vendor: str
    quote: Quote
    vendor_status: VendorStatus
    contract_terms: ContractTerms
    spec_score: SpecScore
    price_score: float
    warranty_score: float
    sla_score: float
    vendor_score: float
    payment_score: float
    tier_bonus: float
    total_score: float
    disqualified: bool = False
    disqualification_reasons: list[str] = Field(default_factory=list)


class PolicyEngine:
    """Deterministic policy engine for vendor selection."""

    def __init__(self, policy: DecisionPolicy | None = None):
        self.policy = policy or DecisionPolicy()

    @classmethod
    def load_policy(cls, path: str | Path) -> "PolicyEngine":
        """Load policy from YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        policy_data = data.get("decision_policy", data)
        policy = DecisionPolicy(**policy_data)
        return cls(policy)

    def _get_vendor_tier(self, vendor: str) -> str:
        """Determine vendor tier."""
        for tier, vendors in self.policy.vendor_tiers.model_dump().items():
            if vendor in vendors:
                return tier
        return "TIER_2"

    def _get_tier_bonus(self, vendor: str) -> float:
        """Get tier bonus for vendor."""
        tier = self._get_vendor_tier(vendor)
        return getattr(self.policy.vendor_tier_bonus, tier, 0.0)

    def _normalize_price(self, total: float, all_totals: list[float]) -> float:
        """Normalize price: inverse, lower=better. Returns 0-100 scale."""
        if not all_totals or total <= 0:
            return 0.0
        min_total = min(all_totals)
        max_total = max(all_totals)
        if max_total == min_total:
            return 100.0
        # Inverse: lower price = higher score
        return 100.0 * (1.0 - (total - min_total) / (max_total - min_total))

    def _normalize_warranty(self, warranty_years: float) -> float:
        """Normalize warranty: higher=better. Returns 0-100 scale."""
        # Cap at 5 years for normalization
        capped = min(warranty_years, 5.0)
        return min(100.0, capped * 20.0)  # 5 years = 100

    def _normalize_sla(self, sla_hours: float) -> float:
        """Normalize SLA: inverse, lower=better. Returns 0-100 scale."""
        if sla_hours <= 0:
            return 0.0
        # Cap at 24 hours for normalization
        capped = min(sla_hours, 24.0)
        return 100.0 * (1.0 - (capped - 1.0) / 23.0)  # 1h=100, 24h=0

    def _normalize_vendor_score(self, vendor_score: float) -> float:
        """Normalize vendor score: 0-100 scale."""
        return max(0.0, min(100.0, vendor_score))

    def _normalize_payment(self, payment: str) -> float:
        """Normalize payment terms. Returns 0-100 scale."""
        payment = payment.strip() if payment else ""
        mapping = {
            "Net 30": 100.0,
            "Net 45": 80.0,
            "Net 60": 60.0,
        }
        return mapping.get(payment, 40.0)

    def _check_disqualifiers(
        self,
        quote: Quote,
        vendor_status: VendorStatus,
        contract_terms: ContractTerms,
        spec_score: SpecScore,
    ) -> tuple[bool, list[str]]:
        """Check hard disqualifiers. Returns (disqualified, reasons)."""
        reasons = []
        data = {
            "warranty_years": contract_terms.warranty_years,
            "sla_hours": contract_terms.sla_hours,
            "vendor_score": spec_score.score,
            "payment": contract_terms.payment,
        }

        for dq in self.policy.disqualifiers:
            field_val = data.get(dq.field)
            if field_val is None:
                continue

            op = dq.operator
            val = dq.value

            disqualified = False
            if op == "lt" and field_val < val:
                disqualified = True
            elif op == "gt" and field_val > val:
                disqualified = True
            elif op == "lte" and field_val <= val:
                disqualified = True
            elif op == "gte" and field_val >= val:
                disqualified = True
            elif op == "eq" and field_val == val:
                disqualified = True
            elif op == "ne" and field_val != val:
                disqualified = True
            elif op == "in" and field_val in val:
                disqualified = True
            elif op == "not_in" and field_val not in val:
                disqualified = True

            if disqualified:
                reasons.append(f"{dq.field} {dq.operator} {dq.value} (value: {field_val})")

        return len(reasons) > 0, reasons

    def _check_constraints(
        self,
        contract_terms: ContractTerms,
        spec_score: SpecScore,
        spec_assessed: bool = True,
    ) -> tuple[bool, list[str]]:
        """Check policy constraints. Returns (passes, reasons)."""
        reasons = []
        c = self.policy.constraints

        if contract_terms.warranty_years < c.min_warranty_years:
            reasons.append(f"warranty_years {contract_terms.warranty_years} < min {c.min_warranty_years}")

        if contract_terms.sla_hours > c.max_sla_hours:
            reasons.append(f"sla_hours {contract_terms.sla_hours} > max {c.max_sla_hours}")

        if contract_terms.payment not in c.required_payment_terms:
            reasons.append(f"payment '{contract_terms.payment}' not in required terms {c.required_payment_terms}")

        # The minimum-vendor-score constraint is meaningful ONLY when a vendor was
        # actually scored against a required spec. If no spec assessment exists
        # (no spec was requested, or spec extraction failed), a default score of 0
        # must not disqualify every vendor for a requirement that was never given.
        if spec_assessed and spec_score.score < c.min_vendor_score:
            reasons.append(f"vendor_score {spec_score.score} < min {c.min_vendor_score}")

        return len(reasons) == 0, reasons

    def score_vendor(
        self,
        quote: Quote,
        vendor_status: VendorStatus,
        contract_terms: ContractTerms,
        spec_score: SpecScore,
        all_quotes: list[Quote] | None = None,
        spec_assessed: bool = True,
    ) -> ScoredVendor:
        """Score a single vendor against the policy."""
        all_totals = [q.total for q in all_quotes] if all_quotes else [quote.total]

        price_score = self._normalize_price(quote.total, all_totals)
        warranty_score = self._normalize_warranty(contract_terms.warranty_years)
        sla_score = self._normalize_sla(contract_terms.sla_hours)
        vendor_score_norm = self._normalize_vendor_score(spec_score.score)
        payment_score = self._normalize_payment(contract_terms.payment)
        tier_bonus = self._get_tier_bonus(quote.vendor)

        w = self.policy.weights
        total_score = (
            w.price * price_score
            + w.warranty_years * warranty_score
            + w.sla_hours * sla_score
            + w.vendor_score * vendor_score_norm
            + w.payment_terms * payment_score
            + tier_bonus
        )

        dq_flag, dq_reasons = self._check_disqualifiers(
            quote, vendor_status, contract_terms, spec_score
        )
        constraint_flag, constraint_reasons = self._check_constraints(
            contract_terms, spec_score, spec_assessed=spec_assessed
        )

        disqualified = dq_flag or not constraint_flag
        all_reasons = dq_reasons + constraint_reasons

        return ScoredVendor(
            vendor=quote.vendor,
            quote=quote,
            vendor_status=vendor_status,
            contract_terms=contract_terms,
            spec_score=spec_score,
            price_score=price_score,
            warranty_score=warranty_score,
            sla_score=sla_score,
            vendor_score=vendor_score_norm,
            payment_score=payment_score,
            tier_bonus=tier_bonus,
            total_score=total_score,
            disqualified=disqualified,
            disqualification_reasons=all_reasons,
        )

    def apply_constraints(self, scored_vendors: list[ScoredVendor]) -> list[ScoredVendor]:
        """Filter out disqualified vendors."""
        return [sv for sv in scored_vendors if not sv.disqualified]

    def select_best(self, scored_vendors: list[ScoredVendor]) -> ScoredVendor | None:
        """Select highest-scoring non-disqualified vendor."""
        eligible = self.apply_constraints(scored_vendors)
        if not eligible:
            return None
        return max(eligible, key=lambda sv: sv.total_score)

    def build_recommendation(
        self,
        best: ScoredVendor,
        all_scored: list[ScoredVendor],
    ) -> Recommendation:
        """Build Recommendation from best scored vendor."""
        reasons: list[RecommendationReason] = []
        evidence_refs: list[str] = []

        # Price reason
        reasons.append(RecommendationReason(
            claim=f"lowest total cost ${best.quote.total:,.0f}",
            evidence_ref=best.quote.source_uri or "quotes",
        ))
        evidence_refs.append(best.quote.source_uri or "quotes")

        # Vendor approval
        if best.vendor_status.approved:
            reasons.append(RecommendationReason(
                claim="approved vendor",
                evidence_ref=f"vendors.json:{best.vendor}",
            ))
            evidence_refs.append(f"vendors.json:{best.vendor}")

        # Warranty
        if best.contract_terms.warranty_years > 0:
            reasons.append(RecommendationReason(
                claim=f"{best.contract_terms.warranty_years:g}-year warranty",
                evidence_ref=best.contract_terms.source_uri or "quotes",
            ))
            evidence_refs.append(best.contract_terms.source_uri or "quotes")

        # Payment terms
        if best.contract_terms.payment:
            reasons.append(RecommendationReason(
                claim=f"payment {best.contract_terms.payment}",
                evidence_ref=best.contract_terms.source_uri or "quotes",
            ))
            evidence_refs.append(best.contract_terms.source_uri or "quotes")

        # SLA
        if best.contract_terms.sla_hours > 0:
            reasons.append(RecommendationReason(
                claim=f"SLA {best.contract_terms.sla_hours:g} hours",
                evidence_ref=best.contract_terms.source_uri or "quotes",
            ))
            evidence_refs.append(best.contract_terms.source_uri or "quotes")

        # Spec score
        if best.spec_score.score > 0:
            reasons.append(RecommendationReason(
                claim=f"spec score {best.spec_score.score:.0f}/100",
                evidence_ref=best.spec_score.notes or "spec",
            ))
            evidence_refs.append(best.spec_score.notes or "spec")

        # Tier bonus
        if best.tier_bonus > 0:
            tier = self._get_vendor_tier(best.vendor)
            reasons.append(RecommendationReason(
                claim=f"vendor tier {tier} bonus",
                evidence_ref="policy.yaml",
            ))
            evidence_refs.append("policy.yaml")

        # Deduplicate evidence refs
        evidence_refs = list(dict.fromkeys(evidence_refs))

        # Data freshness
        data_as_of = best.quote.quote_date if best.quote.quote_date else "DEMO"
        if best.quote.is_demo:
            data_as_of = "DEMO"

        return Recommendation(
            vendor=best.vendor,
            total_cost=best.quote.total,
            reasons=reasons,
            evidence_refs=evidence_refs,
            status="PENDING_APPROVAL",
            data_as_of=data_as_of,
        )

    def evaluate(
        self,
        quotes: list[Quote],
        vendor_statuses: dict[str, VendorStatus],
        contract_terms: dict[str, ContractTerms],
        spec_scores: dict[str, SpecScore],
    ) -> Recommendation:
        """Full evaluation pipeline: score all, filter, select best, build recommendation."""
        scored = []
        for quote in quotes:
            vs = vendor_statuses.get(quote.vendor, VendorStatus(vendor=quote.vendor))
            ct = contract_terms.get(quote.vendor, ContractTerms(vendor=quote.vendor))
            has_spec = quote.vendor in spec_scores
            ss = spec_scores.get(quote.vendor, SpecScore(vendor=quote.vendor))

            scored.append(self.score_vendor(quote, vs, ct, ss, quotes,
                                            spec_assessed=has_spec))

        best = self.select_best(scored)
        if not best:
            # No eligible vendors - return empty recommendation
            return Recommendation(
                vendor="",
                total_cost=0.0,
                reasons=[],
                evidence_refs=[],
                status="DRAFT",
                data_as_of="",
            )

        return self.build_recommendation(best, scored)
