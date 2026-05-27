"""G14 审批收件箱测试（v1.21 语义升级：只收分类器拦截/存疑件）。

验收：
- 放行（allow）不入箱（无人值守照跑）
- 拦截（block）/ 存疑（escalate）入箱等人工
- 人工处置（approve/reject/edit）幂等
- 统计视图（驾驶舱数据源）
"""

from src.loop.inbox import ApprovalInbox


class TestApprovalInbox:
    def test_allow_not_inbox(self):
        """分类器放行 → 不入箱（无人值守照跑）。"""
        inbox = ApprovalInbox()
        item = inbox.add(tool_name="Bash", command="ls -la", verdict_decision="allow", reason="安全", session_id="s1")
        assert item is None
        assert inbox.pending() == []

    def test_block_goes_inbox(self):
        """分类器拦截 → 入箱等人工。"""
        inbox = ApprovalInbox()
        item = inbox.add(tool_name="Bash", command="rm -rf /", verdict_decision="block", reason="hard-deny: 删除根路径", session_id="s1", classifier_verdict_event_id=42)
        assert item is not None
        assert item.status == "pending"
        assert item.classifier_verdict_event_id == 42  # 可溯源
        assert len(inbox.pending()) == 1

    def test_escalate_goes_inbox(self):
        """分类器存疑（escalate）→ 入箱。"""
        inbox = ApprovalInbox()
        item = inbox.add(tool_name="Bash", command="docker rmi $(docker images -q)", verdict_decision="escalate", reason="危险工具", session_id="s1")
        assert item is not None
        assert item.verdict_decision == "escalate"

    def test_respond_approve(self):
        inbox = ApprovalInbox()
        item = inbox.add(tool_name="Bash", command="pip install x", verdict_decision="escalate", reason="新依赖", session_id="s1")
        r = inbox.respond(item.item_id, "approve", operator="alice")
        assert r.status == "approved"
        assert r.operator == "alice"
        assert inbox.pending() == []

    def test_respond_edit(self):
        inbox = ApprovalInbox()
        item = inbox.add(tool_name="Bash", command="rm -rf /tmp/x", verdict_decision="escalate", reason="tmp 清理", session_id="s1")
        inbox.respond(item.item_id, "edit", edited_command="rm -rf /tmp/x/subdir")
        r = inbox.get(item.item_id)
        assert r.status == "edited"
        assert r.edited_command == "rm -rf /tmp/x/subdir"

    def test_respond_idempotent(self):
        inbox = ApprovalInbox()
        item = inbox.add(tool_name="Bash", command="x", verdict_decision="block", reason="r", session_id="s1")
        inbox.respond(item.item_id, "approve")
        inbox.respond(item.item_id, "reject")  # 已 approved 忽略
        assert inbox.get(item.item_id).status == "approved"

    def test_stats(self):
        inbox = ApprovalInbox()
        inbox.add(tool_name="Bash", command="a", verdict_decision="block", reason="r1", session_id="s1")
        inbox.add(tool_name="Bash", command="b", verdict_decision="escalate", reason="r2", session_id="s1")
        inbox.add(tool_name="Bash", command="c", verdict_decision="block", reason="r3", session_id="s1")
        stats = inbox.stats()
        assert stats["total"] == 3
        assert stats["pending"] == 3
        assert stats["by_decision"] == {"block": 2, "escalate": 1}
