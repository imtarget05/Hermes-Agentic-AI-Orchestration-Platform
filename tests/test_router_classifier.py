from hermes.llm import build_router_classifier


class FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply

    def complete(self, prompt: str) -> str:
        assert "project" in prompt.lower()
        return self.reply


def test_classifier_picks_valid_project():
    clf = build_router_classifier(FakeLLM("demo"), ["default", "demo"])
    assert clf("anything") == "demo"


def test_classifier_rejects_unknown():
    clf = build_router_classifier(FakeLLM("nonsense"), ["default", "demo"])
    assert clf("anything") is None


def test_classifier_none_without_llm():
    assert build_router_classifier(None, ["default"]) is None
