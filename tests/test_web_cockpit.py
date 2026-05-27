"""Phase 5 测试：Web 驾驶舱（REST + WebSocket + 鉴权 G5）。"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from src.protocol.ops import UserTurnStart
from src.storage import EventLog


@pytest.fixture
def client(tmp_path, monkeypatch):
    """初始化驾驶舱并返回 TestClient。"""
    import src.web.main as web

    # 重置模块状态（避免测试间污染）
    web.SESSION_DIR = tmp_path / "sessions"
    web.WEB_TOKEN = "test-token-123"
    web.EVENT_LOG = EventLog(web.SESSION_DIR / "web.jsonl")
    web.LOOP = None
    from src.agent import AgentLoop

    web.LOOP = AgentLoop(event_log=web.EVENT_LOG, session_id="web")
    from src.storage import TailWatcher

    web.WATCHER = TailWatcher(web.EVENT_LOG, poll_interval=0.01)
    # 重置 v1.13 模块单例（避免测试间污染）
    web.attach_v113_modules(ledger=None, metrics=None, health=None, recall=None)
    # TestClient 触发 startup（watcher loop 会阻塞，手动不启用）
    with TestClient(web.app) as c:
        yield c


class TestCockpitAuth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_token_valid(self, client):
        r = client.post("/auth/token", json={"token": "test-token-123"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_token_invalid(self, client):
        r = client.post("/auth/token", json={"token": "wrong"})
        assert r.status_code == 401

    def test_events_after_turn(self, client):
        """REST 读事件（Op 处理后事件落库）。"""
        from src.agent import AgentLoop
        import src.web.main as web

        loop = AgentLoop(event_log=web.EVENT_LOG, session_id="web")
        loop.enqueue_op(UserTurnStart(content="读文件"))
        loop.run_until_idle()
        r = client.get("/events", headers={"x-agent-token": "test-token-123"})
        assert r.status_code == 200
        data = r.json()
        assert data["cursor"] >= 1
        types = {e["type"] for e in data["events"]}
        assert "TurnStarted" in types

    def test_events_cursor_incremental(self, client):
        from src.agent import AgentLoop
        import src.web.main as web

        loop = AgentLoop(event_log=web.EVENT_LOG, session_id="web")
        loop.enqueue_op(UserTurnStart(content="x"))
        loop.run_until_idle()
        r = client.get("/events", params={"cursor": 0}, headers={"x-agent-token": "test-token-123"})
        assert r.status_code == 200
        cursor = r.json()["cursor"]
        r2 = client.get("/events", params={"cursor": cursor}, headers={"x-agent-token": "test-token-123"})
        assert r2.json()["events"] == []  # 无新增

    def test_sessions_list(self, client):
        r = client.get("/sessions", headers={"x-agent-token": "test-token-123"})
        assert r.status_code == 200
        assert "sessions" in r.json()

    def test_switch_session_creates_new(self, client):
        """切换/创建会话：不存在自动创建，返回 cursor=0。"""
        r = client.post("/sessions/switch", json={"session_id": "proj-alpha"}, headers={"x-agent-token": "test-token-123"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["session_id"] == "proj-alpha"
        assert data["cursor"] == 0
        # 列表应出现新会话
        r2 = client.get("/sessions", headers={"x-agent-token": "test-token-123"})
        ids = {s["session_id"] for s in r2.json()["sessions"]}
        assert "proj-alpha" in ids

    def test_switch_session_rejects_bad_name(self, client):
        """非法会话名 → 400（防路径穿越/注入）。"""
        for bad in ["../evil", "a/b", "x y", "", "a" * 65]:
            r = client.post("/sessions/switch", json={"session_id": bad}, headers={"x-agent-token": "test-token-123"})
            assert r.status_code == 400, f"{bad!r} 应被拒绝"

    def test_switch_session_persists_and_restores(self, client):
        """切换后写事件 → 切回 → JSONL 持久可恢复（事件溯源：状态永不保存，文件即真相）。"""
        import src.web.main as web

        r = client.post("/sessions/switch", json={"session_id": "alpha"}, headers={"x-agent-token": "test-token-123"})
        assert r.status_code == 200
        from src.agent import AgentLoop

        loop = AgentLoop(event_log=web.EVENT_LOG, session_id="alpha")
        loop.enqueue_op(UserTurnStart(content="alpha 任务"))
        loop.run_until_idle()
        events_after = web.EVENT_LOG.last_id()
        assert events_after >= 1
        # 切走再切回：事件还在
        client.post("/sessions/switch", json={"session_id": "beta"}, headers={"x-agent-token": "test-token-123"})
        r3 = client.post("/sessions/switch", json={"session_id": "alpha"}, headers={"x-agent-token": "test-token-123"})
        assert r3.status_code == 200
        assert r3.json()["cursor"] == events_after
        types = {e.type for e in web.EVENT_LOG.list_after(0)}
        assert "TurnStarted" in types


class TestWebsocket:
    def test_ws_rejects_bad_token(self, client):
        """错误 token → 连接被拒（G5：Web 能审批命令，它本身就是攻击面）。"""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws?token=wrong") as ws:
                ws.receive_text()

    def test_ws_turn_flow(self, client):
        """WebSocket 全流程：Op 上行 → Event 下行（真服务器 + websockets 客户端）。"""
        import socket
        import threading

        import uvicorn

        import src.web.main as web
        from src.agent import AgentLoop

        class MockLLM:
            def generate(self, messages, *, role="editor"):
                return "计划"

        web.LOOP = AgentLoop(event_log=web.EVENT_LOG, llm=MockLLM(), session_id="web")

        # 找空闲端口
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        config = uvicorn.Config(web.app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        import time

        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started

        import asyncio

        import websockets

        async def ws_client():
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token=test-token-123") as ws:
                from src.protocol.ops import UserTurnStart

                op = UserTurnStart(content="任务", mode="act")
                await ws.send(op.model_dump_json(exclude_none=True))
                received = set()
                for _ in range(15):
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    received.add(json.loads(raw)["type"])
                    if "TurnStarted" in received and "AgentMessageContentDelta" in received:
                        break
                return received

        received = asyncio.run(ws_client())
        assert "TurnStarted" in received
        assert "AgentMessageContentDelta" in received
        server.should_exit = True
        thread.join(timeout=5)

    def test_ws_cursor_replay(self, client):
        """断线重连：从事件 ID 游标增量补发（REST 层验证，WS 增量由 watcher 承担）。"""
        import src.web.main as web
        from src.agent import AgentLoop

        class MockLLM:
            def generate(self, messages, *, role="editor"):
                return "先跑一轮"

        loop = AgentLoop(event_log=web.EVENT_LOG, llm=MockLLM(), session_id="web")
        loop.enqueue_op(UserTurnStart(content="先跑一轮"))
        loop.run_until_idle()
        last_id = web.EVENT_LOG.last_id()
        assert last_id >= 1
        # 游标后的增量（模拟断线重连拉取）
        after = web.EVENT_LOG.list_after(last_id)
        assert after == []  # 无新增


class TestV113CockpitEndpoints:
    """v1.13：成本驾驶舱 / 上下文面板 / 健康信号 / 手动压缩端点。"""

    def _attach(self, client):
        import src.web.main as web
        from src.cost import CostLedger
        from src.evaluation.evaluator import ContextMetrics
        from src.context.health import SessionHealth

        ledger = CostLedger(warn_threshold=100)
        ledger.record("op-1", "ToolCall", tokens_in=300, tokens_out=50, tokens_saved=200)
        metrics = ContextMetrics()
        metrics.observe_assembly()
        metrics.observe_assembly(stale=True)
        metrics.observe_recall()
        metrics.observe_compression(0.3)
        health = SessionHealth()
        web.attach_v113_modules(ledger=ledger, metrics=metrics, health=health, recall=None)

    def test_costs_endpoint(self, client):
        self._attach(client)
        r = client.get("/costs", headers={"x-agent-token": "test-token-123"})
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        assert data["summary"]["total_ops"] == 1
        assert data["summary"]["total_tokens_saved"] == 200
        # 高成本预警（300+50=350 > 100）
        assert len(data["high_cost"]) == 1

    def test_costs_not_attached(self, client):
        r = client.get("/costs", headers={"x-agent-token": "test-token-123"})
        assert r.json()["enabled"] is False

    def test_context_endpoint(self, client):
        self._attach(client)
        r = client.get("/context", headers={"x-agent-token": "test-token-123"})
        data = r.json()
        assert data["enabled"] is True
        assert data["metrics"]["stale_hit_rate"] == 0.5
        assert "default" in data["policies"]

    def test_health_signals_endpoint(self, client):
        self._attach(client)
        r = client.get("/health-signals", headers={"x-agent-token": "test-token-123"})
        data = r.json()
        assert data["enabled"] is True
        assert "score" in data["report"]
        assert "advice" in data["report"]

    def test_manual_compact_endpoint(self, client):
        """手动压缩按钮：Compact Op 入队 → 事件产出。"""
        r = client.post("/compact", json={"target": "context"}, headers={"x-agent-token": "test-token-123"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["target"] == "context"
