"""PDF renderer — converts NormalizedReport to PDF bytes."""
from __future__ import annotations

import unicodedata
from typing import Any

from fpdf import FPDF

from .models import DecisionCard, NormalizedReport, ReportTier

# Glyphs the PDF core fonts (Helvetica etc.) cannot draw, mapped to an ASCII
# equivalent the report can actually render.
_GLYPH_REPLACEMENTS = {
    "\u2022": "-",       # bullet
    "\u2713": "OK",      # checkmark
    "\u2717": "FAIL",    # cross
    "\u2605": "*",       # star
    "\u2014": "-",       # em dash
    "\u2013": "-",       # en dash
    "\u2018": "'",       # left single quote
    "\u2019": "'",       # right single quote
    "\u201c": '"',       # left double quote
    "\u201d": '"',       # right double quote
    "\u2026": "...",     # ellipsis
    "\u00b0": " degrees",  # degree symbol
    "\u00d7": "x",       # multiplication sign
    "\u00f7": "/",       # division sign
    "\u00a0": " ",       # non-breaking space
    "\u200b": "",        # zero-width space
    "\u00ad": "",        # soft hyphen
    # arrows
    "\u2190": "<-",      # leftwards arrow
    "\u2192": "->",      # rightwards arrow
    "\u21d2": "=>",      # rightwards double arrow
    # box drawing (the ASCII decision card in report/decision_card.py)
    "\u2500": "-",       # light horizontal
    "\u2502": "|",       # light vertical
    "\u250c": "+",       # down and right
    "\u2510": "+",       # down and left
    "\u2514": "+",       # up and right
    "\u2518": "+",       # up and left
    "\u251c": "+",       # vertical and right
    "\u2524": "+",       # vertical and left
    # status emoji (report/templates/workflow.py)
    "\u2705": "[OK]",    # white heavy check mark
    "\u274c": "[FAIL]",  # cross mark
    "\u2753": "[?]",     # question mark
    "\u2b1c": "[ ]",     # white large square
    "\u23f3": "[wait]",  # hourglass
    "\U0001f504": "[...]",  # counterclockwise arrows button
}

# Latin letters that carry a stroke/hook instead of a combining diacritic, so
# NFD cannot split them ("Đơn vị" is Vietnamese for "unit"). They have no
# canonical decomposition, hence the explicit base letters.
_LATIN_STROKE_LETTERS = {
    "\u0110": "D", "\u0111": "d",   # D with stroke
    "\u0126": "H", "\u0127": "h",   # H with stroke
    "\u0138": "k",                   # kra
    "\u013f": "L", "\u0140": "l",   # L with middle tilde
    "\u014a": "N", "\u014b": "n",   # eng
    "\u0152": "OE", "\u0153": "oe",  # OE ligature
    "\u0166": "T", "\u0167": "t",   # T with stroke
}


class ReportRenderError(ValueError):
    """Text cannot be rendered with the PDF core fonts.

    Raised instead of letting fpdf raise FPDFUnicodeEncodingException from deep
    inside a draw call, so the caller learns which report field is at fault.
    """


def sanitize_text(text: str) -> str:
    """Make `text` drawable with the PDF core (Latin-1) fonts.

    No Unicode font is vendored with this project and none may be downloaded, so
    the core fonts are the only option. Two passes make that usable:

    1. known un-drawable glyphs (bullets, dashes, smart quotes) become ASCII;
    2. accented Latin letters are decomposed and stripped of their combining
       marks, so Vietnamese ("Báo cáo tài chính") renders as "Bao cao tai chinh".

    Anything still outside Latin-1 (Cyrillic, Greek, CJK, emoji) cannot be shown
    at all without an embedded font, so it raises ReportRenderError naming the
    offending character rather than silently dropping it or letting fpdf raise
    a cryptic encoding error from the middle of a draw call.
    """
    for char, replacement in _GLYPH_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    for char, replacement in _LATIN_STROKE_LETTERS.items():
        text = text.replace(char, replacement)
    # NFD splits an accented letter into its base letter + combining marks, so
    # dropping the marks transliterates the whole Latin diacritic range.
    text = "".join(
        c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)
    )
    for i, char in enumerate(text):
        if char in "\t\n\r" or char.isascii():
            continue
        try:
            char.encode("latin-1")
        except UnicodeEncodeError:
            raise ReportRenderError(
                f"cannot render character {char!r} (U+{ord(char):04X}) at index {i} "
                f"of {_excerpt(text, i)}: the PDF core fonts cover Latin-1 only and "
                f"this project vendors no Unicode font"
            ) from None
    return text


def _excerpt(text: str, index: int, width: int = 20) -> str:
    start = max(0, index - width // 2)
    return repr(text[start:index + width // 2])


class HermesPDF(FPDF):
    """fpdf.FPDF with Hermes branding and core-font-safe text.

    This subclasses FPDF rather than wrapping it: the renderer draws with the
    whole FPDF drawing API, and a delegating wrapper silently loses any method
    it forgets to forward (rect/set_x/set_xy were missing, and header/footer
    were never invoked because fpdf calls them on itself). Inheriting means
    there is no forwarding layer to drift out of sync, and the fpdf
    header/footer hooks below actually run on every page.
    """

    def __init__(self) -> None:
        super().__init__()
        self.alias_nb_pages()
        self.set_auto_page_break(auto=True, margin=15)

    # ---- branding: fpdf calls these on every add_page() ----
    def header(self) -> None:
        self.set_font("helvetica", "B", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "HERMES - Multi-Agent Decision Intelligence", ln=True, align="R")
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    # ---- single text chokepoint: nothing reaches a font unsanitised ----
    def cell(self, w, h, text: str = "", *args: Any, **kwargs: Any):
        return super().cell(w, h, sanitize_text(text), *args, **kwargs)

    def multi_cell(self, w, h, text: str = "", *args: Any, **kwargs: Any):
        return super().multi_cell(w, h, sanitize_text(text), *args, **kwargs)


class PDFRenderer:
    """Renders NormalizedReport to PDF."""

    def render(self, report: NormalizedReport) -> bytes:
        pdf = HermesPDF()
        pdf.add_page()

        # Title
        self._render_title(pdf, report)

        # Tier 1 — Executive (always first)
        self._render_tier(pdf, report, ReportTier.EXECUTIVE)

        # Tier 2 — Evidence
        self._render_tier(pdf, report, ReportTier.EVIDENCE)

        # Tier 3 — Audit (always last)
        self._render_tier(pdf, report, ReportTier.AUDIT)

        # Decision Card (always at end)
        self._render_decision_card(pdf, report.decision_card)

        return pdf.output()

    def _render_title(self, pdf: HermesPDF, report: NormalizedReport):
        pdf.set_font("helvetica", "B", 18)
        pdf.set_text_color(0, 51, 102)
        pdf.cell(0, 12, report.title, ln=True, align="C")

        if report.subtitle:
            pdf.set_font("helvetica", "", 12)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(0, 8, report.subtitle, ln=True, align="C")

        pdf.set_font("helvetica", "", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 6, f"Generated: {report.generated_at}", ln=True, align="C")

        if report.workflow_id:
            pdf.cell(0, 6, f"Workflow: {report.workflow_id}", ln=True, align="C")

        pdf.ln(8)

    def _render_tier(self, pdf: HermesPDF, report: NormalizedReport, tier: ReportTier):
        sections = report.sections_by_tier(tier)
        if not sections:
            return

        # Tier header
        pdf.set_font("helvetica", "B", 14)
        pdf.set_text_color(0, 102, 153)
        tier_names = {
            ReportTier.EXECUTIVE: "EXECUTIVE SUMMARY",
            ReportTier.EVIDENCE: "EVIDENCE & ANALYSIS",
            ReportTier.AUDIT: "AUDIT & VERIFICATION",
        }
        pdf.cell(0, 10, tier_names.get(tier, tier.value.upper()), ln=True)
        pdf.set_draw_color(0, 102, 153)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(5)

        for section in sections:
            self._render_section(pdf, section)

    def _render_section(self, pdf: HermesPDF, section):
        # Check if we need a new page
        if pdf.get_y() > 250:
            pdf.add_page()

        # Section title
        pdf.set_font("helvetica", "B", 11)
        pdf.set_text_color(51, 51, 51)
        pdf.cell(0, 8, section.title, ln=True)

        # Table or content
        if section.table_data:
            self._render_table(pdf, section.table_data)
        elif section.content:
            pdf.set_font("helvetica", "", 10)
            pdf.set_text_color(0, 0, 0)
            pdf.multi_cell(0, 5, section.content)

        pdf.ln(4)

    def _render_table(self, pdf: HermesPDF, data: list[list[str]]):
        if not data:
            return

        num_cols = len(data[0])
        col_width = 190 / num_cols

        # Header
        pdf.set_font("helvetica", "B", 8)
        pdf.set_fill_color(230, 230, 230)
        for cell in data[0]:
            pdf.cell(col_width, 7, cell[:15], 1, 0, "C", True)
        pdf.ln()

        # Data rows
        pdf.set_font("helvetica", "", 8)
        for row in data[1:]:
            for cell in row:
                pdf.cell(col_width, 6, cell[:15], 1)
            pdf.ln()

    def _render_decision_card(self, pdf: HermesPDF, card: DecisionCard):
        # Check if we need a new page
        if pdf.get_y() > 200:
            pdf.add_page()

        pdf.ln(5)
        pdf.set_draw_color(0, 51, 102)
        pdf.set_line_width(0.5)

        # Card border
        y_start = pdf.get_y()
        pdf.rect(10, y_start, 190, 60)

        # Card header
        pdf.set_fill_color(0, 51, 102)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("helvetica", "B", 12)
        pdf.set_xy(10, y_start)
        pdf.cell(190, 10, "HERMES DECISION", ln=True, align="C", fill=True)

        # Card content
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("helvetica", "", 10)
        pdf.set_xy(15, y_start + 12)

        lines = [
            f"Recommendation: {card.recommendation}",
            f"Decision: {card.decision}",
            f"Confidence: {card.confidence:.0%}" if card.confidence else "Confidence: N/A",
            f"Policy: {card.policy_status}",
            f"Evidence: {card.evidence_count} sources ({card.verified_count} verified)",
            f"Risks: {card.risk_count}",
            f"Approved by: {card.approver}" if card.approver else "Approved by: Pending",
            f"Execution: {card.execution_status}",
        ]

        for line in lines:
            pdf.cell(0, 6, line, ln=True)
            pdf.set_x(15)

        pdf.set_y(y_start + 65)
