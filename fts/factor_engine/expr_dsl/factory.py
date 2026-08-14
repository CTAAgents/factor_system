"""算子因子工厂 — FTS-Expr → FactorProgram (Phase C.2)。

FactorProgram 契约不变原则: 算子因子同样携带 code（确定性生成），
对评估链/Verifier/组合构建完全透明。
"""

from __future__ import annotations

from typing import Any, Optional

from ..contracts import (
    EconomicLogic,
    FactorKind,
    FactorProgram,
    FactorSignature,
)
from ..factor_program import create_factor_program
from .compiler import analyze_expression, compile_expr_to_code
from .validator import collect_fields


def create_operator_factor(
    expression: str,
    name: str,
    *,
    market: str,
    narrative: str,
    params: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    source: str = "operator",
) -> FactorProgram:
    """从 FTS-Expr 创建算子因子。

    Args:
        expression: FTS-Expr 表达式，如 "rank(ts_zscore(close, 60))"
        name: 因子名
        market: futures/stock/etf/multi
        narrative: 经济逻辑叙述
        params: 可调参数（默认 {}）
        trace_id: 全链路 trace_id
        source: 来源标记（默认 operator）

    Returns:
        携带 kind/expression/max_lookback 的算子因子 FactorProgram
    """
    analysis = analyze_expression(expression)
    code = compile_expr_to_code(expression, name)
    from .parser import parse_expression

    fields = sorted(collect_fields(parse_expression(expression)))
    signature = FactorSignature(
        input_fields=fields or ["close"],
        output_type="signal",
        frequency="daily",
        lookback=max(analysis.max_lookback, 1),
    )
    factor = create_factor_program(
        name=name,
        code=code,
        params=params or {},
        signature=signature,
        economic_logic=EconomicLogic(
            theory=3,
            behavioral=3,
            microstructure=3,
            institutional=3,
            narrative=narrative,
        ),
        source=source,  # type: ignore[typeddict-item]
        market=market,
        trace_id=trace_id,
    )
    factor["kind"] = FactorKind.OPERATOR
    factor["expression"] = expression
    factor["operator_depth"] = analysis.depth
    factor["operator_count"] = analysis.operator_count
    factor["max_lookback"] = analysis.max_lookback
    return factor
