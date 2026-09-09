"""Output Guardrails — deterministic, per-domain enforcement of GenAI limits.

Mirrors the "Tools & Guardrails" brief: rules that validate agent output before
it reaches the user. Deterministic and offline-first (no LLM dependency) to keep
tests and local runs deterministic — matching the repo's grounded-price policy.
"""
from __future__ import annotations

import re

from .schemas import GuardrailViolation, OutputVerdict

# Words that signal unsupported factual claims (price, figures, dates, deadlines)
# which must be backed by citation/evidence in domains where fabrication is risky.
_DISALLOWED_CLAIM_GLOB = re.compile(
    r"\b(price|cost|\$|usd|total|giá|gia|total cost|figure|deadline|date|founded|"
    r"rating|score|rank)\b",
    re.IGNORECASE,
)

_CITATION_RE = re.compile(r"\[source=[^\]]+\]|\[evidence:[^\]]+\]|\[src:[^\]]+\]")

# A fabricated claim needs a concrete figure (digit or currency symbol) to be
# dangerous; qualitative mentions of "giá"/"cost" without numbers are advisory.
_FIGURE_RE = re.compile(r"\d|\$|usd|vnd|đồng", re.IGNORECASE)


def _has_citation(text: str) -> bool:
    return bool(_CITATION_RE.search(text or ""))


def _no_claim_without_citation(text: str) -> bool:
    """Return True if no line both asserts a concrete figure and lacks a citation."""
    for line in (text or "").splitlines():
        if (_DISALLOWED_CLAIM_GLOB.search(line) and _FIGURE_RE.search(line)
                and not _has_citation(line)):
            return False
    return True


_DOMAIN_RULES: dict[str, dict[str, callable]] = {
    "knowledge": {"claims_must_be_cited": _no_claim_without_citation},
    "advisor": {
        "claims_must_be_cited": _no_claim_without_citation,
    },
    "competitor": {
        "facts_must_be_evidence_backed": _no_claim_without_citation,
    },
    "harness": {},
    "ops": {},
    "procurement": {"facts_must_be_evidence_backed": _no_claim_without_citation},
}


class OutputGuardrail:
    """Runs the configured rules for a domain against a generated output."""

    def __init__(self, rules: dict[str, dict[str, callable]] | None = None):
        self.rules = rules if rules is not None else _DOMAIN_RULES

    def register_rule(self, domain: str, rule_name: str, fn: callable) -> None:
        self.rules.setdefault(domain, {})[rule_name] = fn

    def check(self, domain: str, output: str) -> OutputVerdict:
        verdict = OutputVerdict(domain=domain)
        for rule, fn in (self._rules_for(domain) or {}).items():
            try:
                ok = fn(output)
            except Exception as e:  # noqa: BLE001
                ok = False
                message = f"rule '{rule}' raised: {e}"
                verdict.violations.append(
                    GuardrailViolation(rule=rule, message=message, severity="error"))
            if not ok:
                verdict.passed = False
                verdict.violations.append(
                    GuardrailViolation(rule=rule, message=f"output failed {rule}",
                                       offending_text=(output or "")[:200]))
        return verdict

    def _rules_for(self, domain: str) -> dict[str, callable] | None:
        return self.rules.get(domain)


_DEFAULT_GUARDRAIL = OutputGuardrail()


def check_output(domain: str, output: str) -> OutputVerdict:
    """Module-level convenience: run default guardrails for a domain."""
    return _DEFAULT_GUARDRAIL.check(domain, output)