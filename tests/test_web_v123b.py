"""v1.23 落地④ Web 端点测试：metrics/control/routing/ast-index/scheduler。"""

import pytest
from fastapi.testclient import TestClient

from src.knowledge_graph.ast_index import AstSymbolIndex
from src.loop import ControlPolicy, LoopScheduler, PolicyEngine, PolicyRule, RuntimeController
from src.projection.metrics import MetricsProjector
from src.routing import OpRouter
from src.storage import EventLog
import src.web.main as web


@pytest.fixture
def client(tmp_path):
    web.SESSION_DIR = tmp_path
    web.WEB_TOKEN = "test-token"
    web.EVENT_LOG = EventLog(tmp_path / "web.jsonl")
    web.LOOP = None
    web.WATCHER = None
    web.attach_v113_modules(ledger=None, metrics=None, health=None, recall=None)
    web.attach_v123_modules(contribution=None, inbox=None, classifier=None, team=None, otel=None)
    web.attach_v123b_modules(metrics=None, policy=None, runtime=None, router=None, ast=None, scheduler=None)
    with TestClient(web.app) as c:
        yield c
    web.attach_v123b_modules(metrics=None, policy=None, runtime=None, router=None, ast=None, scheduler=None)


class TestMetricsEndpoint:
    def test_not_attached(self, client):
        r = client.get("/api/metrics", headers={"x-agent-token": "test-token"})
        assert r.json()["enabled"] is False

    def test_metrics_report(self, client):
        web.attach_v123b_modules(metrics=MetricsProjector(event_log=web.EVENT_LOG))
        r = client.get("/api/metrics", headers={"x-agent-token": "test-token"})
        data = r.json()
        assert data["enabled"] is True
        assert "task_count" in data["report"]["window"]["metrics"]


class TestControlEndpoint:
    def test_policy_view(self, client):
        policy = ControlPolicy(policy_id="prod")
        policy.rules.append(PolicyRule(action="deny", tool_pattern="Bash"))
        engine = PolicyEngine(policy=policy)
        engine.evaluate("Read", "a.py")
        web.attach_v123b_modules(policy=engine)
        r = client.get("/api/control/policy", headers={"x-agent-token": "test-token"})
        data = r.json()
        assert data["enabled"] is True
        assert data["policy"]["policy_id"] == "prod"
        assert data["audit"][0]["tool"] == "Read"

    def test_intervene(self, client):
        web.attach_v123b_modules(runtime=RuntimeController())
        r = client.post("/api/control/intervene", json={"kind": "cancel", "reason": "用户叫停"}, headers={"x-agent-token": "test-token"})
        assert r.status_code == 200
        assert r.json()["intervention"]["kind"] == "cancel"

    def test_intervene_invalid(self, client):
        web.attach_v123b_modules(runtime=RuntimeController())
        r = client.post("/api/control/intervene", json={"kind": "hack"}, headers={"x-agent-token": "test-token"})
        assert r.status_code == 400


class TestRoutingEndpoint:
    def test_routing_stats(self, client):
        router = OpRouter()
        router.route("Read")
        router.route("Bash")
        web.attach_v123b_modules(router=router)
        r = client.get("/api/routing", headers={"x-agent-token": "test-token"})
        data = r.json()
        assert data["enabled"] is True
        assert data["stats"]["by_tier"]["cheap"] == 1
        assert data["stats"]["by_tier"]["expensive"] == 1


class TestAstIndexEndpoint:
    def test_ast_query(self, client, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
        idx = AstSymbolIndex(str(tmp_path))
        web.attach_v123b_modules(ast=idx)
        r = client.get("/api/ast-index?q=hello", headers={"x-agent-token": "test-token"})
        data = r.json()
        assert data["enabled"] is True
        assert data["query"] == "hello"
        assert data["matches"][0]["file"].endswith("a.py")  # 命中位置
        assert data["matches"][0]["line"] == 1


class TestSchedulerEndpoint:
    def test_scheduler_view(self, client):
        sched = LoopScheduler()
        sched.add_event_trigger("pr-review", "pull_request_opened", "review PR")
        web.attach_v123b_modules(scheduler=sched)
        r = client.get("/api/scheduler", headers={"x-agent-token": "test-token"})
        data = r.json()
        assert data["enabled"] is True
        assert data["rules"][0]["trigger_type"] == "event"
