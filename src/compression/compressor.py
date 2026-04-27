"""上下文压缩引擎 - T1-T5渐进式压缩."""

import re
import ast
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum, auto


class CompressionLevel(Enum):
    """压缩级别."""
    T1_SCRIPT = auto()      # 纯脚本过滤
    T2_AST_PRUNE = auto()   # AST剪枝
    T3_SYMBOL_RENAME = auto()  # 符号替换
    T4_STRUCTURED_SUMMARY = auto()  # 结构化摘要
    T5_SEMANTIC_SUMMARY = auto()    # 语义摘要


@dataclass
class CompressionResult:
    """压缩结果."""
    original_size: int
    compressed_size: int
    level: CompressionLevel
    compressed_code: str
    compression_ratio: float
    preserved_symbols: List[str]


class ContextCompressor:
    """上下文压缩器."""
    
    def __init__(self):
        self.levels = [
            CompressionLevel.T1_SCRIPT,
            CompressionLevel.T2_AST_PRUNE,
            CompressionLevel.T3_SYMBOL_RENAME,
            CompressionLevel.T4_STRUCTURED_SUMMARY,
        ]
    
    def compress(self, code: str, target_size: Optional[int] = None,
                 max_level: CompressionLevel = CompressionLevel.T4_STRUCTURED_SUMMARY) -> CompressionResult:
        """
        压缩代码上下文.
        
        Args:
            code: 原始代码
            target_size: 目标大小（字符数）
            max_level: 最大压缩级别
            
        Returns:
            CompressionResult: 压缩结果
        """
        original_size = len(code)
        current_code = code
        current_level = CompressionLevel.T1_SCRIPT
        preserved_symbols = []
        
        # 逐级压缩直到达到目标大小
        for level in self.levels:
            if level.value > max_level.value:
                break
            
            current_level = level
            
            if level == CompressionLevel.T1_SCRIPT:
                current_code = self._t1_script_filter(current_code)
                preserved_symbols = self._extract_symbols(current_code)
            
            elif level == CompressionLevel.T2_AST_PRUNE:
                current_code = self._t2_ast_prune(current_code)
            
            elif level == CompressionLevel.T3_SYMBOL_RENAME:
                current_code = self._t3_symbol_rename(current_code)
            
            elif level == CompressionLevel.T4_STRUCTURED_SUMMARY:
                current_code = self._t4_structured_summary(current_code)
                preserved_symbols = self._extract_symbols(current_code)
            
            # 检查是否达到目标
            if target_size and len(current_code) <= target_size:
                break
        
        compressed_size = len(current_code)
        
        return CompressionResult(
            original_size=original_size,
            compressed_size=compressed_size,
            level=current_level,
            compressed_code=current_code,
            compression_ratio=compressed_size / original_size if original_size > 0 else 0,
            preserved_symbols=preserved_symbols
        )
    
    def _t1_script_filter(self, code: str) -> str:
        """T1: 纯脚本过滤 - 删除注释和空行."""
        lines = code.split('\n')
        filtered_lines = []
        
        for line in lines:
            # 删除行尾注释
            if '#' in line:
                line = line[:line.index('#')]
            
            # 保留非空行
            if line.strip():
                filtered_lines.append(line.rstrip())
        
        return '\n'.join(filtered_lines)
    
    def _t2_ast_prune(self, code: str) -> str:
        """T2: AST剪枝 - 删除未使用代码."""
        try:
            tree = ast.parse(code)
            
            # 提取所有使用的名称
            used_names = set()
            defined_names = {}
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    if isinstance(node.ctx, ast.Load):
                        used_names.add(node.id)
                    elif isinstance(node.ctx, ast.Store):
                        defined_names[node.id] = node
                
                # 保留函数定义和类定义
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    defined_names[node.name] = node
            
            # 简化：保留所有定义和使用的代码
            return code
        
        except:
            return code
    
    def _t3_symbol_rename(self, code: str) -> str:
        """T3: 符号替换 - 长变量名替换为短标识."""
        # 提取所有标识符
        identifiers = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', code)
        
        # 创建映射（简化实现）
        short_names = {}
        counter = 0
        
        for identifier in set(identifiers):
            if len(identifier) > 10 and identifier not in short_names:
                short_names[identifier] = f'v{counter}'
                counter += 1
        
        # 替换
        for old_name, new_name in short_names.items():
            code = re.sub(r'\b' + old_name + r'\b', new_name, code)
        
        return code
    
    def _t4_structured_summary(self, code: str) -> str:
        """T4: 结构化摘要 - 保留接口隐藏实现."""
        try:
            tree = ast.parse(code)
            summary = []
            
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Import):
                    summary.append(f"import ...")
                
                elif isinstance(node, ast.ImportFrom):
                    summary.append(f"from {node.module} import ...")
                
                elif isinstance(node, ast.FunctionDef):
                    # 提取函数签名
                    args = [arg.arg for arg in node.args.args]
                    decorators = [d.id if isinstance(d, ast.Name) else '...' for d in node.decorator_list]
                    
                    if decorators:
                        summary.append(f"@{', '.join(decorators)}")
                    
                    summary.append(f"def {node.name}({', '.join(args)}): ...")
                
                elif isinstance(node, ast.ClassDef):
                    # 提取类签名
                    bases = [base.id if isinstance(base, ast.Name) else '...' for base in node.bases]
                    base_str = f"({', '.join(bases)})" if bases else ""
                    
                    summary.append(f"class {node.name}{base_str}: ...")
            
            return '\n'.join(summary)
        
        except:
            return code
    
    def _extract_symbols(self, code: str) -> List[str]:
        """提取符号."""
        symbols = []
        
        try:
            tree = ast.parse(code)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    symbols.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    symbols.append(node.name)
        
        except:
            pass
        
        return symbols
    
    def estimate_tokens(self, code: str) -> int:
        """估算token数量."""
        # 简化估算：平均每个词约0.75个token
        words = len(code.split())
        return int(words * 0.75)
    
    def get_compression_stats(self, results: List[CompressionResult]) -> Dict:
        """获取压缩统计."""
        if not results:
            return {}
        
        total_original = sum(r.original_size for r in results)
        total_compressed = sum(r.compressed_size for r in results)
        
        return {
            'total_original_size': total_original,
            'total_compressed_size': total_compressed,
            'overall_ratio': total_compressed / total_original if total_original > 0 else 0,
            'avg_ratio': sum(r.compression_ratio for r in results) / len(results),
            'level_distribution': {
                level.name: sum(1 for r in results if r.level == level)
                for level in CompressionLevel
            }
        }