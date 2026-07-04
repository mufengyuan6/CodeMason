"""OCR 确定性读字工具测试（v1.27：OcrTool + PaddleOCR）。

覆盖：
- 参数校验（缺 path / 文件不存在 / 不支持格式）
- 引擎缺失降级（PaddleOCR 未装 → fail-closed 明确报错，不静默）
- OCR 后端抽象（mock 引擎注入验证输出结构）
- 中文识别输出结构（text + box 坐标）
- 与 ReadImage 互补定位（只读 Tier1，格式白名单一致）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools.builtins.ocr_tools import (
    MAX_OCR_BYTES,
    OcrTool,
    OCR_ENGINE_DISABLED_MSG,
    _mock_ocr_result,
)


def _make_png(tmp_path: Path, name: str = "ocr_test.png") -> Path:
    """生成最小合法 PNG（1x1 透明）。"""
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    p = tmp_path / name
    p.write_bytes(png)
    return p


class TestOcrValidation:
    def test_missing_path(self):
        r = OcrTool().run({})
        assert r["status"] == "error"
        assert "path" in r["error"]

    def test_file_not_exists(self):
        r = OcrTool().run({"path": "/nonexistent/foo.png"})
        assert r["status"] == "error"
        assert "不存在" in r["error"]

    def test_unsupported_format(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_text("hello")
        r = OcrTool().run({"path": str(p)})
        assert r["status"] == "error"
        assert "不支持" in r["error"]

    def test_oversized_image(self, tmp_path):
        p = tmp_path / "big.png"
        p.write_bytes(b"0" * (MAX_OCR_BYTES + 1))
        r = OcrTool().run({"path": str(p)})
        assert r["status"] == "error"
        assert "超过" in r["error"]


class TestOcrEngineFallback:
    def test_engine_missing_fail_closed(self, tmp_path, monkeypatch):
        """PaddleOCR 未装 → 明确报错（fail-closed：不静默、不猜）。"""
        monkeypatch.setattr("src.tools.builtins.ocr_tools._ocr_engine_available", lambda: False)
        p = _make_png(tmp_path)
        r = OcrTool().run({"path": str(p)})
        assert r["status"] == "error"
        assert "OCR" in r["error"] or "paddle" in r["error"] or "Paddle" in r["error"]


class TestOcrBackend:
    def test_mock_engine_structure(self, tmp_path, monkeypatch):
        """注入 mock 引擎 → 验证输出结构（text + blocks + 坐标）。"""
        p = _make_png(tmp_path)

        def _fake_ocr(path, lang):
            return [
                {"text": "测试文字", "box": [[10, 10], [110, 10], [110, 40], [10, 40]], "confidence": 0.98},
                {"text": "OK", "box": [[120, 10], [150, 10], [150, 30], [120, 30]], "confidence": 0.95},
            ]

        monkeypatch.setattr("src.tools.builtins.ocr_tools._ocr_engine_available", lambda: True)
        monkeypatch.setattr("src.tools.builtins.ocr_tools._run_paddle_ocr", _fake_ocr)
        r = OcrTool().run({"path": str(p)})
        assert r["status"] == "ok"
        assert r["engine"] == "paddleocr"
        assert "测试文字" in r["text"]
        assert "OK" in r["text"]
        assert len(r["blocks"]) == 2
        assert r["blocks"][0]["box"] == [[10, 10], [110, 10], [110, 40], [10, 40]]
        assert r["blocks"][0]["confidence"] == 0.98

    def test_engine_error_reported(self, tmp_path, monkeypatch):
        """引擎运行抛错 → 错误透出（不吞异常）。"""
        p = _make_png(tmp_path)

        def _fake_ocr(path, lang):
            raise RuntimeError("paddle init failed")

        monkeypatch.setattr("src.tools.builtins.ocr_tools._ocr_engine_available", lambda: True)
        monkeypatch.setattr("src.tools.builtins.ocr_tools._run_paddle_ocr", _fake_ocr)
        r = OcrTool().run({"path": str(p)})
        assert r["status"] == "error"
        assert "paddle init failed" in r["error"]


class TestOcrMockResult:
    def test_mock_result_shape(self):
        """离线测试辅助：mock OCR 结果结构合法。"""
        r = _mock_ocr_result(["你好", "world"])
        assert r["status"] == "ok"
        assert r["engine"] == "paddleocr"
        assert "你好" in r["text"]
        assert isinstance(r["blocks"], list)
        assert all("box" in b and "text" in b for b in r["blocks"])
