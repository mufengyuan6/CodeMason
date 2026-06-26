"""内置工具：ReadImage（视觉子代理读图，v1.27 新增）。

设计（对标 Echo Agent 多模态能力 / SeeingEye arXiv 2510.25092）：
- 主模型无需视觉能力：读图委托 MiMo-V2.5 等视觉模型（role=vision 子代理）
- 图片 → base64 data URL → OpenAI 兼容多模态消息（image_url 透传）
- 返回结构化 findings（≤2K 回流协议，复用 SubagentManager 语义）
- 只读操作：可归 Tier1 零延迟放行（不过安全分类器）

接入方式：model 参数可选——默认读 ~/.codemason/credentials.yaml 的 api_keys.mimo；
也可显式传 provider_name（将来接其他视觉模型）。
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

from ..base import Tool, ToolContext
from ..registry import register_tool

# 默认视觉模型端点（用户配置的 MiMo-V2.5，OpenAI 兼容）
DEFAULT_VISION_URL = "https://opencode.ai/zen/go/v1/chat/completions"
DEFAULT_VISION_MODEL = "mimo-v2.5"
# 视觉 provider 配置来源（凭据通道，不硬编码 key）
VISION_CREDENTIAL_KEY = "api_keys.mimo"

# 读图保护：最大图片体积（10MB，防超大资产拖垮请求）
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# 支持格式（白名单，防任意文件被当图片解析）
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _load_vision_key() -> Optional[str]:
    """从凭据通道读视觉模型 key（G16③：key 不硬编码，进 credentials.yaml）。"""
    try:
        from src.security.credentials import CredentialStore

        return CredentialStore().get(VISION_CREDENTIAL_KEY)
    except Exception:
        return None


def _encode_image(path: Path) -> str:
    """图片 → base64 data URL（OpenAI 多模态 image_url 格式）。"""
    raw = path.read_bytes()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB: {path.name}")
    mime = "image/png"
    if path.suffix.lower() == ".jpg" or path.suffix.lower() == ".jpeg":
        mime = "image/jpeg"
    elif path.suffix.lower() == ".gif":
        mime = "image/gif"
    elif path.suffix.lower() == ".bmp":
        mime = "image/bmp"
    elif path.suffix.lower() == ".webp":
        mime = "image/webp"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


class ReadImageTool(Tool):
    name = "ReadImage"
    description = "读取图片内容（视觉子代理：调用视觉模型理解图片，返回结构化描述；只读，无需审批）"
    parameters = {
        "path": {"type": "string", "description": "图片路径（绝对路径或相对工作目录）"},
        "prompt": {
            "type": "string",
            "description": "可选：看图问题/要求（默认：描述图片内容）",
        },
        "model": {"type": "string", "description": "可选：视觉模型名（默认 mimo-v2.5）"},
    }

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        raw_path = str(args.get("path", ""))
        if not raw_path:
            return {"status": "error", "error": "缺少 path 参数"}
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(context.cwd if context else ".") / raw_path
        if not path.exists() or not path.is_file():
            return {"status": "error", "error": f"图片不存在: {path}"}
        if path.suffix.lower() not in IMAGE_EXTS:
            return {"status": "error", "error": f"不支持的图片格式: {path.suffix}（支持 {sorted(IMAGE_EXTS)}）"}

        key = _load_vision_key()
        if not key:
            return {
                "status": "error",
                "error": f"视觉模型凭据缺失: {VISION_CREDENTIAL_KEY}（请写入 ~/.codemason/credentials.yaml）",
            }

        model = str(args.get("model", DEFAULT_VISION_MODEL))
        prompt = str(args.get("prompt", "请详细描述这张图片的内容，包括主要元素、文字、布局和关键细节。"))
        try:
            data_url = _encode_image(path)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        # OpenAI 兼容多模态消息：image_url 透传（v1.27 Provider 已支持）
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]

        try:
            import httpx

            with httpx.Client(timeout=180.0) as client:
                resp = client.post(
                    DEFAULT_VISION_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model, "messages": messages, "max_tokens": 4096},
                )
            if resp.status_code >= 400:
                return {"status": "error", "error": f"视觉模型 HTTP {resp.status_code}: {resp.text[:300]}"}
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning_content") or ""
            return {"status": "ok", "path": str(path), "model": model, "description": content.strip()}
        except Exception as e:
            return {"status": "error", "error": f"视觉模型调用失败: {e}"}


register_tool(ReadImageTool())
