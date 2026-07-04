"""内置工具：Ocr（OCR 确定性读字，v1.27 新增）。

设计（对标 PaddleOCR 中文 OCR 事实标准 / 与 ReadImage 互补）：
- 信息通道成本分层：需要"读字"走 OCR（免费、本地、零 token、确定性）；
  需要"理解图意"走 ReadImage（视觉子代理 MiMo，付费但懂语义）
- PaddleOCR 后端：中文 98.2% 事实标准（ICDAR 2019-MLT），轻量化模型 15-30MB，
  CPU ≤50ms；英文场景可降 Tesseract
- 引擎缺失 fail-closed：PaddleOCR 未安装 → 明确报错（不静默、不猜、不降级到视觉模型）
- 只读操作：可归 Tier1 零延迟放行（不过安全分类器）
- 输出结构化：text（全文拼接）+ blocks（text + box 坐标 + confidence）——供视觉执行面板
  可视化文字块位置
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..base import Tool, ToolContext
from ..registry import register_tool

# OCR 后端引擎名（与 ReadImage 的 model 参数对称：一个读字一个看图）
DEFAULT_OCR_ENGINE = "paddleocr"
OCR_ENGINE_DISABLED_MSG = (
    "OCR 引擎不可用：PaddleOCR 未安装（pip install paddleocr paddlepaddle；"
    "轻量化模型自动下载约 15-30MB）。或改用 ReadImage 工具（视觉模型理解图意）。"
)

# 读图保护：最大图片体积（10MB，与 ReadImage 一致）
MAX_OCR_BYTES = 10 * 1024 * 1024
# 支持格式（白名单，与 ReadImage 一致）
OCR_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# 全局引擎可用性缓存（探测一次，避免每次调用 import paddle 拖慢）
_ENGINE_CACHE: Optional[bool] = None


def _ocr_engine_available() -> bool:
    """探测 PaddleOCR 是否可用（import 探测，带缓存）。"""
    global _ENGINE_CACHE
    if _ENGINE_CACHE is not None:
        return _ENGINE_CACHE
    try:
        import paddleocr  # noqa: F401

        _ENGINE_CACHE = True
    except Exception:
        _ENGINE_CACHE = False
    return _ENGINE_CACHE


def _run_paddle_ocr(path: Path, lang: str = "ch") -> list[dict]:
    """调用 PaddleOCR 提取文字块（text + box + confidence）。"""
    from paddleocr import PaddleOCR

    # 延迟初始化：每次调用创建（PaddleOCR 实例较重，但保证线程安全）
    ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    result = ocr.ocr(str(path), cls=True)
    blocks = []
    if not result:
        return blocks
    for page in result:
        if not page:
            continue
        for line in page:
            # line = [box, (text, confidence)]
            box = line[0] if isinstance(line, (list, tuple)) and len(line) >= 1 else None
            text_conf = line[1] if isinstance(line, (list, tuple)) and len(line) >= 2 else None
            text = text_conf[0] if isinstance(text_conf, (list, tuple)) else str(text_conf)
            conf = text_conf[1] if isinstance(text_conf, (list, tuple)) and len(text_conf) >= 2 else None
            blocks.append(
                {
                    "text": str(text),
                    "box": box,
                    "confidence": float(conf) if isinstance(conf, (int, float)) else None,
                }
            )
    return blocks


def _mock_ocr_result(texts: list[str]) -> dict:
    """离线测试辅助：构造 mock OCR 结果（无引擎依赖）。"""
    blocks = []
    for i, t in enumerate(texts):
        x = 10 + i * 110
        blocks.append(
            {
                "text": t,
                "box": [[x, 10], [x + 100, 10], [x + 100, 40], [x, 40]],
                "confidence": 0.98,
            }
        )
    return {
        "status": "ok",
        "engine": DEFAULT_OCR_ENGINE,
        "text": "\n".join(texts),
        "blocks": blocks,
    }


class OcrTool(Tool):
    name = "Ocr"
    description = "OCR 提取图片文字（本地确定性读字，PaddleOCR 中文优化；只读，零 token 零网络）"
    parameters = {
        "path": {"type": "string", "description": "图片路径（绝对路径或相对工作目录）"},
        "lang": {"type": "string", "description": "可选：识别语言（默认 ch，英文用 en）"},
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
        if path.suffix.lower() not in OCR_IMAGE_EXTS:
            return {"status": "error", "error": f"不支持的图片格式: {path.suffix}（支持 {sorted(OCR_IMAGE_EXTS)}）"}
        if path.stat().st_size > MAX_OCR_BYTES:
            return {"status": "error", "error": f"图片超过 {MAX_OCR_BYTES // 1024 // 1024}MB: {path.name}"}

        # 引擎缺失 fail-closed：明确报错，不静默、不自动降级到视觉模型
        if not _ocr_engine_available():
            return {"status": "error", "error": OCR_ENGINE_DISABLED_MSG}

        lang = str(args.get("lang", "ch"))
        try:
            blocks = _run_paddle_ocr(path, lang)
        except Exception as e:
            return {"status": "error", "error": f"OCR 引擎执行失败: {e}"}

        text = "\n".join(b["text"] for b in blocks if b["text"])
        return {
            "status": "ok",
            "path": str(path),
            "engine": DEFAULT_OCR_ENGINE,
            "lang": lang,
            "text": text,
            "blocks": blocks,
        }


register_tool(OcrTool())
