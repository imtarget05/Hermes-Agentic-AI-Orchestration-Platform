from hermes.router import RouterAgent, RoutingRegistry


def test_registry_resolve(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"projects": {"demo": {"channel": "@c", "thread_id": 2}}}')
    reg = RoutingRegistry(str(p))
    assert reg.resolve("demo").channel == "@c"
    assert reg.resolve("missing").channel == "@hermes_default"


def test_router_hint_and_fallback(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"projects": {"demo": {"channel": "@c", "thread_id": 1}}}')
    reg = RoutingRegistry(str(p))
    r = RouterAgent(reg)
    proj, _ = r.route("hello", "demo")
    assert proj == "demo"
    proj2, _ = r.route("hello world", "")
    assert proj2 == "default"
