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
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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


@app.get("/sessions")
async def list_sessions():
    """会话列表（对标 pi-web：按工作目录组织）。"""
    sessions = []
    if SESSION_DIR.exists():
        for f in sorted(SESSION_DIR.glob("*.jsonl")):
            sessions.append({"session_id": f.stem, "size": f.stat().st_size, "events": sum(1 for _ in f.open(encoding="utf-8"))})
    return {"sessions": sessions}


@app.get("/events")
async def read_events(cursor: int = 0, limit: int = 200):
    """REST 兜底读事件（WebSocket 不可用时）。"""
    if EVENT_LOG is None:
        raise HTTPException(status_code=503, detail="驾驶舱未初始化")
    events = EVENT_LOG.list_after(cursor, limit)
    return {"cursor": events[-1].id if events else cursor, "events": [json.loads(event_to_json(e)) for e in events]}


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
            for ev in events:
                await _broadcast(ev)
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
