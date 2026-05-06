"""Pre/Post Hooks - 执行前后拦截."""

import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Callable


class HookType(Enum):
    """Hook类型."""
    PRE_EXECUTION = auto()   # 执行前
    POST_EXECUTION = auto()  # 执行后


class HookPriority(Enum):
    """Hook优先级."""
    HIGH = 1      # 最高优先级
    NORMAL = 2    # 普通
    LOW = 3       # 最低


@dataclass
class HookResult:
    """Hook执行结果."""
    hook_name: str
    allowed: bool
    message: str
    severity: str  # 'block', 'warn', 'pass'
    action_taken: Optional[str] = None


class BaseHook(ABC):
    """Hook基类."""
    
    def __init__(self, name: str, hook_type: HookType, priority: HookPriority = HookPriority.NORMAL):
        self.name = name
        self.hook_type = hook_type
        self.priority = priority
    
    @abstractmethod
    def execute(self, code: str, context: Dict) -> HookResult:
        """
        执行Hook逻辑.
        
        Args:
            code: 代码字符串
            context: 上下文信息
            
        Returns:
            HookResult: 执行结果
        """
        pass


class DangerousOperationHook(BaseHook):
    """高危操作检测Hook."""
    
    DANGEROUS_PATTERNS = [
        (r'rm\s+-rf\s+/\s*$', '删除根目录'),
        (r'DROP\s+DATABASE', '删除数据库'),
        (r'DROP\s+TABLE\s+\w+', '删除数据表'),
        (r'os\.system\s*\(\s*["\']rm', '系统命令删除'),
        (r'shutil\.rmtree\s*\(\s*["\']/', '递归删除根目录'),
        (r'open\s*\(\s*["\']/(etc|usr|bin)', '操作系统文件'),
        (r'subprocess\.call\s*\(\s*\[\s*["\']rm', '子进程删除'),
    ]
    
    def __init__(self):
        super().__init__(
            "DangerousOperationDetector",
            HookType.PRE_EXECUTION,
            HookPriority.HIGH
        )
    
    def execute(self, code: str, context: Dict) -> HookResult:
        """检测高危操作."""
        for pattern, desc in self.DANGEROUS_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                return HookResult(
                    hook_name=self.name,
                    allowed=False,
                    message=f"检测到高危操作: {desc}",
                    severity='block',
                    action_taken="阻断执行"
                )
        
        return HookResult(
            hook_name=self.name,
            allowed=True,
            message="未检测到高危操作",
            severity='pass'
        )


class PermissionCheckHook(BaseHook):
    """权限校验Hook."""
    
    SENSITIVE_PATHS = [
        r'/etc/',
        r'/usr/',
        r'/bin/',
        r'/sbin/',
        r'/var/',
        r'\.env',
        r'\.git/',
        r'config\.py',
        r'settings\.py',
    ]
    
    def __init__(self):
        super().__init__(
            "PermissionChecker",
            HookType.PRE_EXECUTION,
            HookPriority.HIGH
        )
    
    def execute(self, code: str, context: Dict) -> HookResult:
        """检查敏感文件访问."""
        for pattern in self.SENSITIVE_PATHS:
            if re.search(pattern, code, re.IGNORECASE):
                return HookResult(
                    hook_name=self.name,
                    allowed=False,
                    message=f"尝试访问敏感路径: {pattern}",
                    severity='block',
                    action_taken="要求二次确认（暂不支持自动确认）"
                )
        
        return HookResult(
            hook_name=self.name,
            allowed=True,
            message="无敏感文件访问",
            severity='pass'
        )


class DependencyCheckHook(BaseHook):
    """依赖检查Hook."""
    
    def __init__(self):
        super().__init__(
            "DependencyChecker",
            HookType.PRE_EXECUTION,
            HookPriority.NORMAL
        )
    
    def execute(self, code: str, context: Dict) -> HookResult:
        """检查新依赖引入."""
        # 提取import语句
        imports = re.findall(r'(?:from|import)\s+(\w+)', code)
        existing_deps = context.get('existing_dependencies', [])
        
        new_deps = [dep for dep in imports if dep not in existing_deps]
        
        if new_deps:
            return HookResult(
                hook_name=self.name,
                allowed=True,
                message=f"引入新依赖: {', '.join(new_deps)}",
                severity='warn',
                action_taken="建议检查是否已有替代方案"
            )
        
        return HookResult(
            hook_name=self.name,
            allowed=True,
            message="无新依赖引入",
            severity='pass'
        )


class SyntaxCheckHook(BaseHook):
    """语法检查Hook."""
    
    def __init__(self):
        super().__init__(
            "SyntaxChecker",
            HookType.POST_EXECUTION,
            HookPriority.HIGH
        )
    
    def execute(self, code: str, context: Dict) -> HookResult:
        """检查代码语法."""
        language = context.get('language', 'python')
        
        if language == 'python':
            return self._check_python_syntax(code)
        elif language == 'javascript':
            return self._check_js_syntax(code)
        elif language == 'go':
            return self._check_go_syntax(code)
        
        return HookResult(
            hook_name=self.name,
            allowed=True,
            message=f"暂不支持{language}语法检查",
            severity='warn'
        )
    
    def _check_python_syntax(self, code: str) -> HookResult:
        """检查Python语法."""
        try:
            compile(code, '<string>', 'exec')
            return HookResult(
                hook_name=self.name,
                allowed=True,
                message="Python语法检查通过",
                severity='pass'
            )
        except SyntaxError as e:
            return HookResult(
                hook_name=self.name,
                allowed=False,
                message=f"Python语法错误: {e.msg} (行{e.lineno})",
                severity='block',
                action_taken="返回语法错误，触发重试"
            )
    
    def _check_js_syntax(self, code: str) -> HookResult:
        """检查JS语法（简化版）."""
        # 简单检查括号匹配
        if code.count('(') != code.count(')'):
            return HookResult(
                hook_name=self.name,
                allowed=False,
                message="括号不匹配",
                severity='block',
                action_taken="检查括号"
            )
        if code.count('{') != code.count('}'):
            return HookResult(
                hook_name=self.name,
                allowed=False,
                message="花括号不匹配",
                severity='block',
                action_taken="检查花括号"
            )
        
        return HookResult(
            hook_name=self.name,
            allowed=True,
            message="JS语法检查通过",
            severity='pass'
        )
    
    def _check_go_syntax(self, code: str) -> HookResult:
        """检查Go语法（简化版）."""
        # 简单检查package声明
        if not re.search(r'^package\s+\w+', code, re.MULTILINE):
            return HookResult(
                hook_name=self.name,
                allowed=False,
                message="Go文件缺少package声明",
                severity='block',
                action_taken="添加package声明"
            )
        
        return HookResult(
            hook_name=self.name,
            allowed=True,
            message="Go语法检查通过",
            severity='pass'
        )


class BehaviorConsistencyHook(BaseHook):
    """行为一致性验证Hook."""
    
    def __init__(self):
        super().__init__(
            "BehaviorConsistencyChecker",
            HookType.POST_EXECUTION,
            HookPriority.NORMAL
        )
    
    def execute(self, code: str, context: Dict) -> HookResult:
        """验证重构前后行为是否一致."""
        original_code = context.get('original_code')
        
        if not original_code:
            return HookResult(
                hook_name=self.name,
                allowed=True,
                message="无原始代码，跳过行为一致性检查",
                severity='pass'
            )
        
        # 简化检查：对比关键函数签名
        original_funcs = set(re.findall(r'def\s+(\w+)\s*\(', original_code))
        new_funcs = set(re.findall(r'def\s+(\w+)\s*\(', code))
        
        if original_funcs != new_funcs:
            missing = original_funcs - new_funcs
            added = new_funcs - original_funcs
            
            msg_parts = []
            if missing:
                msg_parts.append(f"删除的函数: {missing}")
            if added:
                msg_parts.append(f"新增的函数: {added}")
            
            return HookResult(
                hook_name=self.name,
                allowed=True,
                message=f"函数签名变化: {'; '.join(msg_parts)}",
                severity='warn',
                action_taken="标记为待人工审核"
            )
        
        return HookResult(
            hook_name=self.name,
            allowed=True,
            message="函数签名保持一致",
            severity='pass'
        )


class HooksManager:
    """Hooks管理器."""
    
    def __init__(self):
        self.pre_hooks: List[BaseHook] = []
        self.post_hooks: List[BaseHook] = []
        
        # 注册核心Hooks
        self._register_core_hooks()
    
    def _register_core_hooks(self):
        """注册核心Hooks."""
        # Pre-Hooks（执行前）
        self.register_hook(DangerousOperationHook())
        self.register_hook(PermissionCheckHook())
        self.register_hook(DependencyCheckHook())
        
        # Post-Hooks（执行后）
        self.register_hook(SyntaxCheckHook())
        self.register_hook(BehaviorConsistencyHook())
    
    def register_hook(self, hook: BaseHook):
        """注册Hook."""
        if hook.hook_type == HookType.PRE_EXECUTION:
            self.pre_hooks.append(hook)
            # 按优先级排序
            self.pre_hooks.sort(key=lambda h: h.priority.value)
        else:
            self.post_hooks.append(hook)
            self.post_hooks.sort(key=lambda h: h.priority.value)
    
    def run_pre_hooks(self, code: str, context: Dict) -> Dict:
        """
        运行Pre-Hooks.
        
        Returns:
            Dict: {
                'allowed': bool,
                'results': List[HookResult],
                'blocking_hooks': List[str]
            }
        """
        results = []
        blocking = []
        
        for hook in self.pre_hooks:
            result = hook.execute(code, context)
            results.append(result)
            
            if not result.allowed:
                blocking.append(hook.name)
        
        return {
            'allowed': len(blocking) == 0,
            'results': results,
            'blocking_hooks': blocking
        }
    
    def run_post_hooks(self, code: str, context: Dict) -> Dict:
        """
        运行Post-Hooks.
        
        Returns:
            Dict: {
                'passed': bool,
                'results': List[HookResult],
                'warnings': List[str]
            }
        """
        results = []
        warnings = []
        
        for hook in self.post_hooks:
            result = hook.execute(code, context)
            results.append(result)
            
            if result.severity == 'warn':
                warnings.append(f"{hook.name}: {result.message}")
        
        return {
            'passed': all(r.allowed for r in results),
            'results': results,
            'warnings': warnings
        }
    
    def get_hook_summary(self) -> Dict:
        """获取Hook摘要."""
        return {
            'pre_hooks_count': len(self.pre_hooks),
            'post_hooks_count': len(self.post_hooks),
            'pre_hooks': [h.name for h in self.pre_hooks],
            'post_hooks': [h.name for h in self.post_hooks]
        }