import pytest
from hermes.tools import ToolExecutor, FatalToolError


def test_permission_denied():
    ex = ToolExecutor({"general"})
    with pytest.raises(FatalToolError):
        ex.call("write_file", path="x.txt", content="hi")


def test_injection_guard():
    ex = ToolExecutor({"general", "build"})
    with pytest.raises(FatalToolError):
        ex.call("write_file", path="x.txt", content="rm -rf /")


def test_allowlisted_shell_blocked():
    ex = ToolExecutor({"general", "build"})
    with pytest.raises(FatalToolError):
        ex.call("run_shell", cmd="rm -rf /")


def test_sandbox_write_read(tmp_path):
    ex = ToolExecutor({"general", "build"})
    sb = str(tmp_path)
    ex.call("write_file", path="a.txt", content="hello", sandbox=sb)
    ex2 = ToolExecutor({"general"})
    assert "hello" in ex2.call("read_file", path="a.txt", sandbox=sb)
