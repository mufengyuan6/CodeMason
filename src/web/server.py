"""CodeMason 驾驶舱启动入口。

用法：
    python -m src.web.server [--port 8765] [--token xxx] [--frontend ../frontend/dist]

- 初始化驾驶舱（鉴权 token / 事件存储 / Agent Loop）
- 启动 uvicorn（默认只绑 127.0.0.1，G5 安全基线）
- 挂载前端构建产物（若存在）
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import uvicorn

from . import main as web_main
from .main import (
    attach_v113_modules,
    attach_v123_modules,
    attach_v128_modules,
    init_cockpit,
    mount_frontend,
)


def _mount_v113_modules() -> None:
    """挂载 v1.13 模块（成本台账 / 上下文指标 / 健康信号）——驾驶舱演示有真实数据。"""
    from ..context.health import SessionHealth
    from ..cost import CostLedger
    from ..evaluation.evaluator import ContextMetrics

    attach_v113_modules(
        ledger=CostLedger(warn_threshold=8000),
        metrics=ContextMetrics(),
        health=SessionHealth(),
        recall=None,
    )


def _mount_v123_modules() -> None:
    """挂载 v1.23 模块（贡献报告 / 审批收件箱 / 自动分类器 / Team Kernel / OTel）——G18/G19/G14/G17⑧ 驾驶舱可见。"""
    from ..loop.inbox import ApprovalInbox
    from ..observability.otel_exporter import OTelExporter
    from ..projection.contribution import ContributionReporter
    from ..security import AutoSafetyClassifier
    from ..team import PermissionMatrix, TeamKernel, TeamTriggers

    # 分类器接入内核（G18：审批即事件，Tier 分级）
    classifier = AutoSafetyClassifier()
    if web_main.LOOP is not None:
        web_main.LOOP.set_classifier(classifier)
    # 收件箱 + 贡献报告 + Team Kernel + OTel
    inbox = ApprovalInbox()
    matrix = PermissionMatrix()
    matrix.add_rule("experience/*", "team")
    matrix.add_rule("secrets/*", "secret", allow_agents=["finance-agent"])
    otel = OTelExporter()
    if web_main.EVENT_LOG is not None:
        otel.attach(web_main.EVENT_LOG)
    attach_v123_modules(
        contribution=ContributionReporter(web_main.EVENT_LOG) if web_main.EVENT_LOG is not None else None,
        inbox=inbox,
        classifier=classifier,
        team=TeamKernel(event_log=web_main.EVENT_LOG) if web_main.EVENT_LOG is not None else None,
        otel=otel,
    )


def _mount_v128_modules() -> None:
    """挂载 v1.28 G20 模块（根因分析引擎 / 图谱查询工具 / YAGNI 归因报告）——溯源驾驶舱可见。

    v1.30 T-11c：归因引擎接真实 LLM provider（deepseek-v4-flash）——溯源从
    status=degraded 降级变为 status=complet 真实归因假设（Doubt-driven 证伪）。
    """
    from ..constraints.yagni_attribution import YagniAttributionReporter
    from ..projection.attribution import AttributionEngine
    from ..projection.root_cause_analyzer import RootCauseAnalyzer
    from ..tools.builtins.codegraph_tools import CodegraphQueryTool

    # 归因引擎：从凭据通道获取真实 LLM provider（失败降级纯确定性）
    attribution_provider = None
    try:
        from ..providers.adapter import build_adapter_from_credentials

        adapter = build_adapter_from_credentials()
        # ModelRouterAdapter.generate(role=...) 返回 str，AttributionEngine 需要 provider.chat()→str
        # 用 adapter.router 的主 provider（deepseek，architect/editor）
        if adapter.router is not None:
            attribution_provider = adapter.router.provider
    except Exception:
        pass  # 无凭据 → 纯确定性降级（fail-safe）

    analyzer = None
    if web_main.EVENT_LOG is not None:
        analyzer = RootCauseAnalyzer(
            web_main.EVENT_LOG,
            session_id="web",
            attribution_engine=AttributionEngine(provider=attribution_provider),
        )
        if web_main.LOOP is not None:
            web_main.LOOP.set_root_cause_analyzer(analyzer)  # 失败 → 溯源 → 诊断回喂
    attach_v128_modules(
        root_cause=analyzer,
        codegraph=CodegraphQueryTool(),
        yagni_attribution=YagniAttributionReporter(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cockpit", description="CodeMason 驾驶舱")
    parser.add_argument("--port", type=int, default=48408, help="固定端口（local-port-manager 分配）")
    parser.add_argument("--host", default="127.0.0.1", help="默认只绑 127.0.0.1（G5）")
    parser.add_argument("--token", default=None, help="鉴权 token（默认随机生成）")
    parser.add_argument("--session", default="web")
    parser.add_argument("--frontend", default=None, help="前端构建产物目录（默认 ../frontend/dist）")
    parser.add_argument("--reload", action="store_true", help="开发热重载")
    args = parser.parse_args(argv)

    token = args.token or secrets.token_hex(8)
    init_cockpit(session_id=args.session, token=token)

    # v1.30 T-11c：注入真实 LLM 到 LOOP（deepseek-v4-flash architect/editor）
    # 失败（缺凭据）→ LOOP 保持空 llm（内核不崩，状态机仍运行，只是规划阶段无输出）
    if web_main.LOOP is not None:
        try:
            from ..providers.adapter import build_adapter_from_credentials

            real_llm = build_adapter_from_credentials()
            web_main.LOOP.set_llm(real_llm)
            print(f"  LLM: {real_llm.router.provider.config.name if real_llm.router else 'mock'} "
                  f"({real_llm.router.provider.config.default_model if real_llm.router else 'fallback'})")
        except Exception as e:
            print(f"  LLM: 降级 Mock（{e}）")

    _mount_v113_modules()
    _mount_v123_modules()
    _mount_v128_modules()

    frontend_dir = args.frontend
    if frontend_dir is None:
        frontend_dir = str(Path(__file__).resolve().parent.parent.parent / "frontend" / "dist")
    mount_frontend(frontend_dir)

    print("=" * 50)
    print(f"  CodeMason 驾驶舱已启动")
    print(f"  地址: http://{args.host}:{args.port}")
    print(f"  Token: {token}")
    print(f"  会话: {args.session}  事件: {web_main.EVENT_LOG.path}")
    llm_info = getattr(web_main.LOOP, '_llm_info', 'unknown')
    print(f"  LLM: {llm_info}")
    print("  注意: 默认只绑 127.0.0.1，Web 可审批命令（攻击面最小化 G5）")
    print("=" * 50)

    uvicorn.run("src.web.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
