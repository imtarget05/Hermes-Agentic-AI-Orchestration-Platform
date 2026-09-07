"""Ingestion pipeline tests across failure categories.

Covered categories (behaviour/contract, not implementation detail):
standard document · structured document · metadata-heavy document ·
partially malformed data · duplicated information · conflicting information ·
missing information · dynamic-content limitation · extraction fallback ·
regression (the information-loss bug this architecture fixes).
"""
from __future__ import annotations

import re

from hermes.ingestion import (
    IngestionPipeline,
    RawSource,
    build_canonical,
    chunk_document,
    deduplicate,
)
from hermes.ingestion.extractors import BaseExtractor, ExtractionResult
from hermes.ingestion.model import CanonicalDocument, ContentBlock
from hermes.ingestion.pipeline import Renderer
from hermes.rag import RagIndex, build_case_index, ingest_quotes


def _pipe(**kw) -> IngestionPipeline:
    return IngestionPipeline(**kw)


# ---- standard / structured / metadata documents --------------------------- #


def test_standard_visible_text_document():
    p = _pipe().process(RawSource(source_uri="a.txt", text="Line one.\n\nLine two."))
    assert "Line one." in p.indexed_text()
    assert "Line two." in p.indexed_text()
    assert "visible" in p.canonical.strategy_evidence


def test_structured_document_preserves_all_fields():
    p = _pipe().process(RawSource(
        source_uri="q.pdf", kind="dict",
        payload={"vendor": "Acme", "price": 10.5, "currency": "EUR",
                 "nested": {"tax": True}}, text="free text"))
    out = p.indexed_text()
    for probe in ("Acme", "10.5", "EUR", "free text"):
        assert probe in out
    assert "structured" in p.canonical.strategy_evidence


def test_metadata_heavy_document():
    p = _pipe().process(RawSource(
        source_uri="meta.json", kind="text", text="body",
        metadata={"author": "Ada", "version": "2.1"}))
    out = p.indexed_text()
    assert "Ada" in out and "2.1" in out
    assert "metadata" in p.canonical.strategy_evidence


# ---- malformed / robustness ---------------------------------------------- #


def test_partially_malformed_structured_data():
    payload = {"vendor": "Acme", "broken": object(), "list": [{"a": 1}, "x", None]}
    p = _pipe().process(RawSource(source_uri="bad.json", kind="dict", payload=payload))
    assert "Acme" in p.indexed_text()


def test_stringified_bool_and_quote_like_values():
    p = _pipe().process(RawSource(source_uri="b.json", kind="dict",
                                  payload={"active": True, "incoterms": "DDP",
                                           "valid_until": "2026-03-01"}))
    out = p.indexed_text()
    assert "yes" in out and "DDP" in out and "2026-03-01" in out


# ---- duplication / conflict / missing ------------------------------------ #


def test_duplicate_blocks_collapsed_and_bare_duplicate_removed():
    doc = CanonicalDocument("d", blocks=[
        ContentBlock(source="visible", text="Raw Text: hello world"),
        ContentBlock(source="visible", text="hello world"),
        ContentBlock(source="structured", heading="Currency", text="EUR"),
        ContentBlock(source="structured", heading="Currency", text="EUR"),
    ])
    deduplicate(doc)
    texts = [b.searchable() for b in doc.blocks]
    assert texts.count("hello world") == 1
    assert texts.count("Currency: EUR") == 1


def test_conflicting_information_both_preserved():
    doc = CanonicalDocument("d", blocks=[
        ContentBlock(source="structured", heading="Warranty", text="2 years"),
        ContentBlock(source="structured", heading="Warranty", text="5 years"),
    ])
    deduplicate(doc)
    assert {b.text for b in doc.blocks} == {"2 years", "5 years"}


def test_missing_information_does_not_crash_and_is_flagged():
    p = _pipe().process(RawSource(source_uri="empty.json", kind="dict", payload={},
                                  text="   "))
    assert p.diagnostics.stages["extracted"].ok is False
    p2 = _pipe(min_content_chars=0).process(RawSource(source_uri="void", kind="text"))
    assert p2.indexed_text().strip()
    assert "fallback" in p2.canonical.strategy_evidence


# ---- dynamic content limitation ------------------------------------------ #


def test_dynamic_content_limitation_is_detected_not_faked():
    p = _pipe().process(RawSource(source_uri="https://x.dev/app",
                                  dynamic=True, text="<div id=root>…loading…"))
    assert p.canonical.dynamic is True
    assert any("dynamic" in issue for issue in p.diagnostics.issues)
    assert p.diagnostics.content_ok is False


def test_renderer_extension_renders_dynamic_content():
    class FakeRenderer(Renderer):
        enabled = True

        def render(self, raw) -> RawSource:
            raw.text = "Problem statement Dell XPS 15 Intel i7 32GB."
            raw.dynamic = False
            return raw

    pipe = _pipe()
    pipe.add_renderer(FakeRenderer())
    p = pipe.process(RawSource(source_uri="https://x.dev/app", dynamic=True,
                               text="<div id=root>…loading…"))
    assert "Dell XPS 15" in p.indexed_text()
    assert p.canonical.dynamic is False
    assert "renderer" not in p.diagnostics.issues


# ---- extraction fallback -------------------------------------------------- #


def test_fallback_dumps_unstructured_source_without_dropping():
    p = _pipe().process(RawSource(source_uri="odd.bin", kind="bytes",
                                  text="", html="", payload="blob"))
    assert p.indexed_text().strip()
    # The raw dump must not appear when a primary strategy already worked.
    p2 = _pipe().process(RawSource(source_uri="ok", kind="dict",
                                   payload={"vendor": "Acme"}, text="body"))
    assert "fallback" not in p2.canonical.strategy_evidence


# ---- canonical representation / extension of strategies ------------------- #


def test_new_extractor_registered_without_pipeline_rewrite():
    class UpperExtractor(BaseExtractor):
        name = "upper"

        def extract(self, raw):
            block = ContentBlock(source=self.name, heading="Shout",
                                 text=raw.text.upper())
            doc = CanonicalDocument(raw.source_uri, raw.doc_id, [block],
                                    strategy_evidence=[self.name])
            return ExtractionResult(doc, bool(raw.text))

    pipe = _pipe()
    pipe.add_extractor(UpperExtractor())
    p = pipe.process(RawSource(source_uri="u", text="hello"))
    assert "upper" in p.canonical.strategy_evidence
    assert "HELLO" in p.indexed_text()


def test_chunking_is_bounded_and_keeps_headings():
    blocks = [ContentBlock(source="s", heading=f"Field{i}", text="word " * 30)
              for i in range(20)]
    doc = CanonicalDocument("long", blocks=blocks)
    chunks = chunk_document(doc, max_words=100)
    assert len(chunks) > 1
    assert "Field0" in chunks[0]


def test_quality_validator_flags_abnormally_short_content():
    p = _pipe(min_content_chars=50).process(RawSource(source_uri="tiny", text="hi"))
    assert p.diagnostics.stages["extracted"].ok is False


def test_canonical_representation_is_source_agnostic():
    from_html = build_canonical(RawSource(source_uri="h", html="<b>x</b>", text="x"))
    from_dict = build_canonical(RawSource(source_uri="d", kind="dict", payload={"a": "1"}))
    assert isinstance(from_html, CanonicalDocument)
    assert isinstance(from_dict, CanonicalDocument)
    assert from_html.block_count() > 0 and from_dict.block_count() > 0


# ---- regression: the actual information-loss bug -------------------------- #


def test_regression_quote_structured_fields_survive_ingestion():
    quotes = [{
        "vendor": "Dell", "unit_price": 1200, "quantity": 50, "total": 60000,
        "source_uri": "demo/dell.pdf", "quote_date": "2025-03-01",
        "valid_until": "2026-03-01", "currency": "EUR", "incoterms": "DDP",
        "legal_entity": "Dell GmbH", "tax_included": True, "status": "VALID",
        "source_hash": "abc123", "raw_text": "Unit price EUR 1200. Free ground shipping.",
    }]
    idx = build_case_index(quotes, "Intel i7 16GB RAM")
    text = next(c.text for c in idx.chunks if c.doc_id.startswith("quote"))
    for probe in ("EUR", "DDP", "Dell GmbH", "2026-03-01", "VALID", "abc123",
                  "Free ground shipping"):
        assert probe.lower() in text.lower(), f"lost {probe!r} from ingestion"


def test_regression_no_duplication_and_correct_chunk_count():
    quotes = [{"vendor": "Dell", "unit_price": 1200, "quantity": 50, "total": 60000,
               "source_uri": "demo/dell.pdf", "quote_date": "DEMO", "is_demo": True,
               "raw_text": "Dell quote $1200 x 50 laptops. Payment Net 30."}]
    idx = build_case_index(quotes, "spec")
    text = next(c.text for c in idx.chunks if c.doc_id.startswith("quote"))
    assert text.lower().count("payment net 30") == 1
    assert len(idx) == 1 + 3 + 1  # 1 quote + 3 vendors + 1 spec


def test_ingest_quotes_collects_diagnostics():
    diag = []
    idx = RagIndex()
    ingest_quotes(idx, [{"vendor": "Acme", "unit_price": 1, "quantity": 1,
                         "total": 1, "raw_text": "Acme quote"}], diagnostics=diag)
    assert len(idx) == 1
    assert len(diag) == 1
    assert diag[0].source_uri
    assert diag[0].stages["chunked"].ok


def test_simple_probe_keeps_ascii_content():
    t = _pipe().process(RawSource(source_uri="x", text="alpha beta")).indexed_text()
    assert re.search(r"alpha", t)


# ---- HTML semantic extraction (metadata present in HTTP response) -------- #
# Regression for the failure class: machine-readable semantic content that
# lives in <meta>/<title>/JSON-LD in the static response was dropped because
# extraction only used visible text. These cover behaviour/contract.


def _html_doc(html, text="", **meta):
    return RawSource(source_uri="https://example.com/page", kind="html",
                     html=html, text=text, **meta)


def test_html_meta_title_preserved_in_indexed_text():
    html = ("<html><head><title>Dell XPS 15 — 2024</title>"
            "<meta name='author' content='Dell Technologies'>"
            "<meta property='og:price:amount' content='1899.00'>"
            "<meta property='og:price:currency' content='EUR'>"
            "</head><body><p>Some body text.</p></body></html>")
    p = IngestionPipeline().process(_html_doc(html))
    out = p.indexed_text()
    assert "html_semantic" in p.canonical.strategy_evidence
    for probe in ("Dell XPS 15", "Dell Technologies", "1899.00", "EUR"):
        assert probe.lower() in out.lower(), f"lost {probe!r}"


def test_html_json_ld_preserved_in_indexed_text():
    html = ("<html><head>"
            "<script type='application/ld+json'>"
            '{"@type":"Product","name":"XPS 15 9530","offers":{"price":1899}}'
            "</script></head><body><p>Body.</p></body></html>")
    p = IngestionPipeline().process(_html_doc(html))
    out = p.indexed_text()
    assert "html_semantic" in p.canonical.strategy_evidence
    assert "XPS 15 9530" in out
    assert "1899" in out


def test_html_meta_merged_into_metadata():
    html = ("<html><head>"
            "<meta name='author' content='Dell Technologies'>"
            "<meta property='og:price:amount' content='1499'>"
            "</head><body>text</body></html>")
    p = IngestionPipeline().process(_html_doc(html))
    assert p.canonical.metadata.get("meta:author") == "Dell Technologies"
    assert p.canonical.metadata.get("meta:og:price:amount") == "1499"


def test_html_no_visible_text_but_semantic_meta_preserved():
    # Standard <script>-stripping visible-text extraction would leave nothing.
    html = ("<html><head><title>Only Meta Page</title>"
            "<meta name='description' content='A meta-only document'>"
            "</head></html>")
    p = IngestionPipeline().process(_html_doc(html, text=""))
    out = p.indexed_text()
    assert "html_semantic" in p.canonical.strategy_evidence
    assert "Only Meta Page" in out
    assert "a meta-only document" in out.lower()


def test_html_partially_malformed_still_preserves_what_is_valid():
    # Broken tags/malformed JSON-LD must not abort; valid semantic survives.
    html = ("<html><head><title>Still Here</title>"
            "<meta name='author' content='Acme'>"
            "<script type='application/ld+json'>NOT JSON</script>"
            "<meta name='description' content='partial'>"
            "</head><body><p>unclosed"
            "</html>")
    p = IngestionPipeline().process(_html_doc(html))
    out = p.indexed_text()
    assert "html_semantic" in p.canonical.strategy_evidence
    assert "Still Here" in out
    assert "Acme" in out
    assert "partial" in out


def test_html_duplicate_meta_values_deduped_not_bloated():
    html = ("<html><head>"
            "<meta name='keywords' content='laptop dell'>"
            "<meta name='keywords' content='laptop dell'>"
            "</head><body>text</body></html>")
    p = IngestionPipeline().process(_html_doc(html))
    out = p.indexed_text()
    assert out.lower().count("laptop dell") == 1


def test_html_conflicting_meta_values_both_preserved():
    html = ("<html><head>"
            "<meta name='warranty' content='2 years'>"
            "<meta name='warranty' content='5 years'>"
            "</head><body>text</body></html>")
    p = IngestionPipeline().process(_html_doc(html))
    out = p.indexed_text()
    assert "2 years" in out
    assert "5 years" in out


def test_html_visible_and_semantic_text_both_captured():
    html = ("<html><head><title>Title Here</title></head>"
            "<body><p>Visible body sentence.</p></body></html>")
    p = IngestionPipeline().process(_html_doc(html, text="Visible body sentence."))
    out = p.indexed_text()
    assert "Visible body sentence" in out
    assert "Title Here" in out
def test_dynamic_shell_detected_by_fetcher_heuristic():
    # A page that is mostly <script>/<style> scaffolding with no static text is
    # a client-rendered shell → not silently ingested; detected, not faked.
    from hermes.ingestion.htmlparse import html_is_probably_dynamic_shell
    big_state = "x" * 2000  # realistically large inline app state
    shell = ("<html><head>"
             "<script>window.__INITIAL_STATE__={payload: '" + big_state + "'};</script>"
             "<style>body{display:none}</style>"
             "</head><body><div id='root'></div></body></html>")
    assert html_is_probably_dynamic_shell(shell) is True


def test_static_page_not_marked_dynamic_shell():
    from hermes.ingestion.htmlparse import html_is_probably_dynamic_shell
    static = ("<html><body><h1>Static Page</h1>"
              "<p>A normal paragraph with meaningful content.</p>"
              "<script>window.x = 1;</script></body></html>")
    assert html_is_probably_dynamic_shell(static) is False


def test_html_renderer_extension_renders_dynamic_shell():
    # Confirmed limitation boundary: content behind JS is NOT faked by an HTML
    # parser; a renderer extension produces it.
    from hermes.ingestion.pipeline import Renderer
    pipe = IngestionPipeline()

    class FakeRenderer(Renderer):
        enabled = True

        def render(self, raw) -> RawSource:
            raw.text = "Rendered after JS: Dell XPS 15 32GB."
            raw.dynamic = False
            return raw

    pipe.add_renderer(FakeRenderer())
    big_js = "y" * 1500
    shell = ("<html><head><script>" + big_js + "</script></head>"
             "<body><div id='root'></div></body></html>")
    p = pipe.process(_html_doc(shell, text=""))
    assert "Dell XPS 15" in p.indexed_text()
    assert p.canonical.dynamic is False


def test_quality_validator_flags_html_information_loss():
    # A custom pipeline WITHOUT the HTML extractor must be flagged as loss when
    # the source carried semantic data (regression guard for the architecture).
    from hermes.ingestion.extractors import (
        MetadataExtractor,
        StructuredExtractor,
        TextExtractor,
    )
    pipe = IngestionPipeline(extractors=[
        StructuredExtractor(), MetadataExtractor(), TextExtractor()])
    html = ("<html><head><title>Important</title>"
            "<meta name='description' content='keyspec'>"
            "</head><body>text</body></html>")
    p = pipe.process(_html_doc(html))
    assert any("html carried title/meta/structured content" in i for i in p.diagnostics.issues)
    assert p.diagnostics.content_ok is False
    t = _pipe().process(RawSource(source_uri="x", text="alpha beta")).indexed_text()
    assert re.search(r"alpha", t)