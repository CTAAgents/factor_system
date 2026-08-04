"""tests/scenarios — 宏观行为场景测试包。

HARNESS §11-logic-review-plan.md §A.2:
    构造 20~30 个手工标注的"典型市场片段"，
    验证模型预测是否与经济学直觉一致。
"""

from .definitions import ALL_SCENARIOS, ScenarioDefinition
from .validator import ScenarioValidator, ScenarioResult

__all__ = [
    "ALL_SCENARIOS",
    "ScenarioDefinition",
    "ScenarioValidator",
    "ScenarioResult",
]