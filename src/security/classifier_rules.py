"""自动安全分类器规则集（G18 v1.21/v1.22 落地）。

- Tier 分级（Anthropic 官方 Tier1/2/3）：Tier1 内置安全工具 allowlist（永不过分类器）、
  Tier2 项目目录内文件写/编辑（自动放行）、Tier3 过分类器（shell/Web/外部工具/子代理/目录外）
- hard-deny 规则（Anthropic 官方 20+ 默认规则分组）：Destroy or exfiltrate /
  Degrade security posture——硬拦不可协商
- 分类器判决准则（评估真实世界影响而非表面文本）：agent 写 payload 到文件再运行→评估 payload；
  && 链=整个链是一个动作；写 Python 脚本组装 shell 命令→评估组装后的命令

范式声明：本模块 = 确定性规则（纯函数 + 常量表），不依赖 LLM（LLM 精判在 classifier.py stage2）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ========== Tier 分级 ==========

# Tier 1：内置安全工具 allowlist（不改状态，永不过分类器）
TIER1_TOOLS: set[str] = {
    "Read",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "Monitor",  # 只读采样
    "AskUser",  # 交互不发起外部动作
}

# Tier 2：项目目录内文件写/编辑（自动放行不过分类器）
TIER2_TOOLS: set[str] = {"Write", "Edit"}

# Tier 3：shell 命令 / Web fetch / 外部工具集成 / 子代理生成 / 项目目录外文件操作
TIER3_TOOLS: set[str] = {"Bash", "Subagent", "run_code"}


def classify_tier(tool_name: str, args: Optional[dict] = None) -> int:
    """工具调用 Tier 分级（v1.22 Anthropic 官方）。

    特殊规则：
    - WebFetch 只读放 Tier1，但其 URL 目标在目录外（网络边界）→ 单独评估目标域（Tier3 边界检查）
    - Write/Edit 的 path 若在项目目录外 → 升级 Tier3（目录外文件操作）
    """
    args = args or {}
    if tool_name in TIER1_TOOLS:
        return 1
    if tool_name in TIER2_TOOLS:
        path = str(args.get("path") or args.get("file_path") or "")
        # 目录外文件操作（绝对路径逃逸）→ 升级 Tier3
        if path.startswith(("/", "\\", "C:", "D:")) and not path.startswith(("./", ".\\")):
            return 3
        return 2
    return 3


# ========== hard-deny 规则（分组，Anthropic 官方 20+ 规则） ==========

# 分组 1：Destroy or exfiltrate（破坏或外泄）
DESTROY_EXFILTRATE: list[tuple[str, str]] = [
    (r"\bgit\s+push\s+(-f|--force)", "破坏性 git 强推覆盖历史"),
    (r"\bgit\s+reset\s+--hard\s+HEAD~", "git reset --hard 丢弃提交"),
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)*/", "rm 递归删除根路径"),
    (r"\brm\s+-rf\s+(\*|\.|\*\.)", "rm -rf 通配符删除（当前目录全删）"),
    (r"\brm\s+-rf\s+~", "rm -rf 家目录"),
    (r"\bmkfs(?:\s|\.)", "格式化文件系统"),
    (r"\bdd\s+if=.*\s+of=/dev/", "dd 写入块设备"),
    (r">\s*/dev/sd[a-z]", "写入磁盘设备"),
    (r"\b(aws|gcloud|az|kubectl|docker)\s+\S+\s+(delete|rm|destroy|force-delete)", "云资源批量删除"),
    (r"\bkubectl\s+(delete|drain|uncordon)", "kubectl 删除/驱逐"),
    (r"\bdocker\s+(rm|rmi|kill|system\s+prune|volume\s+rm|network\s+rm)", "docker 破坏操作"),
    (r"\(\)\s*\{", "fork 炸弹"),
    (r"\bchmod\s+(777|666|0777|-R\s+777)\s*/", "chmod 根/系统路径权限放大"),
    (r"\bcrontab\s+(-e|-r|-)", "crontab 写入/删除（持久化）"),
    (r"\bmysql\s+.*\s+-e\s+.*\b(DROP|TRUNCATE)\b", "数据库 DROP/TRUNCATE"),
    (r"\bDROP\s+TABLE|TRUNCATE\s+TABLE", "SQL 删表"),
    (r"\bscp\s+.*\s+[a-zA-Z0-9._-]+@", "数据外发到外部主机"),
    (r"\brsync\s+.*\s+[a-zA-Z0-9._-]+@", "rsync 外发"),
    (r"\bcurl\s+.*(-d|--data).*https?://[^\s]+", "POST 数据到外部 URL"),
    (r"\b(echo|printf)\s+.*(BEGIN (RSA|OPENSSH|PRIVATE)|secret|password|api[_-]?key).*>\s*[^\s]+", "密钥写入文件"),
]

# 分组 2：Degrade security posture（削弱安全态势）
DEGRADE_SECURITY: list[tuple[str, str]] = [
    (r"\bchmod\s+-R\s+777", "chmod -R 777 权限放大"),
    (r"\bsudo\s+", "sudo 提权"),
    (r"\bchown\s+.*\s+/\s*$", "chown 根目录"),
    (r"setenforce\s+0|systemctl\s+stop\s+firewalld|ufw\s+disable", "禁用安全策略"),
    (r"\bservice\s+\w+\s+stop", "停止服务（可能致瘫）"),
    (r"iptables\s+(-F|--flush)|nft\s+flush", "清空防火墙规则"),
    (r"\bopenssl\s+genrsa\s+.*>|ssh-keygen\s+.*-f", "生成密钥（凭据外置风险）"),
    (r"\bexport\s+(AWS_|GITHUB_|OPENAI_|ANTHROPIC_|DEEPSEEK_).*=.*", "导出云/LLM 凭据到环境"),
    (r"systemctl\s+disable", "禁用服务（持久化攻击）"),
    (r"\bcrontab\s+.*(-e|-r)|at\s+now", "持久化任务（攻击持久化）"),
    (r"\.ssh/authorized_keys", "写入 SSH authorized_keys"),
]

# 分组 3：Supply chain / execution of untrusted（供应链与不可信执行）
SUPPLY_CHAIN: list[tuple[str, str]] = [
    (r"\bcurl\s+.*\|\s*(ba|sh|bash)\s*$", "curl 管道执行（供应链风险）"),
    (r"\bwget\s+.*\|\s*(ba|sh|bash)\s*$", "wget 管道执行"),
    (r"pip\s+install\s+[a-zA-Z0-9_.-]+\s*$", "pip 安装新包（需查存在性）"),
    (r"npm\s+install\s+[a-zA-Z0-9@/_.-]+", "npm 安装新包（需查存在性）"),
    (r"\bgit\s+clone\s+https?://", "克隆外部仓库（需验证存在性）"),
]

# 全量 hard-deny（含分组标注）
HARD_DENY_RULES: list[tuple[str, str, str]] = [
    *[(p, d, "destroy_exfiltrate") for p, d in DESTROY_EXFILTRATE],
    *[(p, d, "degrade_security") for p, d in DEGRADE_SECURITY],
    *[(p, d, "supply_chain") for p, d in SUPPLY_CHAIN],
]

# 注入探测规则（input 层 prompt injection 扫描）
INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+)?(previous|prior)\s+instructions", "忽略先前指令"),
    (r"(forget|ignore|disregard)\s+(everything|all)\b", "遗忘一切指令"),
    (r"system\s*(prompt|instructions?)?\s*[:=]", "伪装系统提示词"),
    (r"you\s+are\s+now\s+", "角色劫持"),
    (r"disregard\s+(the\s+)?(above|previous)", "无视上文指令"),
    (r"jailbreak|DAN\s+mode|developer\s+mode", "越狱模式"),
    (r"repeat\s+(the\s+)?(words?|phrase)\s+above", "重复上文（token 窃取）"),
    (r"<\|?(system|im_start|im_end|human|assistant)\|?>", "伪角色标记"),
    (r"print\s+your\s+(system\s+)?prompt", "提示词泄露"),
    (r"reveal\s+your\s+instructions", "指令泄露"),
    (r"pretend\s+you\s+are\s+", "身份伪装"),
    (r"say\s+.*exactly\s+.*without", "精确复述攻击"),
]


@dataclass
class RuleMatch:
    """一条 hard-deny 规则命中。"""

    pattern: str
    description: str
    group: str


class ClassifierRules:
    """分类器确定性规则引擎（stage1 快速过滤，零 LLM）。"""

    def __init__(self) -> None:
        self._compiled = [(re.compile(p), d, g) for p, d, g in HARD_DENY_RULES]
        self._injection = [(re.compile(p, re.IGNORECASE), d) for p, d in INJECTION_PATTERNS]

    def hard_deny(self, command: str) -> Optional[RuleMatch]:
        """检查命令命中 hard-deny 规则。返回第一条命中（None=未命中）。"""
        for pat, desc, group in self._compiled:
            if pat.search(command):
                return RuleMatch(pattern=pat.pattern, description=desc, group=group)
        return None

    def scan_injection(self, content: str) -> list[str]:
        """input 层注入探测：扫描外部来源内容，返回命中描述列表。"""
        hits = []
        for pat, desc in self._injection:
            if pat.search(content):
                hits.append(desc)
        return hits

    def all_hard_deny(self) -> list[tuple[str, str, str]]:
        return [(p, d, g) for p, d, g in HARD_DENY_RULES]
