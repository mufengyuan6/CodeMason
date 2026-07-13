"""根因分析工具（v1.28 落地，G20 溯源报告工具——能力接缝 G16 注册为内核工具）。

design.md G20 落点：
- 溯源报告工具（analyze_root_cause，能力接缝 G16 注册为内核工具）
- 溯源触发：VerifyFailed / Error / 用户"为什么挂"（结构性防误报，非全库扫描）
- 产出：溯源报告（三阶段定位 + 证据集 + 修复指令，机读可消费）

本工具 = RootCauseAnalyzer 的内核工具封装：
- 读取 EventLog 失败事件 → 确定性证据链（图谱 BFS + 失败链 + FixPacket + YAGNI 外环）
- LLM 归因假设（可注入 attribution_fn，缺省纯确定性降级）
- 产出 RootCauseReport 事件（溯源即事件）+ 诊断回喂载荷（CodeTracer 反思回放）

范式声明：工具层 OOP（Tool 协议 + 模块级 register_tool）。
"""

from __future__ import annotations

from typing import Optional

from ..base import Tool, ToolContext
from ..registry import register_tool


class AnalyzeRootCauseTool(Tool):
    name = "analyze_root_cause"
    description = (
        "事件驱动根因分析（G20）：失败/疑问触发溯源——确定性证据链（图谱 BFS 影响面 + "
        "事件流失败链 + FixPacket 机读契约 + YAGNI 外环）→ LLM 归因假设（agent_inferred）→ "
        "溯源报告（search/read/edit 三阶段定位 + 证据集 + 修复指令）→ 诊断回喂。"
        "验证失败的下一跳不是'再试一次'而是'先溯源'。"
    )
    parameters = {
        "trigger": {
            "type": "string",
            "description": "触发源: verify_failed / error / user_query（仅失败/疑问触发，非全库扫描）",
        },
        "trigger_event_id": {
            "type": "integer",
            "description": "失败锚点事件 id（0 = 取该会话最近失败事件）",
        },
        "session_id": {"type": "string", "description": "会话 id（默认 current）"},
        "files": {"type": "array", "items": {"type": "string"}, "description": "YAGNI 外环扫描的文件路径列表"},
    }

    def __init__(self, analyzer=None) -> None:
        """注入 RootCauseAnalyzer（测试/服务挂载注入；None 懒加载）。"""
        self._analyzer = analyzer

    def _get_analyzer(self):
        if self._analyzer is None:
            from ...projection.root_cause_analyzer import RootCauseAnalyzer
            from ...storage import EventLog
            import os

            log_path = os.path.join(os.path.expanduser("~"), ".codemason", "sessions", "web.jsonl")
            self._analyzer = RootCauseAnalyzer(EventLog(log_path), session_id="web")
        return self._analyzer

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        trigger = args.get("trigger", "verify_failed")
        if trigger not in ("verify_failed", "error", "user_query"):
            return {"status": "error", "error": f"非法触发源: {trigger}（verify_failed/error/user_query）"}
        analyzer = self._get_analyzer()
        try:
            report, feed = analyzer.analyze(
                trigger=trigger,
                trigger_event_id=int(args.get("trigger_event_id", 0)),
                session_id=args.get("session_id"),
                files=args.get("files") or [],
            )
            return {
                "status": "ok",
                "report_id": report.report_id,
                "trigger": report.trigger,
                "status_report": report.status,
                "stages": report.stages,
                "attributions": report.attributions,
                "fix_instructions": report.fix_instructions,
                "feed_forward": feed,
            }
        except Exception as e:
            return {"status": "error", "error": f"根因分析失败: {e}"}


# 模块级注册（builtins 自动发现）
_installed = False


def install(analyzer=None) -> AnalyzeRootCauseTool:
    """显式安装（可注入 analyzer，测试用）。返回工具实例。"""
    tool = AnalyzeRootCauseTool(analyzer=analyzer)
    register_tool(tool)
    return tool


def _auto_install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    install()


_auto_install()
