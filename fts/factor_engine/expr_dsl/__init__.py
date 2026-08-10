"""FTS-Expr DSL — 算子表达式语言 (Phase C.2)。

受控函数调用表达式，如: rank(ts_zscore(close, 60))。
"""

from .ast import ExprNode
from .compiler import ExprAnalysis, analyze_expression, compile_expr_to_code
from .executor import DSLExecutionError, evaluate
from .parser import FTSExprError, parse_expression
from .registry import (
    A_SHARE_FIELDS,
    L0_FIELDS,
    OperatorMeta,
    build_registry,
    verify_registry_consistency,
)
from .runtime import eval_fts_expr
from .validator import DSLValidationError, collect_fields, compute_max_lookback, validate_expr

__all__ = [
    "ExprNode",
    "ExprAnalysis",
    "FTSExprError",
    "DSLValidationError",
    "DSLExecutionError",
    "parse_expression",
    "validate_expr",
    "compute_max_lookback",
    "collect_fields",
    "evaluate",
    "build_registry",
    "OperatorMeta",
    "L0_FIELDS",
    "A_SHARE_FIELDS",
    "verify_registry_consistency",
    "compile_expr_to_code",
    "analyze_expression",
    "eval_fts_expr",
]
