"""Multi-Agent RAG tests: index, retrieval ranking, tool, case wiring."""
from __future__ import annotations

from hermes.rag import RagIndex, build_case_index, format_hits, ingest_vendors
from hermes.runtime import default_demo_quotes
from hermes.tools import ToolExecutor


def _index():
    return build_case_index(default_demo_quotes(), "Intel i7 16GB RAM 512GB SSD")


def test_index_ingests_quotes_vendors_spec():
    idx = _index()
    assert len(idx) == 3 + 3 + 1  # quotes + vendors + required spec
    uris = {c.source_uri for c in idx.chunks}
    assert {"demo/dell.pdf", "demo/lenovo.pdf", "demo/hp.pdf", "vendors.json"} <= uris


def test_retrieval_prefers_relevant_vendor_registry():
    idx = _index()
    top = idx.query("is HP an approved vendor", top_k=1)[0]
    assert top.chunk.source_uri == "vendors.json"
    assert "NOT approved" in top.chunk.text


def test_retrieval_finds_lenovo_terms():
    idx = _index()
    hits = idx.query("Lenovo payment warranty SLA terms", top_k=2)
    assert hits and hits[0].chunk.source_uri == "demo/lenovo.pdf"


def test_retrieval_empty_query_and_corpus():
    assert RagIndex().query("anything") == []
    assert _index().query("   ") == []


def test_index_roundtrip_json(tmp_path):
    p = str(tmp_path / "idx.json")
    _index().save(p)
    assert len(RagIndex.load(p)) == 7


def test_retrieve_evidence_tool(tmp_path, monkeypatch):
    p = str(tmp_path / "idx.json")
    _index().save(p)
    monkeypatch.setenv("HERMES_RAG_INDEX", p)
    ex = ToolExecutor({"general"})
    out = ex.call("retrieve_evidence", query="HP approved vendor", top_k=1)
    assert "vendors.json" in out
    assert "NOT approved" in out


def test_ingest_vendors_fallback_without_file(tmp_path):
    idx = ingest_vendors(RagIndex(), vendors_path=str(tmp_path / "missing.json"))
    assert len(idx) == 3


def test_format_hits_empty():
    assert format_hits([]) == "(no evidence retrieved)"
