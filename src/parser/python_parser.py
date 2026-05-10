"""Python代码解析器实现."""

import re
from typing import List, Dict, Any, Optional
from tree_sitter import Language, Parser

from .base import BaseCodeParser, ParsedSymbol, SymbolType, ParseResult


class PythonParser(BaseCodeParser):
    """Python代码解析器."""
    
    def __init__(self):
        super().__init__()
        self.language = "python"
        self.parser = None
        try:
            import tree_sitter_python
            from tree_sitter import Language, Parser
            lang = Language(tree_sitter_python.language())
            self.parser = Parser(lang)
        except (ImportError, TypeError):
            # Fallback: 使用正则解析
            pass
    
    def parse(self, code: str, file_path: str) -> ParseResult:
        """解析Python代码."""
        if self.parser:
            return self._tree_sitter_parse(code, file_path)
        else:
            return self._regex_parse(code, file_path)
    
    def _tree_sitter_parse(self, code: str, file_path: str) -> ParseResult:
        """使用Tree-sitter解析Python代码."""
        tree = self.parser.parse(bytes(code, 'utf8'))
        root_node = tree.root_node
        
        symbols = []
        imports = []
        
        # 注意: tree-sitter 的 start_byte/end_byte 是 UTF-8 字节偏移,
        # 必须用 bytes 切片后再 decode, 否则含多字节字符(中文等)时代码会错位.
        code_bytes = code.encode('utf-8')
        self._traverse_node(root_node, code_bytes, file_path, symbols, imports, None)
        
        return ParseResult(symbols=symbols, imports=imports)
    
    def _traverse_node(self, node, code: str, file_path: str, 
                       symbols: List, imports: List, parent_name: Optional[str]):
        """遍历AST节点."""
        if node.type == 'function_definition':
            symbol = self._extract_function(node, code, file_path, parent_name)
            if symbol:
                symbols.append(symbol)
        elif node.type == 'class_definition':
            symbol = self._extract_class(node, code, file_path)
            if symbol:
                symbols.append(symbol)
                # 遍历类内部: tree-sitter 中方法在 block 子节点下,
                # 必须递归进 block 并携带 class_name, 方法才会被识别为 METHOD.
                class_name = symbol.name
                for child in node.children:
                    if child.type == 'block':
                        self._traverse_node(child, code, file_path, symbols, imports, class_name)
                return  # block 已递归处理, 避免重复遍历
        elif node.type in ('import_statement', 'import_from_statement'):
            symbol = self._extract_import(node, code, file_path)
            if symbol:
                imports.append(symbol)
        
        # 递归遍历子节点
        for child in node.children:
            self._traverse_node(child, code, file_path, symbols, imports, parent_name)
    
    def _extract_function(self, node, code: str, file_path: str, 
                          parent_name: Optional[str]) -> Optional[ParsedSymbol]:
        """提取函数信息."""
        # 获取函数名
        name_node = None
        for child in node.children:
            if child.type == 'identifier':
                name_node = child
                break
        
        if not name_node:
            return None
        
        name = code[name_node.start_byte:name_node.end_byte].decode('utf-8')
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        code_snippet = code[node.start_byte:node.end_byte].decode('utf-8')
        
        # 提取docstring
        docstring = self._extract_docstring(node, code)
        
        # 提取依赖
        dependencies = self._extract_dependencies(node, code)
        
        symbol_type = SymbolType.METHOD if parent_name else SymbolType.FUNCTION
        
        return ParsedSymbol(
            name=name,
            symbol_type=symbol_type,
            start_line=start_line,
            end_line=end_line,
            file_path=file_path,
            code_snippet=code_snippet,
            docstring=docstring,
            parent_name=parent_name,
            dependencies=dependencies,
            metadata={'language': 'python'}
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
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        code_snippet = code[node.start_byte:node.end_byte].decode('utf-8')
        docstring = self._extract_docstring(node, code)
        
        # 提取继承关系
        dependencies = []
        for child in node.children:
            if child.type == 'argument_list':
                for arg in child.children:
                    if arg.type == 'identifier':
                        dep_name = code[arg.start_byte:arg.end_byte].decode('utf-8')
                        if dep_name not in ['object']:
                            dependencies.append(dep_name)
        
        return ParsedSymbol(
            name=name,
            symbol_type=SymbolType.CLASS,
            start_line=start_line,
            end_line=end_line,
            file_path=file_path,
            code_snippet=code_snippet,
            docstring=docstring,
            dependencies=dependencies,
            metadata={'language': 'python'}
        )
    
    def _extract_import(self, node, code: str, file_path: str) -> Optional[ParsedSymbol]:
        """提取导入信息."""
        import_text = code[node.start_byte:node.end_byte].decode('utf-8')
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        
        # 解析导入的模块名
        imported_names = []
        for child in node.children:
            if child.type in ('dotted_name', 'identifier'):
                name = code[child.start_byte:child.end_byte].decode('utf-8')
                imported_names.append(name)
        
        return ParsedSymbol(
            name=imported_names[0] if imported_names else 'unknown',
            symbol_type=SymbolType.IMPORT,
            start_line=start_line,
            end_line=end_line,
            file_path=file_path,
            code_snippet=import_text,
            dependencies=imported_names,
            metadata={'language': 'python'}
        )
    
    def _extract_docstring(self, node, code: str) -> Optional[str]:
        """提取docstring."""
        # 查找函数/类体内的第一个字符串表达式
        body_node = None
        for child in node.children:
            if child.type == 'block':
                body_node = child
                break
        
        if body_node and body_node.children:
            first_stmt = body_node.children[0]
            if first_stmt.type == 'expression_statement':
                expr = first_stmt.children[0] if first_stmt.children else None
                if expr and expr.type == 'string':
                    return code[expr.start_byte:expr.end_byte].decode('utf-8')
        return None
    
    def _extract_dependencies(self, node, code: str) -> List[str]:
        """提取函数依赖的其他符号."""
        dependencies = set()
        
        # 遍历函数体查找调用
        for child in node.children:
            if child.type == 'block':
                self._find_calls(child, code, dependencies)
        
        return list(dependencies)
    
    def _find_calls(self, node, code: str, dependencies: set):
        """递归查找函数调用."""
        if node.type == 'call':
            func_node = node.children[0] if node.children else None
            if func_node and func_node.type == 'identifier':
                func_name = code[func_node.start_byte:func_node.end_byte].decode('utf-8')
                dependencies.add(func_name)
        
        for child in node.children:
            self._find_calls(child, code, dependencies)
    
    def _regex_parse(self, code: str, file_path: str) -> ParseResult:
        """使用正则表达式解析Python代码（Tree-sitter不可用时的降级方案）."""
        symbols = []
        imports = []
        
        # 解析导入
        import_pattern = r'^(?:from\s+(\S+)\s+import|import\s+(\S+))'
        for match in re.finditer(import_pattern, code, re.MULTILINE):
            import_text = match.group(0)
            start_line = code[:match.start()].count('\n') + 1
            end_line = code[:match.end()].count('\n') + 1
            
            module = match.group(1) or match.group(2)
            imports.append(ParsedSymbol(
                name=module,
                symbol_type=SymbolType.IMPORT,
                start_line=start_line,
                end_line=end_line,
                file_path=file_path,
                code_snippet=import_text,
                dependencies=[module],
                metadata={'language': 'python'}
            ))
        
        # 解析函数定义
        func_pattern = r'^\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
        for match in re.finditer(func_pattern, code, re.MULTILINE):
            name = match.group(1)
            start_line = code[:match.start()].count('\n') + 1
            # 找到函数结束（下一个同层级定义或文件结束）
            end_line = self._find_block_end(code, match.start())
            code_snippet = self._extract_block(code, match.start(), end_line)
            
            symbols.append(ParsedSymbol(
                name=name,
                symbol_type=SymbolType.FUNCTION,
                start_line=start_line,
                end_line=end_line,
                file_path=file_path,
                code_snippet=code_snippet,
                dependencies=[],
                metadata={'language': 'python', 'parser': 'regex'}
            ))
        
        # 解析类定义
        class_pattern = r'^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)'
        for match in re.finditer(class_pattern, code, re.MULTILINE):
            name = match.group(1)
            start_line = code[:match.start()].count('\n') + 1
            end_line = self._find_block_end(code, match.start())
            code_snippet = self._extract_block(code, match.start(), end_line)
            
            symbols.append(ParsedSymbol(
                name=name,
                symbol_type=SymbolType.CLASS,
                start_line=start_line,
                end_line=end_line,
                file_path=file_path,
                code_snippet=code_snippet,
                dependencies=[],
                metadata={'language': 'python', 'parser': 'regex'}
            ))
        
        return ParseResult(symbols=symbols, imports=imports)
    
    def _find_block_end(self, code: str, start_pos: int) -> int:
        """找到代码块的结束行."""
        lines = code[start_pos:].split('\n')
        if len(lines) <= 1:
            return code[:start_pos].count('\n') + 1
        
        base_indent = len(lines[0]) - len(lines[0].lstrip())
        end_line = code[:start_pos].count('\n') + 1
        
        for i, line in enumerate(lines[1:], 1):
            if line.strip() and not line.strip().startswith('#'):
                indent = len(line) - len(line.lstrip())
                if indent <= base_indent:
                    break
            end_line = code[:start_pos].count('\n') + i + 1
        
        return end_line
    
    def _extract_block(self, code: str, start_pos: int, end_line: int) -> str:
        """提取代码块."""
        start_line = code[:start_pos].count('\n')
        lines = code.split('\n')
        return '\n'.join(lines[start_line:end_line])
    
    def get_dependencies(self, symbol: ParsedSymbol) -> List[str]:
        """获取符号的依赖关系."""
        return symbol.dependencies