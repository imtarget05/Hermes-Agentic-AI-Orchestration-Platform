"""General-purpose HTML parsing for semantic extraction (stdlib only).

No domain / website / vendor / field / regex-over-a-page hard-coding here.
The parser is *format* driven: it understands well-known HTML container tags
(title, meta, semantically loaded JSON-LD, headings) because those are part of
the HTML *spec*, not any particular site. It never executes JavaScript; content
that only appears after JS is left for a future browser ``Renderer`` — this
module only extracts what is present in the static response.

This is a real parser (``html.parser``), not a regex that fakes "pattern
matching" — which is also why it is the correct place to detect a dynamic JS
shell rather than guessing from the visible-text string.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from .normalize import normalize_key


@dataclass
class HtmlSemanticData:
    """What we can learn from the *static* HTML (metadata + structured content)."""

    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)  # name/property -> content
    jsonld: list[Any] = field(default_factory=list)  # parsed JSON-LD documents
    headings: list[str] = field(default_factory=list)  # h1..h6 text in doc order


class _SemanticHtmlParser(HTMLParser):
    """Collect title, meta, JSON-LD scripts, and heading text in a lenient pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.jsonld: list[Any] = []
        self.headings: list[str] = []
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_script = False
        self._script_body: list[str] = []
        self._script_type = ""
        self._cur_heading = ""
        self._cur_heading_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: D102
        if tag == "title":
            self._in_title = True
            self._title_parts = []
        elif tag == "meta":
            self._handle_meta(attrs)
        elif tag == "script":
            self._in_script = True
            self._script_body = []
            self._script_type = ""
            for k, v in attrs:
                if k in ("type", "language"):
                    self._script_type = (v or "").strip().lower()
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._cur_heading = tag

    def handle_endtag(self, tag: str) -> None:  # noqa: D102
        if tag == "title" and self._in_title:
            self.title = " ".join("".join(self._title_parts).split())
            self._in_title = False
        elif tag == "script":
            if self._in_script:
                if self._script_type == "application/ld+json":
                    snippet = "".join(self._script_body).strip()
                    if snippet:
                        parsed = _parse_jsonld(snippet)
                        if parsed is not None:
                            self.jsonld.append(parsed)
                self._in_script = False
                self._script_body = []
                self._script_type = ""
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            heading = " ".join(self._cur_heading_text.split())
            if heading:
                self.headings.append(heading)
            self._cur_heading = ""
            self._cur_heading_text = ""

    def handle_data(self, data: str) -> None:  # noqa: D102
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_script:
            self._script_body.append(data)
        elif self._cur_heading:
            self._cur_heading_text += data

    def _handle_meta(self, attrs: list[tuple[str, str | None]]) -> None:
        d = {k: (v or "") for k, v in attrs}
        content = (d.get("content") or "").strip()
        if not content:
            return
        key = d.get("property") or d.get("name") or d.get("http-equiv") or d.get("itemprop") or ""
        key = key.strip()
        if not key:
            return
        # Later duplicates for the same key are preserved via a suffixed key so
        # *conflicting* information is never silently overwritten.
        n = 0
        candidate = key
        while candidate in self.meta and self.meta[candidate].strip() != content:
            n += 1
            candidate = f"{key}[{n}]"
        self.meta[candidate] = content


def _parse_jsonld(snippet: str) -> Any:
    try:
        return json.loads(snippet)
    except Exception:
        # Malformed JSON-LD should not abort extraction; a None signals skip.
        return None


def parse_semantic_html(html: str | None) -> HtmlSemanticData:
    """Parse semantic data out of raw HTML. Resistant to malformed markup."""
    data = HtmlSemanticData()
    if not html or not html.strip():
        return data
    parser = _SemanticHtmlParser()
    try:
        parser.feed(html)  # html.parser tolerates unclosed/broken tags
        parser.close()
    except Exception:
        # Even a catastrophic parse error must not lose already-collected data.
        pass
    data.title = parser.title
    data.meta.update({k: v for k, v in parser.meta.items() if (v or "").strip()})
    data.jsonld = [j for j in parser.jsonld if j is not None]
    data.headings = [h for h in parser.headings if (h or "").strip()]
    return data


def html_is_probably_dynamic_shell(html: str | None) -> bool:
    """Heuristic: mostly `<script>`/`<style>` scaffolding with little static text.

    Content that exists only behind JavaScript cannot be extracted by an HTML
    parser; flagging it here routes the document to the dynamic-content guard
    (detected, not faked) instead of silently ingesting a JS shell.
    """
    if not html or not html.strip():
        return True  # nothing static at all
    stripped = html.lower()
    approx_code = 0
    for start, end in (("<script", "</script>"), ("<style", "</style>")):
        lo = 0
        while True:
            a = stripped.find(start, lo)
            if a == -1:
                break
            b = stripped.find(end, a)
            if b == -1:
                b = len(stripped)
            approx_code += b - a
            lo = b + len(end)
    static_len = max(0, len(stripped) - approx_code)
    total_len = len(stripped) or 1
    dynamic_fraction = approx_code / total_len
    # A page that is overwhelmingly scaffolding with almost no static markup is
    # treated as client-rendered. Thresholds are conservative general heuristics.
    return approx_code > 0 and dynamic_fraction > 0.8 and static_len < 200


def meta_to_blocks(meta: dict[str, str], source: str) -> list[Any]:
    """Turn parsed meta key->content into canonical field blocks (general)."""
    from .model import ContentBlock

    blocks: list[ContentBlock] = []
    for key, value in meta.items():
        value = (value or "").strip()
        if not value:
            continue
        label = normalize_key(key) if not key.startswith("og:") else normalize_key(key[3:])
        blocks.append(ContentBlock(source=source, kind="field", heading=label, text=value))
    return blocks