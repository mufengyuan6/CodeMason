"""JavaScript/TypeScript代码解析器实现."""

import re
from typing import List, Optional
from tree_sitter import Parser

from .base import BaseCodeParser, ParsedSymbol, SymbolType, ParseResult


class JavaScriptParser(BaseCodeParser):
    """JavaScript/TypeScript代码解析器."""
    
    def __init__(self):
        super().__init__()
        self.language = "javascript"
        self.parser = None
        try:
            import tree_sitter_javascript
            from tree_sitter import Language, Parser
            lang = Language(tree_sitter_javascript.language())
            self.parser = Parser(lang)
        except (ImportError, TypeError):
            pass
    
    def parse(self, code: str, file_path: str) -> ParseResult:
        """解析JavaScript代码."""
        if self.parser:
            return self._tree_sitter_parse(code, file_path)
        else:
            return self._regex_parse(code, file_path)
    
    def _tree_sitter_parse(self, code: str, file_path: str) -> ParseResult:
        """使用Tree-sitter解析."""
        tree = self.parser.parse(bytes(code, 'utf8'))
        root_node = tree.root_node
        
        symbols = []
        imports = []
        
        self._traverse_node(root_node, code.encode("utf-8"), file_path, symbols, imports, None)
        
        return ParseResult(symbols=symbols, imports=imports)
    
    def _traverse_node(self, node, code: str, file_path: str,
                       symbols: List, imports: List, parent_name: Optional[str]):
        """遍历AST节点."""
        if node.type == 'function_declaration':
            symbol = self._extract_function(node, code, file_path, parent_name)
            if symbol:
                symbols.append(symbol)
        elif node.type == 'class_declaration':
            symbol = self._extract_class(node, code, file_path)
            if symbol:
                symbols.append(symbol)
        elif node.type in ('import_statement', 'import_declaration'):
            symbol = self._extract_import(node, code, file_path)
            if symbol:
                imports.append(symbol)
        elif node.type == 'method_definition':
            symbol = self._extract_method(node, code, file_path, parent_name)
            if symbol:
                symbols.append(symbol)
        
        for child in node.children:
            self._traverse_node(child, code, file_path, symbols, imports, parent_name)
    
    def _extract_function(self, node, code: str, file_path: str,
                          parent_name: Optional[str]) -> Optional[ParsedSymbol]:
        """提取函数信息."""
        name_node = None
        for child in node.children:
            if child.type == 'identifier':
                name_node = child
                break
        
        if not name_node:
            return None
        
        name = code[name_node.start_byte:name_node.end_byte].decode('utf-8')
        
        return ParsedSymbol(
            name=name,
            symbol_type=SymbolType.FUNCTION,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            file_path=file_path,
            code_snippet=code[node.start_byte:node.end_byte].decode('utf-8'),
            parent_name=parent_name,
            dependencies=[],
            metadata={'language': 'javascript'}
        )
    
    def _extract_class(self, node, code: str, file_path: str) -> Optional[ParsedSymbol]:
        """提取类信息."""
        name_node = None
        for child in node.children:
            if child.type == 'identifier':
                name_node = child
                break
        
        if not name_node:
            return None
        
        name = code[name_node.start_byte:name_node.end_byte].decode('utf-8')
        
        return ParsedSymbol(
            name=name,
            symbol_type=SymbolType.CLASS,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            file_path=file_path,
            code_snippet=code[node.start_byte:node.end_byte].decode('utf-8'),
            dependencies=[],
            metadata={'language': 'javascript'}
        )
    
    def _extract_method(self, node, code: str, file_path: str,
                        parent_name: Optional[str]) -> Optional[ParsedSymbol]:
        """提取方法信息."""
        name_node = None
        for child in node.children:
            if child.type == 'property_identifier':
                name_node = child
                break
        
        if not name_node:
            return None
        
        name = code[name_node.start_byte:name_node.end_byte].decode('utf-8')
        
        return ParsedSymbol(
            name=name,
            symbol_type=SymbolType.METHOD,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            file_path=file_path,
            code_snippet=code[node.start_byte:node.end_byte].decode('utf-8'),
            parent_name=parent_name,
            dependencies=[],
            metadata={'language': 'javascript'}
        )
    
    def _extract_import(self, node, code: str, file_path: str) -> Optional[ParsedSymbol]:
        """提取导入信息."""
        import_text = code[node.start_byte:node.end_byte].decode('utf-8')
        
        return ParsedSymbol(
            name='import',
            symbol_type=SymbolType.IMPORT,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            file_path=file_path,
            code_snippet=import_text,
            dependencies=[],
            metadata={'language': 'javascript'}
        )
    
    def _regex_parse(self, code: str, file_path: str) -> ParseResult:
        """使用正则表达式解析（降级方案）."""
        symbols = []
        imports = []
        
        # 解析导入
        import_patterns = [
            r"import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]",
            r"import\s+['\"]([^'\"]+)['\"]",
            r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
        ]
        
        for pattern in import_patterns:
            for match in re.finditer(pattern, code):
                start_line = code[:match.start()].count('\n') + 1
                imports.append(ParsedSymbol(
                    name=match.group(1),
                    symbol_type=SymbolType.IMPORT,
                    start_line=start_line,
                    end_line=start_line,
                    file_path=file_path,
                    code_snippet=match.group(0),
                    dependencies=[match.group(1)],
                    metadata={'language': 'javascript'}
                ))
        
        # 解析函数
        func_patterns = [
            r'function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(',
            r'(?:const|let|var)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>',
            r'([a-zA-Z_$][a-zA-Z0-9_$]*)\s*:\s*(?:async\s*)?\s*function\s*\(',
        ]
        
        for pattern in func_patterns:
            for match in re.finditer(pattern, code):
                name = match.group(1)
                start_line = code[:match.start()].count('\n') + 1
                symbols.append(ParsedSymbol(
                    name=name,
                    symbol_type=SymbolType.FUNCTION,
                    start_line=start_line,
                    end_line=start_line,
                    file_path=file_path,
                    code_snippet=match.group(0),
                    dependencies=[],
                    metadata={'language': 'javascript', 'parser': 'regex'}
                ))
        
        # 解析类
        class_pattern = r'class\s+([a-zA-Z_$][a-zA-Z0-9_$]*)'
        for match in re.finditer(class_pattern, code):
            name = match.group(1)
            start_line = code[:match.start()].count('\n') + 1
            symbols.append(ParsedSymbol(
                name=name,
                symbol_type=SymbolType.CLASS,
                start_line=start_line,
                end_line=start_line,
                file_path=file_path,
                code_snippet=match.group(0),
                dependencies=[],
                metadata={'language': 'javascript', 'parser': 'regex'}
            ))
        
        return ParseResult(symbols=symbols, imports=imports)
    
    def get_dependencies(self, symbol: ParsedSymbol) -> List[str]:
        """获取符号的依赖关系."""
        return symbol.dependencies