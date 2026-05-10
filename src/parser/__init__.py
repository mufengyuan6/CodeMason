"""代码解析模块 - 基于Tree-sitter的多语言代码解析."""

from .base import BaseCodeParser, ParsedSymbol, SymbolType
from .python_parser import PythonParser
from .js_parser import JavaScriptParser
from .go_parser import GoParser

__all__ = [
    'BaseCodeParser',
    'ParsedSymbol',
    'SymbolType',
    'PythonParser',
    'JavaScriptParser',
    'GoParser',
]


def get_parser_for_file(file_path: str) -> BaseCodeParser:
    """根据文件路径获取对应的解析器."""
    if file_path.endswith('.py'):
        return PythonParser()
    elif file_path.endswith(('.js', '.ts', '.jsx', '.tsx')):
        return JavaScriptParser()
    elif file_path.endswith('.go'):
        return GoParser()
    else:
        raise ValueError(f"Unsupported file type: {file_path}")