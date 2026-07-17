"""Event 协议：内核 → 界面/外部的实事（对标 Codex SQ/EQ 的 Event Queue）。

职责边界写死（G3）：
- Event = 内核对外的**事实**（TurnStarted / AgentMessageContentDelta / ExecApprovalRequest / ItemCompleted）
- 所有 Event 带全局递增 id（JSONL 游标增量补发的基础）+ schema 版本
- 判别联合 + frozen=True（范式声明：事件模型层 OOP pydantic）

架构要点（G3 / 1.4）：
- Event id 单调递增 → WebSocket 断线重连后从游标增量补发
- 不直接面向 UI 发指令：UI 从 Event 渲染，状态一律由事件流推导
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# 协议版本：与 Op 同步
PROTOCOL_VERSION = "v1"


class EventType(str, Enum):
    """Event 类型枚举（core→界面的事实集合）。

    v1.13 扩展（上下文健康与时间旅行）：新增 INJECT / CONDENSATION / EVENT_STALE / EVENT_RECALL。
    """

    TURN_STARTED = "TurnStarted"
    AGENT_MESSAGE_DELTA = "AgentMessageContentDelta"
    EXEC_APPROVAL_REQUEST = "ExecApprovalRequest"
    ITEM_COMPLETED = "ItemCompleted"
    TURN_CANCELLED = "TurnCancelled"
    ERROR = "Error"
    ROLLBACK = "Rollback"
    # ---- v1.13 新增 ----
    INJECT = "Inject"                      # 注入即事件：记忆注入审计基础设施
    CONDENSATION = "Condensation"          # 压缩即事件：压缩决策可审计渐进式
    EVENT_STALE = "EventStale"             # 事件级失效：file_changed → 旧 tool_result 标 stale
    EVENT_RECALL = "EventRecall"           # 回读记录：压缩质量信号 + 记忆捕获缺口信号
    # ---- v1.23 落地新增（G18/G17②/G14/G17①） ----
    CLASSIFIER_VERDICT = "ClassifierVerdict"    # 分类器判决：allow/block/alternative/escalate + 理由（审批即事件）
    TRACE_RECORD = "TraceRecord"                # 轨迹记录：沙箱不可知执行轨迹（executor/command/exit_code）
    WRITE_LOCK_GRANTED = "WriteLockGranted"     # Team Kernel：单写者锁授予
    WRITE_LOCK_RELEASED = "WriteLockReleased"   # Team Kernel：单写者锁释放
    SNAPSHOT_CREATED = "SnapshotCreated"        # 投影层：Verified State 快照创建（可审计）
    # ---- v1.26 落地新增（DSH 实现层启发：G6/1.4/G13/G14/G19） ----
    RETRY = "Retry"                             # 重试调度（v1.26，G6）：重试计数可重建（进程重启不丢）
    RETRY_STARTED = "RetryStarted"              # 重试开始等待（v1.26，G6）：等待前追加
    CRASH_CLOSER = "CrashCloser"                # 崩溃轮次合成 closer（v1.26，1.4）：TOOL_NOT_STARTED/TOOL_OUTCOME_UNKNOWN + turn/end interrupted
    GOAL_CHANGE = "GoalChange"                  # 目标域变更（v1.26，G13）：create/edit/pause/resume/complete/block/clear 全生命周期
    WORKFLOW_START = "WorkflowStart"            # 工作流脚本开始（v1.26，G14）
    WORKFLOW_PHASE = "WorkflowPhase"            # 工作流阶段进度（v1.26，G14）
    WORKFLOW_LOG = "WorkflowLog"                # 工作流日志（v1.26，G14）
    WORKFLOW_END = "WorkflowEnd"                # 工作流结束（v1.26，G14）
    PERMISSION_PRESET_SELECTED = "PermissionPresetSelected"  # 权限预设选择（v1.26，G19）：组合开关选择事件
    # ---- v1.28 落地新增（G20 事件驱动根因分析） ----
    ROOT_CAUSE_REPORT = "RootCauseReport"       # 溯源报告（G20 ⑤沉淀）：溯源即事件，可审计可回放


class Event(BaseModel):
    """Event 基类：全局递增 id + 协议版本。"""

    model_config = ConfigDict(frozen=True)

    id: int = Field(description="全局递增事件 id（游标增量补发基准）")
    protocol_version: str = Field(default=PROTOCOL_VERSION, description="协议版本")
    type: EventType
    ts: float = Field(description="事件时间戳（epoch 秒）")


class TurnStarted(Event):
    """一轮新对话开始（对应 Op: UserTurnStart 被接受）。"""

    type: Literal[EventType.TURN_STARTED] = EventType.TURN_STARTED
    session_id: str
    mode: Literal["act", "plan"]
    turn_index: int
    op_id: str = Field(description="触发本轮的 UserTurnStart 的 op_id（幂等追踪）")


class AgentMessageContentDelta(Event):
    """Agent 消息增量（流式输出，UI 按 deltas 拼接渲染）。"""

    type: Literal[EventType.AGENT_MESSAGE_DELTA] = EventType.AGENT_MESSAGE_DELTA
    session_id: str
    message_index: int
    delta: str
    complete: bool = Field(default=False, description="本消息是否结束（delta 为空且 complete=True 表示收尾）")


class ExecApprovalRequest(Event):
    """工具执行需要审批（危险操作进入 WAITING_FOR_CONFIRMATION，事件留库不执行）。"""

    type: Literal[EventType.EXEC_APPROVAL_REQUEST] = EventType.EXEC_APPROVAL_REQUEST
    session_id: str
    approval_id: str = Field(description="审批 id（ApprovalResponse 引用）")
    tool_name: str
    description: str = Field(description="命令/操作的人类可读描述")
    command: str = Field(default="", description="待执行的 shell 命令（Bash 工具）")
    risk_level: Literal["red", "yellow", "green"] = Field(default="red", description="风险等级（红/黄/绿）")
    diff_preview: Optional[str] = Field(default=None, description="写入类操作的 diff 预览（审批中心展示）")
    rationale: Optional[str] = Field(default=None, description="v1.29：模型自述理由（为什么选这个工具，≤20 词）")
    rationale_source: str = Field(default="model_self_report", description="v1.29：rationale 来源标注——模型自述理由，非验证事实（对标 fact-checker 声明态）")


class ItemCompleted(Event):
    """一个工作项完成（工具调用结果 / 子任务结论 / 回合总结）。"""

    type: Literal[EventType.ITEM_COMPLETED] = EventType.ITEM_COMPLETED
    session_id: str
    item_type: Literal["tool_result", "subagent_result", "turn_summary", "task_result"]
    item_id: str
    content: Optional[Any] = Field(default=None, description="结构化结果")
    metrics: Optional[dict] = Field(default=None, description="YAGNI 四维量化指标（可选）")
    rationale: Optional[str] = Field(default=None, description="v1.29：模型自述理由（为什么选这个工具，≤20 词，仅 tool_result）")
    rationale_source: str = Field(default="model_self_report", description="v1.29：rationale 来源标注——模型自述理由，非验证事实")


class TurnCancelled(Event):
    """用户取消了当前轮次（对应 Op: UserTurnCancel）。"""

    type: Literal[EventType.TURN_CANCELLED] = EventType.TURN_CANCELLED
    session_id: str
    reason: Optional[str] = None


class Error(Event):
    """内核错误（不中断会话，仅上报）。

    v1.28 增强（G20 事件驱动根因分析）：补 failure_stage（TRAJEVAL 三阶段口径——
    search/read/edit/verify，溯源定位的依据）+ related_tool（失败关联工具，溯源过滤用）。
    """

    type: Literal[EventType.ERROR] = EventType.ERROR
    session_id: str
    message: str
    error_type: str = Field(default="unknown", description="错误分类（语法/权限/路径/逻辑/网络）")
    failure_stage: Optional[str] = Field(default=None, description="v1.28：失败阶段定位（search/read/edit/verify，TRAJEVAL 口径）")
    related_tool: Optional[str] = Field(default=None, description="v1.28：失败关联的工具名（溯源证据链过滤）")


class Rollback(Event):
    """事件溯源回滚（G4）：追加 rollback 事件 + 代码状态复位。"""

    type: Literal[EventType.ROLLBACK] = EventType.ROLLBACK
    session_id: str
    checkpoint_id: str
    reason: str = Field(default="user_requested", description="回滚原因")
    truncated_event_id: Optional[int] = Field(default=None, description="事件流截断标记")


# ========== v1.13 新增事件（上下文健康与时间旅行） ==========


class Inject(Event):
    """注入即事件（v1.13）：记忆注入审计的基础设施。

    每次记忆注入产生本事件（记忆 ID / 置信度 / source_trust / 任务 ID / 注入位置）——
    with/without 对照需要精确知道"注入过什么、何时、给哪个任务"，污染率/注入相关性靠它取样。
    """

    type: Literal[EventType.INJECT] = EventType.INJECT
    session_id: str
    memory_id: str = Field(description="被注入的记忆条目 ID")
    task_id: str = Field(default="", description="当前任务 ID")
    confidence: float = Field(default=0.0, description="经验置信度（复用计数）")
    source_trust: str = Field(default="agent_inferred", description="来源可信度（user_confirmed/user_corrected/agent_inferred）")
    position: str = Field(default="head", description="注入位置（head/plan/history 等，位置敏感布局）")
    chars: int = Field(default=0, description="注入内容字符数")


class Condensation(Event):
    """压缩即事件（v1.13 / 3.2 阶段4）：每次压缩的审计记录。

    覆盖范围 / 策略参数 / 验证结果 追加进 EventLog —— 可审计（精确追踪何时移除什么）、
    渐进式（压缩事件当书签，不从头压）、下次压缩复用先前压缩结果。
    """

    type: Literal[EventType.CONDENSATION] = EventType.CONDENSATION
    session_id: str
    policy_version: str = Field(default="default", description="condenser 策略版本（A/B 对照标识）")
    first_event_id: int = Field(description="本次压缩覆盖的起始事件 ID")
    last_event_id: int = Field(description="本次压缩覆盖的结束事件 ID")
    params: dict = Field(default_factory=dict, description="策略参数（触发阈值/保留轮数/λ/摘要粒度）")
    tokens_before: int = Field(default=0, description="压缩前 token 估算")
    tokens_after: int = Field(default=0, description="压缩后 token 估算")
    verified: bool = Field(default=False, description="双 LLM 验证结果（关键任务双验证）")
    notes: Optional[str] = Field(default=None, description="验证失败原因/其他说明")


class EventStale(Event):
    """事件级失效（v1.13 / G12 失效传播）：文件变更后旧 tool_result 标 stale。

    `change_detected → 上下文 stale + 记忆 invalid_at + 代码对齐复查`——
    组装进窗口前过滤/降权，防"照着旧文件内容改代码"。
    """

    type: Literal[EventType.EVENT_STALE] = EventType.EVENT_STALE
    session_id: str
    file_path: str = Field(description="发生变更的文件路径")
    change_event_id: int = Field(description="触发失效的 file_changed 事件 ID（Write/Edit/Checkpoint 回滚）")
    stale_event_ids: list[int] = Field(default_factory=list, description="被标记为 stale 的旧事件 ID 列表")
    reason: str = Field(default="file_changed", description="失效原因")


class EventRecall(Event):
    """回读记录（v1.13 / 3.2 阶段5）：agent 回读被压缩/offload 的原始事件。

    回读记录 = 压缩质量信号（re-fetch 率 = 压缩过度信号）+ 记忆捕获缺口信号——
    agent 频繁回读某压缩区域 → 压缩太激进，反哺阈值调参。
    """

    type: Literal[EventType.EVENT_RECALL] = EventType.EVENT_RECALL
    session_id: str
    target_event_id: int = Field(description="被回读的原始事件 ID")
    via: str = Field(default="event_read", description="回读方式（event_read/event_search/Read 附件）")
    query: Optional[str] = Field(default=None, description="event search 时使用的检索词")
    reason: str = Field(default="manual", description="回读原因（manual/compressed_recovery/offload）")


# ========== v1.23 落地新增事件（G18/G17②/G14/G17①） ==========


class ClassifierVerdict(Event):
    """自动安全分类器判决（v1.23 落地，G18）——审批即事件：分类器判决可审计可回放。

    每个 Tier3 工具调用执行前过分类器，判决作为本事件进 EventLog。
    放行照跑、拦截/存疑进审批收件箱等人工——"审批不是黑盒弹窗，是白盒可查的事件流"。
    """

    type: Literal[EventType.CLASSIFIER_VERDICT] = EventType.CLASSIFIER_VERDICT
    session_id: str
    tool_name: str
    command: str = Field(default="", description="被评估的原始命令/动作")
    decision: Literal["allow", "block", "alternative", "escalate"] = Field(description="分类器处置：放行/拦截/替代方案/升级人工")
    reason: str = Field(default="", description="判决理由（拒绝带原因，允许有界重试）")
    tier: int = Field(default=3, description="动作 Tier 分级（1 内置安全/2 目录内写/3 过分类器）")
    confidence: float = Field(default=0.0, description="判决置信度 0-1（stage2 CoT 精判后更新）")
    stage: str = Field(default="stage1", description="判决阶段（stage1 快速过滤/stage2 CoT 精判）")
    suggested_alternative: Optional[str] = Field(default=None, description="safer-alternative 处置时建议的安全替代命令")
    op_id: str = Field(default="", description="触发本判决的 Op id（幂等追踪）")


class TraceRecord(Event):
    """轨迹记录（v1.23 落地，G17② 轨迹协议）——沙箱不可知执行轨迹。

    工具执行流水线守卫埋点，统一 trace schema：换 Docker/gVisor/E2B 只换 executor 字段，
    轨迹协议恒定——沙箱内"发生了什么"可见（rye.ai 点名 Docker 官方方案缺失）。
    """

    type: Literal[EventType.TRACE_RECORD] = EventType.TRACE_RECORD
    session_id: str
    trace_id: str = Field(description="轨迹 id（与 op_id/事件 id 关联）")
    executor: str = Field(default="local", description="沙箱实现层标识（local/docker-sandbox/gvisor/firecracker/e2b）")
    command: str = Field(default="", description="执行命令")
    argv: list = Field(default_factory=list, description="分词后的参数列表")
    cwd: str = Field(default=".", description="执行工作目录")
    exit_code: Optional[int] = Field(default=None, description="退出码")
    output_digest: str = Field(default="", description="输出 SHA256 摘要（防篡改）")
    output_head_tail: str = Field(default="", description="输出头尾摘要（完整输出走 offload）")
    duration_ms: float = Field(default=0.0, description="耗时（毫秒）")
    file_diff: Optional[str] = Field(default=None, description="文件变更 diff 摘要（如有）")
    sandbox_id: str = Field(default="", description="沙箱实例 id")


class WriteLockGranted(Event):
    """Team Kernel 单写者锁授予（v1.23 落地，G14）——多 agent 协作协调事件。

    多 agent 共享同一 EventLog（共享事实源≠共享上下文）+ 单写者原则：写作串行、
    并行读者（探索/审查并行，context firewall）——锁授予/释放进事件流可审计可回放。
    """

    type: Literal[EventType.WRITE_LOCK_GRANTED] = EventType.WRITE_LOCK_GRANTED
    session_id: str
    agent_id: str = Field(description="获得写锁的 agent id")
    lock_id: str = Field(description="锁 id（释放时引用）")
    scope: str = Field(default="session", description="锁作用域（session/project/team）")


class WriteLockReleased(Event):
    """Team Kernel 单写者锁释放（v1.23 落地，G14）。"""

    type: Literal[EventType.WRITE_LOCK_RELEASED] = EventType.WRITE_LOCK_RELEASED
    session_id: str
    agent_id: str = Field(description="释放写锁的 agent id")
    lock_id: str = Field(description="对应 WriteLockGranted 的 lock_id")
    duration_s: float = Field(default=0.0, description="持锁时长（秒）")


class SnapshotCreated(Event):
    """Verified State 快照创建（v1.23 落地，G17① 投影层）——快照本身可审计可重放。

    触发点：机读门禁 status=passed / 任务阶段切换 / loop 轮次结束 / 用户手动。
    恢复 = 快照 + 增量事件重放，替代全量重放；校验失败以事件流重建（fail-safe）。
    """

    type: Literal[EventType.SNAPSHOT_CREATED] = EventType.SNAPSHOT_CREATED
    session_id: str
    snapshot_id: str
    first_event_id: int = Field(description="快照覆盖的事件范围起点")
    last_event_id: int = Field(description="快照覆盖的事件范围终点")
    content_hash: str = Field(description="快照内容 SHA256（重放校验基准）")
    trigger: str = Field(default="manual", description="触发点（gate/phase/loop/manual）")
    files: list = Field(default_factory=list, description="文件清单 [{path, sha256, status}]")


# ========== v1.26 落地新增事件（DSH 实现层启发：G6/1.4/G13/G14/G19） ==========


class Retry(Event):
    """重试调度（v1.26 落地，G6 重试状态事件化）——重试计数可从日志重建。

    每次重试调度先追加本事件（等待开始前），policyKey 序列化保证"同一策略的计数
    才能累加"（策略改了计数重新开始）；进程重启后从事件流算出已重试次数不重置为 0。
    """

    type: Literal[EventType.RETRY] = EventType.RETRY
    session_id: str
    retry_id: str = Field(description="重试 id（RetryStarted 引用，同一次重试共享）")
    turn: int = Field(default=0, description="turn 序号")
    step: int = Field(default=0, description="step 序号")
    provider: str = Field(default="", description="provider 标识")
    policy_key: str = Field(default="", description="policyKey 序列化（mode/initialDelayMs/maxDelayMs/jitterRatio/retryableCodes 排序后 JSON）")
    retry: int = Field(default=1, description="本次重试序号（从 1 起）")
    max_retries: int = Field(default=0, description="最大重试次数（normal 模式）")
    delay_ms: float = Field(default=0.0, description="等待时长（毫秒）")
    failure: str = Field(default="", description="失败信息摘要（结构化）")
    op_id: str = Field(default="", description="触发重试的 Op id（幂等追踪）")


class RetryStarted(Event):
    """重试开始等待（v1.26 落地，G6）——真正开始等待前追加。

    与 Retry 成对：Retry = 调度决策，RetryStarted = 等待已开始（可取消等待）。
    """

    type: Literal[EventType.RETRY_STARTED] = EventType.RETRY_STARTED
    session_id: str
    retry_id: str = Field(description="重试 id（与 Retry 事件对应）")
    turn: int = Field(default=0)
    step: int = Field(default=0)
    retry: int = Field(default=1, description="本次重试序号")


class CrashCloser(Event):
    """崩溃轮次合成 closer（v1.26 落地，1.4）——冷恢复时关闭未完成的轮次。

    检测到有 turn/start 无 turn/end 的崩溃轮次 → 追加合成事件关闭：
    - 无结果的 assistant 调用补 tool/result {TOOL_NOT_STARTED}
    - 有调用无结果补 TOOL_OUTCOME_UNKNOWN
    - 再补 step/end + turn/end {reason: interrupted}
    重放历史仍是合法 transcript；已 flush 事件绝不重写。
    """

    type: Literal[EventType.CRASH_CLOSER] = EventType.CRASH_CLOSER
    session_id: str
    turn: int = Field(description="被关闭的崩溃轮次")
    closed_steps: list = Field(default_factory=list, description="合成的 step/end 列表 [{step, tool_calls, outcome}]")
    outcome: Literal["TOOL_NOT_STARTED", "TOOL_OUTCOME_UNKNOWN", "EMPTY"] = Field(description="轮次关闭类型")
    reason: str = Field(default="interrupted", description="关闭原因")


class GoalChange(Event):
    """目标域变更（v1.26 落地，G13 goal 目标域）——目标全生命周期事件化。

    create/edit 带全量目标快照（last-wins 防增量拼装错误）；clear 带 tombstone
    （目标被清但不物理删）；恢复从事件流 fold 当前目标 + roundsStarted。
    """

    type: Literal[EventType.GOAL_CHANGE] = EventType.GOAL_CHANGE
    session_id: str
    operation: Literal["create", "edit", "pause", "resume", "complete", "block", "clear"] = Field(description="目标操作")
    goal: Optional[dict] = Field(default=None, description="全量目标快照 {id, objective, status, revision, createdAt, updatedAt}（clear 时为 None）")
    cleared_goal_id: str = Field(default="", description="clear tombstone：被清目标 id")
    rounds_started: int = Field(default=0, description="已开始的续写轮数")
    revision: int = Field(default=1, description="目标修订号（edit 递增）")


class WorkflowStart(Event):
    """工作流脚本开始（v1.26 落地，G14 workflow 脚本编排）。"""

    type: Literal[EventType.WORKFLOW_START] = EventType.WORKFLOW_START
    session_id: str
    workflow_run_id: str = Field(description="工作流运行 id")
    name: str = Field(default="", description="脚本名（meta.name）")
    description: str = Field(default="", description="脚本描述")
    phases: list = Field(default_factory=list, description="phase 声明 [{title, detail?}]")


class WorkflowPhase(Event):
    """工作流阶段进度（v1.26 落地，G14）——UI 观察进度词汇。"""

    type: Literal[EventType.WORKFLOW_PHASE] = EventType.WORKFLOW_PHASE
    session_id: str
    workflow_run_id: str
    phase: str = Field(description="阶段标题")
    detail: str = Field(default="", description="阶段描述")


class WorkflowLog(Event):
    """工作流日志（v1.26 落地，G14）——脚本执行过程留痕。"""

    type: Literal[EventType.WORKFLOW_LOG] = EventType.WORKFLOW_LOG
    session_id: str
    workflow_run_id: str
    level: str = Field(default="info", description="日志级别（info/warn/error）")
    message: str = Field(default="", description="日志内容")


class WorkflowEnd(Event):
    """工作流结束（v1.26 落地，G14）——completed/cancelled/error。"""

    type: Literal[EventType.WORKFLOW_END] = EventType.WORKFLOW_END
    session_id: str
    workflow_run_id: str
    stop_reason: Literal["completed", "cancelled", "error"] = Field(description="结束原因")
    agent_calls: int = Field(default=0, description="全程 agent() 调用次数")
    error: str = Field(default="", description="失败信息（error 时）")


class PermissionPresetSelected(Event):
    """权限预设选择（v1.26 落地，G19 权限预设组合开关）——选择事件先落日志。

    一个命名预设同时调沙箱模式+审批策略；选择事件保留用户意图（多个预设共享
    同一组取值时仍可区分）；净变化为零的选择不追加（防事件流噪声）。
    """

    type: Literal[EventType.PERMISSION_PRESET_SELECTED] = EventType.PERMISSION_PRESET_SELECTED
    session_id: str
    preset_name: str = Field(description="选择的预设名（workspace-write/danger-full-access/custom）")
    sandbox_mode: str = Field(default="", description="生效的沙箱模式")
    approval_policy: str = Field(default="", description="生效的审批策略")


# ========== v1.28 落地新增事件（G20 事件驱动根因分析） ==========


class RootCauseReport(Event):
    """溯源报告（v1.28 落地，G20 ⑤沉淀）——溯源即事件，可审计可回放。

    失败/疑问触发根因分析后，完整的溯源报告作为本事件进 EventLog：
    - 确定性证据链（图谱 BFS 影响面 + 事件流失败链 + FixPacket 机读契约 + YAGNI 外环）
    - LLM 归因假设（attributions，全部 agent_inferred 永不自动升级）
    - 溯源报告（search/read/edit 三阶段定位 + 证据集 + 修复指令，机读可消费）
    - 诊断回喂（report_id 供下一轮修复注入引用）
    """

    type: Literal[EventType.ROOT_CAUSE_REPORT] = EventType.ROOT_CAUSE_REPORT
    session_id: str
    report_id: str = Field(description="溯源报告 id（诊断回喂引用）")
    trigger: Literal["verify_failed", "error", "user_query"] = Field(description="触发源：验证失败/错误事件/用户'为什么挂'")
    trigger_event_id: int = Field(default=0, description="触发源事件 id（失败链回溯锚点）")
    status: Literal["completed", "degraded"] = Field(default="completed", description="completed=完整链路 / degraded=LLM 降级仅确定性证据链")
    # ① 确定性证据链
    evidence: dict = Field(default_factory=dict, description="确定性证据链 {call_chain: [...], failure_chain: [...], yagni_findings: [...], fix_packets: [...]}")
    # ② LLM 归因假设
    attributions: list = Field(default_factory=list, description="归因假设 [{hypothesis, confidence, evidence_ref, agent_inferred}]——永不自动升级")
    # ③ 溯源报告
    stages: list = Field(default_factory=list, description="TRAJEVAL 三阶段定位 [{stage: search/read/edit, file, line, issue, confidence}]")
    fix_instructions: list = Field(default_factory=list, description="修复指令（FixPacket 消费闭环：机读可消费）")
    # ④ 诊断回喂
    feed_forward: Optional[dict] = Field(default=None, description="诊断回喂载荷 {injected_turn, injected_step, prompt_fragment}")


EventUnion = Annotated[
    Union[
        TurnStarted,
        AgentMessageContentDelta,
        ExecApprovalRequest,
        ItemCompleted,
        TurnCancelled,
        Error,
        Rollback,
        Inject,
        Condensation,
        EventStale,
        EventRecall,
        ClassifierVerdict,
        TraceRecord,
        WriteLockGranted,
        WriteLockReleased,
        SnapshotCreated,
        # v1.26 新增
        Retry,
        RetryStarted,
        CrashCloser,
        GoalChange,
        WorkflowStart,
        WorkflowPhase,
        WorkflowLog,
        WorkflowEnd,
        PermissionPresetSelected,
        # v1.28 新增
        RootCauseReport,
    ],
    Field(discriminator="type"),
]
