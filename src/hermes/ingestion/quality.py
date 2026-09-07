"""Ingestion quality validation + stage diagnostics.

Checks for information loss / degradation *before* embedding so a silently
empty or gutted document cannot enter the retrieval corpus unnoticed. Also
exposes the stage-by-stage view (source → extracted → normalized → deduped →
chunked) required to answer "where did the information go?".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .htmlparse import parse_semantic_html
from .model import CanonicalDocument, RawSource

MIN_CONTENT_CHARS = 20


@dataclass
class StageMetric:
    stage: str
    ok: bool
    chars: int
    blocks: int = 0
    issues: list[str] = field(default_factory=list)


@dataclass
class IngestionDiagnostics:
    source_uri: str
    stages: dict[str, StageMetric] = field(default_factory=dict)
    extractors_used: list[str] = field(default_factory=list)
    content_ok: bool = True
    issues: list[str] = field(default_factory=list)

    @property
    def info_lost(self) -> bool:
        return any(not m.ok for m in self.stages.values())

    def summarize(self) -> str:
        lines = [f"source={self.source_uri} content_ok={self.content_ok}"]
        for stage in ("source", "extracted", "normalized", "deduped", "chunked"):
            m = self.stages.get(stage)
            if m:
                lines.append(f"  {stage}: ok={m.ok} chars={m.chars} blocks={m.blocks}")
                lines.extend(f"    ! {i}" for i in m.issues)
        if self.issues:
            lines.extend(f"  issue: {i}" for i in self.issues)
            lines.append(f"  extractors={self.extractors_used}")
        return "\n".join(lines)


class QualityValidator:
    """Rules for detecting ingestion degradation (general-purpose thresholds)."""

    def __init__(self, min_content_chars: int = MIN_CONTENT_CHARS):
        self.min_content_chars = min_content_chars

    def validate(self, doc: CanonicalDocument, raw: RawSource) -> IngestionDiagnostics:
        diag = IngestionDiagnostics(source_uri=raw.source_uri,
                                    extractors_used=list(doc.strategy_evidence))
        source_chars = len((raw.text or "").strip())
        diag.stages["source"] = StageMetric("source", True, source_chars)

        # Structural emptiness: no visible text AND no structured payload values.
        has_visible = bool((raw.text or raw.html or "").strip())
        has_structured = bool(raw.payload is not None and (
            (raw.payload if not isinstance(raw.payload, dict) else bool(raw.payload))))
        source_empty = not has_visible and not has_structured

        extracted = doc.to_text()
        extracted_chars = len(extracted.strip())
        extracted_ok, extracted_issues = True, []
        if source_empty:
            extracted_ok = False
            extracted_issues.append("source contains no visible text or structured content")
        elif extracted_chars == 0:
            extracted_ok = False
            extracted_issues.append("extracted content is empty")
        elif extracted_chars < self.min_content_chars:
            extracted_ok = False
            extracted_issues.append(f"extracted content abnormally short "
                                    f"({extracted_chars} < {self.min_content_chars})")
        diag.stages["extracted"] = StageMetric("extracted", extracted_ok,
                                               extracted_chars, doc.block_count(),
                                               extracted_issues)

        normalized = doc.to_text(normalized=True)
        diag.stages["normalized"] = StageMetric("normalized", True,
                                                len(normalized.strip()), doc.block_count())

        dedup_chars = len(extracted.strip())
        diag.stages["deduped"] = StageMetric("deduped", True, dedup_chars, doc.block_count())

        # Structured-information-preservation check: if the source carried a dict
        # payload, the canonical doc must have produced field content, else loss.
        if raw.payload is not None:
            field_blocks = [b for b in doc.blocks if b.kind == "field"]
            if not field_blocks:
                diag.issues.append("structured payload present but no field blocks extracted")
                diag.content_ok = False

        # HTML semantic-preservation check: if the source carried raw HTML with
        # machine-readable semantic content (title/meta/JSON-LD in the response),
        # the canonical doc must preserve it. Visible-text-only extraction that
        # drops it is exactly the loss this architecture guards against.
        html = (getattr(raw, "html", None) or "") if raw.kind == "html" else ""
        if html.strip():
            sem = parse_semantic_html(html)
            had_semantic = bool(sem.title.strip() or sem.meta or sem.jsonld)
            preserved = {"html_semantic", "structured", "metadata"} & set(doc.strategy_evidence)
            if had_semantic and not preserved:
                diag.issues.append("html carried title/meta/structured content "
                                   "but none was extracted (information loss)")
                diag.content_ok = False

        # Degradation check: if the raw source had real text but extraction lost it.
        if source_chars > 0 and doc.dynamic:
            diag.issues.append("dynamic/client-rendered content detected; "
                               "a browser renderer is required (safe placeholder kept)")
            diag.content_ok = False

        if diag.stages["extracted"].ok and not doc.blocks:
            diag.issues.append("extraction produced text but no canonical blocks")
            diag.content_ok = False
        if not diag.stages["extracted"].ok:
            diag.content_ok = False
        return diag


def validate_canonical(doc: CanonicalDocument, raw: RawSource,
                       min_content_chars: int = MIN_CONTENT_CHARS) -> IngestionDiagnostics:
    return QualityValidator(min_content_chars=min_content_chars).validate(doc, raw)