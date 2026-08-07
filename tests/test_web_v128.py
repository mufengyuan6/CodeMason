"""v1.28 G20 Web 端点测试：溯源报告列表/触发分析/图谱查询/YAGNI 归因。

对应 design.md G20 落点（Web 驾驶舱溯源视图 + 溯源报告工具端点）。
"""

import time

import pytest
from fastapi.testclient import TestClient

from src.constraints.yagni_attribution import YagniAttributionReporter
from src.projection.root_cause import RootCauseQuerier
from src.projection.root_cause_analyzer import RootCauseAnalyzer
from src.protocol import ErrorEvent, TurnStarted
from src.storage import EventLog
from src.tools.builtins.codegraph_tools import CodegraphQueryTool
from src.web import main as web


@pytest.fixture()
def client(tmp_path):
    """初始化驾驶舱 + 挂载 v1.28 模块。"""
    log = EventLog(tmp_path / "events.jsonl")
    for ev in [
        TurnStarted(id=1, session_id="s1", mode="act", turn_index=1, op_id="o1", ts=time.time()),
        ErrorEvent(
            id=2, session_id="s1", message="SyntaxError in a.py", error_type="syntax",
            failure_stage="edit", related_tool="Edit", ts=time.time(),
        ),
    ]:
        log.append(ev)
    # 注入 v1.28 模块
    web.EVENT_LOG = log
    web._root_cause_analyzer = RootCauseAnalyzer(log, session_id="s1")
    web._codegraph_query_tool = CodegraphQueryTool()
    web._yagni_attribution = YagniAttributionReporter()
    web.WEB_TOKEN = "test-token"
    c = TestClient(web.app)
    yield c
    # 清理（防污染其他测试）
    web.EVENT_LOG = None
    web._root_cause_analyzer = None
    web._codegraph_query_tool = None
    web._yagni_attribution = None
    web.WEB_TOKEN = None


def _auth():
    return {"x-agent-token": "test-token"}


class TestRootCauseEndpoints:
    def test_reports_empty_then_after_analyze(self, client):
        r = client.get("/api/root-cause/reports", headers=_auth())
        assert r.status_code == 200
        assert r.json()["count"] == 0
        # 触发分析 → 报告落盘（溯源即事件）
        r2 = client.post("/api/root-cause/analyze", headers=_auth(), json={"trigger": "error", "trigger_event_id": 2})
        assert r2.status_code == 200
        body = r2.json()
        assert body["ok"] is True
        assert body["report"]["status"] == "degraded"  # 无 LLM → 纯确定性
        assert body["report"]["trigger"] == "error"
        # 报告列表可见
        r3 = client.get("/api/root-cause/reports", headers=_auth())
        assert r3.json()["count"] == 1
        assert r3.json()["reports"][0]["report_id"] == body["report"]["report_id"]

    def test_analyze_invalid_trigger(self, client):
        r = client.post("/api/root-cause/analyze", headers=_auth(), json={"trigger": "nope"})
        assert r.status_code == 400

    def test_analyze_not_mounted(self):
        """未挂载分析器 → 503。"""
        c = TestClient(web.app)
        web.WEB_TOKEN = "t"
        web._root_cause_analyzer = None
        try:
            r = c.post("/api/root-cause/analyze", headers={"x-agent-token": "t"}, json={"trigger": "error"})
            assert r.status_code == 503
        finally:
            web.WEB_TOKEN = None

    def test_analyze_user_query_auto_anchor(self, client):
        """用户'为什么挂'触发：锚点自动取最近失败。"""
        r = client.post("/api/root-cause/analyze", headers=_auth(), json={"trigger": "user_query"})
        assert r.status_code == 200
        assert r.json()["report"]["trigger_event_id"] == 2  # 自动锚定失败事件

    def test_reports_filter_by_session(self, client):
        client.post("/api/root-cause/analyze", headers=_auth(), json={"trigger": "error", "trigger_event_id": 2, "session_id": "s1"})
        r = client.get("/api/root-cause/reports", headers=_auth(), params={"session_id": "s1"})
        assert r.json()["count"] == 1
        r2 = client.get("/api/root-cause/reports", headers=_auth(), params={"session_id": "other"})
        assert r2.json()["count"] == 0


class TestCodegraphEndpoint:
    def test_query_op(self, client):
        r = client.get("/api/codegraph/query", headers=_auth(), params={"op": "pagerank", "limit": 5})
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        # 空图谱安全
        assert "top" in r.json()

    def test_invalid_op(self, client):
        r = client.get("/api/codegraph/query", headers=_auth(), params={"op": "bogus"})
        assert r.status_code == 200
        assert r.json()["status"] == "error"


class TestAttributionEndpoint:
    def test_empty_report(self, client):
        r = client.get("/api/attribution", headers=_auth())
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        assert r.json()["top_issues"] == []

    def test_after_ingest(self, client):
        from src.constraints.yagni import YagniFinding, YagniReport

        web._yagni_attribution.ingest(
            YagniReport(findings=[YagniFinding(rule="L7", level=7, file="a.py", line=1, message="圈复杂度超", severity="block")]),
            failure_event_id=2,
        )
        r = client.get("/api/attribution", headers=_auth())
        assert r.json()["top_issues"][0]["rule"] == "L7"
        assert r.json()["stats"]["total_reports"] == 1
