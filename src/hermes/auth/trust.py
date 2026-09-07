"""HERMES-11 — Memory Trust Tier + Trust Classification (G-01 extension).

If an external memory/skill store (e.g. TencentDB Agent Memory) is ever
integrated, its assets (Chat Memory, Skill, L3 Persona) are UNTRUSTED CONTENT —
exactly like a retrieved document or a tool result. They must NEVER
auto-promote into an instruction or a tool authorization.

This module makes that boundary explicit and callable (not just a comment):
  * every memory asset is classified as DATA by default, never INSTRUCTION;
  * promotion RAW_STORE -> REVIEWED/APPROVED is FAIL-CLOSED (denied unless an
    explicit operator review signal is present);
  * nothing in this module grants any permission by itself — it only labels.

No external store is integrated today, so this is scaffolding: it adds no
runtime behavior on the live path, but makes the trust boundary
regression-testable before any store is wired in.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class MemoryTier(str, Enum):
    """Trust tiers for a memory asset (HERMES-11).

    RAW_STORE  — ingested/unreviewed content. Treated as untrusted data.
    REVIEWED   — passed a human/rule review gate. Still data, not authority.
    APPROVED   — operator-owned (e.g. a published skill / L3 persona). The only
                 tier that MAY carry declared permissions, and even then only
                 via an explicit ACL (see auth/memory.py), never from content.
    """
    RAW_STORE = "raw_store"
    REVIEWED = "reviewed"
    APPROVED = "approved"


class ContentKind(str, Enum):
    """Is the content DATA (retrieved evidence) or INSTRUCTION (executable)?

    G-01 boundary: memory content is DATA until an explicit, operator-driven
    promotion says otherwise. The platform must never treat a memory asset as
    an instruction just because of how it is phrased.
    """
    DATA = "data"
    INSTRUCTION = "instruction"


@dataclass
class TrustClassification:
    kind: ContentKind
    tier: MemoryTier
    source: str
    requires_review: bool


# Operator signal that unlocks promotion out of RAW_STORE. Absent = denied.
# In production this would be a signed review token / HITL approval; here it is
# an explicit env flag so the gate is observable and testable.
def _review_signal_present() -> bool:
    return os.environ.get("HERMES_MEMORY_TRUST_REVIEWED_ONLY", "").lower() in (
        "1", "true", "yes", "on",
    )


def classify_content(content: str, source: str = "memory") -> TrustClassification:
    """Classify a memory asset (HERMES-11).

    Default posture: everything is DATA in the RAW_STORE tier and requires
    review before it can be treated as anything more trusted. The function
    deliberately does NOT inspect the text for "instruction-like" phrasing —
    content can never promote itself.
    """
    return TrustClassification(
        kind=ContentKind.DATA,
        tier=MemoryTier.RAW_STORE,
        source=source,
        requires_review=True,
    )


def promotion_gate(
    current_tier: MemoryTier,
    requested_tier: MemoryTier,
    *,
    review_ok: bool | None = None,
) -> bool:
    """Fail-closed promotion gate (HERMES-11).

    Returns True only when promotion to `requested_tier` is permitted. Moving
    out of RAW_STORE requires an explicit operator review signal; the function
    never grants promotion on content alone. Downgrades (APPROVED -> RAW_STORE)
    are always allowed (you can always distrust).
    """
    if requested_tier == current_tier:
        return True  # no-op
    # Downgrades are always permitted.
    if requested_tier == MemoryTier.RAW_STORE:
        return True
    # Any upgrade out of RAW_STORE requires an explicit review signal.
    if review_ok is None:
        review_ok = _review_signal_present()
    return bool(review_ok)


def content_requests_instruction(text: str) -> bool:
    """Does the *text itself* look like it is trying to be an instruction?

    This is a DETECTION aid only (defense-in-depth, like the injection
    patterns in guardrails.py). It never authorizes anything — a True result
    just means the asset should stay in RAW_STORE and be flagged for review.
    A memory asset can never use this to promote itself.
    """
    if not text:
        return False
    low = text.lower()
    cues = (
        "ignore", "you are now", "system prompt", "new instruction",
        "from now on", "override", "disregard", "instead,", "as a",
    )
    return any(c in low for c in cues)
