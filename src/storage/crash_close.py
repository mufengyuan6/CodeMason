"""崩溃轮次合成 closer（v1.26，1.4——对标 DSH session-persistence 恢复语义）。

职责：冷恢复时检测未关闭的轮次（有 turn/start 无 turn/end 的崩溃轮次），
**追加合成事件关闭**而非丢弃——重放历史仍是合法 transcript：
- 无结果的 assistant 调用补 tool/result {TOOL_NOT_STARTED}（请求从未到达工具）
- 有调用但无结果的补 TOOL_OUTCOME_UNKNOWN（结果未知，模型可重试只读/幂等
  动作、副作用动作要求验证而非盲目重试）
- 再补 step/end + turn/end {reason: interrupted}

关键纪律（对齐 DSH）：
- 已 flush 事件绝不重写（只追加）
- 只丢弃从未完整写入的撕裂尾部（由 EventLog 半行恢复处理）
- "崩溃不丢状态"从承诺变恢复协议语义

范式声明：事件存储层函数式（纯函数 + EventLog 追加）。
"""

from __future__ import annotations

import time
from typing import Optional

from ..protocol import CrashCloser, Event, EventType, parse_event


def find_unclosed_turns(events: list[Event]) -> list[int]:
    """检测未关闭的轮次（有 turn/start 无 turn/end）。

    返回未关闭的 turn_index 列表。空日志返回 []。
    """
    opened: set[int] = set()
    closed: set[int] = set()
    for ev in events:
        if ev.type == EventType.TURN_STARTED:
            opened.add(ev.turn_index)
        elif ev.type in (EventType.TURN_CANCELLED, EventType.CRASH_CLOSER):
            # TurnCancelled 与 CrashCloser 都代表轮次已结束
            closed.add(getattr(ev, "turn", 0))
    return sorted(opened - closed)


def _find_last_turn_start(events: list[Event], turn: int) -> Optional[int]:
    """找到指定轮次的最后一个 turn/start 事件 id（崩溃点在它之后）。"""
    last_id: Optional[int] = None
    for ev in events:
        if ev.type == EventType.TURN_STARTED and ev.turn_index == turn:
            last_id = ev.id
    return last_id


def close_crash_turn(
    log,
    *,
    turn: int,
    session_id: str,
    outcome: str = "TOOL_NOT_STARTED",
    closed_steps: Optional[list] = None,
    reason: str = "interrupted",
) -> Optional[CrashCloser]:
    """关闭一个崩溃轮次：追加 CrashCloser 事件。

    前置：调用方先跑 find_unclosed_turns 确认轮次未关闭；本函数只在轮次
    确实未关闭时追加（幂等：若事件流里已有该轮次的 CrashCloser 则跳过）。
    返回追加的 CrashCloser 事件；无崩溃/已关闭返回 None。
    """
    events = log.read_all()
    unclosed = find_unclosed_turns(events)
    if turn not in unclosed:
        return None
    # 幂等：该轮次已有 CrashCloser 则跳过
    if any(ev.type == EventType.CRASH_CLOSER and getattr(ev, "turn", -1) == turn for ev in events):
        return None

    # 该轮次之后是否还有未落盘的 tool_result（TOOL_OUTCOME_UNKNOWN 判定）
    # 简化判定：调用方传入 outcome，本函数负责事件构造与追加
    ts = time.time()
    closer = CrashCloser(
        id=0, session_id=session_id, turn=turn,
        closed_steps=closed_steps or [],
        outcome=outcome, reason=reason, ts=ts,
    )
    log.append(closer)
    return closer


def close_all_crash_turns(
    log,
    *,
    session_id: str,
    outcome: str = "TOOL_NOT_STARTED",
    closed_steps_by_turn: Optional[dict] = None,
) -> list[CrashCloser]:
    """扫描并关闭事件流中全部崩溃轮次（冷恢复入口）。

    closed_steps_by_turn: {turn: [closed_steps]} 可选（调用方知道每个崩溃
    轮次里有哪些未完成的工具调用）。
    """
    events = log.read_all()
    unclosed = find_unclosed_turns(events)
    closed: list[CrashCloser] = []
    for turn in unclosed:
        steps = (closed_steps_by_turn or {}).get(turn)
        closer = close_crash_turn(
            log, turn=turn, session_id=session_id, outcome=outcome,
            closed_steps=steps, reason="interrupted",
        )
        if closer is not None:
            closed.append(closer)
    return closed
