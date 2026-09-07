"""General-purpose, pluggable extraction strategies.

Principles:
- Each extractor is *independent* and *general* — it knows nothing about a
  specific domain, website, product, vendor, or field set.
- Extraction never throws: a failing strategy is skipped and the pipeline
  falls through to the next one instead of aborting.
- Multiple strategies merge into a single canonical document (source
  preservation), with provenance kept for diagnostics.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .htmlparse import meta_to_blocks, parse_semantic_html
from .model import CanonicalDocument, ContentBlock, RawSource
from .normalize import normalize_key


@dataclass
class ExtractionResult:
    doc: CanonicalDocument
    ok: bool
    error: str = ""


class BaseExtractor:
    """Base class for a general-purpose extractor.

    Subclasses define ``name`` and implement :meth:`extract`; the method must
    catch its own exceptions and return an ``ExtractionResult`` (never raise).

    ``rescue`` strategies (fallback / dynamic guard) only run when no primary
    strategy produced content, so a working extractor's output is never padded
    with a raw JSON dump into the corpus.
    """

    name = "base"
    rescue = False

    def extract(self, raw: RawSource) -> ExtractionResult:
        raise NotImplementedError


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _dict_to_blocks(data: dict[str, Any], source: str, prefix: str = "") -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for key, value in data.items():
        label = normalize_key(key) if not prefix else f"{prefix} / {normalize_key(key)}"
        if isinstance(value, dict):
            blocks.extend(_dict_to_blocks(value, source, label))
        elif isinstance(value, (list, tuple)):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    blocks.extend(_dict_to_blocks(item, source, f"{label}[{idx}]"))
                elif item is not None:
                    blocks.append(ContentBlock(source=source, kind="field",
                                               heading=f"{label}[{idx}]", text=_scalar_text(item)))
        elif value is not None:
            blocks.append(ContentBlock(source=source, kind="field", heading=label,
                                       text=_scalar_text(value)))
    return blocks


class StructuredExtractor(BaseExtractor):
    """Convert ANY key->value dict into canonical field blocks.

    Works for quotes, product sheets, contracts, configs, manifests — no field
    whitelist, so nothing the source provides is dropped by design.
    """

    name = "structured"

    def extract(self, raw: RawSource) -> ExtractionResult:
        payload = raw.payload
        if not isinstance(payload, dict):
            return ExtractionResult(
                CanonicalDocument(raw.source_uri, raw.doc_id), False, "no dict payload")
        blocks = _dict_to_blocks(payload, self.name)
        doc = CanonicalDocument(raw.source_uri, raw.doc_id, blocks, dict(raw.metadata),
                                [self.name], raw.dynamic)
        return ExtractionResult(doc, bool(blocks))
class MetadataExtractor(BaseExtractor):
    """Pull a metadata dict into canonical field blocks (best-effort)."""

    name = "metadata"

    def extract(self, raw: RawSource) -> ExtractionResult:
        if not raw.metadata:
            return ExtractionResult(
                CanonicalDocument(raw.source_uri, raw.doc_id), False, "no metadata")
        blocks = _dict_to_blocks(raw.metadata, self.name)
        doc = CanonicalDocument(raw.source_uri, raw.doc_id, blocks, dict(raw.metadata),
                                [self.name], raw.dynamic)
        return ExtractionResult(doc, bool(blocks))


class TextExtractor(BaseExtractor):
    """Visible/semantic free-text content (e.g. PDF text, body text)."""

    name = "visible"

    def extract(self, raw: RawSource) -> ExtractionResult:
        if raw.kind == "html":
            # Visible text for HTML is what the fetcher/parser already produced
            # (raw.text). Never fall back to dumping raw markup into the corpus
            # as if it were prose — that is noise, and it would defeat the
            # HTML semantic extractor that preserves structured content.
            text = (raw.text or "").strip()
        else:
            text = (raw.text or raw.html or "").strip()
        if not text:
            return ExtractionResult(
                CanonicalDocument(raw.source_uri, raw.doc_id), False, "no visible text")
        block = ContentBlock(source=self.name, kind="text", text=text)
        doc = CanonicalDocument(raw.source_uri, raw.doc_id, [block], dict(raw.metadata),
                                [self.name], raw.dynamic)
        return ExtractionResult(doc, True)


class FallbackExtractor(BaseExtractor):
    """Last resort: dump whatever we have as a single bounded raw block.

    Guarantees the pipeline never drops an entire document just because every
    structured strategy was inapplicable.
    """

    name = "fallback"
    max_chars = 20000
    rescue = True

    def extract(self, raw: RawSource) -> ExtractionResult:
        try:
            body = json.dumps({"text": raw.text, "payload": raw.payload,
                               "metadata": raw.metadata}, default=str)[: self.max_chars]
        except Exception:
            body = str(raw.payload if raw.payload is not None else raw.text)[: self.max_chars]
        doc = CanonicalDocument(
            raw.source_uri, raw.doc_id,
            [ContentBlock(source=self.name, kind="raw", text=body)],
            dict(raw.metadata), [self.name], raw.dynamic)
        return ExtractionResult(doc, True)


class DynamicContentGuard(BaseExtractor):
    """Mark client-rendered (JS-only) sources without pretending to fix them.

    A regex/HTML parser cannot execute JavaScript. When the fetcher only saw a
    shell (``raw.dynamic`` or no rendered text), we emit a safe placeholder so
    the document is not silently treated as empty, and flag it for a future
    browser-renderer extension point.
    """

    name = "dynamic"
    rescue = True

    def extract(self, raw: RawSource) -> ExtractionResult:
        if raw.dynamic or not raw.rendered:
            note = ("dynamic/client-rendered content: no static text captured; "
                    "a browser renderer is required to extract this source")
            doc = CanonicalDocument(
                raw.source_uri, raw.doc_id,
                [ContentBlock(source=self.name, kind="raw", text=note)],
                dict(raw.metadata), [self.name], True)
            return ExtractionResult(doc, True)
        return ExtractionResult(
            CanonicalDocument(raw.source_uri, raw.doc_id), False, "static content present")


class HtmlSemanticExtractor(BaseExtractor):
    """Extract machine-readable semantic content embedded in *static* HTML.

    General-purpose and format-driven: understands HTML-spec containers
    (``<title>``, ``<meta name|property|itemprop>``, OpenGraph, JSON-LD in
    ``<script type=application/ld+json>``, ``h1..h6`` headings). It knows
    nothing about any website, vendor, product, or field set — every piece of
    structured information it finds is mapped onto domain-neutral field blocks
    and merged into metadata, so content that exists *in the HTTP response*
    but not in the rendered visible text is preserved before chunking.

    It never executes JavaScript and intentionally does not parse ``<script>``
    bodies other than ``application/ld+json`` data (which is static data, not
    code). Client-rendered content is left to the dynamic-content guard +
    ``Renderer`` extension point.

    Like every extractor it never raises: malformed HTML / bad JSON-LD is
    skipped and the remaining strategies still run.
    """

    name = "html_semantic"

    def extract(self, raw: RawSource) -> ExtractionResult:
        html = getattr(raw, "html", None) or ""
        if raw.kind != "html" or not html.strip():
            return ExtractionResult(
                CanonicalDocument(raw.source_uri, raw.doc_id), False, "no html payload")

        sem = parse_semantic_html(html)
        blocks: list[ContentBlock] = []
        if sem.title.strip():
            blocks.append(ContentBlock(source=self.name, kind="text",
                                       heading="Title", text=sem.title.strip()))
        blocks.extend(meta_to_blocks(sem.meta, self.name))
        for heading in sem.headings:
            blocks.append(ContentBlock(source=self.name, kind="text",
                                       heading="Heading", text=heading))
        for jd in sem.jsonld:
            if isinstance(jd, dict):
                blocks.extend(_dict_to_blocks(jd, self.name))
            elif jd is not None:
                blocks.append(ContentBlock(source=self.name, kind="field",
                                           heading="Schema", text=str(jd)))

        # Merge discovered metadata into a copy so downstream metadata is richer
        # without mutating the caller's RawSource.
        metadata = dict(raw.metadata)
        if sem.title.strip():
            metadata.setdefault("title", sem.title.strip())
        for k, v in sem.meta.items():
            metadata.setdefault(f"meta:{k}", v)

        doc = CanonicalDocument(raw.source_uri, raw.doc_id, blocks, metadata,
                                [self.name], raw.dynamic)
        return ExtractionResult(doc, bool(blocks))


DEFAULT_EXTRACTORS: list[BaseExtractor] = [
    StructuredExtractor(),
    MetadataExtractor(),
    HtmlSemanticExtractor(),
    TextExtractor(),
    FallbackExtractor(),
    DynamicContentGuard(),
]


def build_canonical(raw: RawSource,
                    extractors: list[BaseExtractor] | None = None) -> CanonicalDocument:
    """Run primary strategies, merging every successful strategy's blocks.

    Each primary extractor (structured / metadata / visible) is attempted
    independently; failures are recorded and skipped, never fatal. Rescue
    strategies (fallback / dynamic guard) run only if no primary produced
    content, so a working extractor's output is never padded with a raw JSON
    dump. This guarantees no document is silently empty or dropped.
    """
    chain = list(extractors) if extractors else list(DEFAULT_EXTRACTORS)
    primaries = [e for e in chain if not e.rescue]
    rescues = [e for e in chain if e.rescue]
    merged = CanonicalDocument(raw.source_uri, raw.doc_id,
                              metadata=dict(raw.metadata), dynamic=raw.dynamic)

    def _merge(extractor: BaseExtractor) -> bool:
        try:
            result = extractor.extract(raw)
        except Exception:
            return False
        if not result or not result.ok:
            return False
        added = False
        for block in result.doc.blocks:
            if (block.text or "").strip():
                merged.add_block(block)
                added = True
        # Propagate metadata enriched by this extractor (e.g. HTML title/meta)
        # into the canonical document. Existing values are never overwritten;
        # a *conflicting* value for the same key is preserved under a suffixed
        # key rather than silently discarded.
        for key, value in result.doc.metadata.items():
            if value is None:
                continue
            if key not in merged.metadata or merged.metadata[key] == value:
                if merged.metadata.get(key) is None:
                    merged.metadata[key] = value
                continue
            n, candidate = 1, key
            while candidate in merged.metadata:
                n += 1
                candidate = f"{key}[{n}]"
            merged.metadata[candidate] = value
        for name in result.doc.strategy_evidence:
            if name not in merged.strategy_evidence:
                merged.strategy_evidence.append(name)
        return added

    had_primary = False
    for extractor in primaries:
        if _merge(extractor):
            had_primary = True

    if not had_primary:
        # Pick the most appropriate rescue: dynamic guard only when the fetcher
        # signalled client-rendered content; fallback dump otherwise.
        preferred = "dynamic" if raw.dynamic else "fallback"
        rescue_order = sorted(rescues, key=lambda e: (e.name != preferred))
        for rescue in rescue_order:
            if _merge(rescue):
                break
        if not merged.blocks:  # final guarantee: nothing at all
            merged.add_block(ContentBlock(source="fallback", kind="raw",
                                          text="(no extractable content)"))
            if "fallback" not in merged.strategy_evidence:
                merged.strategy_evidence.append("fallback")
    return merged