# 编舟 · CodeMason

> CodeMason 不保存任何状态——记忆是投影，回滚是重放，审计是回看。因为一切皆事件，所以敢让 AI 写更少：写错了回滚，写对了复用，每一步都有据可查。

CodeMason 是 Claude Code / Cline 同级的自主编码 Agent：理解需求 → 规划 → 改码 → 验证 → 交付，全链路自主完成。与市面上"把状态当真相"的工具相反，它以**事件流为唯一真相**（状态永不保存、只从事件推导），因此敢让 Agent **写得更少**（YAGNI 独立确定性验证 Hook）——写错了可回滚，写对了可复用，每一步都完全透明可审计。

## 核心特性

- **自主 Agent Loop**：手写事件流状态机（非框架），四类终止条件 + 每步 Checkpoint 打点，可回滚到任意节点；Plan/Act 双模式隔离，Plan 阶段只读三保险（prompt 层 + 工具预设 + shell 黑名单）
- **YAGNI 约束引擎（独立验证 Hook）**：生成完成后对 staging diff 做确定性静态分析——七级决策阶梯（真需要吗 → 库里有吗 → 标准库能吗 → 平台原生吗 → 现有依赖覆盖吗 → 能一行吗 → 写最少代码），硬规则机械化（L2-L6）+ 软规则语义判断（L1），输出四维量化报告：行数减少 / 依赖未新增 / 重复实现数 / 可读性守门
- **Staging 审查沙盒**：所有 AI 变更先进入 staging diff，经 YAGNI / 安全 / 权限 Hook 验证通过后才落盘——Hook 拦截的改动从未落盘，零回滚成本，全流程可审计
- **Op/Event 协议驱动多界面**：双向契约（Op=意图 / Event=事实），schema 版本化 + Op 幂等；JSONL append-only 事件存储 + flock 写锁，断线从事件 ID 游标增量补发；加界面不改内核
- **视图时间旅行**：`view(event_id, policy)` 在任意历史时刻重建当时的窗口视图——压缩 A/B 对照不用重跑任务、故障精确复现、断点精确续接；"当前视图"只是时间函数的一个采样点，状态永不保存只推导
- **自动安全分类器（人在环位置重构）**：Tier1/2/3 分级（只读 allowlist 不过分类器 / 目录内写自动放行 / shell·外网·子代理过分类器，90% 常规动作零延迟）；hard-deny 20+ 规则（destroy-exfiltrate / degrade-security / supply-chain 三组硬拦）；input 层注入探测 + 执行后二次探测；两阶段判决（stage1 单 token 激进过滤 + stage2 CoT 精判，误报 8.5%→0.4%）；**reasoning-blind**——分类器只见用户消息与原始工具调用，结构性抗注入；三级处置（block / safer-alternative / escalate 人工）；连续 3 次拦截自动回退人工审批（fail-closed，无法证明安全就弹窗）
- **执行沙箱四档矩阵**：SandboxProvider 抽象四后端全实现——L3 Firecracker microVM 默认（独立内核+KVM，数据不出域）+ L1 加固容器（`--network=none --cap-drop=ALL --read-only`，开发降级）+ L2 gVisor runsc（中档）+ L4 E2B 云托管；网络出口白名单 + 凭据绝不进沙箱；换层只换 Provider 实现，轨迹协议恒定
- **纵深防御安全层**：shell 黑名单硬锁 + ensemble 多分析器投票（静态 AST + LLM 判断）+ Prompt 注入防御 + 密钥脱敏；Web 默认只绑 127.0.0.1 + session token + 审批二次确认；安全四层分工——黑名单挡已知 → 分类器判未知 → 人工审拦截 → 审计追全部
- **审批收件箱**：无人值守 loop 中分类器放行的照跑、被拦截/存疑的进收件箱等人工——人类只审拦截件，不审每个动作；审批即事件（`ClassifierVerdict` 100% 落盘，白盒可溯源）
- **控制平面（策略即代码）**：PolicyEngine 工具执行前 deny / require_approval 判定（企业管理面先于运行时防御）+ RuntimeController 运行时干预 + LoopLibrary 预置模板；LoopScheduler 调度触发 → UserTurnStart 入队 + LoopBudget 每 Op 记账、超限熔断
- **双模型按 Op 分派 + 熔断降级**：architect（规划强推理）/ editor（执行快吞吐）分工，OpRouter 按工具名分派 cheap/standard/expensive 三档（成本归因 + 合规审计，防软配置绕过）；Provider 抽象层不绑定单一厂商，同角色 fallback 链 + 指数退避重试
- **代码图谱 AST 索引**：Tree-sitter 符号索引（Python/JS/Go），一次查询替代 N 次 grep + token 估算，供上下文召回与知识图谱
- **三层记忆 + T1-T5 渐进压缩**：会话 JSONL / 项目规则 / 跨会话经验，同类任务第二次执行步骤数下降；上下文超窗自动压缩，Token 消耗降 40%
- **Lazy Skills 渐进加载**：技能元数据分阶段加载（name → SKILL.md → references），未命中 Skill 零 token 开销
- **Subagents + MCP**：独立上下文窗口 + 结论回流协议（findings schema ≤2K）；MCP 客户端 + 3 个示例 Server（GitHub / 数据库 / 云服务）
- **Team Kernel 多人协作**：单写者协调（并行读者 context firewall）+ 事件触发（GitHub Issue/PR @agent + Slack/飞书 mention → 自动开任务）+ 三级权限矩阵（team/department/org × public/team/secret 敏感度，secret 白名单）+ AGENTS.md 渐进式披露（目录角色 + 32KiB 校验）
- **AI 贡献报告（合规透明）**：`ContributionReport = f(EventLog, policy)` 纯投影零 LLM——files+line_range+changed_by+provenance / ai_involvement / verification / cost 四维明细；git_attribution_metadata 生成 `Co-Authored-By: CodeMason AI Agent` trailer（EU AI Act Article 50 透明披露）
- **OTel 遥测导出**：事件流订阅 → OTLP 记录（prompt/审批决策/工具结果/沙箱轨迹），无端点优雅降级 + 快照 JSONL 导出，企业合规可对接

## 架构

```
headless 内核（Python 进程，--mode rpc / run）
    │  Event Queue（core→界面）：TurnStarted / MessageDelta / ExecApprovalRequest / ItemCompleted
    │  Op Queue（界面→core）：UserTurnStart / ApprovalResponse / UserTurnCancel / Compact
    ▼
协议层（Op/Event 双向契约，schema 版本化 + Op 幂等）
    ├── JSONL 持久化（append-only 事实源 + flock 写锁）
    ├── OTel 导出（prompt / 审批 / 工具 / 沙箱轨迹）
    └── WebSocket 广播（Web 驾驶舱，多标签页共享会话）
Web 驾驶舱（React 18 + Vite）← 唯一界面
```

```
Agent Loop（手写状态机 + 事件流）
  ├── Plan 模式：architect 强模型 → 规划 / 反思归因 / 只读
  ├── Act 模式：editor 快模型 → 10 内置工具（Read/Write/Edit/Bash/Glob/Grep/WebSearch/WebFetch/Monitor/AskUser）
  ├── 控制平面：PolicyEngine 策略判定 → LoopScheduler 调度 → LoopBudget 记账 → OpRouter 按 Op 分派
  ├── 变更控制：工具执行 → staging diff → Hook 验证（YAGNI/安全/权限）→ 落盘 → Git Checkpoint
  ├── 执行沙箱：SandboxProvider（默认 Firecracker microVM，L1-L4 可换）——沙箱内无凭据、网络白名单
  ├── 安全分类器：Tier3 动作 → 两阶段判决（allow/block/alternative/escalate）→ 拦截件进审批收件箱
  ├── 记忆：session / project / global 三层 + T1-T5 压缩
  └── 审计：全部事件落盘 + OTel 导出 + AI 贡献报告（Co-Authored-By trailer）
```

驾驶舱是**任务工作台**而非仪表盘集合——三区信息架构：

```
┌─────────────┬─────────────────────────────┬──────────────┐
│  左·会话栏   │   中·事件流主流程             │  右·监控抽屉  │
│ 列表/新建/恢复│ 审批内联可操作 · 交付横幅      │ 文件/成本/    │
│             │ 首次引导卡 · 任务输入          │ YAGNI/上下文/ │
│             │                             │ 健康信号/贡献  │
└─────────────┴─────────────────────────────┴──────────────┘
```

切换会话 = 换 JSONL 重放（事件溯源兑现），审批阻塞点始终在事件流内即时可见，监控一律抽屉化不稀释主流程。收件箱语义：人类只审被拦截/存疑的动作（分类器放行的照跑）。

## 技术栈

- **后端**：Python / FastAPI + WebSocket / pydantic
- **前端**：React 18 + Vite（三区工作台：事件流时间线 / 审批中心+收件箱 / YAGNI 看板 / 成本 / 上下文 / 健康信号 / 贡献审计 / 只读代码查看器 / 主题）
- **解析**：Tree-sitter（Python / JS / Go）+ 引用图 + AST 符号索引
- **存储**：JSONL 事件存储 + Git Checkpoint（三-parent stash + 私用 refs）
- **沙箱**：Firecracker microVM（默认）/ Docker 加固 / gVisor / E2B（SandboxProvider 可插拔）
- **遥测**：OTel（OTLP gRPC，无端点降级为快照 JSONL）
- **模型**：讯飞（默认 Provider）+ Provider 抽象层（OpenAI / Ollama 可插拔）

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/mufengyuan666/CodeMason.git
cd CodeMason

# 2. 环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. 启动驾驶舱（默认 127.0.0.1:8765）
python -m src.web.server

# 4. headless 模式（自动化 / CI）
python -m src.cli.main run --task "修复 xxx"
python -m src.cli.main rpc --task "读取 README 并总结"   # 结构化事件输出
```

Docker 部署：

```bash
docker compose up --build
docker run -p 8765:8765 codemason
```

## 测试与评测

```bash
pytest tests/ -v        # 单元测试（Agent Loop / 协议 / 安全 / 工具 / 记忆 / 压缩 / 沙箱 / 分类器 / Team / 投影）
cd frontend && npx playwright test   # E2E（WebSocket 事件推送 / 审批交互 / 收件箱）
```

评测体系（自建任务集 + 同模型对照）：

- **自建 30 任务集**：bug 修复 10 / 功能开发 8 / 重构 6 / 测试 6，全部标注来源（改编自 SWE-bench 与真实 issue）
- **同模型对照**：Cline 配置为同一模型跑同任务集，变量唯一——对比 Agent 实现而非模型
- **fail-to-pass**：失败自动归因 → 修复重试 → 记录成功率（SWE-bench 官方指标口径）
- **安全专项**：危险命令拦截率 ≥89%（100 条测试集）/ prompt injection 成功率 0%（720 场景）/ 误报率 ≤1% / 连续 3 次拦截自动回退人工 / `ClassifierVerdict` 事件 100% 落盘
- **固定环境**：Docker 固定镜像，评测可复现

## 目录结构

```
src/
├── agent/          # Agent Loop：事件流状态机 / Plan-Act / 反思归因 / Subagents
├── protocol/       # Op/Event 双向契约（schema 版本化 + 幂等）
├── storage/        # JSONL 事件存储 + flock 写锁
├── cli/            # headless 入口（run / --mode rpc）
├── tools/          # 10 内置工具 + 注册表 + 流水线
├── security/       # 纵深防御：黑名单 / ensemble / 脱敏 / 三级审批 / 自动分类器 / 执行沙箱
├── checkpoint/     # Git Checkpoint（三-parent stash + 私用 refs）
├── staging/        # Staging 审查沙盒（变更先验证后落盘）
├── harness/        # Hook 框架（before/after/on_failure + block/cancel）+ 规则引擎
├── constraints/    # YAGNI 七级决策阶梯 + 四维量化
├── skills/         # Lazy Skills 渐进加载
├── memory/         # 三层记忆（session / project / global）+ 事件投影
├── context/        # 视图时间旅行 / 健康信号 / condenser 插件 / 召回回读 / schema 裁剪
├── compression/    # T1-T5 渐进压缩 + auto-compact（CONDENSER_REGISTRY 插件化）
├── cost/           # 成本台账（每次 Op 计费 + 总量预算）
├── loop/           # 控制平面：PolicyEngine / RuntimeController / LoopLibrary / 调度 / 预算 / 收件箱 / 判定
├── routing/        # 双模型路由 + OpRouter 按 Op 分派 + Provider 抽象 + 熔断降级
├── mcp/            # MCP 客户端 + 3 示例 Server
├── parser/         # Tree-sitter 多语言解析
├── knowledge_graph/# 引用图 / 调用关系 / AST 符号索引
├── team/           # Team Kernel：单写者协调 / 事件触发 / 权限矩阵 / AGENTS.md 披露
├── projection/     # 投影层：状态 / 轨迹 / 指标 / AI 贡献报告（纯投影零 LLM）
├── observability/  # OTel 遥测导出（prompt / 审批 / 工具 / 沙箱轨迹）
├── verify/         # 防幻觉验证：fact-preservation / phantom-edit / fix-packet / anti-spurious
├── evaluation/     # 评测闭环（fail-to-pass + 机读验证门禁）
└── web/            # FastAPI + WebSocket 驾驶舱后端
frontend/
└── src/            # React 驾驶舱（Timeline / ApprovalCenter / ApprovalInbox / YagniBoard / CostCockpit / ContextPanel / HealthSignals / Contribution / ThemePanel）
```

## 许可

MIT
