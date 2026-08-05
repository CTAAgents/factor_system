"""FTS-Expr 校验器 — 静态分析（算子/字段/参数边界/最大 lookback）。

关键价值: 通过算子注册表的 lookback_param 声明，静态计算表达式最大
lookback，从根源上杜绝未来函数 (PIT 校验自动化)。
"""
from __future__ import annotations

from typing import Iterable

from .ast import ExprNode
from .parser import FTSExprError
from .registry import L0_FIELDS, OperatorMeta


class DSLValidationError(FTSExprError):
    """FTS-Expr 校验失败。"""


def validate_expr(
    node: ExprNode,
    registry: dict[str, OperatorMeta],
    fields: Iterable[str] = L0_FIELDS,
) -> tuple[list[str], int]:
    """校验表达式，返回 (错误列表, 最大 lookback)。"""
    field_set = set(fields)
    errors: list[str] = []
    max_lookback = compute_max_lookback(node, registry)
    _validate_node(node, registry, field_set, errors)
    return errors, max_lookback


def compute_max_lookback(
    node: ExprNode,
    registry: dict[str, OperatorMeta],
) -> int:
    """静态计算表达式最大 lookback（PIT 校验）。"""
    if node.kind in ("field", "const"):
        return 0
    meta = registry.get(node.op)
    if meta is None:
        return 0
    child_max = max((compute_max_lookback(a, registry) for a in node.args), default=0)
    if meta.lookback_param is None:
        return child_max
    try:
        idx = meta.params.index(meta.lookback_param)
    except ValueError:
        return child_max
    arg = node.args[idx]
    if arg.kind == "const":
        try:
            return max(child_max, int(float(arg.op)))
        except ValueError:
            pass
    # lookback 参数非常量（动态）→ 取注册边界上限
    bounds = meta.param_bounds.get(meta.lookback_param)
    if bounds:
        return max(child_max, int(bounds[1]))
    return child_max


def collect_fields(node: ExprNode) -> set[str]:
    """收集表达式引用的全部字段。"""
    if node.kind == "field":
        return {node.op}
    result: set[str] = set()
    for arg in node.args:
        result |= collect_fields(arg)
    return result


def _validate_node(
    node: ExprNode,
    registry: dict[str, OperatorMeta],
    field_set: set[str],
    errors: list[str],
) -> None:
    if node.kind == "const":
        return
    if node.kind == "field":
        if node.op not in field_set:
            errors.append(f"未知字段 '{node.op}', 可用: {', '.join(sorted(field_set))}")
        return
    meta = registry.get(node.op)
    if meta is None:
        errors.append(f"未知算子 '{node.op}'")
        return
    if len(node.args) != len(meta.params):
        errors.append(
            f"算子 '{node.op}' 期望 {len(meta.params)} 个参数, 实际 {len(node.args)}"
        )
    for arg in node.args:
        _validate_node(arg, registry, field_set, errors)
    if len(node.args) != len(meta.params):
        return
    for i, pname in enumerate(meta.params):
        arg = node.args[i]
        if arg.kind == "const" and pname in meta.param_bounds:
            lo, hi = meta.param_bounds[pname]
            try:
                val = float(arg.op)
            except ValueError:
                continue
            if not (lo <= val <= hi):
                errors.append(
                    f"算子 '{node.op}' 参数 {pname}={val} 越界 [{lo}, {hi}]"
                )
