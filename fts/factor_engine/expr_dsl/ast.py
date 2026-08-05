"""FTS-Expr DSL 抽象语法树。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExprNode:
    """表达式节点。

    kind:
        - "op": 算子调用，op 为算子名，args 为子节点
        - "field": 字段引用（close/volume 等），op 为字段名
        - "const": 数值常量，op 为数值文本（如 "60" / "2.0"）
    """
    op: str
    args: list["ExprNode"] = field(default_factory=list)
    kind: str = "op"
