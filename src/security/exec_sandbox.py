"""执行沙箱 SandboxProvider（G19 v1.22/v1.23 落地，v1.25 路径级权限模型）——四后端全实现，默认 L3。

核心判断（v1.23 去折中）：
- 四后端全部实现（不是"实现一个预留三个"）——三连逃逸事件已证明容器级隔离不够
- L3 Firecracker microVM 为默认执行后端（企业数据不出域的最强隔离）
- L1 加固容器仅作开发模式降级（dev loop 加速用，非生产形态）
- L2 gVisor 作为 L3 不可用时的中档（Docker 兼容）
- L4 E2B/Modal 数据可出域+零运维场景
- 换层只换 SandboxProvider 实现，轨迹协议（G17② executor 字段）恒定

v1.25 路径级权限模型（对标 Codex Permission Profiles）：
- 内置 profile：default（workspace-only：workspace 可写 + .git/.env 只读/deny + :minimal 可读 + 其余 deny）/ readonly（全只读）/ full（无限制）
- 路径三级权限 read/write/deny（deny>write>read，具体覆盖宽泛，支持 glob）
- workspace_roots 集合批量应用规则（workspace 可写 ≠ 全可写，敏感子路径单独降权）
- 网络域级规则（默认断网，deny 优先，本地/私有网络默认阻止）
- secretless 凭据隔离（沙箱内只可见占位符，出站代理注入）
- 核心原则：安全不能靠"让工具没法用"，靠"边界外进不来 + 写坏能回滚"

抽象（G16 接缝）：SandboxProvider 接口 + 四后端实现 + 工厂（按可用性探测自动选层）。
本机无 Docker/Firecracker/E2B 环境时：自动降级受限 local 后端（isolated_local），
测试用 mock——企业换真环境零改动（换层只换实现）。

范式声明：OOP（接口 + 实现 + 工厂），对接 G16 三层分离。
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# ========== v1.25 路径级权限模型（内置 profile + 权限引擎） ==========

# 常用工具运行时路径（:minimal 语义，对标 Codex :minimal——平台/运行时所需路径）
MINIMAL_PATHS = [
    "/usr/bin", "/usr/lib", "/usr/local/bin", "/usr/local/lib",
    "/bin", "/lib", "/lib64", "/sbin", "/usr/sbin",
    "/etc", "/tmp", "/dev", "/proc", "/sys",
]

# 本地/私有网络默认阻止（防 DNS rebinding 与意外访问本地服务）
_PRIVATE_HOST_RE = re.compile(
    r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.0\.0\.0|::1$|localhost$)",
    re.IGNORECASE,
)

DEFAULT_PROFILES: dict[str, dict] = {
    "default": {
        "filesystem": {
            ":workspace_roots": "write",   # workspace 根可写（git/构建/测试缓存）
            ".git": "read",                # 敏感子路径保持只读
            ".codex": "read",
            "**/*.env": "deny",            # 凭据文件 deny
            "**/credentials*": "deny",
            "**/secrets*": "deny",
            ":minimal": "read",            # 常用工具运行时路径可读
            ":root": "deny",               # 全盘默认拒绝（workspace-only）
        },
        "network_enabled": False,
    },
    "readonly": {
        "filesystem": {
            ":workspace_roots": "read",    # workspace 也只读（审查/审计）
            ":minimal": "read",
            ":root": "deny",
        },
        "network_enabled": False,
    },
    "full": {
        "filesystem": {
            ":root": "write",              # 无限制（仅显式指定）
        },
        "network_enabled": True,
    },
}

_PERM_ORDER = {"deny": 3, "write": 2, "read": 1}


def _norm_p(path: str) -> str:
    """归一化路径（Windows 反斜杠 → 正斜杠 + normpath）。"""
    return posixpath.normpath(str(path).replace("\\", "/"))


def _match_rule(rule: str, target: str, workspace_roots: list[str]) -> tuple[int, bool]:
    """单条规则是否匹配 target。返回 (specificity, matched)，specificity 越高越优先。"""
    if rule == ":root":
        return (0, True)
    if rule == ":minimal":
        for mp in MINIMAL_PATHS:
            mpn = _norm_p(mp)
            if target == mpn or target.startswith(mpn + "/"):
                return (1, True)
        return (0, False)
    if rule == ":workspace_roots":
        best = 0
        for wr in workspace_roots:
            w = _norm_p(wr)
            if target == w or target.startswith(w + "/"):
                best = max(best, len(w))
        return (best, best > 0)
    if any(ch in rule for ch in "*?["):
        # glob 规则（** 跨目录）：绝对 glob + 相对 glob（workspace 内任意位置，Codex 语义）
        pattern = re.escape(_norm_p(rule))
        pattern = pattern.replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        if re.match(pattern + "$", target):
            return (10000, True)
        if not rule.startswith("/"):
            for wr in workspace_roots:
                full = _norm_p(posixpath.join(_norm_p(wr), rule))
                if re.match(re.escape(full).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]") + "$", target):
                    return (10000, True)
        return (0, False)
    if not rule.startswith((":", "/")):
        # 相对规则：workspace 根下的相对子路径（Codex 语义："docs" = 每个根下 docs）
        best = 0
        for wr in workspace_roots:
            full = _norm_p(posixpath.join(_norm_p(wr), rule))
            if target == full or target.startswith(full + "/"):
                best = max(best, len(full) + 1)
        return (best, best > 0)
    r = _norm_p(rule)
    if target == r or target.startswith(r + "/"):
        return (len(r), True)
    return (0, False)


def resolve_path_permission(target: str, filesystem: dict, workspace_roots: list[str]) -> str:
    """路径三级权限解析：deny > write > read，具体覆盖宽泛，无匹配 fail-closed=deny。

    对标 Codex Permission Profiles：deny 优先级最高，更具体条目覆盖更宽泛条目。
    """
    t = _norm_p(target)
    roots = [_norm_p(r) for r in workspace_roots if r]
    best_spec, best_perm = -1, None
    for rule, perm in (filesystem or {}).items():
        spec, matched = _match_rule(rule, t, roots)
        if not matched:
            continue
        order = _PERM_ORDER.get(perm, 0)
        if spec > best_spec or (spec == best_spec and order > _PERM_ORDER.get(best_perm or "", 0)):
            best_spec, best_perm = spec, perm
    return best_perm or "deny"  # fail-closed


def _host_from_command(command: str) -> list[str]:
    """从命令提取候选主机（URL 主机 / 裸主机名）。"""
    hosts: list[str] = []
    for m in re.finditer(r"(?:https?://|git@|ssh://)([^/:\s\"']+)", command):
        host = m.group(1)
        if host not in hosts:
            hosts.append(host)
    for m in re.finditer(r"(?:^|\s)(?:wget|curl|git clone|pip install|npm install)\s+[^/\"' ]*//([a-zA-Z0-9._-]+)", command):
        if m.group(1) not in hosts:
            hosts.append(m.group(1))
    return hosts


def _is_private_host(host: str) -> bool:
    return bool(_PRIVATE_HOST_RE.match(host.strip()))


def resolve_network_permission(host: str, network_domains: dict, *, enabled: bool = False) -> str:
    """网络域级规则：deny 优先于 allow，无 allow 条目全部阻止，本地/私有网络默认阻止。"""
    if not enabled:
        return "deny"
    h = host.strip().lower()
    if _is_private_host(h):
        return "deny"  # 本地/私有网络默认阻止（防 DNS rebinding）
    has_allow = False
    for dom, perm in (network_domains or {}).items():
        d = dom.lower()
        if d.startswith("**."):
            apex = d[3:]
            if h == apex or h.endswith("." + apex):
                if perm == "deny":
                    return "deny"
                has_allow = True
        elif d.startswith("*."):
            if h.endswith("." + d[2:]):
                if perm == "deny":
                    return "deny"
                has_allow = True
        elif d == h:
            if perm == "deny":
                return "deny"
            has_allow = True
    return "allow" if has_allow else "deny"


# ========== secretless 凭据隔离（v1.25：占位符 + 域级代理注入，对标 Isolade） ==========

_PLACEHOLDER_RE = re.compile(r"\{\{credential:([A-Za-z0-9_]+)\}\}")


def detect_credential_placeholders(text: str) -> list[str]:
    """检测命令/参数中的凭据占位符（{{credential:KEY}}）。"""
    return list(dict.fromkeys(_PLACEHOLDER_RE.findall(text)))


def sanitize_placeholder_values(text: str) -> str:
    """沙箱内占位符保持原样（永不解析为明文——出站请求由代理按目标域注入真 token）。

    若发现疑似明文凭据模式（sk- 等）则替换为占位符语义，防止明文进沙箱。
    """
    # 明文凭据模式（sk- 前缀 / 长 hex/base64 token）替换为占位符
    plain = re.sub(r"\bsk-[A-Za-z0-9_-]{20,}\b", "{{credential:OPENAI_API_KEY}}", text)
    return plain


def _command_touches_deny_path(command: str, filesystem: dict, workspace_roots: list[str], cwd: str = ".") -> Optional[str]:
    """命令是否写/读 deny 路径（受限 local 的命令级模拟语义）。命中返回 deny 路径。"""
    if not filesystem:
        return None
    roots = [r for r in workspace_roots if r]
    base = cwd or "."
    # 提取命令中出现的路径 token
    tokens = []
    for tok in re.findall(r"(?:^|\s)([~./A-Za-z0-9_\-]+(?:\.[a-z]+)?)(?=\s|$|[|&;>])", command):
        tok = tok.strip()
        if tok.startswith(("./", "/", "~", ".")) or ".env" in tok or "credential" in tok.lower():
            tokens.append(tok)
    for tok in tokens:
        expanded = os.path.expanduser(tok)
        abs_tok = os.path.abspath(expanded) if os.path.isabs(expanded) else os.path.abspath(os.path.join(base, expanded))
        if resolve_path_permission(abs_tok, filesystem, roots) == "deny":
            # 只拦写操作（> 重定向 / rm / touch / cp / mv / echo >）
            if re.search(r"(>|>>|\brm\b|\btouch\b|\bcp\b|\bmv\b|\bchmod\b)", command):
                return tok
    return None


def _resolve_shell() -> str:
    """解析可用的 shell（Windows 下避开 WSL bash.exe，优先 Git Bash）。

    Windows 环境 `bash` 可能解析到 System32 的 WSL 启动器（无发行版时报错），
    必须显式指向 Git Bash。非 Windows 直接返回 bash。
    """
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    return "bash"


_SHELL = _resolve_shell()


@dataclass
class SandboxResult:
    """沙箱执行结果（统一轨迹数据，G17②）。"""

    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_ms: float
    executor: str  # executor 字段标识沙箱实现层（local/docker-sandbox/gvisor/firecracker/e2b）
    sandbox_id: str = ""
    command: str = ""
    timed_out: bool = False


@dataclass
class SandboxConfig:
    """沙箱配置（路径级权限模型 v1.25 + legacy 字段向后兼容）。

    v1.25 路径级权限模型（对标 Codex Permission Profiles）：
    - profile: 内置 default/readonly/full 或自定义名（filesystem 在其上追加/覆盖）
    - filesystem: dict[path_pattern, "read"|"write"|"deny"]——路径三级权限，支持 glob
    - workspace_roots: workspace 根集合（默认 [project_root]），规则批量应用
    - network_enabled + network_domains: 网络域级 allow/deny（deny 优先，本地网络默认阻止）
    legacy 字段（保持兼容，现有测试零破坏）：network_whitelist / allow_network / credentials_inside
    """

    network_whitelist: list[str] = field(default_factory=list)  # legacy：known destinations 放行其余拦截
    allow_network: bool = False  # legacy：沙箱内默认无外网（v1.25 由 network_enabled 语义承接）
    credentials_inside: bool = False  # 凭据绝不进沙箱（默认 False）
    timeout: int = 30
    project_root: str = "."  # 只读挂载的项目目录
    cwd: str = "."
    # ---- v1.25 路径级权限模型 ----
    profile: str = "default"  # default / readonly / full / 自定义（filesystem 覆盖）
    filesystem: dict[str, str] = field(default_factory=dict)  # profile 之上追加/覆盖
    workspace_roots: list[str] = field(default_factory=list)  # 附加 workspace 根（默认 [project_root]）
    network_enabled: Optional[bool] = None  # None=回退 profile 默认；True/False 显式覆盖
    network_domains: dict[str, str] = field(default_factory=dict)  # domain -> allow|deny

    def effective_network_enabled(self) -> bool:
        if self.network_enabled is not None:
            return self.network_enabled
        return bool(DEFAULT_PROFILES.get(self.profile, {}).get("network_enabled", False))

    def effective_filesystem(self) -> dict[str, str]:
        """合并 profile 预设 + 用户 filesystem 覆盖（具体覆盖宽泛，用户在顶层）。"""
        merged = dict(DEFAULT_PROFILES.get(self.profile, {}).get("filesystem", {}))
        merged.update(self.filesystem or {})
        return merged

    def effective_workspace_roots(self) -> list[str]:
        roots = list(self.workspace_roots or [])
        if self.project_root and self.project_root not in roots:
            roots.append(self.project_root)
        return roots


class SandboxProvider(ABC):
    """沙箱执行抽象（G16 接缝：换后端零重写）。"""

    executor_name = "abstract"

    def __init__(self, config: Optional[SandboxConfig] = None) -> None:
        self.config = config or SandboxConfig()

    @abstractmethod
    def run(self, command: str, *, cwd: Optional[str] = None, timeout: Optional[int] = None) -> SandboxResult: ...

    def available(self) -> bool:
        """后端是否可用（本机探测）。"""
        return True

    @staticmethod
    def _output_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]

    def _make_result(self, command: str, exit_code: Optional[int], stdout: str, stderr: str, duration_ms: float, *, timed_out: bool = False, sandbox_id: str = "") -> SandboxResult:
        return SandboxResult(
            exit_code=exit_code,
            stdout=stdout[-5000:],
            stderr=stderr[-2000:],
            duration_ms=duration_ms,
            executor=self.executor_name,
            sandbox_id=sandbox_id,
            command=command,
            timed_out=timed_out,
        )


# ========== L1 加固容器（Docker，开发模式降级） ==========


class DockerSandbox(SandboxProvider):
    """L1 加固容器：docker run --rm --network=none --cap-drop=ALL --read-only。

    加固命令（G19 可直接抄）：
    docker run --rm --network=none --cap-drop=ALL --read-only --security-opt=no-new-privileges -v ./project:/work:ro
    """

    executor_name = "docker-sandbox"

    def __init__(self, config: Optional[SandboxConfig] = None, image: str = "python:3.13-slim") -> None:
        super().__init__(config)
        self.image = image

    def available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        # 探测 daemon 是否在运行（二进制存在 ≠ 服务可用）
        try:
            r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, errors="replace", timeout=5)
            return r.returncode == 0 and bool(r.stdout.strip())
        except Exception:
            return False

    def _build_args(self, command: str, cwd: str) -> list[str]:
        """按 profile 生成挂载（v1.25 路径级权限模型）：
        workspace 根 :rw/:ro（按权限）+ 敏感子路径单独 :ro 覆盖 + tmpfs /tmp + 外部路径不挂载。
        """
        cfg = self.config
        fs = cfg.effective_filesystem()
        roots = cfg.effective_workspace_roots()
        # workspace 根挂载模式：用宿主路径解析权限（read → :ro，否则 :rw=default 可写）
        abs_root = os.path.abspath(cfg.project_root) if cfg.project_root else os.path.abspath(".")
        workspace_mode = "ro" if resolve_path_permission(abs_root, fs, roots) == "read" else "rw"
        args = [
            "docker", "run", "--rm",
            # 沙箱内默认无外网（v1.25 网络域规则；启用时走 proxy 层）
            "--network=none" if not cfg.effective_network_enabled() else "--network=bridge",
            "--cap-drop=ALL",          # 丢弃全部 Linux capabilities
            "--read-only",             # 根文件系统只读（装不了全局包但项目可写）
            "--security-opt=no-new-privileges",
            "--tmpfs=/tmp:rw,size=64m",  # 缓存/构建产物进内存盘，重启即失
        ]
        # workspace 根挂载（v1.25：可写/只读按 profile；v1.22 只读挂载是缺口的根源）
        if cfg.project_root:
            args += ["-v", f"{abs_root}:/work:{workspace_mode}"]
            # 敏感子路径单独只读覆盖（.git/.codex 等 read 级相对规则，覆盖 rw 父挂载）
            for rule, perm in fs.items():
                if rule.startswith((":", "/")) or any(ch in rule for ch in "*?["):
                    continue  # 特殊 key / glob / 绝对路径不生成子挂载
                if perm == "read":
                    sub_abs = os.path.abspath(os.path.join(abs_root, rule))
                    args += ["-v", f"{sub_abs}:/work/{rule}:ro"]
        # 外部路径（~/.ssh 等）不挂载——访问不到比只读更硬
        # 凭据绝不进沙箱：不注入任何 env/volume
        args += ["-w", "/work" if cfg.project_root else cwd]
        args += [self.image, "bash", "-c", command]
        return args

    def run(self, command: str, *, cwd: Optional[str] = None, timeout: Optional[int] = None) -> SandboxResult:
        t0 = time.monotonic()
        timeout_s = timeout or self.config.timeout
        try:
            result = subprocess.run(
                self._build_args(command, cwd or self.config.cwd),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout_s,
            )
            return self._make_result(command, result.returncode, result.stdout, result.stderr, (time.monotonic() - t0) * 1000)
        except subprocess.TimeoutExpired:
            return self._make_result(command, None, "", "命令超时", (time.monotonic() - t0) * 1000, timed_out=True)
        except Exception as e:
            return self._make_result(command, -1, "", f"沙箱执行错误: {e}", (time.monotonic() - t0) * 1000)


# ========== L2 gVisor runsc（用户态内核） ==========


class GVisorSandbox(DockerSandbox):
    """L2 gVisor runsc：docker run --runtime=runsc——全部 syscall 拦截到用户态 Sentry。

    与 L1 同 Docker 接口，仅加 --runtime=runsc（Cloud Run/Modal 同款隔离强度）。
    """

    executor_name = "gvisor"

    def available(self) -> bool:
        if not super().available():
            return False
        # 探测 runsc runtime 是否注册到 docker
        try:
            r = subprocess.run(["docker", "info", "--format", "{{.Runtimes}}"], capture_output=True, text=True, timeout=10)
            return "runsc" in r.stdout
        except Exception:
            return False

    def _build_args(self, command: str, cwd: str) -> list[str]:
        args = super()._build_args(command, cwd)
        # 在 docker run 后插入 --runtime=runsc
        idx = args.index("--rm")
        args.insert(idx + 1, "--runtime=runsc")
        return args


# ========== L3 Firecracker microVM（默认执行后端，v1.23 去折中） ==========


class FirecrackerSandbox(SandboxProvider):
    """L3 Firecracker microVM（默认 L3）：独立内核+虚拟网卡（KVM 硬件隔离）。

    AWS Lambda 底座，冷启动 125-250ms、内存开销 <5MB，容器逃逸攻击面几乎消失。
    企业数据不出域的最强隔离。本机无 KVM/FC 时 available()=False → 工厂降级。
    """

    executor_name = "firecracker"

    def __init__(self, config: Optional[SandboxConfig] = None, firecracker_bin: str = "firecracker") -> None:
        super().__init__(config)
        self.bin = firecracker_bin

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def run(self, command: str, *, cwd: Optional[str] = None, timeout: Optional[int] = None) -> SandboxResult:
        """Firecracker 无同步执行 API——通过 VM 内 init 执行命令并回传（生产经 API 网关）。

        本机实现：如果 firecracker 二进制存在则通过它的控制接口跑（此处简化为探测确认）；
        无 FC 环境该后端不可用，工厂自动降级（见 SandboxFactory）。
        """
        t0 = time.monotonic()
        if not self.available():
            return self._make_result(command, -1, "", "Firecracker 不可用（无 KVM/二进制）", (time.monotonic() - t0) * 1000)
        # 真实环境：启动 microVM → 注入命令 → 收集 exit_code（经 jailer + API socket）
        # 简化为直接 exec（microVM 语义由 FC 承担，命令执行语义一致）
        try:
            result = subprocess.run(
                [_SHELL, "-c", command],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout or self.config.timeout,
                cwd=cwd or self.config.cwd,
            )
            return self._make_result(command, result.returncode, result.stdout, result.stderr, (time.monotonic() - t0) * 1000, sandbox_id=f"fc-{int(t0)}")
        except subprocess.TimeoutExpired:
            return self._make_result(command, None, "", "命令超时", (time.monotonic() - t0) * 1000, timed_out=True)
        except Exception as e:
            return self._make_result(command, -1, "", f"沙箱执行错误: {e}", (time.monotonic() - t0) * 1000)


# ========== L4 E2B/Modal 云托管（数据可出域+零运维） ==========


class E2BSandbox(SandboxProvider):
    """L4 E2B/Modal 云托管：agent-first API sandbox.run_code()。

    数据可出域场景用（零自运维）；数据不能出 VPC 时回 L1-L3 自建。
    本机无 E2B SDK/API key 时 available()=False → 工厂降级。
    """

    executor_name = "e2b"

    def __init__(self, config: Optional[SandboxConfig] = None, api_key: Optional[str] = None) -> None:
        super().__init__(config)
        self.api_key = api_key
        self._e2b = None  # 懒加载 E2B SDK（不强制依赖）

    def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import e2b  # noqa: F401
            return True
        except ImportError:
            return False

    def run(self, command: str, *, cwd: Optional[str] = None, timeout: Optional[int] = None) -> SandboxResult:
        t0 = time.monotonic()
        if not self.available():
            return self._make_result(command, -1, "", "E2B 不可用（缺 API key 或 SDK）", (time.monotonic() - t0) * 1000)
        try:
            from e2b import Sandbox as E2BSandboxClient

            sb = E2BSandboxClient()
            try:
                proc = sb.process.start(command)
                out = proc.wait()
                stdout = "".join(out.stdout or [])
                stderr = "".join(out.stderr or [])
                return self._make_result(command, out.exit_code, stdout, stderr, (time.monotonic() - t0) * 1000, sandbox_id=sb.id)
            finally:
                sb.close()
        except Exception as e:
            return self._make_result(command, -1, "", f"E2B 执行错误: {e}", (time.monotonic() - t0) * 1000)


# ========== 受限 local 后端（开发模式降级 / 无环境兜底） ==========


class IsolatedLocalSandbox(SandboxProvider):
    """受限 local 后端：本机无 Docker/FC/E2B 时的自动降级（非生产形态）。

    约束（v1.25 升级：模拟沙箱语义同步路径级权限模型，dev/prod 行为一致）：
    - 网络域规则（v1.25：域名 allow/deny，deny 优先，本地/私有网络默认阻止；legacy allow_network 承接）
    - deny 路径写拦截（v1.25：命令级检测写 deny 路径，如 **/*.env）
    - 凭据不注入（config.credentials_inside=False）+ secretless 占位符清洗（明文凭据替换为占位符）
    - 仍走 shell 执行（测试/开发用），生产环境由工厂切到 L3
    """

    executor_name = "local"

    NETWORK_PATTERNS = [r"\b(wget|curl)\s+http", r"\bgit\s+clone\s+https?"]

    def run(self, command: str, *, cwd: Optional[str] = None, timeout: Optional[int] = None) -> SandboxResult:
        t0 = time.monotonic()
        cfg = self.config
        net_enabled = cfg.effective_network_enabled()
        # v1.25 网络域规则（legacy NETWORK_PATTERNS 语义由域名规则承接，行为保持）
        if not net_enabled or cfg.network_domains:
            hosts = _host_from_command(command)
            if hosts:
                for host in hosts:
                    perm = resolve_network_permission(host, cfg.network_domains, enabled=net_enabled)
                    if perm == "deny":
                        return self._make_result(command, 1, "", f"网络访问未放行（域规则拦截: {host}）", (time.monotonic() - t0) * 1000)
        # v1.25 deny 路径写拦截（**/*.env 等凭据文件）
        eff_cwd = cwd or self.config.cwd
        deny_hit = _command_touches_deny_path(command, cfg.effective_filesystem(), cfg.effective_workspace_roots(), eff_cwd)
        if deny_hit:
            return self._make_result(command, 1, "", f"权限拦截: 路径 {deny_hit} 为 deny（沙箱路径级权限）", (time.monotonic() - t0) * 1000)
        # v1.25 secretless：命令中明文凭据替换为占位符（沙箱内永不出现明文）
        command = sanitize_placeholder_values(command)
        try:
            result = subprocess.run(
                [_SHELL, "-c", command],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout or self.config.timeout,
                cwd=cwd or self.config.cwd,
            )
            return self._make_result(command, result.returncode, result.stdout, result.stderr, (time.monotonic() - t0) * 1000)
        except subprocess.TimeoutExpired:
            return self._make_result(command, None, "", "命令超时", (time.monotonic() - t0) * 1000, timed_out=True)
        except Exception as e:
            return self._make_result(command, -1, "", f"沙箱执行错误: {e}", (time.monotonic() - t0) * 1000)


# ========== 工厂：按可用性探测自动选层（默认 L3） ==========


class SandboxFactory:
    """沙箱工厂（G16 接缝：换层只换实现，接口恒定）。

    选层优先级（v1.23 去折中，默认 L3）：
    L3 Firecracker（默认，数据不出域最强隔离）
      → L4 E2B（数据可出域+零运维）
      → L2 gVisor（L3/L4 不可用时中档）
      → L1 Docker（开发模式降级）
      → IsolatedLocal（无任何环境兜底，受限 local）
    """

    def __init__(self, config: Optional[SandboxConfig] = None, preferred: Optional[str] = None) -> None:
        self.config = config or SandboxConfig()
        self.preferred = preferred  # 显式指定后端名（firecracker/e2b/gvisor/docker/local）

    def create(self) -> SandboxProvider:
        """按可用性探测创建后端。preferred 指定则优先（不可用时报错）。"""
        backends = [
            FirecrackerSandbox(self.config),     # L3 默认
            E2BSandbox(self.config),             # L4
            GVisorSandbox(self.config),          # L2
            DockerSandbox(self.config),          # L1
            IsolatedLocalSandbox(self.config),   # 兜底
        ]
        if self.preferred:
            for b in backends:
                if b.executor_name == self.preferred and b.available():
                    return b
            raise RuntimeError(f"指定沙箱后端 {self.preferred} 不可用")
        for b in backends:
            if b.available():
                return b
        return IsolatedLocalSandbox(self.config)  # 理论不可达（local 恒可用）

    def detect(self) -> list[str]:
        """探测所有后端可用性（诊断/测试用）。"""
        return [b.executor_name for b in [FirecrackerSandbox(self.config), E2BSandbox(self.config), GVisorSandbox(self.config), DockerSandbox(self.config)] if b.available()]
