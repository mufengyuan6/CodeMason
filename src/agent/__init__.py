"""Agent 层：内部事件 + 状态机 + 主循环 + Plan/Act + 反思。"""

from .events import InternalEvent, InternalEventType, TerminationReason
from .loop import AgentLoop, EventIdGenerator, LLMProtocol, ToolProtocol
from .plan_act import PlanActCoordinator
from .reflection import ErrorClass, ReflectionEngine, Strategy
from .state_machine import AgentState, StateMachine, StateMachineError, TRANSITIONS

__all__ = [
    "InternalEvent",
    "InternalEventType",
    "TerminationReason",
    "AgentLoop",
    "EventIdGenerator",
    "LLMProtocol",
    "ToolProtocol",
    "AgentState",
    "StateMachine",
    "StateMachineError",
    "TRANSITIONS",
    "PlanActCoordinator",
    "ReflectionEngine",
    "ErrorClass",
    "Strategy",
]
