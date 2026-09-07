# Ingestion pipeline

Architecture for **information-preserving ingestion**, fixing the failure class
where content present in a source document never reaches the retrieval corpus.

## Pipeline

```text
Fetcher
   ↓
RawSource                         (source_uri, text, html, payload, metadata)
   ↓
Extractor(s)  — strategy chain    (structured · metadata · html_semantic · visible · fallback · dynamic guard)
   ↓
CanonicalDocument                 (domain-neutral: tagged ContentBlock[] + metadata)
   ↓
Normalizer                        (whitespace / label canonicalization)
   ↓
Deduplicator                      (drop exact repeats, preserve conflicts)
   ↓
QualityValidator + Diagnostics    (pre-embedding gate + stage-by-stage view)
   ↓
Chunker                           (bounded chunks, headings preserved)
   ↓
(Embedder → Vector Store)          — handled by the retrieval layer (see limitations)
```

The chunker, embedder and vector store **only ever read the canonical
representation**. A new source type adds a new *general-purpose* extractor (or
a browser renderer) without touching the rest of the pipeline.

## Root cause (proven by data flow)

`RagIndex.ingest_quotes` collapsed each quote to:

```
unit price ${unit_price} quantity ${quantity} total ${total}. ${raw_text}
```

Only three hand-picked numeric fields plus a canned `raw_text` substring were
indexed. Every other structured value present in the source quote object —
`currency`, `incoterms`, `legal_entity`, `valid_until`, `status`, `source_hash`,
nested fields, etc. — was **silently dropped**. The trace:

```text
parse_quote_pdf → Quote dict {vendor, unit_price, …, currency:EUR, …}
  → ingest_quotes → chunk text "unit price … : Unit price EUR 1200."
  → vector-store payload      ← currency/incoterms/legal_entity/valid_until LOST
```

That is a *class* of failure: **ingestion collapsed a rich source to an ad-hoc
field whitelist in a single bespoke function.** New vendors, formats or fields
failed the same way. The fix removes the collapse — every quote is routed
through the canonical ingestion pipeline, so **all** extracted structured
content is preserved (normalised + deduplicated + validated) before chunking.

## Design principles

- **Source preservation before chunking.** `HTML visible text == all semantic
  content` is never assumed; structured fields and metadata are preserved as
  first-class searchable blocks.
- **No raw dump by default.** The fallback/dynamic strategies are *rescue-only*:
  they run only when no primary strategy produced content, so a working extractor
  is never padded with raw JSON/HTML into the corpus.
- **General-purpose extractors only.** No domain, website, product, field,
  vendor, or single-record regex anywhere. `StructuredExtractor` turns *any*
  dict into field blocks; `MetadataExtractor` pulls document metadata;
  `HtmlSemanticExtractor` (stdlib `html.parser`) pulls machine-readable semantic
  content out of *static* HTML — `<title>`, `<meta name|property|itemprop>`,
  OpenGraph, JSON-LD in `<script type=application/ld+json>`, `h1..h6` — mapping
  each onto domain-neutral field blocks and metadata. So a web page whose value
  lives in `<head>` metadata (not just rendered body text) is preserved into the
  corpus instead of losing it to visible-text-only extraction.
- **Graceful robustness.** Every extractor is `try/except`-isolated; a failing
  strategy is skipped, never fatal. Missing/malformed/duplicated/conflicting
  data, partial extraction failure and unusual responses all fall through
  safely.
- **Quality gate before embedding.** `QualityValidator` flags empty content,
  abnormally short content, missing structured content, and severe degradation,
  and records per-stage metrics (`source → extracted → normalized → deduped →
  chunked`) so information loss is localisable (`IngestionDiagnostics`).

## Dynamic / client-rendered content

The pipeline **distinguishes**:

1. content in the HTTP response that an extractor missed → handled by adding a
   general-purpose extractor;
2. content that only appears after JavaScript executes → **cannot be fixed with
   a regex/HTML parser**, and the pipeline does not pretend otherwise.

A `DynamicContentGuard` detects unrendered sources (fetcher `dynamic=True`, or
no static content) and emits a safe placeholder while flagging the document so
it is never silently treated as empty. The `WebFetcher` additionally applies a
conservative *JS-shell heuristic* (`htmlparse.html_is_probably_dynamic_shell`):
a page that is overwhelmingly inline `<script>`/`<style>` scaffolding with
negligible static text is flagged `dynamic=True`, so a client-rendered shell is
detected and signposted rather than silently ingested. A future renderer can be
plugged in via `IngestionPipeline.add_renderer(...)` — today it degrades
*safely*, it does not fabricate content.

## Extension points

- `IngestionPipeline.add_extractor(extractor)` — add a new general-purpose
  strategy without rewriting the pipeline.
- `IngestionPipeline.add_renderer(renderer)` — enable a browser renderer for
  dynamic sources.

## Limitations

- **Embedding/vector store are out of scope here.** The retrieval layer owns
  those (`RagIndex.embed` is an optional cosine hook; there is no external
  vector DB in this codebase). The pipeline’s boundary is the *chunked,
  validated canonical text*, which is what the retrieval layer ingests.
- **No built-in HTML DOM / browser rendering** — dynamic content is detected and
  deferred to a future renderer, not faked.
- **Nothing is *authenticated-authoritative*:** preserve conflicting field
  values (dedup keeps them) so downstream verification can decide, rather than
  the extractor silently discarding a source of truth.

## Testing

`tests/test_ingestion.py` covers the failure categories as behaviour contracts:
standard document · structured document · metadata-heavy document · partially
malformed data · duplicated information · conflicting information · missing
information · dynamic-content limitation · extraction fallback · regression.
All fixtures are synthetic/offline → deterministic.