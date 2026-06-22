"""Claude Code hooks 生态兼容（v1.26，G16——对标 DSH hooks-claude-code）。

在自建 Hook 框架（G1 7 事件点）之上加**生态兼容层**：直接读未修改的
Claude Code `hooks.json`（SessionStart/PreToolUse/PostToolUse/Stop/
SubagentStart/SubagentStop 等），解析后映射到 G1 7 事件点执行——企业已有
Claude Code hooks 配置零迁移可用（生态复用而非自造格式）。

纪律（对齐 DSH）：
- `updatedInput` 等不支持的语义**显式告警不静默吞**（skipped 列表记录，
  防止"hook 配置了但没生效"的静默失败）
- hook 执行受流水线守卫（超时等——由调用方接入 HooksManager.run）

范式声明：函数式 + 薄类（纯解析）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# Claude Code hooks 事件 → G1 事件点映射
# 支持的：SessionStart/PreToolUse/PostToolUse/Stop/SubagentStart/SubagentStop
# 不支持的（显式告警进 skipped）：PreCompact 等需特殊上下文语义的类型
CC_HOOK_TO_EVENT: dict[str, str] = {
    "SessionStart": "on_session_start",
    "UserPromptSubmit": "on_user_prompt_submit",
    "PreToolUse": "on_pre_tool_use",
    "PostToolUse": "on_post_tool_use",
    "Stop": "on_stop",
    "SubagentStart": "on_pre_tool_use",   # 子代理委派 = 工具调用前（G1 语义）
    "SubagentStop": "on_post_tool_use",   # 子代理返回 = 工具调用后
}

# 已知但不支持的类型（进 skipped 告警，不静默吞）
# PreCompact 等需特殊上下文语义的类型不映射（DSH 同样跳过并告警）
KNOWN_UNSUPPORTED = {"Notification", "PreCompact", "SessionEnd"}


class ClaudeCodeHookLoader:
    """Claude Code hooks.json 解析器（未修改格式直接读）。"""

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)

    def parse(self) -> list[dict]:
        """解析 hooks.json → 映射后的 hook 列表 [{event, matcher, command}]。

        失败（缺失/损坏 JSON）→ 返回空（不崩，调用方告警）。
        """
        hooks, _ = self.parse_with_skipped()
        return hooks

    def parse_with_skipped(self) -> tuple[list[dict], list[str]]:
        """解析 hooks.json → (hooks, skipped)。

        - hooks: 映射成功的 [{event, matcher, command, timeout_ms?}]
        - skipped: 不支持的 hook 类型描述（显式告警用）
        """
        hooks: list[dict] = []
        skipped: list[str] = []
        try:
            raw = self.config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            return hooks, skipped  # 缺失/损坏：空列表（调用方决定告警）

        hook_groups = data.get("hooks", {}) if isinstance(data, dict) else {}
        for cc_event, entries in hook_groups.items():
            event_point = CC_HOOK_TO_EVENT.get(cc_event)
            if event_point is None:
                skipped.append(f"{cc_event}: unsupported hook type（未映射到 G1 事件点）")
                continue
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                matcher = entry.get("matcher", "*")
                for h in entry.get("hooks", []):
                    if not isinstance(h, dict):
                        continue
                    htype = h.get("type")
                    if htype != "command":
                        skipped.append(f"{cc_event}/{matcher}: type={htype} 不支持（仅 command hooks 运行）")
                        continue
                    hooks.append({
                        "event": event_point,
                        "matcher": matcher,
                        "command": h.get("command", ""),
                        "timeout_ms": h.get("timeout", 600000),
                        "source": f"claude-code:{cc_event}",
                    })
        return hooks, skipped


def load_claude_code_hooks(config_path: str | Path) -> tuple[list[dict], list[str]]:
    """顶层入口：加载 Claude Code hooks → (映射后 hooks, skipped 告警列表)。"""
    loader = ClaudeCodeHookLoader(config_path)
    return loader.parse_with_skipped()
