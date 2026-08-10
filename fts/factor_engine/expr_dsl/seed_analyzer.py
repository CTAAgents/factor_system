"""seed_analyzer.py — 种子表达式静态分析（WQ 风格表达式 PIT 审计，GAP-S09）。

背景:
    股票种子 636/645 为 `expression:` 算子表达式（wq101/qlib158/jq/gtja/fundamental），
    执行走 seed_loader 老模板（内联转 Python），未走 expr_dsl 编译链，导致
    PIT 静态审计（compute_max_lookback/collect_fields）未对种子生效。

    本模块为种子表达式提供等价静态分析：解析 WQ 风格表达式调用树，静态提取
    - max_lookback: 各窗口算子（ts_mean/ts_corr/delay 等）窗口参数的严格上界
    - fields: 引用的数据字段集合
    - operators / operator_count / depth: 算子使用分布
    使种子因子在保持老模板执行路径的同时，获得与 DSL 编译链一致的 PIT 静态审计。

版本: v2.67.0 (GAP-S09)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ─── 已知算子集合（_EXPRESSION_OPS_SOURCE + np.* + 基本面 cs_*） ──
_KNOWN_OPS: frozenset[str] = frozenset(
    {
        "rank",
        "scale",
        "ifelse",
        "signed_power",
        "decay_linear",
        "delta",
        "delay",
        "log",
        "sign",
        "abs",
        "neg",
        "highday",
        "lowday",
        "ts_sum",
        "ts_mean",
        "ts_stddev",
        "ts_std_dev",
        "ts_corr",
        "ts_covariance",
        "ts_argmax",
        "ts_argmin",
        "ts_rank",
        "ts_min",
        "ts_max",
        "ts_product",
        "ts_median",
        "ts_delta",
        "ts_momentum",
        "ts_volatility",
        "np.tanh",
        "np.maximum",
        "np.abs",
        "np.sign",
        "np.sqrt",
        "np.power",
        "np.exp",
        "np.log",
        "np.where",
        "cs_rank",
        "cs_zscore",
    }
)

# 第 2 参数为窗口/滞后的算子
_WINDOW_OP_2: frozenset[str] = frozenset(
    {
        "ts_sum",
        "ts_mean",
        "ts_stddev",
        "ts_std_dev",
        "ts_argmax",
        "ts_argmin",
        "ts_rank",
        "ts_min",
        "ts_max",
        "ts_product",
        "ts_median",
        "ts_delta",
        "ts_momentum",
        "ts_volatility",
        "decay_linear",
        "delay",
        "delta",
    }
)
# 第 3 参数为窗口的算子
_WINDOW_OP_3: frozenset[str] = frozenset({"ts_corr", "ts_covariance"})

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SeedExprParseError(ValueError):
    """种子表达式解析失败。"""


@dataclass(frozen=True)
class SeedExprAnalysis:
    """种子表达式静态分析结果（与 ExprAnalysis 对齐）。"""

    expression: str
    max_lookback: int
    operator_count: int
    depth: int
    fields: tuple[str, ...] = field(default_factory=tuple)
    operators: tuple[str, ...] = field(default_factory=tuple)


# ─── 词法分析 ────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """切分表达式为 token 序列。

    支持: 数字（含负号）、标识符、`np.xxx` 复合标识符、括号、逗号、
    二元/一元运算符（+ - * / ** < <= > >= == !=）。
    """
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        # 数字（含负号与科学计数法）
        m = re.match(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text[i:])
        if m and (ch.isdigit() or (ch == "-" and i + 1 < n and text[i + 1].isdigit())):
            tokens.append(m.group(0))
            i += len(m.group(0))
            continue
        # 标识符（含 np.xxx 复合）
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            name = text[i:j]
            # 合并 np.xxx / cs.xxx 复合标识符
            if name in ("np", "cs") and j + 1 < n and text[j] == ".":
                j += 1
                k = j
                while k < n and (text[k].isalnum() or text[k] == "_"):
                    k += 1
                name = f"{name}.{text[j:k]}"
                j = k
            tokens.append(name)
            i = j
            continue
        # 多字符运算符
        two = text[i : i + 2]
        if two in ("**", "<=", ">=", "==", "!="):
            tokens.append(two)
            i += 2
            continue
        if ch in "+-*/%<>()[],&|!":
            tokens.append(ch)
            i += 1
            continue
        raise SeedExprParseError(f"位置 {i}: 无法识别的字符 '{ch}'")
    return tokens


# ─── 递归下降解析 ─────────────────────────────────────────────


class _SeedParser:
    """WQ 风格表达式递归下降解析器（仅静态分析，不求值）。"""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _expect(self, tok: str) -> None:
        if self._peek() != tok:
            raise SeedExprParseError(f"期望 '{tok}', 实际 '{self._peek()}'")
        self.pos += 1

    def parse(self) -> "tuple[int, int, set[str], set[str]]":
        """返回 (max_lookback, depth, fields, operators)。"""
        lookback, depth, fields, ops = self._parse_term(0)
        if self.pos != len(self.tokens):
            raise SeedExprParseError(f"解析失败: 位置 {self.pos} 存在多余内容 '{self.tokens[self.pos :]}'")
        return lookback, depth, fields, ops

    def _parse_term(self, depth: int) -> "tuple[int, int, set[str], set[str]]":
        """解析二元表达式层级。"""
        lb, d, fields, ops = self._parse_unary(depth)
        max_lb, max_d = lb, d
        while self._peek() in ("+", "-", "*", "/", "%", "**", "<", "<=", ">", ">=", "==", "!=", "&", "|"):
            self.pos += 1  # 消费运算符（跳过，无需记录）
            lb2, d2, f2, o2 = self._parse_unary(depth)
            max_lb = max(max_lb, lb2)
            max_d = max(max_d, d2)
            fields |= f2
            ops |= o2
        return max_lb, max_d, fields, ops

    def _parse_unary(self, depth: int) -> "tuple[int, int, set[str], set[str]]":
        """解析一元前缀。"""
        if self._peek() in ("-", "+", "!", "~"):
            self.pos += 1
            return self._parse_unary(depth)
        return self._parse_atom(depth)

    def _parse_atom(self, depth: int) -> "tuple[int, int, set[str], set[str]]":
        """解析原子：数字 / 字段 / 函数调用 / 括号子表达式。"""
        tok = self._peek()
        if tok is None:
            raise SeedExprParseError("意外的表达式结束")
        if tok == "(":
            self.pos += 1
            lb, d, fields, ops = self._parse_term(depth + 1)
            self._expect(")")
            return lb, d, fields, ops
        if _NUM_RE.match(tok):
            self.pos += 1
            return 0, depth, set(), set()
        if _IDENT_RE.match(tok) or ("." in tok and tok.split(".", 1)[0] in ("np", "cs")):
            self.pos += 1
            if self._peek() == "(":
                return self._parse_call(tok, depth)
            # 裸标识符 = 数据字段
            return 0, depth, {tok}, set()
        raise SeedExprParseError(f"无法解析 token '{tok}'")

    def _parse_call(self, name: str, depth: int) -> "tuple[int, int, set[str], set[str]]":
        """解析函数调用。"""
        self._expect("(")
        args: list[tuple[int, int, set[str], set[str]]] = []
        if self._peek() != ")":
            while True:
                args.append(self._parse_term(depth + 1))
                if self._peek() == ",":
                    self.pos += 1
                    continue
                break
        self._expect(")")

        max_lb = 0
        max_d = depth + 1
        fields: set[str] = set()
        ops: set[str] = {name}
        for i, (lb, d, f, o) in enumerate(args):
            max_lb = max(max_lb, lb)
            max_d = max(max_d, d)
            fields |= f
            ops |= o

        # 窗口算子 → 静态提取 lookback（token 级精确提取窗口参数常量）
        win_index = None
        if name in _WINDOW_OP_2:
            win_index = 2
        elif name in _WINDOW_OP_3:
            win_index = 3
        if win_index is not None:
            const_val = _window_const_from_tokens(self.tokens, name, win_index)
            if const_val is not None:
                max_lb = max(max_lb, const_val)
        return max_lb, max_d, fields, ops


# 窗口参数常量精确提取（token 级括号配对）：
def _window_const_from_tokens(tokens: list[str], op_name: str, win_index: int) -> Optional[int]:
    """在 token 流上定位 op_name 调用的第 win_index 个参数常量（支持嵌套）。"""
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok != op_name:
            continue
        # 找到 '('
        j = i + 1
        if j >= n or tokens[j] != "(":
            continue
        # 配对扫描参数
        depth_paren = 0
        arg_idx = 0
        arg_start = -1
        k = j
        while k < n:
            t = tokens[k]
            if t == "(":
                depth_paren += 1
            elif t == ")":
                depth_paren -= 1
                if depth_paren == 0:
                    break
            elif t == "," and depth_paren == 1:
                arg_idx += 1
                if arg_idx == win_index - 1:
                    arg_start = k + 1
                elif arg_idx > win_index - 1:
                    break
            k += 1
        if arg_start is None:
            continue
        # 取 win_index 参数（arg_idx == win_index-1 时 arg_start 指向其首 token）
        # 重扫该参数范围（到下一个逗号/右括号）
        end = k
        for m in range(arg_start, end):
            if tokens[m] in (",", ")"):
                end = m
                break
        param_tokens = tokens[arg_start:end]
        if len(param_tokens) == 1 and _NUM_RE.match(param_tokens[0]):
            return int(float(param_tokens[0]))
    return None


def analyze_seed_expression(expression: str) -> SeedExprAnalysis:
    """静态分析 WQ 风格种子表达式。

    Args:
        expression: 种子 YAML 中的 expression 字符串

    Returns:
        SeedExprAnalysis（max_lookback / fields / operators / 结构指标）

    Raises:
        SeedExprParseError: 表达式语法无法解析
    """
    tokens = _tokenize(expression)
    parser = _SeedParser(tokens)
    max_lookback, depth, fields, ops = parser.parse()
    return SeedExprAnalysis(
        expression=expression,
        max_lookback=max_lookback,
        operator_count=len(ops),
        depth=depth,
        fields=tuple(sorted(fields)),
        operators=tuple(sorted(ops)),
    )


def estimate_lookback_static(expression: str) -> int:
    """静态估算种子表达式最大 lookback（窗口算子常量参数上界，无则 10）。

    替代 seed_loader 中基于正则的粗糙估计（_estimate_lookback），
    仅统计真实窗口/滞后参数，避免把 signed_power 幂次、ifelse 分支常量等
    非窗口数字计入 lookback。
    """
    try:
        tokens = _tokenize(expression)
    except SeedExprParseError:
        return 10
    max_lb = 0
    for op_name in _WINDOW_OP_2:
        v = _window_const_from_tokens(tokens, op_name, 2)
        if v is not None:
            max_lb = max(max_lb, v)
    for op_name in _WINDOW_OP_3:
        v = _window_const_from_tokens(tokens, op_name, 3)
        if v is not None:
            max_lb = max(max_lb, v)
    return max_lb if max_lb > 0 else 10


__all__ = [
    "SeedExprAnalysis",
    "SeedExprParseError",
    "analyze_seed_expression",
    "estimate_lookback_static",
]
