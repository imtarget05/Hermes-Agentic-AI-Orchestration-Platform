"""Text normalization utilities (general-purpose, no domain knowledge)."""
from __future__ import annotations

import re

_WS = re.compile(r"[ \t]+")


def normalize_key(key: str) -> str:
    """Turn snake_case / kebab / raw keys into presentable labels.

    ``valid_until_years`` → ``Valid Until Years``; ``DDP`` → ``Ddp``.
    This is cosmetic for readability; casing is not semantic.
    """
    k = str(key or "").replace("_", " ").replace("-", " ").strip()
    return " ".join(part for part in k.split() if part).title()


def normalize_text(text: str) -> str:
    """Collapse repeated whitespace / blank lines without losing paragraph breaks."""
    lines: list[str] = []
    prev_blank = False
    for raw in (text or "").splitlines():
        line = _WS.sub(" ", raw).strip()
        if not line:
            if prev_blank:
                continue
            prev_blank = True
            lines.append("")
        else:
            prev_blank = False
            lines.append(line)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)