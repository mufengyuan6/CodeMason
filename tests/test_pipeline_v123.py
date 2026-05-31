"""G16④ 工具执行流水线守卫测试（v1.23 落地）。

验收：
- 统一流水线六阶段（前置策略→守卫→执行→后置→整理→通知）
- 前置策略拦截（G18 分类器接入点）
- 安全守卫拦截（黑名单）
- 并发安全声明：只读并行 / 状态屏障独占
- TraceRecord 轨迹埋点
"""

from src.tools.pipeline import PipelineContext, ToolPipeline


class TestToolPipeline:
    def test_ok_flow(self):
        """正常流程：六阶段全走，status=ok。"""
        marks = []
        pipeline = ToolPipeline(
            executor=lambda name, args: {"status": "ok", "value": 42},
            notifier=lambda ctx: marks.append("notified"),
        )
        result = pipeline.execute("Read", {"path": "a.py"})
        assert result.status == "ok"
        assert result.result["value"] == 42
        assert marks == ["notified"]  # 结果通知执行

    def test_pre_policy_blocks(self):
        """前置策略拦截（G18 分类器接入点）。"""
        pipeline = ToolPipeline(
            executor=lambda name, args: {"status": "ok"},
            pre_policy=lambda ctx: "hard-deny: 危险命令" if "rm -rf" in str(ctx.args.get("command", "")) else None,
        )
        result = pipeline.execute("Bash", {"command": "rm -rf /"})
        assert result.status == "blocked"
        assert result.blocked_by == "pre_policy"
        assert "hard-deny" in result.reason

    def test_guard_blocks(self):
        """安全守卫拦截。"""
        pipeline = ToolPipeline(
            executor=lambda name, args: {"status": "ok"},
            guards=[lambda ctx: "黑名单: sudo" if "sudo" in str(ctx.args.get("command", "")) else None],
        )
        result = pipeline.execute("Bash", {"command": "sudo apt install x"})
        assert result.status == "blocked"
        assert result.blocked_by == "guard"

    def test_post_processor_redacts(self):
        """后置处理（内容整理/脱敏）。"""
        pipeline = ToolPipeline(
            executor=lambda name, args: {"status": "ok", "output": "sk-abcdefghij1234567890"},
            post_processors=[lambda ctx: ctx.result.update({"output": "***"}) if ctx.result else None],
        )
        result = pipeline.execute("Bash", {"command": "cat .env"})
        assert result.result["output"] == "***"

    def test_executor_error(self):
        """执行异常 → status=error（不吞异常）。"""

        def boom(name, args):
            raise RuntimeError("boom")

        pipeline = ToolPipeline(executor=boom)
        result = pipeline.execute("Bash", {"command": "x"})
        assert result.status == "error"
        assert "boom" in result.reason

    def test_concurrency_barrier(self):
        """状态修改工具：屏障独占（同时只允许一个）。"""
        state = {"busy": False}

        def slow_executor(name, args):
            state["busy"] = True
            return {"status": "ok"}

        pipeline = ToolPipeline(executor=slow_executor, concurrency_decl={"Bash": False})
        # 模拟工具"正在执行"（active 集合非空）
        pipeline._active.add("Bash")
        result = pipeline.execute("Bash", {"command": "x"})
        assert result.status == "blocked"
        assert "屏障" in result.reason

    def test_parallel_safe_no_barrier(self):
        """只读工具声明并发安全 → 不被屏障拦截。"""
        pipeline = ToolPipeline(executor=lambda name, args: {"status": "ok"}, concurrency_decl={"Read": True})
        pipeline._active.add("Read")  # 即使有活动，只读仍可并行
        result = pipeline.execute("Read", {"path": "a.py"})
        assert result.status == "ok"

    def test_trace_callback(self):
        """轨迹埋点（G17②）：trace_callback 收到 TraceRecord 数据。"""
        traces = []
        pipeline = ToolPipeline(
            executor=lambda name, args: {"status": "ok", "value": 1},
            trace_callback=traces.append,
        )
        result = pipeline.execute("Bash", {"command": "ls"})
        assert result.trace is not None
        assert result.trace["tool_name"] == "Bash"
        assert "ls" in result.trace["command"]
        assert len(traces) == 1
        assert "stages" in traces[0]

    def test_stage_marks(self):
        """各阶段耗时标记（性能观测）——经 trace 回调验证。"""
        traces = []
        pipeline = ToolPipeline(
            executor=lambda name, args: {"status": "ok"},
            trace_callback=traces.append,
        )
        result = pipeline.execute("Read", {"path": "a.py"})
        assert result.status == "ok"
        assert traces
        stages = [s["stage"] for s in traces[0]["stages"]]
        assert "pre_policy" in stages
        assert "guard" in stages
        assert "execute" in stages
