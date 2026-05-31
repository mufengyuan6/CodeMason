"""FixPacket 机器可读失败契约（G11/G15 v1.16 落地）。

设计（design.md G11，对标 Rigour FixPacket v3）：
- staging Hook 验证失败时输出结构化失败包
- violation 带 file+line+endLine 精确位置 + hint + instructions 修复指令 +
  verification.commands（agent 修复后必须运行的验证命令）+
  constraints（allowed_scope / do_not_touch / max_files_changed / no_new_deps）
- 失败反馈是机器可读契约而非自然语言，agent 自修复闭环可机读验证

范式声明：业务逻辑层 OOP（结构化数据契约）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Violation:
    """一条验证失败。"""

    code: str  # 错误码（如 YAGNI_001 / SYNTAX_ERR / TEST_FAIL）
    file: str
    line: Optional[int] = None
    end_line: Optional[int] = None
    message: str = ""
    hint: str = ""
    severity: str = "error"  # error / warning


@dataclass
class FixPacket:
    """机器可读失败契约（agent 自修复的输入）。"""

    packet_id: str
    stage: str  # staging_apply / verification_gate / lint
    violations: list[Violation] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    status: str = "failed"  # failed / retryable

    def to_dict(self) -> dict:
        return {
            "packet_id": self.packet_id,
            "stage": self.stage,
            "violations": [
                {"code": v.code, "file": v.file, "line": v.line, "end_line": v.end_line, "message": v.message, "hint": v.hint, "severity": v.severity}
                for v in self.violations
            ],
            "instructions": self.instructions,
            "verification_commands": self.verification_commands,
            "constraints": self.constraints,
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def has_p0(self) -> bool:
        return any(v.severity == "error" for v in self.violations)


class FixPacketBuilder:
    """FixPacket 构建器：Hook 失败 → 结构化失败包。"""

    def __init__(self) -> None:
        self._seq = 0

    def build(
        self,
        *,
        stage: str,
        violations: list[Violation],
        instructions: Optional[list[str]] = None,
        verification_commands: Optional[list[str]] = None,
        constraints: Optional[dict] = None,
        status: str = "failed",
    ) -> FixPacket:
        self._seq += 1
        return FixPacket(
            packet_id=f"fp-{self._seq}",
            stage=stage,
            violations=violations,
            instructions=instructions or [],
            verification_commands=verification_commands or [],
            constraints=constraints or {},
            status=status,
        )

    @staticmethod
    def from_verify_failure(
        *,
        stage: str,
        file: str,
        message: str,
        line: Optional[int] = None,
        end_line: Optional[int] = None,
        hint: str = "",
        code: str = "VERIFY_FAIL",
        verification_commands: Optional[list[str]] = None,
        constraints: Optional[dict] = None,
    ) -> FixPacket:
        """从单条验证失败快速构建（staging apply 失败统一出口）。"""
        return FixPacket(
            packet_id=f"fp-{abs(hash((file, message))) % 100000}",
            stage=stage,
            violations=[Violation(code=code, file=file, line=line, end_line=end_line, message=message, hint=hint)],
            verification_commands=verification_commands or [],
            constraints=constraints or {},
        )
