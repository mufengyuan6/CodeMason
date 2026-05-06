"""Rules引擎 - YAGNI规则与安全策略."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Callable


class RuleType(Enum):
    """规则类型."""
    YAGNI = auto()       # YAGNI约束
    SECURITY = auto()    # 安全策略
    STYLE = auto()       # 代码风格


class RuleSeverity(Enum):
    """规则严重级别."""
    BLOCK = auto()       # 阻断执行
    WARN = auto()        # 警告
    INFO = auto()        # 信息


@dataclass
class RuleResult:
    """规则检查结果."""
    rule_name: str
    passed: bool
    severity: RuleSeverity
    message: str
    line_number: Optional[int] = None
    suggestion: Optional[str] = None


class BaseRule(ABC):
    """规则基类."""
    
    def __init__(self, name: str, rule_type: RuleType, severity: RuleSeverity):
        self.name = name
        self.rule_type = rule_type
        self.severity = severity
    
    @abstractmethod
    def check(self, code: str, context: Optional[Dict] = None) -> List[RuleResult]:
        """
        检查代码是否违反规则.
        
        Args:
            code: 代码字符串
            context: 上下文信息
            
        Returns:
            List[RuleResult]: 检查结果列表
        """
        pass


class YAGNI_Rule(BaseRule):
    """YAGNI规则 - 检测冗余代码."""
    
    def __init__(self):
        super().__init__(
            name="YAGNI_Detector",
            rule_type=RuleType.YAGNI,
            severity=RuleSeverity.WARN
        )
        # YAGNI检测模式
        self.patterns = {
            'unused_import': r'^import\s+\w+(?:\s+as\s+\w+)?\s*$',
            'unused_variable': r'^\s*\w+\s*=\s*[^=].*$',
            'dead_code': r'^\s*#.*TODO.*$|^\s*#.*FIXME.*$|^\s*pass\s*$',
        }
    
    def check(self, code: str, context: Optional[Dict] = None) -> List[RuleResult]:
        """检查YAGNI违规."""
        results = []
        lines = code.split('\n')
        
        for i, line in enumerate(lines, 1):
            # 检测死代码
            if re.match(r'^\s*pass\s*$', line) or re.match(r'^\s*#\s*TODO', line):
                results.append(RuleResult(
                    rule_name=self.name,
                    passed=False,
                    severity=RuleSeverity.WARN,
                    message=f"可能的死代码: {line.strip()}",
                    line_number=i,
                    suggestion="考虑删除或完成TODO"
                ))
            
            # 检测过度设计
            if re.search(r'class.*Factory|Abstract.*Factory', line):
                results.append(RuleResult(
                    rule_name=self.name,
                    passed=False,
                    severity=RuleSeverity.INFO,
                    message="检测到工厂模式，确认是否真的需要",
                    line_number=i,
                    suggestion="YAGNI原则：先写能工作的最简单实现"
                ))
        
        return results


class SecurityRule(BaseRule):
    """安全规则 - 检测高危操作."""
    
    def __init__(self):
        super().__init__(
            name="Security_Checker",
            rule_type=RuleType.SECURITY,
            severity=RuleSeverity.BLOCK
        )
        # 高危操作模式
        self.dangerous_patterns = [
            (r'rm\s+-rf\s+[/~]', "危险删除操作", "使用更安全的删除方式"),
            (r'DROP\s+TABLE', "数据库危险操作", "确认是否需要备份"),
            (r'os\.system\s*\(', "命令执行风险", "使用subprocess并验证输入"),
            (r'eval\s*\(', "代码执行风险", "使用ast.literal_eval替代"),
            (r'exec\s*\(', "动态代码执行", "避免使用exec"),
            (r'password\s*=\s*["\'][^"\']+["\']', "硬编码密码", "使用环境变量或密钥管理"),
            (r'api[_-]?key\s*=\s*["\'][^"\']+["\']', "硬编码API密钥", "使用环境变量"),
            (r'select\s+.*\s+from\s+\w+.*%s', "SQL注入风险", "使用参数化查询"),
        ]
    
    def check(self, code: str, context: Optional[Dict] = None) -> List[RuleResult]:
        """检查安全违规."""
        results = []
        lines = code.split('\n')
        
        for i, line in enumerate(lines, 1):
            for pattern, msg, suggestion in self.dangerous_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    results.append(RuleResult(
                        rule_name=self.name,
                        passed=False,
                        severity=RuleSeverity.BLOCK,
                        message=f"安全风险: {msg}",
                        line_number=i,
                        suggestion=suggestion
                    ))
        
        return results


class StyleRule(BaseRule):
    """代码风格规则."""
    
    def __init__(self):
        super().__init__(
            name="Style_Checker",
            rule_type=RuleType.STYLE,
            severity=RuleSeverity.INFO
        )
    
    def check(self, code: str, context: Optional[Dict] = None) -> List[RuleResult]:
        """检查代码风格."""
        results = []
        lines = code.split('\n')
        
        for i, line in enumerate(lines, 1):
            # 检测行长
            if len(line) > 120:
                results.append(RuleResult(
                    rule_name=self.name,
                    passed=False,
                    severity=RuleSeverity.INFO,
                    message=f"行长度超过120字符",
                    line_number=i,
                    suggestion="换行或简化表达式"
                ))
            
            # 检测Tab
            if '\t' in line:
                results.append(RuleResult(
                    rule_name=self.name,
                    passed=False,
                    severity=RuleSeverity.INFO,
                    message=f"使用Tab而非空格",
                    line_number=i,
                    suggestion="使用4个空格替代Tab"
                ))
        
        return results


class YAGNI_Level(Enum):
    """YAGNI决策阶梯级别."""
    L1_USER_NEED = auto()      # 功能真的需要吗
    L2_EXISTS = auto()           # 库里已有吗
    L3_STDLIB = auto()           # 标准库能搞定吗
    L4_NATIVE = auto()          # 平台原生能力
    L5_DEPENDENCY = auto()        # 已安装依赖能覆盖吗
    L6_COMPACT = auto()           # 能写成一行吗
    L7_MINIMAL = auto()           # 最少代码


@dataclass
class YAGNI_Decision:
    """YAGNI决策结果."""
    level: YAGNI_Level
    recommendation: str
    should_reduce: bool
    alternative: Optional[str] = None


class YAGNI_DecisionEngine:
    """YAGNI七级决策引擎."""
    
    def __init__(self):
        self.levels = [
            (YAGNI_Level.L1_USER_NEED, "确认用户需求是否明确"),
            (YAGNI_Level.L2_EXISTS, "检查是否已有类似实现"),
            (YAGNI_Level.L3_STDLIB, "尝试使用标准库"),
            (YAGNI_Level.L4_NATIVE, "使用平台原生能力"),
            (YAGNI_Level.L5_DEPENDENCY, "复用现有依赖"),
            (YAGNI_Level.L6_COMPACT, "用更紧凑的方式实现"),
            (YAGNI_Level.L7_MINIMAL, "写能工作的最少代码"),
        ]
    
    def evaluate(self, code: str, context: Dict) -> YAGNI_Decision:
        """
        评估代码是否符合YAGNI原则.
        
        Args:
            code: 代码字符串
            context: 上下文（已有依赖、标准库等）
            
        Returns:
            YAGNI_Decision: 决策结果
        """
        # L1: 用户需求
        if not context.get('user_confirmed', False):
            return YAGNI_Decision(
                level=YAGNI_Level.L1_USER_NEED,
                recommendation="请确认用户明确需要此功能",
                should_reduce=True,
                alternative="与用户沟通需求"
            )
        
        # L2: 检查已有实现
        existing = context.get('existing_similar', [])
        if existing:
            return YAGNI_Decision(
                level=YAGNI_Level.L2_EXISTS,
                recommendation="发现已有类似实现",
                should_reduce=True,
                alternative=f"复用: {existing[0]}"
            )
        
        # L3: 标准库检查
        stdlib_alternatives = self._check_stdlib(code, context.get('language', 'python'))
        if stdlib_alternatives:
            return YAGNI_Decision(
                level=YAGNI_Level.L3_STDLIB,
                recommendation="可使用标准库替代",
                should_reduce=True,
                alternative=stdlib_alternatives[0]
            )
        
        # L4-L7: 渐进式检查
        # ... 根据具体情况返回决策
        
        return YAGNI_Decision(
            level=YAGNI_Level.L7_MINIMAL,
            recommendation="当前实现已是较简洁方案",
            should_reduce=False
        )
    
    def _check_stdlib(self, code: str, language: str) -> List[str]:
        """检查是否可用标准库替代."""
        alternatives = []
        
        if language == 'python':
            # 检查是否可用list comprehension替代循环
            if re.search(r'for\s+\w+\s+in\s+\w+:\s*\n\s+\w+\.append', code):
                alternatives.append("使用列表推导式替代循环append")
            
            # 检查是否可用内置函数
            if re.search(r'def\s+sum\s*\(', code):
                alternatives.append("使用内置sum()函数")
            
            # 检查是否可用collections
            if re.search(r'class\s+\w+.*:\s*\n.*def\s+__init__.*\(.*self.*\):', code):
                if 'dataclass' not in code:
                    alternatives.append("使用@dataclass替代手写类")
        
        return alternatives


class RulesEngine:
    """规则引擎主类."""
    
    def __init__(self):
        self.rules: List[BaseRule] = []
        self.yagni_engine = YAGNI_DecisionEngine()
        
        # 注册核心规则（硬编码，不可绕过）
        self._register_core_rules()
    
    def _register_core_rules(self):
        """注册核心规则."""
        # 安全规则（最高优先级，不可禁用）
        self.register_rule(SecurityRule())
        
        # YAGNI规则
        self.register_rule(YAGNI_Rule())
        
        # 风格规则
        self.register_rule(StyleRule())
    
    def register_rule(self, rule: BaseRule):
        """注册规则."""
        self.rules.append(rule)
    
    def check_code(self, code: str, context: Optional[Dict] = None) -> Dict[str, List[RuleResult]]:
        """
        检查代码.
        
        Args:
            code: 代码字符串
            context: 上下文信息
            
        Returns:
            Dict: 按规则类型分组的结果
        """
        results = {
            'security': [],
            'yagni': [],
            'style': [],
            'passed': True
        }
        
        for rule in self.rules:
            rule_results = rule.check(code, context)
            
            if rule.rule_type == RuleType.SECURITY:
                results['security'].extend(rule_results)
            elif rule.rule_type == RuleType.YAGNI:
                results['yagni'].extend(rule_results)
            elif rule.rule_type == RuleType.STYLE:
                results['style'].extend(rule_results)
            
            # 检查是否有阻断级违规
            for r in rule_results:
                if r.severity == RuleSeverity.BLOCK:
                    results['passed'] = False
        
        return results
    
    def evaluate_yagni(self, code: str, context: Dict) -> YAGNI_Decision:
        """执行YAGNI评估."""
        return self.yagni_engine.evaluate(code, context)
    
    def get_security_report(self, code: str) -> Dict:
        """生成安全报告."""
        results = self.check_code(code)
        security_results = results['security']
        
        blocks = [r for r in security_results if r.severity == RuleSeverity.BLOCK]
        warnings = [r for r in security_results if r.severity == RuleSeverity.WARN]
        
        return {
            'passed': len(blocks) == 0,
            'block_count': len(blocks),
            'warning_count': len(warnings),
            'issues': security_results,
            'summary': f"发现 {len(blocks)} 个阻断级问题, {len(warnings)} 个警告"
        }