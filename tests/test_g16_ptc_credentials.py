"""G16②③ 测试：凭据独立通道 + PTC run_code（v1.23 落地）。"""

import pytest

from src.security import CredentialStore
from src.tools import ToolContext, ToolRegistry


class TestCredentialStore:
    """凭据独立通道（G16③：日志零明文 + 引用替代 + 轮换不改事件流）。"""

    def test_set_get(self, tmp_path):
        store = CredentialStore(str(tmp_path / "creds.yaml"))
        store.set("api_keys.xfyun", "sk-test-123")
        assert store.get("api_keys.xfyun") == "sk-test-123"

    def test_persist_reload(self, tmp_path):
        path = tmp_path / "creds.yaml"
        store1 = CredentialStore(str(path))
        store1.set("api_keys.xfyun", "sk-abc")
        store2 = CredentialStore(str(path))  # 重新加载
        assert store2.get("api_keys.xfyun") == "sk-abc"

    def test_reference_not_plaintext(self, tmp_path):
        """事件流只存引用，不存明文。"""
        store = CredentialStore(str(tmp_path / "c.yaml"))
        store.set("api_keys.xfyun", "sk-super-secret-99")
        ref = store.reference("api_keys.xfyun")
        assert ref == "{{credential:api_keys.xfyun}}"
        assert "sk-super-secret-99" not in ref  # 引用无明文

    def test_resolve_reference(self, tmp_path):
        store = CredentialStore(str(tmp_path / "c.yaml"))
        store.set("api_keys.xfyun", "sk-real-value")
        resolved = store.resolve("curl -H 'Authorization: {{credential:api_keys.xfyun}}'")
        assert "sk-real-value" in resolved
        assert "{{credential" not in resolved

    def test_scrub_plaintext(self, tmp_path):
        """事件写前清洗：明文凭据被脱敏。"""
        store = CredentialStore(str(tmp_path / "c.yaml"))
        cleaned = store.scrub({"command": "export OPENAI_API_KEY=sk-abcdefghij1234567890"})
        assert "sk-abcdefghij1234567890" not in str(cleaned)
        assert "***" in str(cleaned)

    def test_has_plaintext_detection(self, tmp_path):
        store = CredentialStore(str(tmp_path / "c.yaml"))
        assert store.has_plaintext("api_key: sk-abcdefghij1234567890") is True
        assert store.has_plaintext("echo hello world") is False

    def test_rotation_does_not_change_events(self, tmp_path):
        """凭据轮换不改事件流（事件流只引用 credential_id）。"""
        store = CredentialStore(str(tmp_path / "c.yaml"))
        store.set("api_keys.xfyun", "sk-v1")
        ref = store.reference("api_keys.xfyun")
        # 事件流存的是引用
        event_payload = f"call with {ref}"
        # 轮换
        store.set("api_keys.xfyun", "sk-v2")
        # 事件流不变（引用还在），解析出新值
        assert event_payload == "call with {{credential:api_keys.xfyun}}"
        assert "sk-v2" in store.resolve(event_payload)


class TestRunCodeTool:
    """PTC 程序化工具调用（G16②：一次 run_code 组合多步，中间过程不进上下文）。"""

    def _registry(self):
        from src.tools import run_code as _run_code  # noqa: F401 模块导入触发 register_tool
        from src.tools.builtins import exec_tools as et
        from src.tools.builtins import file_tools as ft
        from src.tools.run_code import RunCodeTool

        reg = ToolRegistry()
        reg.register(et.BashTool())
        reg.register(ft.ReadTool())
        reg.register(RunCodeTool())
        return reg

    def test_ptc_returns_final_result(self, tmp_path):
        """中间 print 捕获，只返回最终结果（中间过程不进模型上下文）。"""
        reg = self._registry()
        result = reg.call("run_code", {"code": "x = 1\ny = 2\nprint('intermediate')\nreturn x + y"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "ok"
        assert "[return] 3" in result["result"]
        assert "intermediate" in result["result"]  # print 也被捕获（但不进模型主上下文）

    def test_ptc_no_output(self, tmp_path):
        reg = self._registry()
        result = reg.call("run_code", {"code": "x = 42"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "ok"
        assert "[no output]" in result["result"]

    def test_ptc_loop_composition(self, tmp_path):
        """循环组合多步：写循环/条件（PTC 核心价值：少往返）。"""
        reg = self._registry()
        code = """
results = []
for i in range(5):
    if i % 2 == 0:
        results.append(i * 10)
return results
"""
        result = reg.call("run_code", {"code": code}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "ok"
        assert "0" in result["result"] and "20" in result["result"] and "40" in result["result"]
        assert "1" not in result["result"].split("[return]")[1]  # 奇数被过滤

    def test_ptc_untrusted_tool_blocked(self, tmp_path):
        """程序内只能调白名单工具（防绕过安全）。"""
        reg = self._registry()
        result = reg.call("run_code", {"code": "tools.evil_tool(x=1)"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "error"
        assert "不在 PTC 白名单" in result["error"]

    def test_ptc_error_traceback(self, tmp_path):
        reg = self._registry()
        result = reg.call("run_code", {"code": "raise ValueError('boom')"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "error"
        assert "boom" in result["error"]
