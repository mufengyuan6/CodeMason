"""Web 驾驶舱后端。

- WebSocket /ws：Web 驾驶舱 ↔ 内核（Op 上行 / Event 下行）
- 事件广播：TailWatcher 轮询 JSONL 尾指针 → 增量广播（G3 跨进程同步）
- 鉴权（G5）：默认只绑 127.0.0.1 + session token + 审批接口二次确认
- 审批即事件：ExecApprovalRequest → 用户 ApprovalResponse 回传内核
- REST 辅助端点：health / sessions / token 校验
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agent import AgentLoop
from ..protocol import Op, event_to_json, parse_op
from ..protocol.ops import ApprovalResponse, UserTurnCancel, UserTurnStart
from ..storage import EventLog, TailWatcher

_watcher_task = None
_watcher_stop = None


async def _watcher_loop(stop_event: asyncio.Event) -> None:
    """尾指针轮询 → 增量广播（G3 跨进程同步）。"""
    if WATCHER is None:
        return
    while not stop_event.is_set():
        try:
            new_events = WATCHER.poll()
            for ev in new_events:
                await _broadcast(ev)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=0.2)
        except asyncio.TimeoutError:
            continue


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """生命周期：启动 watcher 循环，关闭时停止（避免测试挂起）。"""
    global _watcher_task, _watcher_stop
    if WATCHER is not None and _watcher_task is None:
        _watcher_stop = asyncio.Event()
        _watcher_task = asyncio.create_task(_watcher_loop(_watcher_stop))
    yield
    if _watcher_task is not None:
        _watcher_stop.set()
        try:
            await asyncio.wait_for(_watcher_task, timeout=2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        _watcher_task = None


app = FastAPI(title="CodeMason Cockpit", version="2.0.0", lifespan=_lifespan)

# ========== 配置 ==========

SESSION_DIR = Path.home() / ".codemason" / "sessions"
WEB_TOKEN: Optional[str] = None  # 启动时注入；None = 未启用 token（仅本机）
EVENT_LOG: Optional[EventLog] = None
LOOP: Optional[AgentLoop] = None
WATCHER: Optional[TailWatcher] = None
_watcher_task = None
_watcher_stop = None

# 已连接 WebSocket 客户端
clients: set[WebSocket] = set()


class TokenRequest(BaseModel):
    token: str


class OpSubmit(BaseModel):
    op: dict
    confirm_token: Optional[str] = None  # 审批二次确认


class SessionSwitchRequest(BaseModel):
    session_id: str


# ========== 启动钩子 ==========

def init_cockpit(session_id: str = "web", token: Optional[str] = None, loop: Optional[AgentLoop] = None) -> None:
    """初始化驾驶舱（server 启动时调用）。"""
    global WEB_TOKEN, EVENT_LOG, LOOP, WATCHER
    WEB_TOKEN = token or secrets.token_hex(16)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    EVENT_LOG = EventLog(SESSION_DIR / f"{session_id}.jsonl")
    LOOP = loop or AgentLoop(event_log=EVENT_LOG, session_id=session_id)
    WATCHER = TailWatcher(EVENT_LOG, poll_interval=0.2)


# ========== 鉴权（G5） ==========

def _check_token(request_token: Optional[str]) -> bool:
    if WEB_TOKEN is None:
        return True
    return request_token == WEB_TOKEN


def _authorize(headers) -> Optional[str]:
    """从 headers 提取并校验 token。"""
    token = headers.get("x-agent-token")
    if not _check_token(token):
        raise HTTPException(status_code=401, detail="无效 token")
    return token


def require_auth(x_agent_token: Optional[str] = Header(default=None, alias="x-agent-token")) -> str:
    """FastAPI 依赖：受保护 REST 端点统一鉴权（P1-1 修复：REST 端点不再裸奔）。

    适用范围：/sessions /events /costs /context /health-signals /compact。
    豁免：/health（监控探活）、/auth/token（自身即鉴权接口）、/ws（独立握手鉴权）。
    """
    if not _check_token(x_agent_token):
        raise HTTPException(status_code=401, detail="无效 token")
    return x_agent_token or ""


# ========== REST 端点 ==========

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "codemason-cockpit", "version": "2.0.0", "ts": time.time()}


@app.post("/auth/token")
async def auth_token(req: TokenRequest):
    """token 校验（前端登录）。"""
    if _check_token(req.token):
        return {"ok": True}
    raise HTTPException(status_code=401, detail="无效 token")


@app.get("/sessions", dependencies=[Depends(require_auth)])
async def list_sessions():
    """会话列表（对标 pi-web：按工作目录组织）。"""
    sessions = []
    if SESSION_DIR.exists():
        for f in sorted(SESSION_DIR.glob("*.jsonl")):
            sessions.append({"session_id": f.stem, "size": f.stat().st_size, "events": sum(1 for _ in f.open(encoding="utf-8"))})
    return {"sessions": sessions}


@app.post("/sessions/switch", dependencies=[Depends(require_auth)])
async def switch_session(req: SessionSwitchRequest):
    """切换/创建会话：重建 EVENT_LOG/LOOP/WATCHER 指向该会话 JSONL。

    事件溯源哲学：状态永不保存，切会话 = 换 JSONL 文件 + 重放。
    历史会话 JSONL 在磁盘，随时可切回；不存在则自动创建。
    切换后返回 cursor，前端 WS 重连从此游标增量补发。
    """
    global EVENT_LOG, LOOP, WATCHER
    session_id = req.session_id.strip()
    if not re.fullmatch(r"[\w\-]{1,64}", session_id):
        raise HTTPException(status_code=400, detail="非法会话名（仅字母数字 - _，≤64 字符）")
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SESSION_DIR / f"{session_id}.jsonl"
    new_log = EventLog(log_path)
    new_loop = AgentLoop(event_log=new_log, session_id=session_id)
    new_watcher = TailWatcher(new_log, poll_interval=0.2)
    # 原子替换：watcher 循环每次迭代读全局 WATCHER，替换后下一轮自动广播新会话事件
    WATCHER = new_watcher
    EVENT_LOG = new_log
    LOOP = new_loop
    last = new_log.file_last_id()
    return {"ok": True, "session_id": session_id, "cursor": last, "events": last}


@app.get("/events", dependencies=[Depends(require_auth)])
async def read_events(cursor: int = 0, limit: int = 200):
    """REST 兜底读事件（WebSocket 不可用时）。"""
    if EVENT_LOG is None:
        raise HTTPException(status_code=503, detail="驾驶舱未初始化")
    events = EVENT_LOG.list_after(cursor, limit)
    return {"cursor": events[-1].id if events else cursor, "events": [json.loads(event_to_json(e)) for e in events]}


# ========== v1.13 端点：成本驾驶舱 / 上下文面板 / 健康信号 ==========

# 模块级单例（T-H 集成：由 init_cockpit 或测试注入）
_cost_ledger = None
_context_metrics = None
_health_session = None
_recall_service = None
# v1.23 落地：AI 贡献报告 / 审批收件箱 / 自动分类器 / Team Kernel
_contribution_reporter = None
_approval_inbox = None
_safety_classifier = None
_team_kernel = None
_otel_exporter = None
# v1.23 落地③/④：指标投影 / 控制平面 / 按 Op 路由 / 代码图谱 AST / Loop 调度
_metrics_projector = None
_policy_engine = None
_runtime_controller = None
_op_router = None
_ast_index = None
_loop_scheduler = None


def attach_v113_modules(ledger=None, metrics=None, health=None, recall=None) -> None:
    """挂载 v1.13 模块实例（server 启动时调用）。"""
    global _cost_ledger, _context_metrics, _health_session, _recall_service
    _cost_ledger = ledger
    _context_metrics = metrics
    _health_session = health
    _recall_service = recall


def attach_v123_modules(contribution=None, inbox=None, classifier=None, team=None, otel=None) -> None:
    """挂载 v1.23 模块实例（server 启动时调用）：贡献报告/收件箱/分类器/Team Kernel/OTel。"""
    global _contribution_reporter, _approval_inbox, _safety_classifier, _team_kernel, _otel_exporter
    _contribution_reporter = contribution
    _approval_inbox = inbox
    _safety_classifier = classifier
    _team_kernel = team
    _otel_exporter = otel


def attach_v123b_modules(metrics=None, policy=None, runtime=None, router=None, ast=None, scheduler=None) -> None:
    """挂载 v1.23 落地③④模块实例：指标投影/策略引擎/运行时干预/按 Op 路由/AST 索引/调度器。"""
    global _metrics_projector, _policy_engine, _runtime_controller, _op_router, _ast_index, _loop_scheduler
    _metrics_projector = metrics
    _policy_engine = policy
    _runtime_controller = runtime
    _op_router = router
    _ast_index = ast
    _loop_scheduler = scheduler


@app.get("/costs", dependencies=[Depends(require_auth)])
async def cost_dashboard():
    """成本驾驶舱：每 Op token 消耗/节省台账 + 高成本操作预警。"""
    if _cost_ledger is None:
        return {"enabled": False, "message": "成本台账未挂载"}
    return {"enabled": True, **{"summary": _cost_ledger.summary(), "by_op_type": _cost_ledger.by_op_type(), "high_cost": [r.to_dict() for r in _cost_ledger.high_cost_ops()]}}


@app.get("/context", dependencies=[Depends(require_auth)])
async def context_panel():
    """上下文管理面板：四维指标 + 压缩策略对照（A/B 子区）。"""
    if _context_metrics is None:
        return {"enabled": False, "message": "上下文指标未挂载"}
    metrics = _context_metrics.report() if hasattr(_context_metrics, "report") else _context_metrics
    return {"enabled": True, "metrics": metrics, "policies": ["default", "aggressive_forgetting", "gentle"]}


@app.get("/health-signals", dependencies=[Depends(require_auth)])
async def health_signals():
    """健康信号：stuck 检测 + 会话健康度（驱动生命周期建议）。"""
    if _health_session is None:
        return {"enabled": False, "message": "健康信号未挂载"}
    return {"enabled": True, "report": _health_session.report().to_dict()}


@app.post("/compact", dependencies=[Depends(require_auth)])
async def manual_compact(req: dict = None):
    """手动压缩按钮（对标 Claude Code /compact，与 agent 主动 CompressRequest 并存）。"""
    req = req or {}
    target = req.get("target", "context")
    if LOOP is None:
        raise HTTPException(status_code=503, detail="内核未初始化")
    from ..protocol import Compact as CompactOp

    LOOP.enqueue_op(CompactOp(target=target))
    events = LOOP.run_until_idle(max_steps=10)
    return {"ok": True, "target": target, "events": len(events)}


# ========== v1.23 端点：AI 贡献报告 / 审批收件箱 / 分类器审计 / 遥测 ==========


@app.get("/api/contribution", dependencies=[Depends(require_auth)])
async def contribution_report(task_id: str = "task-1"):
    """AI 贡献报告导出（G17⑧）：纯事件投影，零 LLM。"""
    if _contribution_reporter is None:
        return {"enabled": False, "message": "贡献报告投影器未挂载"}
    report = _contribution_reporter.build(task_id=task_id)
    return {"enabled": True, "report": report.to_dict()}


@app.get("/api/inbox", dependencies=[Depends(require_auth)])
async def approval_inbox_view():
    """审批收件箱（G14）：只收分类器拦截/存疑件（人类审拦截件，不审每个动作）。"""
    if _approval_inbox is None:
        return {"enabled": False, "message": "审批收件箱未挂载"}
    items = [
        {
            "item_id": i.item_id,
            "tool_name": i.tool_name,
            "command": i.command,
            "verdict_decision": i.verdict_decision,
            "reason": i.reason,
            "status": i.status,
            "created_at": i.created_at,
        }
        for i in _approval_inbox.pending()
    ]
    return {"enabled": True, "items": items, "stats": _approval_inbox.stats()}


@app.post("/api/inbox/respond", dependencies=[Depends(require_auth)])
async def approval_inbox_respond(req: dict = None):
    """人工处置收件箱条目（approve/reject/edit）。"""
    req = req or {}
    if _approval_inbox is None:
        raise HTTPException(status_code=503, detail="审批收件箱未挂载")
    item_id = req.get("item_id", "")
    decision = req.get("decision", "")
    if decision not in ("approve", "reject", "edit"):
        raise HTTPException(status_code=400, detail="非法处置决策")
    item = _approval_inbox.respond(item_id, decision, edited_command=req.get("edited_command"), operator="web")
    if item is None:
        raise HTTPException(status_code=404, detail="条目不存在或已处理")
    return {"ok": True, "item_id": item_id, "status": item.status}


@app.get("/api/classifier", dependencies=[Depends(require_auth)])
async def classifier_audit():
    """分类器判决审计（G18）：审批即事件，白盒可查。"""
    if _safety_classifier is None:
        return {"enabled": False, "message": "分类器未挂载"}
    return {
        "enabled": True,
        "history": _safety_classifier.history()[-100:],
        "fallback_human": _safety_classifier.should_fallback_human(),
    }


@app.get("/api/telemetry", dependencies=[Depends(require_auth)])
async def telemetry_status():
    """OTel 遥测状态（G13 治理）。"""
    if _otel_exporter is None:
        return {"enabled": False, "message": "OTel 导出器未挂载"}
    return {"enabled": True, "stats": _otel_exporter.stats()}


# ========== v1.23 落地③④端点：指标/控制平面/按 Op 路由/AST 索引/调度 ==========


@app.get("/api/metrics", dependencies=[Depends(require_auth)])
async def metrics_dashboard():
    """指标投影（G17③）：任务成功率/工具准确率/延迟/成本/失败分布。"""
    if _metrics_projector is None:
        return {"enabled": False, "message": "指标投影器未挂载"}
    return {"enabled": True, "report": _metrics_projector.report()}


@app.get("/api/control/policy", dependencies=[Depends(require_auth)])
async def control_policy_view():
    """策略即代码（G14 v1.23）：策略规则 + 执行审计。"""
    if _policy_engine is None:
        return {"enabled": False, "message": "策略引擎未挂载"}
    return {"enabled": True, "policy": _policy_engine.policy.to_dict(), "audit": _policy_engine.audit(limit=50)}


@app.post("/api/control/intervene", dependencies=[Depends(require_auth)])
async def control_intervene(req: dict = None):
    """运行时干预（G14 v1.23）：对话中途换模型/切模式/改策略/cancel。"""
    req = req or {}
    if _runtime_controller is None:
        raise HTTPException(status_code=503, detail="运行时控制器未挂载")
    kind = req.get("kind", "")
    if kind not in ("switch_model", "switch_mode", "update_policy", "cancel"):
        raise HTTPException(status_code=400, detail="非法干预类型")
    iv = _runtime_controller.intervene(kind, target=req.get("target", ""), reason=req.get("reason", ""))
    return {"ok": True, "intervention": iv.to_dict()}


@app.get("/api/routing", dependencies=[Depends(require_auth)])
async def routing_stats():
    """按 Op 分派路由（4.1 v1.23）：分派统计 + 合规审计。"""
    if _op_router is None:
        return {"enabled": False, "message": "Op 路由未挂载"}
    return {"enabled": True, "stats": _op_router.stats(), "audit": _op_router.audit(limit=50)}


@app.get("/api/ast-index", dependencies=[Depends(require_auth)])
async def ast_index_query(q: str = ""):
    """代码图谱 AST 索引查询（4.1b v1.23）：一次查询替代 N 次 grep。"""
    if _ast_index is None:
        return {"enabled": False, "message": "AST 索引未挂载"}
    if not q:
        return {"enabled": True, "stats": _ast_index.stats(), "query": ""}
    result = _ast_index.query(q)
    return {"enabled": True, "query": q, "matches": result.matches, "token_estimate": result.token_estimate}


@app.get("/api/scheduler", dependencies=[Depends(require_auth)])
async def scheduler_view():
    """Loop 调度规则 + 触发历史（G14 Automations）。"""
    if _loop_scheduler is None:
        return {"enabled": False, "message": "调度器未挂载"}
    return {"enabled": True, "rules": _loop_scheduler.rules(), "history": _loop_scheduler.history(limit=20)}


# ========== Skill 生态对接（v1.27） ==========

_skill_registry = None  # 由 setup 挂载（懒初始化）


def _get_skill_registry():
    """懒初始化 Skill registry（本地盘点索引，复用 LazySkillLoader 发现层）。"""
    global _skill_registry
    if _skill_registry is None:
        from pathlib import Path as _P

        from ..skills.registry import SkillRegistry

        # 项目内 skills 目录（不存在则用默认路径，scan 空目录安全）
        skills_dir = _P(__file__).resolve().parents[2] / "skills"
        _skill_registry = SkillRegistry(skills_dir)
        _skill_registry.rebuild_index()
    return _skill_registry


@app.get("/api/skills", dependencies=[Depends(require_auth)])
async def skills_view(q: str = ""):
    """Skill 本地盘点索引（v1.27：scan → index → search，对接 Agent Skills 标准）。"""
    registry = _get_skill_registry()
    if q:
        return {"enabled": True, "query": q, "skills": registry.search(q), "stats": registry.stats()}
    return {"enabled": True, "skills": registry.list_all(), "stats": registry.stats()}


@app.get("/api/skills/health", dependencies=[Depends(require_auth)])
async def skills_health():
    """Skill registry 健康信号（索引路径/条目数）。"""
    registry = _get_skill_registry()
    return {"enabled": True, **registry.stats()}


# ========== WebSocket（核心） ==========

async def _broadcast(event_obj) -> None:
    """广播事件给所有客户端（多标签页复用，G3）。"""
    raw = event_to_json(event_obj)
    dead = []
    for ws in clients:
        try:
            await ws.send_text(raw)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


# ========== WebSocket（核心） ==========

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket 主端点：Op 上行 / Event 下行。"""
    # 鉴权握手（G5：Web 能审批命令，它本身就是攻击面）
    token = ws.headers.get("x-agent-token") or ws.query_params.get("token")
    if not _check_token(token):
        await ws.close(code=4401, reason="unauthorized")
        return
    await ws.accept()
    # 断线重连：客户端带 cursor 参数 → 从游标增量补发
    cursor = int(ws.query_params.get("cursor", 0) or 0)
    if EVENT_LOG is not None and cursor > 0:
        for ev in EVENT_LOG.list_after(cursor):
            await ws.send_text(event_to_json(ev))
    clients.add(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                op: Op = parse_op(raw)
            except Exception as e:
                await ws.send_text(json.dumps({"type": "OpParseError", "message": str(e)}))
                continue
            if LOOP is None:
                await ws.send_text(json.dumps({"type": "Error", "message": "内核未初始化"}))
                continue
            # 审批二次确认（G5）：ApprovalResponse 需二次提交确认
            if isinstance(op, ApprovalResponse):
                data = json.loads(raw)
                if data.get("confirm") is not True:
                    await ws.send_text(json.dumps({"type": "ApprovalConfirmRequired", "approval_id": op.approval_id}))
                    continue
            LOOP.enqueue_op(op)
            events = LOOP.run_until_idle(max_steps=50)
            # 广播收敛（G3 修复）：事件已通过 EventLog.append 写入 JSONL + 更新 .tail，
            # TailWatcher 轮询会自动广播——这里不再手动 _broadcast，避免同一事件广播两次
            # （旧实现：handler 广播一次 + watcher 轮询广播一次 = 前端收到重复事件）
            _ = events  # 返回值仅用于调试/测试；广播统一走 watcher 通道

    except WebSocketDisconnect:
        clients.discard(ws)
    except Exception as e:
        clients.discard(ws)
        try:
            await ws.close(code=1011, reason=str(e))
        except Exception:
            pass


# ========== 静态文件（前端构建产物） ==========

def mount_frontend(dist_dir: str) -> None:
    """挂载前端构建产物（dist/）。"""
    d = Path(dist_dir)
    if d.exists():
        app.mount("/", StaticFiles(directory=str(d), html=True), name="frontend")
