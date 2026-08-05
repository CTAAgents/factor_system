"""FTS-Expr 编译器 — 表达式 → 确定性沙箱安全代码 + 静态分析 (Phase C.2)。"""
from __future__ import annotations

from dataclasses import dataclass

from .parser import parse_expression
from .registry import build_registry
from .validator import (
    DSLValidationError,
    compute_max_lookback,
    validate_expr,
)

_RUNTIME_MODULE = "fts.factor_engine.expr_dsl.runtime"


@dataclass(frozen=True)
class ExprAnalysis:
    """表达式静态分析结果（写入 FactorProgram 元数据）。"""

    expression: str
    max_lookback: int
    operator_count: int
    depth: int


def compile_expr_to_code(expression: str, name: str = "operator_factor") -> str:
    """将 FTS-Expr 编译为确定性沙箱安全 FactorProgram.code。

    生成的代码经 validate_factor_code 校验通过，可在现有沙箱执行，
    保证上层（持久化/评估链/Verifier）零改动。
    """
    node = parse_expression(expression)
    registry = build_registry()
    errors, _ = validate_expr(node, registry)
    if errors:
        raise DSLValidationError("; ".join(errors))
    return (
        f'"""算子因子 {name}: {expression}"""\n'
        "\n"
        "import numpy as np\n"
        "import pandas as pd\n"
        f"from {_RUNTIME_MODULE} import eval_fts_expr\n"
        "\n"
        "def factor_program(data, params):\n"
        f"    return eval_fts_expr({expression!r}, data, params)\n"
    )


def analyze_expression(expression: str) -> ExprAnalysis:
    """静态分析表达式（校验 + 元数据）。"""
    node = parse_expression(expression)
    registry = build_registry()
    errors, max_lookback = validate_expr(node, registry)
    if errors:
        raise DSLValidationError("; ".join(errors))
    return ExprAnalysis(
        expression=expression,
        max_lookback=max_lookback,
        operator_count=_count_ops(node),
        depth=_depth(node),
    )


def _count_ops(node) -> int:
    return (1 if node.kind == "op" else 0) + sum(_count_ops(a) for a in node.args)


def _depth(node) -> int:
    if not node.args:
        return 0
    return 1 + max(_depth(a) for a in node.args)
