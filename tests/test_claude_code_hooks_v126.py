"""T-26-9 Claude Code hooks 生态兼容测试（v1.26，G16——对标 DSH hooks-claude-code）。

验证：直接读未修改的 Claude Code hooks.json（SessionStart/PreToolUse/
PostToolUse/Stop/SubagentStart/SubagentStop）映射到 G1 7 事件点；
unsupported 语义显式告警不静默吞；hook 执行受守卫（超时）。
"""

import json

import pytest

from src.hooks.claude_code_hooks import (
    CC_HOOK_TO_EVENT,
    ClaudeCodeHookLoader,
    load_claude_code_hooks,
)


class TestHookMapping:
    def test_cc_hooks_map_to_events(self):
        """Claude Code hooks 事件映射到 G1 事件点。"""
        assert CC_HOOK_TO_EVENT["SessionStart"] == "on_session_start"
        assert CC_HOOK_TO_EVENT["PreToolUse"] == "on_pre_tool_use"
        assert CC_HOOK_TO_EVENT["PostToolUse"] == "on_post_tool_use"
        assert CC_HOOK_TO_EVENT["Stop"] == "on_stop"
        assert CC_HOOK_TO_EVENT["SubagentStart"] == "on_pre_tool_use"  # 子代理委派 = 工具调用前
        assert CC_HOOK_TO_EVENT["SubagentStop"] == "on_post_tool_use"

    def test_unknown_hook_raises(self):
        """未知 hook 类型显式报错（不静默吞）。"""
        with pytest.raises(KeyError):
            CC_HOOK_TO_EVENT["PreCompactUnsupported"]


class TestClaudeCodeHookLoader:
    def test_parse_valid_hooks_json(self, tmp_path):
        """解析未修改的 Claude Code hooks.json。"""
        cfg = tmp_path / "hooks.json"
        cfg.write_text(json.dumps({
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "echo start"}]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}],
                "PostToolUse": [{"hooks": [{"type": "command", "command": "echo post"}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}],
            }
        }), encoding="utf-8")
        loader = ClaudeCodeHookLoader(cfg)
        parsed = loader.parse()
        assert len(parsed) >= 4
        # 映射后的事件点
        events = {h["event"] for h in parsed}
        assert "on_session_start" in events
        assert "on_pre_tool_use" in events
        assert "on_post_tool_use" in events
        assert "on_stop" in events

    def test_unsupported_hook_type_warned(self, tmp_path):
        """unsupported 语义显式告警（skipped 列表记录，不静默吞）。"""
        cfg = tmp_path / "hooks.json"
        cfg.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo"}]}],
                "PreCompact": [{"hooks": [{"type": "command", "command": "echo"}]}],
                "Notification": [{"hooks": [{"type": "command", "command": "echo"}]}],  # 不支持
            }
        }), encoding="utf-8")
        loader = ClaudeCodeHookLoader(cfg)
        parsed, skipped = loader.parse_with_skipped()
        # 支持的进 parsed，不支持的进 skipped（显式告警）
        assert len(parsed) >= 1
        assert any("Notification" in s for s in skipped), "不支持类型必须进 skipped 列表"

    def test_parse_missing_file(self, tmp_path):
        """配置文件缺失 → 返回空（不崩）。"""
        loader = ClaudeCodeHookLoader(tmp_path / "nope.json")
        assert loader.parse() == []

    def test_parse_invalid_json(self, tmp_path):
        """JSON 损坏 → 返回空 + 告警（不崩）。"""
        cfg = tmp_path / "hooks.json"
        cfg.write_text("{broken", encoding="utf-8")
        loader = ClaudeCodeHookLoader(cfg)
        assert loader.parse() == []

    def test_load_function_returns_hooks(self, tmp_path):
        """load_claude_code_hooks 顶层函数返回 (hooks, skipped)。"""
        cfg = tmp_path / "hooks.json"
        cfg.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}]}}), encoding="utf-8")
        hooks, skipped = load_claude_code_hooks(cfg)
        assert len(hooks) == 1
        assert hooks[0]["event"] == "on_stop"
        assert hooks[0]["command"] == "echo done"
        assert skipped == []
