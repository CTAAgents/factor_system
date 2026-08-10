"""FTS-Expr 沙箱 runtime — 供生成的因子代码在沙箱内调用 (Phase C.2)。

安全说明: 本模块是沙箱白名单中唯一放行的 FTS 模块
(_SANDBOX_ALLOWED_FTS_MODULES 精确匹配)，仅暴露 eval_fts_expr。
"""

from __future__ import annotations

import numpy as np

from .executor import evaluate
from .parser import parse_expression
from .registry import build_registry

_REGISTRY = build_registry()


def eval_fts_expr(expression: str, data, params: dict) -> np.ndarray:
    """在沙箱内执行 FTS-Expr，返回 np.ndarray（与代码因子输出对齐）。

    Args:
        expression: FTS-Expr 字符串
        data: DataFrame 或 _ArrayDataWrapper
        params: 因子参数（预留）

    Returns:
        np.ndarray: 信号数组
    """
    node = parse_expression(expression)
    series = evaluate(node, data, _REGISTRY)
    return np.asarray(series, dtype=np.float64)
