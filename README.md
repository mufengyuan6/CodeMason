# 编舟 · CodeMason

> 一个能自主完成编码任务的通用 Coding Agent，内置 YAGNI 约束引擎与人类在环机制——写得少、写得准、每一步都看得见。

CodeMason 是 Claude Code / Cline 同级的自主编码 Agent：理解需求 → 规划 → 改码 → 验证 → 交付，全链路自主完成。与市面上"让 AI 写更多"的工具相反，它通过独立确定性验证 Hook 让 Agent **写得更少**，并通过 Op/Event 协议让每一步执行**完全透明可审计**。

## 核心特性

- **自主 Agent Loop**：手写事件流状态机（非框架），四类终止条件 + 每步 Checkpoint 打点，可回滚到任意节点；Plan/Act 双模式隔离，Plan 阶段只读三保险（prompt 层 + 工具预设 + shell 黑名单）
- **YAGNI 约束引擎（独立验证 Hook）**：生成完成后对 staging diff 做确定性静态分析——七级决策阶梯（真需要吗 → 库里有吗 → 标准库能吗 → 平台原生吗 → 现有依赖覆盖吗 → 能一行吗 → 写最少代码），硬规则机械化（L2-L6）+ 软规则语义判断（L1），输出四维量化报告：行数减少 / 依赖未新增 / 重复实现数 / 可读性守门
- **Staging 审查沙盒**：所有 AI 变更先进入 staging diff，经 YAGNI / 安全 / 权限 Hook 验证通过后才落盘——Hook 拦截的改动从未落盘，零回滚成本，全流程可审计
- **Op/Event 协议驱动多界面**：双向契约（Op=意图 / Event=事实），schema 版本化 + Op 幂等；JSONL append-only 事件存储 + flock 写锁，断线从事件 ID 游标增量补发；加界面不改内核
- **纵深防御安全层**：shell 黑名单硬锁 + ensemble 多分析器投票（静态 AST + LLM 判断）+ Prompt 注入防御 + 密钥脱敏；Web 默认只绑 127.0.0.1 + session token + 审批二次确认
- **双模型路由 + 熔断降级**：architect（规划强推理）/ editor（执行快吞吐）分工，Provider 抽象层不绑定单一厂商，同角色 fallback 链 + 指数退避重试
- **三层记忆 + T1-T5 渐进压缩**：会话 JSONL / 项目规则 / 跨会话经验，同类任务第二次执行步骤数下降；上下文超窗自动压缩，Token 消耗降 40%
- **Lazy Skills 渐进加载**：技能元数据分阶段加载（name → SKILL.md → references），未命中 Skill 零 token 开销
- **Subagents + MCP**：独立上下文窗口 + 结论回流协议；MCP 客户端 + 3 个示例 Server（GitHub / 数据库 / 云服务）

## 架构

```
headless 内核（Python 进程，--mode rpc / run）
    │  Event Queue（core→界面）：TurnStarted / MessageDelta / ExecApprovalRequest / ItemCompleted
    │  Op Queue（界面→core）：UserTurnStart / ApprovalResponse / UserTurnCancel / Compact
    ▼
协议层（Op/Event 双向契约，schema 版本化 + Op 幂等）
    ├── JSONL 持久化（append-only 事实源 + flock 写锁）
    └── WebSocket 广播（Web 驾驶舱，多标签页共享会话）
Web 驾驶舱（React 18 + Vite）← 唯一界面
```

```
Agent Loop（手写状态机 + 事件流）
  ├── Plan 模式：architect 强模型 → 规划 / 反思归因 / 只读
  ├── Act 模式：editor 快模型 → 10 内置工具（Read/Write/Edit/Bash/Glob/Grep/WebSearch/WebFetch/Monitor/AskUser）
  ├── 变更控制：工具执行 → staging diff → Hook 验证（YAGNI/安全/权限）→ 落盘 → Git Checkpoint
  ├── 记忆：session / project / global 三层 + T1-T5 压缩
  └── 安全：黑名单硬锁 → ensemble 投票 → 审批（三级权限 + auto-approve）
```

## 技术栈

- **后端**：Python / FastAPI + WebSocket / pydantic
- **前端**：React 18 + Vite（事件流时间线 / 审批中心 / YAGNI 看板 / 只读代码查看器）
- **解析**：Tree-sitter（Python / JS / Go）+ 引用图
- **存储**：JSONL 事件存储 + Git Checkpoint（三-parent stash + 私用 refs）
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
docker build -t codemason .
docker run -p 8765:8765 codemason
```

## 测试与评测

```bash
pytest tests/ -v        # 单元测试（Agent Loop / 协议 / 安全 / 工具 / 记忆 / 压缩）
cd frontend && npx playwright test   # E2E（WebSocket 事件推送 / 审批交互）
```

评测体系（自建任务集 + 同模型对照）：

- **自建 30 任务集**：bug 修复 10 / 功能开发 8 / 重构 6 / 测试 6，全部标注来源（改编自 SWE-bench 与真实 issue）
- **同模型对照**：Cline 配置为同一模型跑同任务集，变量唯一——对比 Agent 实现而非模型
- **fail-to-pass**：失败自动归因 → 修复重试 → 记录成功率（SWE-bench 官方指标口径）
- **固定环境**：Docker 固定镜像，评测可复现

## 目录结构

```
src/
├── agent/          # Agent Loop：事件流状态机 / Plan-Act / 反思归因 / Subagents
├── protocol/       # Op/Event 双向契约（schema 版本化 + 幂等）
├── storage/        # JSONL 事件存储 + flock 写锁
├── cli/            # headless 入口（run / --mode rpc）
├── tools/          # 10 内置工具 + 注册表
├── security/       # 纵深防御：黑名单 / ensemble / 脱敏 / 三级审批
├── checkpoint/     # Git Checkpoint（三-parent stash + 私用 refs）
├── staging/        # Staging 审查沙盒（变更先验证后落盘）
├── harness/        # Hook 框架（before/after/on_failure + block/cancel）
├── constraints/    # YAGNI 七级决策阶梯 + 四维量化
├── skills/         # Lazy Skills 渐进加载
├── memory/         # 三层记忆（session / project / global）
├── compression/    # T1-T5 渐进压缩 + auto-compact
├── routing/        # 双模型路由 + Provider 抽象 + 熔断降级
├── mcp/            # MCP 客户端 + 3 示例 Server
├── parser/         # Tree-sitter 多语言解析
├── knowledge_graph/# 引用图 / 调用关系
├── evaluation/     # 评测闭环（fail-to-pass）
└── web/            # FastAPI + WebSocket 驾驶舱后端
frontend/
└── src/            # React 驾驶舱（Timeline / ApprovalCenter / YagniBoard / FileViewer / Cockpit）
```

## 许可

MIT
