from hermes.llm import build_llm
from hermes.llm.cloudflare import CloudflareLLM
from hermes.agents import AGENTS, configure_agents_llm


def test_build_llm_stub_without_creds():
    assert build_llm("cloudflare", "@cf/meta/llama-3.1-8b-instruct", "", "") is None
    assert build_llm("stub") is None


def test_cloudflare_url():
    llm = CloudflareLLM("acct123", "tok", "@cf/meta/llama-3.1-8b-instruct")
    assert llm.url == "https://api.cloudflare.com/client/v4/accounts/acct123/ai/run/@cf/meta/llama-3.1-8b-instruct"


def test_cloudflare_complete_parses_response(monkeypatch):
    llm = CloudflareLLM("a", "t", "m")

    class FakeResp:
        status_code = 200
        text = "ok"

        def json(self):
            return {"success": True, "result": {"response": "hello AI"}}

    monkeypatch.setattr("hermes.llm.cloudflare.httpx.post", lambda *a, **k: FakeResp())
    assert llm.complete("hi") == "hello AI"


def test_cloudflare_error_surfaces(monkeypatch):
    llm = CloudflareLLM("a", "t", "m")

    class FakeResp:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {"success": False, "errors": [{"message": "bad token"}]}

    monkeypatch.setattr("hermes.llm.cloudflare.httpx.post", lambda *a, **k: FakeResp())
    try:
        llm.complete("hi")
        raise AssertionError("should raise")
    except Exception as e:
        assert "401" in str(e)


def test_configure_agents_llm():
    configure_agents_llm(None)
    assert all(a.llm is None for a in AGENTS.values())
    fn = lambda p: "x"  # noqa
    configure_agents_llm(fn)
    assert all(a.llm is fn for a in AGENTS.values())
    configure_agents_llm(None)  # reset to stub for other tests
