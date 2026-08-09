"""v1.30 Agent Runtime 统一层测试。

测试覆盖：
1. EventBus 事件总线
2. RuntimeLifecycle 生命周期管理器
3. ToolExecutionLoop 工具执行循环
4. AgentCoordinator 多Agent协调器
5. ObservabilityLayer 可观测性层
6. SandboxManager 沙箱管理器
"""

import tempfile
import time
from pathlib import Path

import pytest

from src.runtime import (
    EventBus,
    EventBusError,
    RuntimeLifecycle,
    SessionState,
    ToolExecutionLoop,
    AgentCoordinator,
    ObservabilityLayer,
    SandboxManager,
)
from src.storage import EventLog
from src.protocol import Event, EventType


class TestEventBus:
    """测试事件总线。"""

    def test_event_bus_creation(self):
        """测试事件总线创建。"""
        bus = EventBus()
        assert bus is not None
        stats = bus.get_stats()
        assert stats["dispatch_count"] == 0
        assert stats["error_count"] == 0

    def test_subscribe_and_publish(self):
        """测试订阅和发布事件。"""
        bus = EventBus()
        received_events = []

        def callback(event):
            received_events.append(event)

        # 订阅事件（使用正确的事件类型名称）
        bus.subscribe("test_subscriber", "ItemCompleted", callback)

        # 创建测试事件
        event = Event(
            id=1,
            type=EventType.ITEM_COMPLETED,
            ts=time.time(),
        )

        # 发布事件
        bus.publish(event)

        # 验证收到事件
        assert len(received_events) == 1
        assert received_events[0].type == EventType.ITEM_COMPLETED

    def test_unsubscribe(self):
        """测试取消订阅。"""
        bus = EventBus()
        received_events = []

        def callback(event):
            received_events.append(event)

        # 订阅事件（使用正确的事件类型名称）
        bus.subscribe("test_subscriber", "ItemCompleted", callback)

        # 取消订阅
        result = bus.unsubscribe("test_subscriber", "ItemCompleted")
        assert result is True

        # 发布事件
        event = Event(id=1, type=EventType.ITEM_COMPLETED, ts=time.time())
        bus.publish(event)

        # 验证未收到事件
        assert len(received_events) == 0

    def test_wildcard_subscription(self):
        """测试通配符订阅。"""
        bus = EventBus()
        received_events = []

        def callback(event):
            received_events.append(event)

        # 订阅所有事件
        bus.subscribe("test_subscriber", "*", callback)

        # 发布不同类型的事件
        event1 = Event(id=1, type=EventType.ITEM_COMPLETED, ts=time.time())
        event2 = Event(id=2, type=EventType.ERROR, ts=time.time())

        bus.publish(event1)
        bus.publish(event2)

        # 验证收到所有事件
        assert len(received_events) == 2

    def test_event_history(self):
        """测试事件历史。"""
        bus = EventBus()

        # 发布多个事件
        for i in range(5):
            event = Event(id=i, type=EventType.ITEM_COMPLETED, ts=time.time())
            bus.publish(event)

        # 获取历史
        history = bus.get_history(limit=3)
        assert len(history) == 3

        # 按类型过滤
        history_filtered = bus.get_history(event_type="ItemCompleted", limit=10)
        assert len(history_filtered) == 5


class TestRuntimeLifecycle:
    """测试生命周期管理器。"""

    def setup_method(self):
        """设置测试环境。"""
        self.temp_dir = tempfile.mkdtemp()
        self.event_log = EventLog(Path(self.temp_dir) / "events.jsonl")
        self.event_bus = EventBus()
        self.lifecycle = RuntimeLifecycle(self.event_log, self.event_bus)

    def test_create_session(self):
        """测试创建会话。"""
        session = self.lifecycle.create_session("test_session")

        assert session.session_id == "test_session"
        assert session.state == SessionState.CREATED
        assert session.created_at > 0

    def test_get_session(self):
        """测试获取会话。"""
        self.lifecycle.create_session("test_session")

        session = self.lifecycle.get_session("test_session")
        assert session is not None
        assert session.session_id == "test_session"

    def test_update_session_state(self):
        """测试更新会话状态。"""
        self.lifecycle.create_session("test_session")

        result = self.lifecycle.update_session_state(
            "test_session",
            SessionState.RUNNING,
        )
        assert result is True

        session = self.lifecycle.get_session("test_session")
        assert session.state == SessionState.RUNNING

    def test_destroy_session(self):
        """测试销毁会话。"""
        self.lifecycle.create_session("test_session")

        result = self.lifecycle.destroy_session("test_session")
        assert result is True

        session = self.lifecycle.get_session("test_session")
        assert session.state == SessionState.DESTROYED


class TestToolExecutionLoop:
    """测试工具执行循环。"""

    def setup_method(self):
        """设置测试环境。"""
        self.temp_dir = tempfile.mkdtemp()
        self.event_log = EventLog(Path(self.temp_dir) / "events.jsonl")
        self.event_bus = EventBus()
        self.tool_loop = ToolExecutionLoop(self.event_log, self.event_bus)

    def test_register_tool(self):
        """测试注册工具。"""

        def dummy_tool():
            return {"result": "success"}

        self.tool_loop.register_tool("dummy", dummy_tool)

        stats = self.tool_loop.get_stats()
        assert stats["total_executions"] == 0

    def test_execute_tool(self):
        """测试执行工具。"""

        def dummy_tool(param1: str, param2: int):
            return {"param1": param1, "param2": param2}

        self.tool_loop.register_tool("dummy", dummy_tool)

        execution = self.tool_loop.execute_tool(
            "dummy",
            {"param1": "test", "param2": 42},
        )

        assert execution.tool_name == "dummy"
        assert execution.state.value == "completed"
        assert execution.result == {"param1": "test", "param2": 42}

    def test_execute_tool_failure(self):
        """测试工具执行失败。"""

        def failing_tool():
            raise ValueError("工具执行失败")

        self.tool_loop.register_tool("failing", failing_tool)

        execution = self.tool_loop.execute_tool("failing", {})

        assert execution.tool_name == "failing"
        assert execution.state.value == "failed"
        assert "工具执行失败" in execution.error


class TestAgentCoordinator:
    """测试多Agent协调器。"""

    def setup_method(self):
        """设置测试环境。"""
        self.temp_dir = tempfile.mkdtemp()
        self.event_log = EventLog(Path(self.temp_dir) / "events.jsonl")
        self.event_bus = EventBus()
        self.coordinator = AgentCoordinator(self.event_log, self.event_bus)

    def test_create_task(self):
        """测试创建任务。"""
        task = self.coordinator.create_task(
            "task_1",
            "测试任务",
            assigned_to="agent_1",
        )

        assert task.task_id == "task_1"
        assert task.description == "测试任务"
        assert task.assigned_to == "agent_1"

    def test_assign_task(self):
        """测试分配任务。"""
        self.coordinator.create_task("task_1", "测试任务")

        result = self.coordinator.assign_task("task_1", "agent_1")
        assert result is True

        task = self.coordinator.get_task("task_1")
        assert task.assigned_to == "agent_1"

    def test_complete_task(self):
        """测试完成任务。"""
        self.coordinator.create_task("task_1", "测试任务")
        self.coordinator.assign_task("task_1", "agent_1")
        self.coordinator.start_task("task_1")

        result = self.coordinator.complete_task(
            "task_1",
            {"output": "任务完成"},
        )
        assert result is True

        task = self.coordinator.get_task("task_1")
        assert task.result == {"output": "任务完成"}


class TestObservabilityLayer:
    """测试可观测性层。"""

    def setup_method(self):
        """设置测试环境。"""
        self.temp_dir = tempfile.mkdtemp()
        self.event_log = EventLog(Path(self.temp_dir) / "events.jsonl")
        self.event_bus = EventBus()
        self.observability = ObservabilityLayer(self.event_log, self.event_bus)

    def test_start_span(self):
        """测试开始追踪跨度。"""
        span = self.observability.start_span(
            "span_1",
            "test_operation",
        )

        assert span.span_id == "span_1"
        assert span.operation == "test_operation"
        assert span.start_time > 0

    def test_end_span(self):
        """测试结束追踪跨度。"""
        self.observability.start_span("span_1", "test_operation")

        result = self.observability.end_span("span_1")
        assert result is True

        span = self.observability.get_span("span_1")
        assert span.end_time is not None

    def test_record_audit(self):
        """测试记录审计日志。"""
        from src.runtime.observability import AuditAction

        record = self.observability.record_audit(
            AuditAction.READ,
            "test_resource",
            "test_user",
            "success",
        )

        assert record.record_id.startswith("audit_")
        assert record.action == AuditAction.READ
        assert record.resource == "test_resource"


class TestSandboxManager:
    """测试沙箱管理器。"""

    def setup_method(self):
        """设置测试环境。"""
        self.temp_dir = tempfile.mkdtemp()
        self.event_log = EventLog(Path(self.temp_dir) / "events.jsonl")
        self.event_bus = EventBus()
        self.sandbox_manager = SandboxManager(self.event_log, self.event_bus)

    def test_create_sandbox(self):
        """测试创建沙箱。"""
        sandbox = self.sandbox_manager.create_sandbox("sandbox_1")

        assert sandbox.sandbox_id == "sandbox_1"
        assert sandbox.state.value == "created"

    def test_start_sandbox(self):
        """测试启动沙箱。"""
        self.sandbox_manager.create_sandbox("sandbox_1")

        result = self.sandbox_manager.start_sandbox("sandbox_1")
        assert result is True

        sandbox = self.sandbox_manager.get_sandbox("sandbox_1")
        assert sandbox.state.value == "running"

    def test_execute_in_sandbox(self):
        """测试在沙箱中执行命令。"""
        self.sandbox_manager.create_sandbox("sandbox_1")
        self.sandbox_manager.start_sandbox("sandbox_1")

        result = self.sandbox_manager.execute_in_sandbox(
            "sandbox_1",
            "echo hello",
        )

        assert result["command"] == "echo hello"
        assert result["exit_code"] == 0


class TestRuntimeIntegration:
    """测试 Runtime 统一层集成。"""

    def setup_method(self):
        """设置测试环境。"""
        self.temp_dir = tempfile.mkdtemp()
        self.event_log = EventLog(Path(self.temp_dir) / "events.jsonl")
        self.event_bus = EventBus()

        # 创建所有组件
        self.lifecycle = RuntimeLifecycle(self.event_log, self.event_bus)
        self.tool_loop = ToolExecutionLoop(self.event_log, self.event_bus)
        self.coordinator = AgentCoordinator(self.event_log, self.event_bus)
        self.observability = ObservabilityLayer(self.event_log, self.event_bus)
        self.sandbox_manager = SandboxManager(self.event_log, self.event_bus)

    def test_event_bus_integration(self):
        """测试事件总线集成。"""
        received_events = []

        def callback(event):
            received_events.append(event)

        # 订阅所有事件
        self.event_bus.subscribe("integration_test", "*", callback)

        # 创建会话
        self.lifecycle.create_session("test_session")

        # 验证收到事件
        assert len(received_events) > 0
        # Event 类使用 type 字段，需要获取枚举值
        assert any(
            (e.type.value if hasattr(e.type, 'value') else str(e.type)) == "SessionCreated"
            for e in received_events
        )

    def test_lifecycle_tool_loop_integration(self):
        """测试生命周期和工具执行循环集成。"""
        # 创建会话
        session = self.lifecycle.create_session("test_session")

        # 注册工具
        def test_tool():
            return {"session_id": session.session_id}

        self.tool_loop.register_tool("test_tool", test_tool)

        # 执行工具
        execution = self.tool_loop.execute_tool("test_tool", {})

        assert execution.result["session_id"] == "test_session"

    def test_observability_integration(self):
        """测试可观测性层集成。"""
        # 开始追踪
        span = self.observability.start_span(
            "integration_span",
            "integration_test",
        )

        # 执行操作
        self.lifecycle.create_session("test_session")

        # 结束追踪
        self.observability.end_span("integration_span")

        # 验证追踪记录
        span = self.observability.get_span("integration_span")
        assert span.end_time is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
