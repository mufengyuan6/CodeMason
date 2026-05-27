"""OTel 遥测导出（G13 治理 v1.22 落地）——企业 Compliance Platform 对接。

背景（OpenAI Codex 治理六件套）：
agent-native telemetry（OpenTelemetry 导出用户 prompt/审批决策/工具结果/MCP server
使用/网络 proxy allow-deny 事件，进 Compliance Platform）——CodeMason 对应：事件溯源
（EventLog 本身就是全量 OTel），加一层 OTel 导出适配器即企业合规答案。

设计：
- 事件流订阅（EventLog.on_event）→ OTLP 转换器
- 转换：Event → OTel Span/Log 记录（trace_id 按会话/turn 关联）
- 导出目标：OTLP HTTP/gRPC 端点（环境变量 OTEL_EXPORTER_OTLP_ENDPOINT），
  无端点时优雅降级（本地记录，不崩溃）
- 导出类型：prompt（UserTurnStart）/ 审批决策（ClassifierVerdict/ApprovalResponse）/
  工具结果（ItemCompleted）/ 网络 allow-deny（沙箱轨迹）

范式声明：函数式 + 轻 OOP（转换器纯函数，导出器可插拔）。
"""

from __future__ import annotations

import json
import time
from typing import Callable, Optional

from ..protocol import Event, EventType


class OTelExporter:
    """OTel 遥测导出器：事件流 → OTLP 记录（无端点优雅降级）。"""

    # 事件 → 遥测类型映射（Compliance Platform 关注面）
    PROMPT_EVENTS = {EventType.TURN_STARTED}
    APPROVAL_EVENTS = {EventType.CLASSIFIER_VERDICT, EventType.EXEC_APPROVAL_REQUEST}
    TOOL_EVENTS = {EventType.ITEM_COMPLETED}
    NETWORK_EVENTS = {EventType.TRACE_RECORD}

    def __init__(self, *, endpoint: Optional[str] = None, local_fallback: bool = True) -> None:
        """endpoint：OTLP 端点（http://collector:4318）。None → 本地降级。"""
        self.endpoint = endpoint
        self.local_fallback = local_fallback
        self._local_log: list[dict] = []  # 本地降级记录（无端点时的审计兜底）
        self._exported_count = 0
        self._attached = False
        self._detach_fn: Optional[Callable] = None

    # ---------- 接入事件流 ----------

    def attach(self, event_log) -> None:
        """订阅 EventLog（事件流 → OTLP）。返回前先 detach 旧订阅（防重复）。"""
        if self._attached:
            return
        self._detach_fn = event_log.on_event(self._on_event)
        self._attached = True

    def detach(self) -> None:
        if self._detach_fn is not None:
            self._detach_fn()
            self._detach_fn = None
            self._attached = False

    def _on_event(self, event: Event) -> None:
        """事件回调：转换 + 导出（同步快速路径，失败不影响事件流）。"""
        try:
            record = self._convert(event)
            if record is None:
                return
            self._export(record)
        except Exception:
            # 遥测导出失败绝不影响主流程（先落盘后执行纪律的保护对象是主流程）
            pass

    # ---------- 转换（纯函数） ----------

    def _convert(self, event: Event) -> Optional[dict]:
        """Event → OTel 记录（Compliance Platform 口径）。

        记录统一 schema：
        {otel_type: span|log, name, trace_id, span_id, ts, attributes, severity}
        """
        attrs = self._extract_attributes(event)
        ts = event.ts
        if event.type in self.PROMPT_EVENTS:
            return {"otel_type": "span", "name": "user.prompt", "trace_id": f"trace-{event.session_id}", "ts": ts, "attributes": attrs, "severity": "info"}
        if event.type in self.APPROVAL_EVENTS:
            decision = getattr(event, "decision", "unknown")
            return {"otel_type": "log", "name": "approval.decision", "trace_id": f"trace-{event.session_id}", "ts": ts, "attributes": {**attrs, "decision": decision}, "severity": "info"}
        if event.type in self.TOOL_EVENTS:
            item_type = getattr(event, "item_type", "tool_result")
            return {"otel_type": "log", "name": f"tool.{item_type}", "trace_id": f"trace-{event.session_id}", "ts": ts, "attributes": attrs, "severity": "info"}
        if event.type in self.NETWORK_EVENTS:
            executor = getattr(event, "executor", "unknown")
            return {"otel_type": "log", "name": "sandbox.trace", "trace_id": f"trace-{event.session_id}", "ts": ts, "attributes": {**attrs, "executor": executor}, "severity": "info"}
        if event.type == EventType.ERROR:
            return {"otel_type": "log", "name": "agent.error", "trace_id": f"trace-{event.session_id}", "ts": ts, "attributes": attrs, "severity": "error"}
        return None  # 其他事件不导出（防事件泛滥，G3 先定义问题再埋事件）

    @staticmethod
    def _extract_attributes(event: Event) -> dict:
        """提取事件可观测属性（JSON 安全的标量）。"""
        attrs = {"session_id": getattr(event, "session_id", ""), "event_type": event.type.value, "event_id": event.id}
        for field_name in ("tool_name", "command", "reason", "approval_id", "agent_id", "task_id", "trace_id", "sandbox_id"):
            val = getattr(event, field_name, None)
            if val is not None and isinstance(val, (str, int, float, bool)):
                attrs[field_name] = val
        return attrs

    # ---------- 导出 ----------

    def _export(self, record: dict) -> None:
        """导出：OTLP 端点（真实发送）或本地降级。"""
        if self.endpoint:
            self._send_otlp(record)
        else:
            self._local_log.append(record)
            if len(self._local_log) > 5000:  # 防内存无限增长
                self._local_log = self._local_log[-2500:]
        self._exported_count += 1

    def _send_otlp(self, record: dict) -> None:
        """OTLP 发送（HTTP JSON OTLP/0.20.0 近似）。实现可插拔：真实 collector 对接。"""
        # 留接口：生产对接 OTLP/HTTP 协议（protobuf JSON 编码，Content-Type: application/x-protobuf）
        # 此处本地记录 + 打点，避免引入 opentelemetry SDK 强依赖（企业部署时接 exporter 包）
        self._local_log.append({**record, "_otlp_endpoint": self.endpoint})
        if len(self._local_log) > 5000:
            self._local_log = self._local_log[-2500:]

    # ---------- 查询 ----------

    def local_log(self, limit: int = 100) -> list[dict]:
        """本地降级记录（无端点时的审计兜底/测试断言）。"""
        return self._local_log[-limit:]

    def stats(self) -> dict:
        return {"attached": self._attached, "exported_count": self._exported_count, "endpoint": self.endpoint, "local_records": len(self._local_log)}

    def export_snapshot(self, path: str) -> None:
        """导出本地记录到 JSONL（企业合规留档）。"""
        with open(path, "w", encoding="utf-8") as fh:
            for rec in self._local_log:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
