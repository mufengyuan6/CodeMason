"""压缩层：T1-T5 渐进压缩 + auto-compact。"""

from .t1_t5 import CompressionLevel, CompressionResult, ContextCompressor

__all__ = ["CompressionLevel", "CompressionResult", "ContextCompressor"]
