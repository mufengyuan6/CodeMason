"""基础解析器接口定义."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional


class SymbolType(Enum):
    """符号类型枚举."""
    FUNCTION = auto()
    CLASS = auto()
    METHOD = auto()
    VARIABLE = auto()
    IMPORT = auto()
    INTERFACE = auto()
    MODULE = auto()


@dataclass(frozen=True)
class ParsedSymbol:
    """解析后的符号信息."""
    name: str
    symbol_type: SymbolType
    start_line: int
    end_line: int
    file_path: str
    code_snippet: str
    docstring: Optional[str] = None
    parent_name: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParseResult:
    """代码解析结果."""
    symbols: List[ParsedSymbol]
    imports: List[ParsedSymbol]
    ast_dump: Optional[str] = None


class BaseCodeParser(ABC):
    """代码解析器基类."""
    
    def __init__(self):
        self.language = None
    
    @abstractmethod
    def parse(self, code: str, file_path: str) -> ParseResult:
        """
        解析代码并提取符号信息.
        
        Args:
            code: 源代码字符串
            file_path: 文件路径（用于上下文）
            
        Returns:
            ParseResult: 解析结果
        """
        pass
    
    @abstractmethod
    def get_dependencies(self, symbol: ParsedSymbol) -> List[str]:
        """
        获取符号的依赖关系.
        
        Args:
            symbol: 目标符号
            
        Returns:
            List[str]: 依赖的符号名称列表
        """
        pass
    
    def extract_call_graph(self, symbols: List[ParsedSymbol]) -> Dict[str, List[str]]:
        """
        提取调用关系图.
        
        Args:
            symbols: 符号列表
            
        Returns:
            Dict[str, List[str]]: 调用关系图
        """
        call_graph = {}
        for symbol in symbols:
            if symbol.symbol_type in [SymbolType.FUNCTION, SymbolType.METHOD]:
                call_graph[symbol.name] = symbol.dependencies
        return call_graph