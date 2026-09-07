"""Deduplication for canonical content (general-purpose).

Removes exact duplicate blocks without throwing away *conflicting* information:
two blocks with the same heading but different text are both kept, while blocks
that repeat an identical (heading, text) pair are dropped. This avoids context
bloat while preserving disagreements.
"""
from __future__ import annotations

from .model import CanonicalDocument, ContentBlock


def _is_noise(block: ContentBlock) -> bool:
    # Drop whitespace-only, and standalone punctuation-only lines (e.g. a dash).
    text = (block.text or "").strip()
    if not text:
        return True
    if block.heading and not text.strip(".,;:-*_ \t\""):
        return False
    return all(ch in " \t.,;:-*_'\"()" for ch in text)


def deduplicate(doc: CanonicalDocument) -> CanonicalDocument:
    """In-place, returns the same doc with redundant blocks removed.

    Rules (preserving conflicting data, dropping exact repeats):
    - a *headed* block is dropped only if the identical (heading, text) already
      appeared (i.e. the same field was provided twice);
    - a *bare* (unheaded) block is dropped if its text already appeared anywhere
      (e.g. a free-text block duplicating a "Raw Text: ..." field);
    - two headed blocks with the *same* text but *different* headings are both
      kept (that is conflicting information, not duplication).
    """
    seen_content: set[str] = set()          # text seen under any heading
    seen_headed: set[tuple[str, str]] = set()
    kept: list[ContentBlock] = []
    for block in doc.blocks:
        if _is_noise(block):
            continue
        text = (block.text or "").strip()
        content = text.lower()
        if not content:
            continue
        heading = (block.heading or "").strip().lower()
        if heading:
            if (heading, content) in seen_headed:
                continue
            seen_headed.add((heading, content))
        elif content in seen_content:
            # bare duplicate of content already captured (possibly under a label)
            continue
        seen_content.add(content)
        kept.append(block)
    doc.blocks = kept
    return doc