"""评测闭环 - fail-to-pass评测框架."""

import subprocess
import tempfile
import os
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from enum import Enum, auto
from datetime import datetime


class TestStatus(Enum):
    """测试状态."""
    PASS = auto()
    FAIL = auto()
    ERROR = auto()
    TIMEOUT = auto()


@dataclass
class TestCase:
    """测试用例."""
    name: str
    code: str
    test_code: str
    expected_behavior: str
    language: str = "python"


@dataclass
class TestRun:
    """测试运行结果."""
    test_name: str
    status: TestStatus
    output: str
    error: str
    duration: float
    attempt: int = 1


@dataclass
class EvaluationResult:
    """评测结果."""
    task_id: str
    passed: bool
    attempts: int
    final_code: str
    test_results: List[TestRun]
    fix_history: List[Dict] = field(default_factory=list)
    metrics: Dict = field(default_factory=dict)


class FailToPassEvaluator:
    """fail-to-pass评测器."""
    
    def __init__(self):
        self.test_history: List[EvaluationResult] = []
        self.max_attempts = 3
    
    def evaluate(self, task: TestCase, code_generator: Callable) -> EvaluationResult:
        """
        执行fail-to-pass评测.
        
        Args:
            task: 测试用例
            code_generator: 代码生成函数
            
        Returns:
            EvaluationResult: 评测结果
        """
        attempts = 0
        test_results = []
        fix_history = []
        current_code = task.code
        
        while attempts < self.max_attempts:
            attempts += 1
            
            # 运行测试
            run = self._run_test(current_code, task)
            test_results.append(run)
            
            if run.status == TestStatus.PASS:
                break
            
            # 分析失败原因
            failure_analysis = self._analyze_failure(run)
            
            # 尝试修复
            fixed_code = code_generator(current_code, failure_analysis)
            
            if fixed_code != current_code:
                fix_history.append({
                    'attempt': attempts,
                    'failure': failure_analysis,
                    'fix_applied': True
                })
                current_code = fixed_code
            else:
                fix_history.append({
                    'attempt': attempts,
                    'failure': failure_analysis,
                    'fix_applied': False
                })
                break
        
        # 计算指标
        metrics = self._calculate_metrics(test_results)
        
        result = EvaluationResult(
            task_id=task.name,
            passed=any(r.status == TestStatus.PASS for r in test_results),
            attempts=attempts,
            final_code=current_code,
            test_results=test_results,
            fix_history=fix_history,
            metrics=metrics
        )
        
        self.test_history.append(result)
        return result
    
    def _run_test(self, code: str, task: TestCase) -> TestRun:
        """运行测试."""
        import time
        start = time.time()
        
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(code + '\n\n' + task.test_code)
                temp_file = f.name
            
            # 运行pytest
            result = subprocess.run(
                ['python', '-m', 'pytest', temp_file, '-v', '--tb=short'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            duration = time.time() - start
            
            if result.returncode == 0:
                return TestRun(
                    test_name=task.name,
                    status=TestStatus.PASS,
                    output=result.stdout,
                    error="",
                    duration=duration
                )
            else:
                return TestRun(
                    test_name=task.name,
                    status=TestStatus.FAIL,
                    output=result.stdout,
                    error=result.stderr,
                    duration=duration
                )
        
        except subprocess.TimeoutExpired:
            return TestRun(
                test_name=task.name,
                status=TestStatus.TIMEOUT,
                output="",
                error="执行超时",
                duration=30.0
            )
        
        except Exception as e:
            return TestRun(
                test_name=task.name,
                status=TestStatus.ERROR,
                output="",
                error=str(e),
                duration=time.time() - start
            )
        
        finally:
            if 'temp_file' in locals():
                try:
                    os.unlink(temp_file)
                except:
                    pass
    
    def _analyze_failure(self, run: TestRun) -> Dict:
        """分析失败原因."""
        error = run.error + run.output
        
        # 分类错误类型
        if 'SyntaxError' in error:
            return {'type': 'syntax', 'description': '语法错误'}
        elif 'NameError' in error:
            return {'type': 'name', 'description': '未定义变量'}
        elif 'TypeError' in error:
            return {'type': 'type', 'description': '类型错误'}
        elif 'AssertionError' in error:
            return {'type': 'assertion', 'description': '断言失败'}
        elif 'ImportError' in error:
            return {'type': 'import', 'description': '导入错误'}
        else:
            return {'type': 'unknown', 'description': '未知错误'}
    
    def _calculate_metrics(self, test_results: List[TestRun]) -> Dict:
        """计算评测指标."""
        total = len(test_results)
        passed = sum(1 for r in test_results if r.status == TestStatus.PASS)
        failed = sum(1 for r in test_results if r.status == TestStatus.FAIL)
        errors = sum(1 for r in test_results if r.status == TestStatus.ERROR)
        
        avg_duration = sum(r.duration for r in test_results) / total if total > 0 else 0
        
        return {
            'total_attempts': total,
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'pass_rate': passed / total if total > 0 else 0,
            'avg_duration': round(avg_duration, 2),
            'first_pass_attempt': next(
                (r.attempt for r in test_results if r.status == TestStatus.PASS),
                None
            )
        }
    
    def get_statistics(self) -> Dict:
        """获取评测统计."""
        if not self.test_history:
            return {'total_tasks': 0}
        
        total = len(self.test_history)
        passed = sum(1 for r in self.test_history if r.passed)
        
        return {
            'total_tasks': total,
            'passed': passed,
            'failed': total - passed,
            'pass_rate': passed / total,
            'avg_attempts': sum(r.attempts for r in self.test_history) / total,
            'avg_fixes': sum(len(r.fix_history) for r in self.test_history) / total
        }
    
    def export_report(self, output_path: str):
        """导出评测报告."""
        report = {
            'generated_at': datetime.now().isoformat(),
            'statistics': self.get_statistics(),
            'results': [
                {
                    'task_id': r.task_id,
                    'passed': r.passed,
                    'attempts': r.attempts,
                    'metrics': r.metrics
                }
                for r in self.test_history
            ]
        }
        
        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)