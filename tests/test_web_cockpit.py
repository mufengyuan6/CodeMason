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
        r = client.get("/events")
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
        r = client.get("/events", params={"cursor": 0})
        assert r.status_code == 200
        cursor = r.json()["cursor"]
        r2 = client.get("/events", params={"cursor": cursor})
        assert r2.json()["events"] == []  # 无新增

    def test_sessions_list(self, client):
        r = client.get("/sessions")
        assert r.status_code == 200
        assert "sessions" in r.json()


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
