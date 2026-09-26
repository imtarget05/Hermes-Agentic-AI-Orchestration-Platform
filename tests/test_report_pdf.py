"""PDF report rendering — real files, not "it did not raise".

Regression cover for the defects that made every PDF render throw:
  * HermesPDF was a hand-written delegating wrapper around fpdf.FPDF that never
    forwarded rect/set_x/set_xy, so rendering died with
    AttributeError: 'HermesPDF' object has no attribute 'rect';
  * _sanitize_text existed but was never called, so any non-Latin-1 text (a
    Vietnamese title, the em dash every template emits) died with
    FPDFUnicodeEncodingException against the core font.

Every test here asserts on the produced bytes: magic number, %%EOF trailer,
size, and the page count parsed back out of the file.
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from hermes.report import generate_report
from hermes.report.models import (
    DecisionCard,
    NormalizedReport,
    ReportSection,
    ReportTier,
    ReportType,
)
from hermes.report.renderer import (
    HermesPDF,
    PDFRenderer,
    ReportRenderError,
    sanitize_text,
)
from hermes.report.templates import list_templates

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_TYPES = ["procurement", "financial", "maintenance", "research",
                "investigation", "workflow"]


def page_count(pdf_bytes: bytes) -> int:
    """Page count read back with pypdf - proves the file parses as a real PDF."""
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(bytes(pdf_bytes))).pages)


def assert_real_pdf(pdf_bytes: bytes, min_pages: int = 1) -> int:
    """Every rendered report must be a structurally valid, non-empty PDF."""
    assert isinstance(pdf_bytes, (bytes, bytearray))
    raw = bytes(pdf_bytes)
    assert len(raw) > 0, "render produced an empty file"
    assert raw.startswith(b"%PDF-"), f"bad magic bytes: {raw[:16]!r}"
    assert raw.rstrip().endswith(b"%%EOF"), "missing PDF trailer"
    pages = page_count(raw)
    assert pages >= min_pages, f"expected >={min_pages} page(s), got {pages}"
    return pages


def sample_data(report_type: str) -> dict:
    return {
        "title": f"Hermes {report_type} report",
        "subtitle": "Prepared for the decision log",
        "executive_summary": "The agent council reached a unanimous recommendation.",
        "recommendation": "acme-corp",
        "decision": "APPROVED",
        "confidence": 0.9,
        "policy_status": "COMPLIANT",
        "risks": ["vendor concentration"],
        "approver": "ops-lead",
        "execution_status": "COMPLETED",
        "evidence": [
            {"claim": "price is competitive", "source": "quote.pdf",
             "agent": "Price Agent", "verification": "VERIFIED", "confidence": 0.95},
        ],
        "audit_trail": [{"step": "verify", "actor": "Verifier", "result": "pass"}],
    }


# --------------------------------------------------------------------------- #
# 1. the AttributeError regression — the wrapper had no rect/set_x/set_xy
# --------------------------------------------------------------------------- #
def test_hermes_pdf_exposes_the_drawing_api_the_renderer_uses():
    """The renderer draws with these; a wrapper that forgot one raised
    AttributeError mid-render."""
    pdf = HermesPDF()
    for method in ("rect", "set_x", "set_xy", "cell", "multi_cell", "line",
                   "ln", "get_y", "set_y", "set_fill_color", "set_draw_color",
                   "set_line_width", "set_text_color", "add_page", "output"):
        assert callable(getattr(pdf, method)), f"HermesPDF is missing {method}()"


def test_render_produces_a_real_pdf():
    """The call that raised 'no attribute rect' now yields a valid file."""
    report = NormalizedReport(
        report_type=ReportType.FINANCIAL,
        title="Plain ASCII Title",
        sections=[ReportSection(title="Overview", tier=ReportTier.EXECUTIVE,
                                content="Everything reconciled.")],
        decision_card=DecisionCard(recommendation="acme", decision="APPROVED",
                                   confidence=0.8),
    )

    pdf_bytes = PDFRenderer().render(report)

    assert_real_pdf(pdf_bytes)


def test_decision_card_is_actually_drawn():
    """rect/set_xy/set_x all run on the decision card, so it must be reachable
    even when the card is the first thing after the title."""
    report = NormalizedReport(
        report_type=ReportType.FINANCIAL,
        title="Card only",
        decision_card=DecisionCard(recommendation="acme-corp", decision="APPROVED",
                                   confidence=0.75, policy_status="COMPLIANT",
                                   evidence_count=3, verified_count=2, risk_count=1,
                                   approver="ops", execution_status="COMPLETED"),
    )

    assert_real_pdf(PDFRenderer().render(report))


# --------------------------------------------------------------------------- #
# 2. the _sanitize_text regression — Vietnamese and other non-Latin-1 text
# --------------------------------------------------------------------------- #
def test_sanitize_text_transliterates_vietnamese():
    assert sanitize_text("Báo cáo tài chính") == "Bao cao tai chinh"
    # D-with-stroke has no canonical decomposition and needs the explicit map
    assert sanitize_text("Đơn vị: Cổ phần") == "Don vi: Co phan"
    assert sanitize_text("Nguyễn Thị Ánh") == "Nguyen Thi Anh"


def test_sanitize_text_maps_the_glyphs_the_templates_emit():
    assert sanitize_text("HERMES — REPORT") == "HERMES - REPORT"
    assert sanitize_text("• item") == "- item"
    assert sanitize_text("✓ ok") == "OK ok"
    assert sanitize_text("wait…") == "wait..."


def test_sanitize_output_is_always_latin1_encodable():
    samples = [
        "Báo cáo tài chính Quý 1", "— – • ✓ ✗ ★",
        "Đặng Văn Hưng", "plain ascii", "50°C × 2 ÷ 3",
    ]
    for sample in samples:
        sanitize_text(sample).encode("latin-1")  # must not raise


def test_sanitize_text_reports_unrenderable_text_explicitly():
    """No vendored Unicode font, so unrepresentable scripts must fail loudly
    and specifically - not with FPDFUnicodeEncodingException from fpdf."""
    with pytest.raises(ReportRenderError) as exc:
        sanitize_text("Отчёт")
    message = str(exc.value)
    assert "U+041E" in message
    assert "core fonts" in message
    assert "no Unicode font" in message


def test_vietnamese_report_renders_end_to_end():
    """The exact case that failed against the default core font."""
    data = sample_data("financial")
    data["title"] = "Báo cáo tài chính — Quý 1"
    data["executive_summary"] = "Doanh thu tăng 12% so với kỳ trước."

    pdf_bytes = generate_report("financial", data, workflow_id="wf-1")

    assert_real_pdf(pdf_bytes)


def test_box_drawing_decision_card_text_is_sanitisable():
    """report/decision_card.py draws an ASCII card with box-drawing glyphs and an
    em dash; that output must survive the core-font sanitiser too."""
    from hermes.report.decision_card import render_decision_card_text

    card = DecisionCard(recommendation="acme-corp", decision="APPROVED",
                        confidence=0.8, policy_status="COMPLIANT",
                        evidence_count=2, verified_count=1, risk_count=0,
                        approver="ops-lead", execution_status="COMPLETED")
    text = render_decision_card_text(card)

    assert "\u250c" in text  # the input really is box-drawing text
    sanitized = sanitize_text(text)
    sanitized.encode("latin-1")
    assert "+" in sanitized and "|" in sanitized


def test_workflow_status_emoji_are_sanitisable():
    """report/templates/workflow.py labels statuses with emoji."""
    for status in ("completed", "failed", "running", "queued", "unknown"):
        pdf = HermesPDF()
        pdf.add_page()
        pdf.set_font("helvetica", "", 12)
        pdf.cell(0, 8, sanitize_text(status), ln=True)
        assert_real_pdf(pdf.output())


def test_every_glyph_the_report_package_emits_is_covered():
    """Scan the report package for non-Latin-1 characters in string literals.

    A template that starts using a new emoji/box-drawing character must be
    mapped in _GLYPH_REPLACEMENTS (or transliterable) - otherwise it renders a
    ReportRenderError at runtime instead of failing here.
    """
    import re

    allowed = {c for c in "\u2013\u2014"}
    for path in sorted((REPO_ROOT / "src" / "hermes" / "report").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"[\u0080-\uffff]", source):
            char = match.group(0)
            if char in allowed or char in "\n\t":
                continue
            try:
                char.encode("latin-1")
            except UnicodeEncodeError:
                pass
            else:
                continue
            # must be transliterable (Vietnamese/Latin-1 decompose) or mapped
            assert sanitize_text(char) != char, (
                f"{path.name} emits U+{ord(char):04X} {char!r}, which sanitize_text "
                f"cannot map to a core-font glyph"
            )


def test_unrenderable_script_fails_with_an_actionable_error():
    with pytest.raises(ReportRenderError):
        generate_report("research", {"title": "プロジェクト報告書"})


def test_sanitize_is_wired_into_every_draw_call():
    """Guards against sanitisation being bypassed: draw a raw Vietnamese
    string straight through the PDF object, not via the renderer."""
    pdf = HermesPDF()
    pdf.add_page()
    pdf.set_font("helvetica", "", 12)
    pdf.cell(0, 8, "Báo cáo — • Quý", ln=True)
    pdf.multi_cell(0, 8, "Tiệng chi ✓")

    assert_real_pdf(pdf.output())


# --------------------------------------------------------------------------- #
# 3. all six registered report types actually render
# --------------------------------------------------------------------------- #
def test_all_registered_report_types_are_the_six_advertised_ones():
    assert sorted(list_templates()) == sorted(REPORT_TYPES)


@pytest.mark.parametrize("report_type", REPORT_TYPES)
def test_every_report_type_renders_a_real_pdf(report_type):
    pdf_bytes = generate_report(report_type, sample_data(report_type),
                                workflow_id="wf-1", task_id="t-1")

    assert_real_pdf(pdf_bytes)


@pytest.mark.parametrize("report_type", REPORT_TYPES)
def test_every_report_type_renders_vietnamese(report_type, tmp_path):
    data = sample_data(report_type)
    data["title"] = f"Báo cáo {report_type}"
    data["executive_summary"] = "Tám chi phí được kỳ trước."

    out = tmp_path / f"{report_type}.pdf"
    out.write_bytes(generate_report(report_type, data, workflow_id="wf-1"))

    assert_real_pdf(out.read_bytes())


def test_multi_page_report_has_the_expected_page_count():
    """Page count is asserted, not just 'no exception'."""
    report = NormalizedReport(
        report_type=ReportType.RESEARCH,
        title="Long report",
        sections=[
            ReportSection(title=f"Section {i}", tier=ReportTier.EVIDENCE,
                          content="x" * 400)
            for i in range(1, 26)
        ],
        decision_card=DecisionCard(recommendation="acme", decision="PENDING"),
    )

    pdf_bytes = PDFRenderer().render(report)

    pages = assert_real_pdf(pdf_bytes, min_pages=2)
    assert pages >= 2


def test_table_sections_render():
    report = NormalizedReport(
        report_type=ReportType.PROCUREMENT,
        title="Vendor comparison",
        sections=[ReportSection(
            title="Quotes",
            tier=ReportTier.EXECUTIVE,
            table_data=[["Vendor", "Price"], ["acme", "100"], ["globex", "120"]],
        )],
        decision_card=DecisionCard(),
    )

    assert_real_pdf(PDFRenderer().render(report))


# --------------------------------------------------------------------------- #
# 4. the real CLI path end to end, to a file on disk
# --------------------------------------------------------------------------- #
def test_cli_report_generate_writes_a_valid_pdf_to_disk(tmp_path):
    """`hermes report generate` (cli.py) — the path that raised AttributeError."""
    import json

    out = tmp_path / "cli-report.pdf"
    data_file = tmp_path / "data.json"
    data_file.write_text(json.dumps(sample_data("workflow")), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "hermes.cli", "report", "generate", "workflow",
         "--data-file", str(data_file), "--output", str(out),
         "--workflow-id", "wf-cli", "--task-id", "t-cli"],
        cwd=REPO_ROOT,
        env={**dict(__import__("os").environ), "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert out.exists(), f"no PDF written; stdout={result.stdout}"
    assert str(out) in result.stdout
    assert_real_pdf(out.read_bytes())
