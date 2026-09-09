"""PDF renderer — converts NormalizedReport to PDF bytes."""
from __future__ import annotations

from .models import DecisionCard, NormalizedReport, ReportTier


class HermesPDF:
    """Custom PDF class with Hermes branding."""

    def __init__(self):
        from fpdf import FPDF
        self._pdf = FPDF()
        self._pdf.alias_nb_pages()
        self._pdf.set_auto_page_break(auto=True, margin=15)

    def header(self):
        self._pdf.set_font("helvetica", "B", 9)
        self._pdf.set_text_color(100, 100, 100)
        self._pdf.cell(0, 8, "HERMES — Multi-Agent Decision Intelligence", ln=True, align="R")
        self._pdf.set_draw_color(200, 200, 200)
        self._pdf.line(10, self._pdf.get_y(), 200, self._pdf.get_y())
        self._pdf.ln(3)

    def footer(self):
        self._pdf.set_y(-15)
        self._pdf.set_font("helvetica", "I", 8)
        self._pdf.set_text_color(150, 150, 150)
        self._pdf.cell(0, 10, f"Page {self._pdf.page_no()}/{{nb}}", align="C")

    def add_page(self):
        self._pdf.add_page()

    def set_font(self, style: str = "", size: int = 10):
        self._pdf.set_font("helvetica", style, size)

    def set_text_color(self, r: int, g: int, b: int):
        self._pdf.set_text_color(r, g, b)

    def set_draw_color(self, r: int, g: int, b: int):
        self._pdf.set_draw_color(r, g, b)

    def set_fill_color(self, r: int, g: int, b: int):
        self._pdf.set_fill_color(r, g, b)

    def set_line_width(self, w: float):
        self._pdf.set_line_width(w)

    def cell(self, w: float, h: float, txt: str, border: int = 0, ln: int = 0, align: str = "", fill: bool = False):
        self._pdf.cell(w, h, txt, border, ln, align, fill)

    def multi_cell(self, w: float, h: float, txt: str):
        self._pdf.multi_cell(w, h, txt)

    def get_y(self) -> float:
        return self._pdf.get_y()

    def set_y(self, y: float):
        self._pdf.set_y(y)

    def ln(self, h: float = 0):
        self._pdf.ln(h)

    def line(self, x1: float, y1: float, x2: float, y2: float):
        self._pdf.line(x1, y1, x2, y2)

    def output(self) -> bytes:
        return self._pdf.output()


class PDFRenderer:
    """Renders NormalizedReport to PDF."""

    def _sanitize_text(self, text: str) -> str:
        """Replace Unicode characters with ASCII alternatives for Helvetica font."""
        replacements = {
            "\u2022": "-",  # bullet
            "\u2713": "OK",  # checkmark
            "\u2717": "FAIL",  # cross
            "\u2605": "*",  # star
            "\u2014": "-",  # em dash
            "\u2013": "-",  # en dash
            "\u2018": "'",  # left single quote
            "\u2019": "'",  # right single quote
            "\u201c": '"',  # left double quote
            "\u201d": '"',  # right double quote
            "\u2026": "...",  # ellipsis
            "\u00b0": " degrees",  # degree symbol
            "\u00d7": "x",  # multiplication sign
            "\u00f7": "/",  # division sign
        }
        for unicode_char, ascii_replacement in replacements.items():
            text = text.replace(unicode_char, ascii_replacement)
        return text

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
        pdf.set_font("B", 18)
        pdf.set_text_color(0, 51, 102)
        pdf.cell(0, 12, report.title, ln=True, align="C")

        if report.subtitle:
            pdf.set_font("", 12)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(0, 8, report.subtitle, ln=True, align="C")

        pdf.set_font("", 10)
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
        pdf.set_font("B", 14)
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
        pdf.set_font("B", 11)
        pdf.set_text_color(51, 51, 51)
        pdf.cell(0, 8, section.title, ln=True)

        # Table or content
        if section.table_data:
            self._render_table(pdf, section.table_data)
        elif section.content:
            pdf.set_font("", 10)
            pdf.set_text_color(0, 0, 0)
            pdf.multi_cell(0, 5, section.content)

        pdf.ln(4)

    def _render_table(self, pdf: HermesPDF, data: list[list[str]]):
        if not data:
            return

        num_cols = len(data[0])
        col_width = 190 / num_cols

        # Header
        pdf.set_font("B", 8)
        pdf.set_fill_color(230, 230, 230)
        for cell in data[0]:
            pdf.cell(col_width, 7, cell[:15], 1, 0, "C", True)
        pdf.ln()

        # Data rows
        pdf.set_font("", 8)
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
        pdf.set_font("B", 12)
        pdf.set_xy(10, y_start)
        pdf.cell(190, 10, "HERMES DECISION", ln=True, align="C", fill=True)

        # Card content
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("", 10)
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
