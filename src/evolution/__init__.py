"""自进化引擎（v1.31，G22）——统一闭环自进化。

观察→分析→改进→验证→沉淀，五个作用目标共享同一闭环，策略层控制进化边界。
"""

from .engine import EvolutionEngine
from .policy import EvolutionPolicy

__all__ = ["EvolutionEngine", "EvolutionPolicy"]
