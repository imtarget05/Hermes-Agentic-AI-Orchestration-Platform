"""Unit tests for P0-1 Intent Router (deterministic, no LLM)."""
from __future__ import annotations

import pytest

from hermes.router import (
    AGENT_ANALYSIS,
    AGENT_CONTRACT,
    AGENT_PRICE,
    AGENT_SPEC,
    AGENT_VENDOR,
    AGENT_VERIFICATION,
    COMPARE_AGENTS,
    FULL_PROCUREMENT_AGENTS,
    SIMPLE_AGENTS,
    Complexity,
    Intent,
    RoutingPlan,
    build_routing_plan,
    classify_intent,
    route,
)


class TestClassifyIntent:
    """Test intent classification logic."""

    def test_help_intent(self):
        assert classify_intent("/help") == Intent.HELP
        assert classify_intent("/start") == Intent.HELP
        assert classify_intent("help") == Intent.HELP
        assert classify_intent("xin chào") == Intent.HELP
        assert classify_intent("hello") == Intent.HELP

    def test_inbox_intent(self):
        assert classify_intent("/tasks") == Intent.INBOX
        assert classify_intent("inbox") == Intent.INBOX
        assert classify_intent("danh sách") == Intent.INBOX
        assert classify_intent("/task 123") == Intent.INBOX

    def test_approval_intent(self):
        assert classify_intent("approve ABC123") == Intent.APPROVAL
        assert classify_intent("duyệt ABC123") == Intent.APPROVAL
        assert classify_intent("reject REQ123") == Intent.APPROVAL
        assert classify_intent("từ chối REQ123") == Intent.APPROVAL

    def test_price_question_intent(self):
        assert classify_intent("giá laptop dell bao nhiêu?") == Intent.PRICE_QUESTION
        assert classify_intent("giá thị trường hiện nay") == Intent.PRICE_QUESTION
        assert classify_intent("cập nhật giá") == Intent.PRICE_QUESTION
        # Spec constraint should NOT be price question
        assert classify_intent("RAM 16GB, giá dưới 30 triệu") != Intent.PRICE_QUESTION
        # "price check" / "check price" are simple tasks (check specific price), not market questions
        assert classify_intent("price check") == Intent.SIMPLE_TASK
        assert classify_intent("check price") == Intent.SIMPLE_TASK

    def test_simple_task_quote_validity(self):
        assert classify_intent("check quote validity") == Intent.SIMPLE_TASK
        assert classify_intent("còn hiệu lực không") == Intent.SIMPLE_TASK
        assert classify_intent("quote date") == Intent.SIMPLE_TASK
        assert classify_intent("quote valid") == Intent.SIMPLE_TASK
        assert classify_intent("hết hạn") == Intent.SIMPLE_TASK

    def test_simple_task_vendor_check(self):
        assert classify_intent("check vendor") == Intent.SIMPLE_TASK
        assert classify_intent("is vendor approved") == Intent.SIMPLE_TASK

    def test_simple_task_price_check(self):
        assert classify_intent("check price") == Intent.SIMPLE_TASK
        assert classify_intent("giá check") == Intent.SIMPLE_TASK
        assert classify_intent("check giá") == Intent.SIMPLE_TASK
        # "so sánh giá" is compare task
        assert classify_intent("so sánh giá") == Intent.COMPARE_TASK

    def test_simple_task_spec_check(self):
        assert classify_intent("check spec") == Intent.SIMPLE_TASK
        assert classify_intent("đủ spec không") == Intent.SIMPLE_TASK
        assert classify_intent("spec score") == Intent.SIMPLE_TASK

    def test_compare_task(self):
        assert classify_intent("so sánh vendor") == Intent.COMPARE_TASK
        assert classify_intent("compare vendors") == Intent.COMPARE_TASK
        assert classify_intent("which vendor") == Intent.COMPARE_TASK
        assert classify_intent("vendor comparison") == Intent.COMPARE_TASK
        assert classify_intent("giá nào rẻ") == Intent.COMPARE_TASK
        assert classify_intent("cheapest") == Intent.COMPARE_TASK
        assert classify_intent("so sánh giá") == Intent.COMPARE_TASK

    def test_full_procurement_intent(self):
        assert classify_intent("mua 50 laptop") == Intent.PROCUREMENT_DECISION
        assert classify_intent("procurement") == Intent.PROCUREMENT_DECISION
        assert classify_intent("báo giá laptop") == Intent.PROCUREMENT_DECISION
        assert classify_intent("mua sắm thiết bị") == Intent.PROCUREMENT_DECISION
        # With spec details
        assert classify_intent("mua 50 laptop RAM 16GB CPU i7") == Intent.PROCUREMENT_DECISION
        # With multiple quotes implied
        assert classify_intent("quote dell lenovo hp") == Intent.PROCUREMENT_DECISION

    def test_chitchat_fallback(self):
        assert classify_intent("random text") == Intent.CHITCHAT
        assert classify_intent("how are you") == Intent.CHITCHAT

    def test_session_state_awaiting_spec(self):
        assert classify_intent("RAM 16GB SSD 512GB", session_state="awaiting_spec") == Intent.PROCUREMENT_DECISION

    def test_quote_count_heuristic(self):
        # Multiple quotes mentioned should push to full procurement
        assert classify_intent("quote 1 quote 2 quote 3") == Intent.PROCUREMENT_DECISION


class TestBuildRoutingPlan:
    """Test routing plan generation."""

    def test_simple_task_plan(self):
        plan = build_routing_plan(Intent.SIMPLE_TASK, "check quote validity")
        assert plan.intent == Intent.SIMPLE_TASK
        assert plan.required_agents == [AGENT_CONTRACT]
        assert plan.estimated_complexity == Complexity.LOW
        assert plan.estimated_latency_ms == 2000

    def test_compare_task_plan(self):
        plan = build_routing_plan(Intent.COMPARE_TASK, "so sánh vendor")
        assert plan.intent == Intent.COMPARE_TASK
        assert plan.required_agents == COMPARE_AGENTS
        assert plan.estimated_complexity == Complexity.MEDIUM
        assert plan.estimated_latency_ms == 8000

    def test_full_procurement_plan(self):
        plan = build_routing_plan(Intent.PROCUREMENT_DECISION, "mua 50 laptop")
        assert plan.intent == Intent.PROCUREMENT_DECISION
        assert plan.required_agents == FULL_PROCUREMENT_AGENTS
        assert plan.estimated_complexity == Complexity.HIGH
        assert plan.estimated_latency_ms == 30000

    def test_non_procurement_plan(self):
        for intent in [Intent.HELP, Intent.INBOX, Intent.APPROVAL, Intent.PRICE_QUESTION, Intent.CHITCHAT]:
            plan = build_routing_plan(intent, "text")
            assert plan.required_agents == []
            assert plan.estimated_complexity == Complexity.LOW
            assert plan.estimated_latency_ms == 500

    def test_quote_count_and_spec_details(self):
        quotes = [{"vendor": "Dell"}, {"vendor": "Lenovo"}, {"vendor": "HP"}]
        plan = build_routing_plan(Intent.PROCUREMENT_DECISION, "mua laptop RAM 16GB", quotes)
        assert plan.quote_count == 3
        assert plan.has_spec_details is True


class TestRoute:
    """Test main route() entry point."""

    def test_route_simple_task(self):
        plan = route("check quote validity")
        assert plan.intent == Intent.SIMPLE_TASK
        assert plan.required_agents == [AGENT_CONTRACT]

    def test_route_compare_task(self):
        plan = route("so sánh vendor dell lenovo")
        assert plan.intent == Intent.COMPARE_TASK
        assert plan.required_agents == COMPARE_AGENTS

    def test_route_full_procurement(self):
        plan = route("mua 50 laptop RAM 16GB CPU i7 bảo hành 3 năm")
        assert plan.intent == Intent.PROCUREMENT_DECISION
        assert plan.required_agents == FULL_PROCUREMENT_AGENTS
        assert plan.has_spec_details is True

    def test_route_price_question(self):
        plan = route("giá laptop dell bao nhiêu?")
        assert plan.intent == Intent.PRICE_QUESTION
        assert plan.required_agents == []

    def test_route_with_quotes(self):
        quotes = [{"vendor": "Dell"}, {"vendor": "Lenovo"}]
        plan = route("mua laptop", quotes=quotes)
        assert plan.quote_count == 2


class TestRoutingPlanModel:
    """Test RoutingPlan Pydantic model."""

    def test_serialization(self):
        plan = RoutingPlan(
            intent=Intent.PROCUREMENT_DECISION,
            required_agents=FULL_PROCUREMENT_AGENTS,
            estimated_complexity=Complexity.HIGH,
            estimated_latency_ms=30000,
            quote_count=3,
            has_spec_details=True,
            reasoning="full_procurement:quotes=3,spec=True",
        )
        json_str = plan.model_dump_json()
        assert "procurement_decision" in json_str
        assert "price" in json_str
        assert "vendor" in json_str
        assert "contract" in json_str
        assert "spec" in json_str
        assert "analysis" in json_str
        assert "verification" in json_str


class TestAgentConstants:
    """Test agent constant definitions."""

    def test_full_procurement_agents(self):
        assert len(FULL_PROCUREMENT_AGENTS) == 6
        assert FULL_PROCUREMENT_AGENTS == [
            AGENT_PRICE,
            AGENT_VENDOR,
            AGENT_CONTRACT,
            AGENT_SPEC,
            AGENT_ANALYSIS,
            AGENT_VERIFICATION,
        ]

    def test_compare_agents(self):
        assert len(COMPARE_AGENTS) == 3
        assert COMPARE_AGENTS == [AGENT_PRICE, AGENT_VENDOR, AGENT_SPEC]

    def test_simple_agents(self):
        assert SIMPLE_AGENTS["quote_validity"] == [AGENT_CONTRACT]
        assert SIMPLE_AGENTS["vendor_check"] == [AGENT_VENDOR]
        assert SIMPLE_AGENTS["price_check"] == [AGENT_PRICE]
        assert SIMPLE_AGENTS["spec_check"] == [AGENT_SPEC]


# ---- New-domain intents (Phase 1-4) ----

def test_classify_advisor():
    plan = route("nên làm gì với dòng tiền tháng này? cố vấn ơi")
    assert plan.intent.value == "ask_advisor"
    assert plan.required_agents == ["advisor"]


def test_classify_ops_status():
    plan = route("hôm nay cần chú ý gì?")
    assert plan.intent.value == "ops_status"


def test_classify_ops_connect():
    plan = route("kết nối nguồn CRM")
    assert plan.intent.value == "ops_connect"


def test_classify_competitor_brief():
    plan = route("weekly brief đối thủ cạnh tranh")
    assert plan.intent.value == "competitor_brief"
    assert "competitor_collect" in plan.required_agents


def test_classify_kb_query_and_ingest():
    assert route("SOP quy trình onboarding là gì?").intent.value == "kb_query"
    assert route("lưu vào knowledge base giúp tôi").intent.value == "kb_ingest"


def test_classify_brain_query_and_ingest():
    assert route("hợp đồng của tôi lưu ở đâu?").intent.value == "brain_query"
    assert route("lưu tài liệu của tôi").intent.value == "brain_ingest"


def test_procurement_still_wins_over_new_domains():
    # 'mua' keywords take priority (checked before new domains)
    plan = route("mua 50 laptop dell lenovo")
    assert plan.intent.value == "procurement_decision"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
