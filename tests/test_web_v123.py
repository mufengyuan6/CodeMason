"""v1.23 Web 端点测试：贡献报告 / 审批收件箱 / 分类器审计 / 遥测状态。"""

import pytest
from fastapi.testclient import TestClient

from src.loop.inbox import ApprovalInbox
from src.observability.otel_exporter import OTelExporter
from src.projection.contribution import ContributionReporter
from src.protocol.events import ItemCompleted, TurnStarted
from src.security import AutoSafetyClassifier
from src.storage import EventLog
import src.web.main as web


@pytest.fixture
def client(tmp_path):
    """带 v1.23 模块的 TestClient（重置全部单例，防污染——bug-log #3 教训）。"""
    web.SESSION_DIR = tmp_path
    web.WEB_TOKEN = "test-token"
    web.EVENT_LOG = EventLog(tmp_path / "web.jsonl")
    web.LOOP = None
    web.WATCHER = None
    web.attach_v113_modules(ledger=None, metrics=None, health=None, recall=None)
    web.attach_v123_modules(contribution=None, inbox=None, classifier=None, team=None, otel=None)
    with TestClient(web.app) as c:
        yield c
    # 清理：防单例污染后续测试
    web.attach_v123_modules(contribution=None, inbox=None, classifier=None, team=None, otel=None)


def _auth(client):
    return client.get("/health")  # 触发 app 上下文即可


class TestContributionEndpoint:
    def test_not_attached(self, client):
        r = client.get("/api/contribution", headers={"x-agent-token": "test-token"})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_report_export(self, client):
        # 构造事件 + 挂载投影器
        eid = web.EVENT_LOG.next_event_id
        web.EVENT_LOG.append_many([
            TurnStarted(id=eid(), session_id="s1", mode="act", turn_index=1, op_id="op1", ts=100.0),
            ItemCompleted(id=eid(), session_id="s1", item_type="tool_result", item_id="w1", content={"path": "a.py", "tokens": 100}, ts=101.0),
        ])
        web.attach_v123_modules(contribution=ContributionReporter(web.EVENT_LOG))
        r = client.get("/api/contribution?task_id=t1", headers={"x-agent-token": "test-token"})
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        assert data["report"]["files"][0]["path"] == "a.py"
        assert data["report"]["ai_involvement"] == "full_auto"


class TestInboxEndpoint:
    def test_not_attached(self, client):
        r = client.get("/api/inbox", headers={"x-agent-token": "test-token"})
        assert r.json()["enabled"] is False

    def test_inbox_view_and_respond(self, client):
        inbox = ApprovalInbox()
        item = inbox.add(tool_name="Bash", command="rm -rf /", verdict_decision="block", reason="hard-deny", session_id="s1")
        web.attach_v123_modules(inbox=inbox)
        # 视图
        r = client.get("/api/inbox", headers={"x-agent-token": "test-token"})
        data = r.json()
        assert data["enabled"] is True
        assert data["items"][0]["verdict_decision"] == "block"
        assert data["stats"]["pending"] == 1
        # 处置
        r = client.post("/api/inbox/respond", json={"item_id": item.item_id, "decision": "approve"}, headers={"x-agent-token": "test-token"})
        assert r.status_code == 200
        assert r.json()["status"] == "approved"
        # 已处理后 pending 为空
        r = client.get("/api/inbox", headers={"x-agent-token": "test-token"})
        assert r.json()["items"] == []

    def test_respond_invalid_decision(self, client):
        web.attach_v123_modules(inbox=ApprovalInbox())
        r = client.post("/api/inbox/respond", json={"item_id": "x", "decision": "maybe"}, headers={"x-agent-token": "test-token"})
        assert r.status_code == 400


class TestClassifierEndpoint:
    def test_classifier_audit(self, client):
        classifier = AutoSafetyClassifier()
        from src.security import ClassifierInput

        classifier.classify(ClassifierInput(tool_name="Bash", args={"command": "rm -rf /"}, user_message="x"))
        web.attach_v123_modules(classifier=classifier)
        r = client.get("/api/classifier", headers={"x-agent-token": "test-token"})
        data = r.json()
        assert data["enabled"] is True
        assert data["history"][-1]["decision"] == "block"
        assert data["fallback_human"] is False


class TestTelemetryEndpoint:
    def test_telemetry_status(self, client):
        exporter = OTelExporter()
        web.attach_v123_modules(otel=exporter)
        r = client.get("/api/telemetry", headers={"x-agent-token": "test-token"})
        data = r.json()
        assert data["enabled"] is True
        assert "exported_count" in data["stats"]
