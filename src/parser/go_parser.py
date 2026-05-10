"""Go代码解析器实现."""

import re
from typing import List, Optional
from tree_sitter import Parser

from .base import BaseCodeParser, ParsedSymbol, SymbolType, ParseResult


class GoParser(BaseCodeParser):
    """Go代码解析器."""
    
    def __init__(self):
        super().__init__()
        self.language = "go"
        self.parser = None
        try:
            import tree_sitter_go
            from tree_sitter import Language, Parser
            lang = Language(tree_sitter_go.language())
            self.parser = Parser(lang)
        except (ImportError, TypeError):
            pass
    
    def parse(self, code: str, file_path: str) -> ParseResult:
        """解析Go代码."""
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
            symbol = self._extract_function(node, code, file_path)
            if symbol:
                symbols.append(symbol)
        elif node.type == 'method_declaration':
            symbol = self._extract_method(node, code, file_path)
            if symbol:
                symbols.append(symbol)
        elif node.type == 'type_declaration':
            symbol = self._extract_type(node, code, file_path)
            if symbol:
                symbols.append(symbol)
        elif node.type == 'import_declaration':
            symbol = self._extract_import(node, code, file_path)
            if symbol:
                imports.append(symbol)
        
        for child in node.children:
            self._traverse_node(child, code, file_path, symbols, imports, parent_name)
    
    def _extract_function(self, node, code: str, file_path: str) -> Optional[ParsedSymbol]:
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
            dependencies=[],
            metadata={'language': 'go'}
        )
    
    def _extract_method(self, node, code: str, file_path: str) -> Optional[ParsedSymbol]:
        """提取方法信息."""
        # Go方法: func (r *Receiver) MethodName()
        name_node = None
        for child in node.children:
            if child.type == 'identifier':
                name_node = child
                break
        
        if not name_node:
            return None
        
        name = code[name_node.start_byte:name_node.end_byte].decode('utf-8')
        
        # 提取接收者类型作为parent_name
        receiver_type = None
        for child in node.children:
            if child.type == 'parameter_list':
                for param in child.children:
                    if param.type == 'parameter_declaration':
                        for p_child in param.children:
                            if p_child.type in ('pointer_type', 'type_identifier'):
                                receiver_type = code[p_child.start_byte:p_child.end_byte].decode('utf-8')
                                break
        
        return ParsedSymbol(
            name=name,
            symbol_type=SymbolType.METHOD,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            file_path=file_path,
            code_snippet=code[node.start_byte:node.end_byte].decode('utf-8'),
            parent_name=receiver_type,
            dependencies=[],
            metadata={'language': 'go'}
        )
    
    def _extract_type(self, node, code: str, file_path: str) -> Optional[ParsedSymbol]:
        """提取类型声明."""
        # 查找type_spec中的标识符
        for child in node.children:
            if child.type == 'type_spec':
                for spec_child in child.children:
                    if spec_child.type == 'type_identifier':
                        name = code[spec_child.start_byte:spec_child.end_byte].decode('utf-8')
                        return ParsedSymbol(
                            name=name,
                            symbol_type=SymbolType.CLASS,  # Go中用CLASS表示类型
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            file_path=file_path,
                            code_snippet=code[node.start_byte:node.end_byte].decode('utf-8'),
                            dependencies=[],
                            metadata={'language': 'go'}
                        )
        return None
    
    def _extract_import(self, node, code: str, file_path: str) -> Optional[ParsedSymbol]:
        """提取导入信息."""
        import_text = code[node.start_byte:node.end_byte].decode('utf-8')
        
        # 提取导入路径
        imported_paths = []
        for child in node.children:
            if child.type == 'import_spec':
                for spec_child in child.children:
                    if spec_child.type == 'interpreted_string_literal':
                        path = code[spec_child.start_byte:spec_child.end_byte].decode('utf-8')
                        imported_paths.append(path.strip('"'))
        
        return ParsedSymbol(
            name=imported_paths[0] if imported_paths else 'import',
            symbol_type=SymbolType.IMPORT,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            file_path=file_path,
            code_snippet=import_text,
            dependencies=imported_paths,
            metadata={'language': 'go'}
        )
    
    def _regex_parse(self, code: str, file_path: str) -> ParseResult:
        """使用正则表达式解析（降级方案）."""
        symbols = []
        imports = []
        
        # 解析导入
        import_pattern = r'import\s+(?:\(\s*([^)]+)\s*\)|["\']([^"\']+)["\'])'
        for match in re.finditer(import_pattern, code, re.DOTALL):
            import_text = match.group(0)
            start_line = code[:match.start()].count('\n') + 1
            
            paths = match.group(1) or match.group(2)
            if paths:
                for path in re.findall(r'["\']([^"\']+)["\']', paths):
                    imports.append(ParsedSymbol(
                        name=path,
                        symbol_type=SymbolType.IMPORT,
                        start_line=start_line,
                        end_line=start_line,
                        file_path=file_path,
                        code_snippet=import_text,
                        dependencies=[path],
                        metadata={'language': 'go'}
                    ))
        
        # 解析函数/方法（支持 receiver: func (u *User) GetName()）
        func_pattern = r'^func\s+(?:\(([^)]*)\)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
        for match in re.finditer(func_pattern, code, re.MULTILINE):
            name = match.group(2)
            start_line = code[:match.start()].count('\n') + 1
            receiver = match.group(1)
            if receiver:
                # 提取 receiver 类型作为 parent_name（去掉指针/空白）
                recv_match = re.search(r'\*?\s*([a-zA-Z_][a-zA-Z0-9_]*)', receiver)
                parent_name = recv_match.group(1) if recv_match else None
                symbols.append(ParsedSymbol(
                    name=name,
                    symbol_type=SymbolType.METHOD,
                    start_line=start_line,
                    end_line=start_line,
                    file_path=file_path,
                    code_snippet=match.group(0),
                    parent_name=parent_name,
                    dependencies=[],
                    metadata={'language': 'go', 'parser': 'regex'}
                ))
            else:
                symbols.append(ParsedSymbol(
                    name=name,
                    symbol_type=SymbolType.FUNCTION,
                    start_line=start_line,
                    end_line=start_line,
                    file_path=file_path,
                    code_snippet=match.group(0),
                    dependencies=[],
                    metadata={'language': 'go', 'parser': 'regex'}
                ))
        
        # 解析类型
        type_pattern = r'^type\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:struct|interface)'
        for match in re.finditer(type_pattern, code, re.MULTILINE):
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
                metadata={'language': 'go', 'parser': 'regex'}
            ))
        
        return ParseResult(symbols=symbols, imports=imports)
    
    def get_dependencies(self, symbol: ParsedSymbol) -> List[str]:
        """获取符号的依赖关系."""
        return symbol.dependencies