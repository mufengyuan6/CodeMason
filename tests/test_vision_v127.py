"""视觉子代理工具测试（v1.27：ReadImage 读图）。

覆盖：
- 参数校验（缺 path / 文件不存在 / 不支持格式）
- 图片编码（base64 data URL 白名单格式）
- 凭据缺失降级（fail-closed：明确报错而非静默）
- 路由：ModelRouter.route_role / generate_role（vision 角色不干扰主链路）
- provider_from_credentials（凭据通道构造）
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.providers import MockProvider, OpenAICompatProvider, ProviderConfig, provider_from_credentials
from src.routing.router import ModelRouter, ModelSpec
from src.tools.builtins.image_tools import (
    DEFAULT_VISION_MODEL,
    IMAGE_EXTS,
    MAX_IMAGE_BYTES,
    ReadImageTool,
    _encode_image,
)


def _make_png(tmp_path: Path, name: str = "test.png", size: int = 64) -> Path:
    """生成最小合法 PNG（1x1 像素，硬编码头+尾）。"""
    p = tmp_path / name
    # 1x1 透明 PNG
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    p.write_bytes(png)
    return p


# ---------- 参数校验 ----------


class TestReadImageValidation:
    def test_missing_path(self):
        r = ReadImageTool().run({})
        assert r["status"] == "error"
        assert "path" in r["error"]

    def test_file_not_exists(self):
        r = ReadImageTool().run({"path": "/nonexistent/foo.png"})
        assert r["status"] == "error"
        assert "不存在" in r["error"]

    def test_unsupported_format(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_text("hello")
        r = ReadImageTool().run({"path": str(p)})
        assert r["status"] == "error"
        assert "不支持" in r["error"]

    def test_oversized_image(self, tmp_path):
        p = tmp_path / "big.png"
        p.write_bytes(b"0" * (MAX_IMAGE_BYTES + 1))
        with pytest.raises(ValueError, match="超过"):
            _encode_image(p)


# ---------- 图片编码 ----------


class TestImageEncode:
    def test_png_data_url(self, tmp_path):
        p = _make_png(tmp_path)
        url = _encode_image(p)
        assert url.startswith("data:image/png;base64,")
        # 解码回读一致
        raw = base64.b64decode(url.split(",", 1)[1])
        assert raw == p.read_bytes()

    def test_jpeg_mime(self, tmp_path):
        p = tmp_path / "x.jpeg"
        p.write_bytes(b"jpegdata")
        url = _encode_image(p)
        assert url.startswith("data:image/jpeg;base64,")

    def test_webp_mime(self, tmp_path):
        p = tmp_path / "x.webp"
        p.write_bytes(b"webpdata")
        url = _encode_image(p)
        assert url.startswith("data:image/webp;base64,")


# ---------- 凭据降级（fail-closed） ----------


class TestReadImageCredential:
    def test_missing_credential_errors(self, tmp_path, monkeypatch):
        """无凭据时明确报错（不静默、不猜）。"""
        monkeypatch.setattr("src.tools.builtins.image_tools._load_vision_key", lambda: None)
        p = _make_png(tmp_path)
        r = ReadImageTool().run({"path": str(p)})
        assert r["status"] == "error"
        assert "凭据" in r["error"] or "api_keys.mimo" in r["error"]


# ---------- 路由：vision 角色 ----------


class TestVisionRoleRouting:
    def test_route_role_vision(self):
        router = ModelRouter(
            provider=MockProvider(reply=""),
            models=[
                ModelSpec(name="deepseek-v4-flash", role="architect", priority=1),
                ModelSpec(name="deepseek-v4-flash", role="editor", priority=1),
                ModelSpec(name=DEFAULT_VISION_MODEL, role="vision", priority=1),
            ],
        )
        d = router.route_role("vision")
        assert d.role == "vision"
        assert d.model == DEFAULT_VISION_MODEL

    def test_vision_does_not_leak_to_main_chain(self):
        """vision 角色不干扰 Plan/Act 主链路（模型 schema 恒定，只换实现）。"""
        router = ModelRouter(
            provider=MockProvider(reply=""),
            models=[
                ModelSpec(name="arch-model", role="architect", priority=1),
                ModelSpec(name="edit-model", role="editor", priority=1),
                ModelSpec(name="mimo-v2.5", role="vision", priority=1),
            ],
        )
        assert router.route("plan").model == "arch-model"
        assert router.route("act").model == "edit-model"
        # vision 模型只出现在显式 role 路由
        assert router._pick_model("vision") == "mimo-v2.5"

    def test_generate_role_uses_vision_provider(self):
        provider = MockProvider(reply="看图结果")
        router = ModelRouter(
            provider=provider,
            models=[ModelSpec(name="mimo-v2.5", role="vision", priority=1)],
        )
        out = router.generate_role([{"role": "user", "content": "看这张图"}], role="vision")
        assert out == "看图结果"
        # 调用确实打到 vision 模型（MockProvider.calls 记录 model）
        assert provider.calls[-1]["model"] == "mimo-v2.5"


# ---------- provider_from_credentials ----------


class TestProviderFromCredentials:
    def test_missing_credential_raises(self, monkeypatch):
        class _EmptyStore:
            def get(self, key):
                return None

        monkeypatch.setattr("src.security.credentials.CredentialStore", _EmptyStore)
        with pytest.raises(Exception, match="api_keys.mimo"):
            provider_from_credentials(
                "api_keys.mimo",
                name="mimo",
                base_url="https://opencode.ai/zen/go/v1",
                default_model="mimo-v2.5",
            )

    def test_builds_openai_compat(self, monkeypatch):
        class _Store:
            def get(self, key):
                return "sk-test-key-1234567890abcdef"

        monkeypatch.setattr("src.security.credentials.CredentialStore", _Store)
        p = provider_from_credentials(
            "api_keys.mimo",
            name="mimo",
            base_url="https://opencode.ai/zen/go/v1",
            default_model="mimo-v2.5",
        )
        assert isinstance(p, OpenAICompatProvider)
        assert p.config.default_model == "mimo-v2.5"
        assert p.config.max_tokens >= 4096


# ---------- OpenAICompatProvider 推理模型兜底 ----------


class TestReasoningContentFallback:
    def test_content_null_falls_back_to_reasoning(self, monkeypatch):
        """MiMo-V2.5 等推理模型 content=null 时回退 reasoning_content（v1.27 修复）。"""
        provider = OpenAICompatProvider(
            ProviderConfig(name="mimo", base_url="http://mock", api_key="k", default_model="mimo-v2.5")
        )

        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "choices": [{"message": {"content": None, "reasoning_content": "思考中...最终结论"}}]
                }

        class _Client:
            def post(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr(provider, "_client", _Client())
        out = provider.chat([{"role": "user", "content": "hi"}])
        assert out == "思考中...最终结论"

    def test_normal_content_unchanged(self, monkeypatch):
        provider = OpenAICompatProvider(
            ProviderConfig(name="mimo", base_url="http://mock", api_key="k", default_model="mimo-v2.5")
        )

        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": "正常回答"}}]}

        class _Client:
            def post(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr(provider, "_client", _Client())
        assert provider.chat([{"role": "user", "content": "hi"}]) == "正常回答"
