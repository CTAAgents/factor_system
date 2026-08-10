"""FTS-Expr 解释执行器 — 直接解释 AST，向量化计算 (Phase C.2)。

性能优势: 不经沙箱编译/exec，算子间共享 Series，速度远超代码因子。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ast import ExprNode
from .parser import FTSExprError
from .registry import OperatorMeta


class DSLExecutionError(FTSExprError):
    """FTS-Expr 执行失败。"""


def evaluate(
    node: ExprNode,
    data,
    registry: dict[str, OperatorMeta],
) -> "pd.Series | float":
    """解释执行表达式节点。

    Args:
        node: AST 节点
        data: DataFrame（或具 columns/index 的包装器）
        registry: 算子注册表

    Returns:
        pd.Series（算子/字段输出）或 float（常量）
    """
    if node.kind == "const":
        try:
            return float(node.op)
        except ValueError as e:
            raise DSLExecutionError(f"非法常量: {node.op}") from e
    if node.kind == "field":
        return _read_field(node.op, data)
    meta = registry.get(node.op)
    if meta is None:
        raise DSLExecutionError(f"未知算子: {node.op}")
    values = [evaluate(a, data, registry) for a in node.args]
    kwargs = _bind_args(meta, values)
    result = meta.func(**kwargs)
    if result is None:
        raise DSLExecutionError(f"算子 '{node.op}' 返回 None")
    return result


def _read_field(name: str, data) -> pd.Series:
    """读取字段列，兼容 DataFrame 与 _ArrayDataWrapper（列返回 ndarray）。"""
    if hasattr(data, "columns"):
        if name not in data.columns:
            raise DSLExecutionError(f"字段 '{name}' 不在数据中")
        return data[name].astype(float)
    # 包装器/dict 形态: data[name] 返回 ndarray
    arr = data[name]
    series = pd.Series(np.asarray(arr, dtype=np.float64))
    index = getattr(data, "index", None)
    if index is not None:
        series.index = index
    return series


def _bind_args(meta: OperatorMeta, values: list) -> dict:
    """按参数名绑定参数（int/float 转换）。"""
    kwargs: dict = {}
    for i, pname in enumerate(meta.params):
        v = values[i]
        if pname in meta.int_params:
            v = int(v)
        elif pname in meta.float_params:
            v = float(v)
        kwargs[pname] = v
    return kwargs
