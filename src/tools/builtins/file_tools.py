"""内置工具：Read / Write / Edit / Glob / Grep（只读+写入文件操作）。"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Optional

from ..base import Tool, ToolContext
from ..registry import register_tool

MAX_READ_LINES = 2000
MAX_WRITE_BYTES = 1_000_000


class ReadTool(Tool):
    name = "Read"
    description = "读取文件内容（只读，最多 2000 行）"
    parameters = {"path": {"type": "string", "description": "文件路径"}, "limit": {"type": "integer", "description": "最大行数"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        path = Path(args["path"])
        if not path.is_absolute():
            path = Path(context.cwd if context else ".") / path
        if not path.exists():
            return {"status": "error", "error": f"文件不存在: {path}"}
        if path.is_dir():
            return {"status": "error", "error": f"是目录: {path}"}
        limit = int(args.get("limit", MAX_READ_LINES))
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            content = "\n".join(lines[:limit])
            return {"status": "ok", "path": str(path), "content": content, "total_lines": total, "truncated": total > limit}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class WriteTool(Tool):
    name = "Write"
    description = "写入/覆盖文件（写入类，需审批）"
    parameters = {"path": {"type": "string", "description": "文件路径"}, "content": {"type": "string", "description": "文件内容"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        path = Path(args["path"])
        if not path.is_absolute():
            path = Path(context.cwd if context else ".") / path
        content = str(args.get("content", ""))
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            return {"status": "error", "error": f"内容超过 {MAX_WRITE_BYTES} 字节"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(path), "bytes": len(content.encode("utf-8"))}


class EditTool(Tool):
    name = "Edit"
    description = "编辑文件（diff 级替换，写入类，需审批；staging 沙盒先落暂存区）"
    parameters = {
        "path": {"type": "string", "description": "文件路径"},
        "old_string": {"type": "string", "description": "要替换的原文"},
        "new_string": {"type": "string", "description": "替换后的内容"},
    }

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        path = Path(args["path"])
        if not path.is_absolute():
            path = Path(context.cwd if context else ".") / path
        if not path.exists():
            return {"status": "error", "error": f"文件不存在: {path}"}
        old_string = args["old_string"]
        new_string = args.get("new_string", "")
        content = path.read_text(encoding="utf-8")
        if old_string not in content:
            return {"status": "error", "error": "old_string 未在文件中找到"}
        count = content.count(old_string)
        if count > 1 and not args.get("replace_all"):
            return {"status": "error", "error": f"old_string 出现 {count} 次，需指定 replace_all"}
        new_content = content.replace(old_string, new_string) if args.get("replace_all") else content.replace(old_string, new_string, 1)
        diff = self._make_diff(content, new_content, str(path))
        path.write_text(new_content, encoding="utf-8")
        return {"status": "ok", "path": str(path), "diff": diff}

    @staticmethod
    def _make_diff(old: str, new: str, path: str) -> str:
        return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}"))


class GlobTool(Tool):
    name = "Glob"
    description = "按模式查找文件（只读）"
    parameters = {"pattern": {"type": "string", "description": "glob 模式，如 **/*.py"}, "path": {"type": "string", "description": "搜索根目录"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        root = Path(args.get("path") or (context.cwd if context else "."))
        pattern = args["pattern"]
        files = [str(p) for p in root.glob(pattern)]
        return {"status": "ok", "files": sorted(files)[:1000], "count": len(files)}


class GrepTool(Tool):
    name = "Grep"
    description = "按正则搜索文件内容（只读）"
    parameters = {"pattern": {"type": "string", "description": "正则表达式"}, "path": {"type": "string", "description": "搜索根目录"}, "glob": {"type": "string", "description": "文件过滤"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        import re

        root = Path(args.get("path") or (context.cwd if context else "."))
        pattern = re.compile(args["pattern"])
        glob_filter = args.get("glob")
        matches = []
        for p in root.rglob("*"):
            if p.is_dir():
                continue
            if glob_filter and not p.match(glob_filter):
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pattern.search(line):
                        matches.append({"file": str(p), "line": i, "content": line[:300]})
                        if len(matches) >= 500:
                            break
            except Exception:
                continue
            if len(matches) >= 500:
                break
        return {"status": "ok", "matches": matches, "count": len(matches)}


register_tool(ReadTool())
register_tool(WriteTool())
register_tool(EditTool())
register_tool(GlobTool())
register_tool(GrepTool())
