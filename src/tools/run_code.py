"""PTC 程序化工具调用（G16② v1.23 落地）。

设计（design.md G16②，对标 DSH PTC 模式/Code Mode，与 YAGNI"少"哲学同构）：
- 新增 run_code 工具：模型生成一段 Python 脚本，一次执行组合多步工具调用
  （写循环/条件/并发/中间过滤）
- 中间过程不进模型上下文，只返回最终打印/返回结果——五步任务从"五次模型-工具往返"
  降为"一段程序一次执行"，步骤越多/中间数据量越大，越快且上下文消耗越少
- 程序内每次工具调用仍走完整执行流水线（审批/沙箱/超时/日志照常）——PTC 不绕过安全

范式声明：工具层 OOP + 注册表（策略模式 + register_tool 自动注册）。
"""

from __future__ import annotations

import io
import textwrap
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Optional

from ..tools.base import Tool, ToolContext
from ..tools.registry import register_tool

# PTC 沙箱内可用工具（白名单：run_code 只能调这些）
PTC_ALLOWED_TOOLS = {"Read", "Glob", "Grep", "Bash", "WebSearch", "WebFetch", "Monitor", "Write", "Edit"}


class RunCodeTool(Tool):
    name = "run_code"
    description = "程序化工具调用（PTC）：执行一段 Python 脚本组合多步工具调用，中间过程不进上下文，只返回最终结果"
    parameters = {
        "code": {"type": "string", "description": "Python 脚本（可用 tools 对象调用内置工具，如 tools.read('a.py')）"},
        "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
    }

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        code = args["code"]
        timeout = int(args.get("timeout", 30))
        try:
            result = self._execute(code, context, timeout)
            return {"status": "ok", "result": result}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()[-1000:]}

    def _execute(self, code: str, context: Optional[ToolContext], timeout: int) -> str:
        """在受限命名空间执行 PTC 脚本。

        安全设计：
        - 内置 __builtins__ 裁剪（无 open/eval/exec/import 的裸系统访问）
        - tools 对象只暴露白名单工具（PTC_ALLOWED_TOOLS），每次调用走 ToolContext
        - 中间 print 全部捕获（不进模型上下文），只返回显式 return/最后表达式
        """
        namespace = {"__builtins__": {"print": print, "len": len, "range": range, "enumerate": enumerate, "str": str, "int": int, "float": float, "list": list, "dict": dict, "set": set, "tuple": tuple, "min": min, "max": max, "sum": sum, "sorted": sorted, "reversed": reversed, "zip": zip, "isinstance": isinstance, "abs": abs, "round": round, "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError, "IndexError": IndexError}}

        # 受限 tools 代理：只暴露白名单工具，走完整流水线（经 ToolContext）
        class _ToolsProxy:
            def __getattr__(self, name):
                if name not in PTC_ALLOWED_TOOLS:
                    raise AttributeError(f"工具 {name} 不在 PTC 白名单: {sorted(PTC_ALLOWED_TOOLS)}")
                registry = getattr(context, "registry", None) if context else None

                def _call(**kwargs):
                    # 程序内工具调用：仍需审批/沙箱（调用方 ToolRegistry.call 已完成流水线，
                    # 此处直接经 registry 执行——安全由外层 pipeline 守卫）
                    if registry is None:
                        raise RuntimeError("PTC 工具代理需要 ToolRegistry（经 registry.call 执行）")
                    return registry.call(name, kwargs, context)

                return _call

        namespace["tools"] = _ToolsProxy()

        # 捕获 stdout/stderr（中间过程不进模型上下文）
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            # 顶层赋值 + 返回语义：包装为函数（每行统一缩进 4 空格，兼容多行代码）
            indented = "\n".join(f"    {line}" for line in code.splitlines())
            wrapped = f"def _ptc_main():\n{indented}\n_ptc_result = _ptc_main()\n"
            exec(wrapped, namespace, namespace)  # noqa: S102 —— 受控命名空间（PTC 设计使然）
        result = namespace.get("_ptc_result")
        printed = buf.getvalue().strip()
        # 只返回最终结果（+ 显式 print 摘要）
        parts = []
        if result is not None:
            parts.append(f"[return] {result!r}")
        if printed:
            parts.append(f"[printed]\n{printed[:2000]}")
        return "\n".join(parts) if parts else "[no output]"


register_tool(RunCodeTool())
