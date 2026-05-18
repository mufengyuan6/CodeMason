"""Web 驾驶舱 API 冒烟测试（重写自旧 REST 测试：/analyze 等已废弃 → Op/Event + 驾驶舱 API）。"""

import pytest
from fastapi.testclient import TestClient

from src.agent import AgentLoop
from src.protocol.ops import UserTurnStart
from src.storage import EventLog


@pytest.fixture
def client(tmp_path):
    import src.web.main as web

    web.SESSION_DIR = tmp_path / "sessions"
    web.WEB_TOKEN = "smoke-token"
    web.EVENT_LOG = EventLog(web.SESSION_DIR / "smoke.jsonl")
    web.LOOP = AgentLoop(event_log=web.EVENT_LOG, session_id="smoke")
    from src.storage import TailWatcher

    web.WATCHER = TailWatcher(web.EVENT_LOG, poll_interval=0.05)
    with TestClient(web.app) as c:
        yield c


class TestCockpitSmoke:
    def test_root_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_auth(self, client):
        assert client.post("/auth/token", json={"token": "smoke-token"}).status_code == 200
        assert client.post("/auth/token", json={"token": "bad"}).status_code == 401

    def test_turn_produces_events(self, client):
        """发一轮任务 → 事件落库可查（Op/Event 协议闭环）。"""
        import src.web.main as web
        from src.agent import AgentLoop

        class MockLLM:
            def generate(self, messages, *, role="editor"):
                return "计划：完成冒烟任务"

        loop = AgentLoop(event_log=web.EVENT_LOG, llm=MockLLM(), session_id="smoke")
        loop.enqueue_op(UserTurnStart(content="冒烟任务"))
        loop.run_until_idle()
        r = client.get("/events")
        assert r.status_code == 200
        events = r.json()["events"]
        types = {e["type"] for e in events}
        assert "TurnStarted" in types
        assert "AgentMessageContentDelta" in types

    def test_sessions(self, client):
        r = client.get("/sessions")
        assert r.status_code == 200
        assert isinstance(r.json()["sessions"], list)
