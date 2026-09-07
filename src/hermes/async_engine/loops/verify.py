"""Loop 5 — VERIFICATION LOOP.

Validate worker output before it is accepted: schema check, evidence check,
quality check. PASS -> aggregate; FAIL -> retry (quality-retryable) or dead-letter.

Wired into the worker between EXECUTE and COMPLETED.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable


class VerificationError(RuntimeError):
    """Raised when worker output fails verification (loop 5 -> loop 6)."""

    def __init__(self, reason: str, retryable: bool = True):
        super().__init__(reason)
        self.retryable = retryable


@dataclass
class VerificationResult:
    passed: bool
    reason: str = ""
    retryable: bool = True


Validator = Callable[[Any], str]  # returns "" if OK, else failure reason


class TaskAwareValidator:
    """Validator that receives the full Task (payload included), not just the
    result string. Used for HERMES-03 independent recomputation: the verifier
    re-derives facts from the ORIGINAL evidence in `task.payload`, so it never
    has to trust another agent's output (agreement ≠ verification)."""

    def __init__(self, fn: Callable[[Any, Any], str]):
        self.fn = fn

    def __call__(self, task, result: Any) -> str:
        try:
            return self.fn(task, result)
        except Exception as e:  # a broken validator must never pass silently
            return f"trust check: validator error: {e}"


def schema_check(result: Any) -> str:
    """Result must be a non-empty string (canonical worker output contract)."""
    if not isinstance(result, str):
        return "schema error: result is not a string"
    if not result.strip():
        return "schema error: empty result"
    return ""


def evidence_check(result: str) -> str:
    """Result must look like evidence (a URI/marker the aggregator can store)."""
    if len(result) < 4:
        return "evidence check: result too short to be evidence"
    return ""


def contains_check(needle: str) -> Validator:
    def _check(result: str) -> str:
        return "" if needle.lower() in result.lower() else f"quality check: missing '{needle}'"
    return _check


# ---- HERMES-03: independent recomputation from source evidence ---------- #

def _recommendation_vendor_and_total(result: str) -> tuple[str, float | None]:
    """Extract (vendor, total) from a Recommendation JSON or verification prose."""
    import json as _json
    data = None
    try:
        data = _json.loads(result) if isinstance(result, str) else None
    except Exception:
        data = None
    if not isinstance(data, dict) or "vendor" not in data:
        data = None  # prose (verification agent) — caller falls back to regex
    if isinstance(data, dict) and data.get("vendor"):
        total = data.get("total")
        try:
            return str(data["vendor"]), float(total) if total is not None else None
        except (TypeError, ValueError):
            return str(data["vendor"]), None
    return "", None


def grounded_vendor_check(task, result: str) -> str:
    """Independently verify a recommendation/verification claim against the
    ORIGINAL quotes in task.payload (HERMES-03).

    Two agents agreeing on a vendor that is NOT in the source quotes is the
    echo-chamber failure mode — this check recomputes from the source and
    rejects it regardless of how many agents concur.
    """
    import re as _re

    payload = getattr(task, "payload", None) or {}
    quotes = payload.get("quotes") if isinstance(payload, dict) else None
    if not isinstance(quotes, list) or not quotes:
        return ""  # no source evidence attached — nothing to recompute against
    vendors = {str(q.get("vendor", "")).lower() for q in quotes
               if isinstance(q, dict) and q.get("vendor")}
    if not vendors:
        return ""
    # claimed vendor: from recommendation JSON, else from prose
    vendor, _total = _recommendation_vendor_and_total(result)
    if not vendor:
        m = _re.search(r'"vendor"\s*:\s*"([^"]+)"', result or "")
        if m:
            vendor = m.group(1)
        else:
            for v in vendors:
                if v in (result or "").lower():
                    vendor = v
                    break
    if not vendor:
        return ""
    if vendor.lower() not in vendors:
        return (f"trust check: recommended vendor '{vendor}' is not in source quotes "
                f"{sorted(vendors)} — echo-chamber/ungrounded claim rejected")
    return ""


def grounded_price_check(task, result: str) -> str:
    """Recompute the winning (lowest-total) quote from source evidence and
    require the recommendation total to match it (HERMES-03)."""
    import json as _json

    payload = getattr(task, "payload", None) or {}
    quotes = payload.get("quotes") if isinstance(payload, dict) else None
    if not isinstance(quotes, list) or not quotes:
        return ""
    totals = {}
    for q in quotes:
        if isinstance(q, dict) and q.get("vendor") and q.get("total") is not None:
            try:
                totals[str(q["vendor"]).lower()] = float(q["total"])
            except (TypeError, ValueError):
                continue
    if not totals:
        return ""
    try:
        data = _json.loads(result) if isinstance(result, str) else None
    except Exception:
        data = None
    if not isinstance(data, dict) or data.get("total") is None or not data.get("vendor"):
        return ""  # prose output — price check handled by grounded_vendor_check
    best_total = min(totals.values())
    try:
        claimed = float(data["total"])
    except (TypeError, ValueError):
        return "trust check: recommendation total is not a number"
    if abs(claimed - best_total) > max(0.01, best_total * 1e-6):
        return (f"trust check: recommendation total {claimed} does not match "
                f"recomputed best quote total {best_total} from source evidence")
    return ""


# Universal contract for every worker output: a non-empty string.
# Domain-specific evidence/quality checks are opt-in via Verifier.by_task_type
# (e.g. require a "DONE" marker on report tasks).
DEFAULT_VALIDATORS: list[Validator] = [schema_check]


def procurement_evidence_check(result: str) -> str:
    """Recommendation must be evidence-grounded: vendor + reasons with evidence_refs."""
    import json as _json
    if "VERIFICATION FAILED" in (result or ""):
        return "quality check: upstream verification failed"
    try:
        data = _json.loads(result) if isinstance(result, str) else None
    except Exception:
        data = None
    if not isinstance(data, dict) or "vendor" not in data:
        # verification agent outputs "VERIFICATION PASSED\n..." text — accept if passed
        if "VERIFICATION PASSED" in (result or ""):
            return ""
        return "quality check: recommendation is not grounded Recommendation JSON"
    if not data.get("vendor"):
        return "quality check: recommendation has no vendor"
    reasons = data.get("reasons") or []
    if not reasons:
        return "quality check: recommendation has no reasons"
    for r in reasons:
        if not (r.get("evidence_ref") if isinstance(r, dict) else False):
            return f"quality check: claim without evidence: {str(r)[:80]}"
    if not data.get("evidence_refs"):
        return "quality check: recommendation has no evidence_refs"
    return ""


# P0-2: Evidence Trust Validators


def _parse_iso_date(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return date.fromisoformat(d.strip())
    except ValueError:
        return None


def _compute_file_hash(path: str) -> str | None:
    """Compute SHA256 hash of a file for source integrity check."""
    try:
        from pathlib import Path
        p = Path(path)
        if not p.exists():
            return None
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        return None


def evidence_trust_check(result: str) -> str:
    """Validate Quote trust fields: valid_until > now, legal_entity present, tax_included boolean, currency consistent."""
    import json as _json
    try:
        data = _json.loads(result) if isinstance(result, str) else None
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    # Only validate if this looks like a Quote object
    if "vendor" not in data and "unit_price" not in data:
        return ""
    today = date.today()
    # valid_until > now
    vu = _parse_iso_date(data.get("valid_until"))
    if vu and vu < today:
        return f"trust check: quote expired (valid_until={data.get('valid_until')})"
    # legal_entity present
    if not data.get("legal_entity"):
        return "trust check: missing legal_entity"
    # tax_included must be boolean
    if "tax_included" in data and not isinstance(data.get("tax_included"), bool):
        return "trust check: tax_included must be boolean"
    # currency consistent (basic ISO 4217 check: 3 uppercase letters)
    curr = data.get("currency", "USD")
    if not isinstance(curr, str) or len(curr) != 3 or not curr.isupper():
        return f"trust check: invalid currency code: {curr}"
    return ""


def quote_validity_check(result: str) -> str:
    """Compute quote status from valid_until vs now. Updates status field."""
    import json as _json
    try:
        data = _json.loads(result) if isinstance(result, str) else None
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    if "vendor" not in data and "unit_price" not in data:
        return ""
    today = date.today()
    vu = _parse_iso_date(data.get("valid_until"))
    if vu is None:
        data["status"] = "UNKNOWN"
    elif vu < today:
        data["status"] = "EXPIRED"
    else:
        data["status"] = "VALID"
    # Return updated JSON so caller can persist
    return _json.dumps(data)


def source_integrity_check(result: str) -> str:
    """Verify source_hash matches current file (optional, only if source_uri and source_hash present)."""
    import json as _json
    try:
        data = _json.loads(result) if isinstance(result, str) else None
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    source_uri = data.get("source_uri")
    source_hash = data.get("source_hash")
    if not source_uri or not source_hash:
        return ""  # optional check — skip if not provided
    current_hash = _compute_file_hash(source_uri)
    if current_hash is None:
        return "integrity check: source file not found"
    if current_hash != source_hash:
        return f"integrity check: source hash mismatch (expected {source_hash[:16]}..., got {current_hash[:16]}...)"
    return ""


PROCUREMENT_VALIDATORS: dict[str, list[Validator]] = {
    "verification": [procurement_evidence_check,
                     TaskAwareValidator(grounded_vendor_check)],
    "analysis": [procurement_evidence_check,
                 TaskAwareValidator(grounded_vendor_check),
                 TaskAwareValidator(grounded_price_check)],
}


class Verifier:
    """Runs a validator chain; first failure wins (fail fast, fail loudly).

    Supports both plain `Validator(result)` callables and `TaskAwareValidator`
    callables that recompute from the task's original evidence (HERMES-03).
    """

    def __init__(self, validators: list[Validator] | None = None,
                 by_task_type: dict[str, list[Validator]] | None = None,
                 max_length: int = 100_000):
        self.validators = validators if validators is not None else list(DEFAULT_VALIDATORS)
        self.by_task_type = by_task_type or {}
        self.max_length = max_length

    def verify(self, task, result: Any) -> VerificationResult:
        if isinstance(result, str) and len(result) > self.max_length:
            return VerificationResult(False, "schema error: result exceeds max length")
        for v in self.validators + self.by_task_type.get(task.task_type, []):
            reason = v(task, result) if isinstance(v, TaskAwareValidator) else v(result)
            if reason:
                return VerificationResult(False, reason, retryable="quality" in reason)
        return VerificationResult(True)
