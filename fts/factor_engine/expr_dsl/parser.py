"""FTS-Expr DSL 递归下降解析器。

语法 (受控函数调用，介于纯代码与纯算子树之间):
    expr  := term
    term  := ident "(" args ")"   # 算子调用
           | ident                 # 字段引用
           | number                # 数值常量
    args  := term ("," term)*
"""
from __future__ import annotations

from .ast import ExprNode


class FTSExprError(ValueError):
    """FTS-Expr 解析/校验错误。"""


class ExprParser:
    """递归下降解析器。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.n = len(text)

    def parse(self) -> ExprNode:
        node = self._parse_term()
        self._skip_ws()
        if self.pos != self.n:
            raise FTSExprError(
                f"解析失败: 位置 {self.pos} 存在多余内容 '{self.text[self.pos:]}'"
            )
        return node

    def _skip_ws(self) -> None:
        while self.pos < self.n and self.text[self.pos].isspace():
            self.pos += 1

    def _peek(self) -> str:
        return self.text[self.pos] if self.pos < self.n else ""

    def _parse_term(self) -> ExprNode:
        self._skip_ws()
        ch = self._peek()
        if ch and (ch.isdigit() or (ch == "-" and self.pos + 1 < self.n
                                    and self.text[self.pos + 1].isdigit())):
            return self._parse_number()
        name = self._parse_ident()
        if self._peek() == "(":
            self.pos += 1
            args: list[ExprNode] = []
            self._skip_ws()
            if self._peek() != ")":
                while True:
                    args.append(self._parse_term())
                    self._skip_ws()
                    if self._peek() == ",":
                        self.pos += 1
                        continue
                    break
            if self._peek() != ")":
                raise FTSExprError(f"位置 {self.pos}: 期望 ')'")
            self.pos += 1
            return ExprNode(op=name, args=args, kind="op")
        return ExprNode(op=name, kind="field")

    def _parse_ident(self) -> str:
        self._skip_ws()
        start = self.pos
        while self.pos < self.n and (
            self.text[self.pos].isalnum() or self.text[self.pos] == "_"
        ):
            self.pos += 1
        if start == self.pos:
            raise FTSExprError(f"位置 {self.pos}: 期望标识符, 实际 '{self._peek()}'")
        return self.text[start:self.pos]

    def _parse_number(self) -> ExprNode:
        self._skip_ws()
        start = self.pos
        if self._peek() == "-":
            self.pos += 1
        while self.pos < self.n and (
            self.text[self.pos].isdigit() or self.text[self.pos] == "."
        ):
            self.pos += 1
        text = self.text[start:self.pos]
        if text in ("", "-", "."):
            raise FTSExprError(f"位置 {start}: 非法数值 '{text}'")
        return ExprNode(op=text, kind="const")


def parse_expression(text: str) -> ExprNode:
    """解析 FTS-Expr 表达式为 AST。"""
    return ExprParser(text).parse()
