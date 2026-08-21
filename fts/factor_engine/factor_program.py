"""
loop_engine/factor_program.py — 因子程序接口（图灵完备代码 + 安全沙箱）

factorengine 核心约束：
    1. 因子程序必须是可执行 Python 代码（图灵完备）
    2. 仅允许 numpy/pandas/scipy/statsmodels/talib
    3. 输入为 OHLCV DataFrame，输出为 np.ndarray（-1~+1 信号 或 score）
    4. 必须可被安全沙箱编译执行，禁止 import os/sys/subprocess/open

版本: v1.2.0（修复 Pandas FutureWarning + np.exp 溢出）
"""
# pylint: disable=too-many-branches,too-many-arguments,too-many-positional-arguments,exec-used,redefined-builtin

from __future__ import annotations

import ast
import hashlib
import logging
import re
import secrets
import types
import warnings
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── 全局警告抑制 ─────────────────────────────────────────
# 抑制 Pandas FutureWarning: Series.__getitem__ treating keys as positions is deprecated
# 因子代码中使用 series[i] 进行位置索引，新版 Pandas 建议使用 iloc[i]
# 在因子执行层面统一处理，避免修改 LLM 生成的因子代码
warnings.filterwarnings("ignore", category=FutureWarning, message=".*treating keys as positions is deprecated.*")
warnings.filterwarnings(
    "ignore", category=FutureWarning, message=".*Series.__setitem__ treating keys as positions is deprecated.*"
)

# 抑制 numpy RuntimeWarning: overflow encountered in exp / divide by zero 等
# 因子代码中的 np.exp() 在极端数值下溢出，已通过后置 np.clip 处理
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")

from .contracts import (  # noqa: E402 — 前置 warnings.filterwarnings 后导入
    EconomicLogic,
    FactorKind,
    FactorProgram,
    FactorSignature,
)

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from .signal_cache import SignalCache


# ─── 安全沙箱约束 ─────────────────────────────────────────

ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "numpy",
        "np",
        "pandas",
        "pd",
        "scipy",
        "statsmodels",
        "talib",
        "math",
        "statistics",
    }
)

FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "input",
        "breakpoint",
        "exit",
        "quit",
        "help",
        "memoryview",
        "bytearray",
    }
)

FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "pathlib",
        "socket",
        "http",
        "urllib",
        "requests",
        "ctypes",
        "multiprocessing",
        "threading",
        "asyncio",
        "pickle",
        "marshal",
        "importlib",
    }
)


class FactorCompileError(Exception):
    """因子程序编译/验证失败。"""


# ─── 因子 ID 生成 ─────────────────────────────────────────


def generate_factor_id(name: str, code: str) -> str:
    """生成全局唯一的因子 ID: fct_<8hex>。

    基于 name + code + secrets 随机熵 哈希，确保唯一性。
    """
    raw = f"{name}|{code}|{secrets.token_hex(8)}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"fct_{h}"


# ─── 代码自动修复 ─────────────────────────────────────────


def _rewrite_condition_assignment(code: str) -> str:
    """将 if/elif/while 条件行内的赋值 `=` 改写为 `==`（LLM 常见误写）。

    仅处理以 if/elif/while 开头的条件行，且目标为独立 ` = `（不触碰 ==/>=/<=/!=）。
    """
    lines = code.split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if (
            re.match(r"(?:if|elif|while)\b", stripped)
            and " == " not in line
            and re.search(r"(?<![!<>=]) = (?![=])", line) is not None
        ):
            line = re.sub(r"(?<![!<>=]) = (?![=])", " == ", line, count=1)
        out.append(line)
    return "\n".join(out)


def fix_factor_code(code: str, error_reason: str = "") -> tuple[bool, str]:
    """尝试自动修复因子代码中的常见语法错误。

    支持的修复策略:
    1. 补全未闭合的字符串字面量（unterminated string literal）
    2. 修复不匹配的括号（closing parenthesis does not match opening）
    3. 通用语法修复（invalid syntax）— 行末补冒号
    4. 缩进类错误修复（unexpected indent / unindent does not match / expected an indented block）
    5. 全局括号平衡修复
    6. LLM 高频语法瑕疵全局修复（&&/||、一元 !、^ 幂、if 条件行内赋值 =、行尾残留反斜杠）

    Args:
        code: 原始因子代码
        error_reason: 原始错误信息（如 "语法错误: unterminated string literal (line 16)"）

    Returns:
        (fixed, fixed_code) — 修复成功返回 (True, 修复后的代码)，否则 (False, 原始代码)
    """
    error_msg = error_reason.lower()

    # 提取行号
    line_no: Optional[int] = None
    m = re.search(r"line\s+(\d+)", error_reason)
    if m:
        line_no = int(m.group(1))

    lines = code.split("\n")
    candidate_codes: list[str] = []  # 待试的修复候选

    # ── Strategy 1: 修复未闭合的字符串字面量 ──
    if "unterminated string" in error_msg and line_no is not None:
        idx = line_no - 1
        if 0 <= idx < len(lines):
            line = lines[idx]
            # 1a: 行末补单引号
            if line.count("'") % 2 != 0:
                fixed = lines.copy()
                fixed[idx] = line + "'"
                candidate_codes.append("\n".join(fixed))
            # 1b: 行末补双引号
            if line.count('"') % 2 != 0:
                fixed = lines.copy()
                fixed[idx] = line + '"'
                candidate_codes.append("\n".join(fixed))
            # 1c: 尝试将整行字符串包裹为三引号
            stripped = line.strip()
            if stripped.startswith("'") and not stripped.endswith("'"):
                fixed = lines.copy()
                fixed[idx] = line.replace("'", '"""', 1)
                fixed[idx] = fixed[idx][::-1].replace("'", '"""', 1)[::-1]
                candidate_codes.append("\n".join(fixed))

    # ── Strategy 2: 修复不匹配的括号 ──
    # Python 错误消息格式示例:
    #   "closing parenthesis ']' does not match opening parenthesis '('"
    #   "closing parenthesis ']' mismatch '('"
    _BRACKET_ERR_KEYWORDS = ("does not match opening", "mismatch")
    if any(kw in error_msg for kw in _BRACKET_ERR_KEYWORDS) and line_no is not None:
        idx = line_no - 1
        if 0 <= idx < len(lines):
            line = lines[idx]
            # 尝试各种括号交换组合（swap_pairs 为固定组合表）
            swap_pairs = [
                (")", "]"),  # 错用 ) 实际应为 ]
                ("]", ")"),  # 错用 ] 实际应为 )
                ("(]", "()"),  # 错用 (] 实际应为 ()
                ("[)", "[]"),  # 错用 [) 实际应为 []
            ]
            for old, new in swap_pairs:
                if old in line:
                    fixed = lines.copy()
                    fixed[idx] = line.replace(old, new)
                    if fixed[idx] != line:
                        candidate_codes.append("\n".join(fixed))

    # ── Strategy 3: 通用 invalid syntax 修复 ──
    if "invalid syntax" in error_msg and line_no is not None:
        idx = line_no - 1
        if 0 <= idx < len(lines):
            line = lines[idx]
            stripped = line.rstrip()
            # 3a: 行末补冒号 — 适用于 def/if/for/while/with/class/try/except/elif/else/finally
            _STMT_KEYWORDS = (
                "def ",
                "if ",
                "for ",
                "while ",
                "with ",
                "class ",
                "try:",
                "except",
                "elif ",
                "else:",
                "finally:",
            )
            if stripped and not stripped.endswith(":"):
                leading = stripped[: len(stripped) - len(stripped.lstrip())]
                content = stripped[len(leading) :]
                if any(content.startswith(kw) for kw in _STMT_KEYWORDS):
                    fixed = lines.copy()
                    fixed[idx] = line + ":"  # noqa: E701 — deliberate
                    candidate_codes.append("\n".join(fixed))
            # 3b: 行末补圆括号 — 适用于函数调用/表达式未闭合
            if stripped and not stripped.endswith(")"):
                # 检查当前行左括号数 > 右括号数
                open_count = stripped.count("(")
                close_count = stripped.count(")")
                if open_count > close_count:
                    fixed = lines.copy()
                    fixed[idx] = line + ")" * (open_count - close_count)
                    candidate_codes.append("\n".join(fixed))
            # 3c: 行末补方括号 — 适用于列表/索引表达式未闭合
            if stripped:
                open_sq = stripped.count("[")
                close_sq = stripped.count("]")
                if open_sq > close_sq:
                    fixed = lines.copy()
                    fixed[idx] = line + "]" * (open_sq - close_sq)
                    candidate_codes.append("\n".join(fixed))
            # 3d: 检查行内是否有明显语法错误 — 修复双运算符
            # 如 "return 1 ++ 2" → "return 1 + 2"
            _DOUBLE_OPS = [
                ("++", "+"),
                ("--", "-"),
                ("**", "**"),  # 合法的，不处理
            ]
            for old, new in _DOUBLE_OPS:
                if old == new:
                    continue
                if old in stripped:
                    # 只在特定上下文中修复（如不在字符串内）
                    fixed = lines.copy()
                    fixed[idx] = line.replace(old, new)
                    if fixed[idx] != line:
                        candidate_codes.append("\n".join(fixed))

    # ── Strategy 6: 缩进类错误修复（IndentationError） ──
    # LLM 生成的因子代码常见问题：语句缩进错位导致
    #   unexpected indent / unindent does not match any outer indentation level
    #   / expected an indented block
    _INDENT_ERR_KEYWORDS = (
        "unexpected indent",
        "unindent does not match",
        "expected an indented block",
    )

    def _leading_spaces(s: str) -> int:
        return len(s) - len(s.lstrip(" "))

    if any(kw in error_msg for kw in _INDENT_ERR_KEYWORDS):
        # 根因常出现在错误行前/后一行（如某语句被错误地反缩进，
        # 解析器报错的却是紧随其后的缩进行），故对错误行邻域做候选。
        focus_idx: list[int] = []
        if line_no is not None:
            idx = line_no - 1
            for j in (idx - 1, idx, idx + 1):
                if 0 <= j < len(lines):
                    focus_idx.append(j)
        else:
            focus_idx = list(range(len(lines)))
        for j in focus_idx:
            cur_line = lines[j]
            if not cur_line.strip():
                continue
            # 候选缩进 = 标准倍数 {0,4,8,12} ∪ 前后最近非空行的缩进
            cand_indents: set[int] = {0, 4, 8, 12}
            for k in range(j - 1, -1, -1):
                if lines[k].strip():
                    cand_indents.add(_leading_spaces(lines[k]))
                    break
            for k in range(j + 1, len(lines)):
                if lines[k].strip():
                    cand_indents.add(_leading_spaces(lines[k]))
                    break
            for ci in cand_indents:
                if ci == _leading_spaces(cur_line):
                    continue
                fixed = lines.copy()
                fixed[j] = " " * ci + cur_line.lstrip(" ")
                candidate_codes.append("\n".join(fixed))
        # expected an indented block：冒号行后补缩进 pass 形成代码块
        if "expected an indented block" in error_msg and line_no is not None:
            idx = line_no - 1
            if 0 <= idx < len(lines) and lines[idx].rstrip().endswith(":"):
                fixed = lines.copy()
                fixed.insert(
                    idx + 1, " " * (_leading_spaces(lines[idx]) + 4) + "pass"
                )
                candidate_codes.append("\n".join(fixed))

    # ── Strategy 6: LLM 高频语法瑕疵全局修复（invalid syntax 兜底） ──
    # LLM 生成代码的常见非 Python 写法：&&/||、一元 !、^ 幂、if/while 条件行内赋值 =、
    # 行尾残留反斜杠等。这些瑕疵常出现在非报错行，故对整段代码做全局变换并逐候选 ast 校验。
    # 触发条件覆盖真实 Python 错误消息：invalid syntax / cannot assign（如 "if x = y:"）/
    # unexpected character（如行尾反斜杠续行残留）。
    if (
        "invalid syntax" in error_msg
        or "cannot assign" in error_msg
        or "unexpected character" in error_msg
    ):
        base = code

        def _push(v: str) -> None:
            if v != base and v not in candidate_codes:
                candidate_codes.append(v)

        # 6a: 逻辑运算符 && / || → and / or
        _push(base.replace("&&", " and ").replace("||", " or "))

        # 6b: 幂运算 ^ → **（因子代码中 ^ 几乎均为数学幂，位异或场景可忽略）
        _push(re.sub(r"\^", "**", base))

        # 6c: if/elif/while 条件行内赋值 = → ==
        _push(_rewrite_condition_assignment(base))

        # 6d: 行尾残留反斜杠（非转义用途）去除
        s7d_lines: list[str] = []
        for _ln in base.split("\n"):
            _stripped = _ln.rstrip()
            if _stripped.endswith("\\"):
                _ln = _stripped[:-1] + _ln[len(_stripped):]
            s7d_lines.append(_ln)
        _push("\n".join(s7d_lines))

        # 6e: 一元 ! → not（先掩蔽 != 防误改，仅修复 ! 后接标识符/括号的场景）
        masked = base.replace("!=", "\x00NEQ\x00")
        s7e = re.sub(r"(?<![=!<>])\!(?=\s*\w|\s*\()", " not ", masked).replace("\x00NEQ\x00", "!=")
        _push(s7e)

        # 6f: 组合变换（覆盖同段多类瑕疵同时出现）
        combo = "\n".join(s7d_lines)
        combo_masked = combo.replace("!=", "\x00NEQ\x00")
        combo = re.sub(r"(?<![=!<>])\!(?=\s*\w|\s*\()", " not ", combo_masked).replace("\x00NEQ\x00", "!=")
        combo = combo.replace("&&", " and ").replace("||", " or ")
        combo = _rewrite_condition_assignment(combo)
        combo = re.sub(r"\^", "**", combo)
        _push(combo)

    # ── Strategy 4: 全局括号平衡修复 ──
    # 当上面所有策略都无效时，尝试对整个代码做括号对齐
    # 仅在明确检测到括号不匹配的情况下尝试
    def _balance_brackets(s: str) -> str:
        """尝试修复括号不匹配问题。"""
        # 统计各类括号数量
        opens = {"(": 0, "[": 0, "{": 0}
        closes = {")": 0, "]": 0, "}": 0}
        for ch in s:
            if ch in opens:
                opens[ch] += 1
            elif ch in closes:
                closes[ch] += 1
        # 如果某类括号数量不匹配，尝试在末尾补充
        for op, cl in [("(", ")"), ("[", "]"), ("{", "}")]:
            diff = opens[op] - closes[cl]
            if diff > 0:
                s += cl * diff
        return s

    # 尝试每个修复候选
    for fixed_code in candidate_codes:
        try:
            ast.parse(fixed_code)
            if fixed_code != code:
                logger.info(
                    "[fix_factor_code] 修复成功, error=%s, original_len=%d, fixed_len=%d",
                    error_reason,
                    len(code),
                    len(fixed_code),
                )
                return True, fixed_code
        except SyntaxError:
            continue

    # ── Strategy 5: 全局括号平衡（兜底） ──
    _BRACKET_ERR_KEYWORDS_S5 = ("does not match opening", "mismatch")
    if any(kw in error_msg for kw in _BRACKET_ERR_KEYWORDS_S5):
        balanced = _balance_brackets(code)
        if balanced != code:
            try:
                ast.parse(balanced)
                logger.info(
                    "[fix_factor_code] 全局括号平衡修复成功, error=%s, original_len=%d, fixed_len=%d",
                    error_reason,
                    len(code),
                    len(balanced),
                )
                return True, balanced
            except SyntaxError:
                pass

    return False, code


# ─── 安全沙箱验证 ─────────────────────────────────────────


def validate_factor_code(code: str) -> tuple[bool, list[str]]:
    """验证因子代码是否符合安全沙箱约束。

    检查项:
        1. 语法正确性（ast.parse）
        2. 必须定义 `def factor_program(data, params):` 函数
        3. 禁止 import 黑名单模块
        4. 禁止调用黑名单内置函数
        5. 禁止访问 __builtins__、__import__

    Returns:
        (passed, failure_reasons)
    """
    reasons: list[str] = []

    # 1. 语法检查
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"语法错误: {e.msg} (line {e.lineno})"]

    # 2. 必须定义 factor_program 函数
    has_factor_func = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "factor_program":
            has_factor_func = True
            # 检查签名: (data, params)
            args = node.args
            if len(args.args) != 2:
                reasons.append(f"factor_program 必须接受 2 个参数 (data, params)，实际 {len(args.args)}")
            break
    if not has_factor_func:
        reasons.append("代码必须定义 `def factor_program(data, params):` 函数")

    # 3. 检查 import 语句
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod in FORBIDDEN_MODULES:
                    reasons.append(f"禁止 import 黑名单模块: {mod}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in FORBIDDEN_MODULES:
                reasons.append(f"禁止 from {mod} import ...")
        elif isinstance(node, ast.Attribute):
            # 检查 __import__、__builtins__ 访问
            if isinstance(node.attr, str) and node.attr.startswith("__"):
                if node.attr in ("__import__", "__builtins__", "__globals__"):
                    reasons.append(f"禁止访问内部属性: {node.attr}")

    # 4. 检查禁止的内置函数调用
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
                reasons.append(f"禁止调用黑名单函数: {func.id}")

    # 5. 检查 import 之外的非白名单模块使用
    # （放宽：允许 import numpy as np 等白名单写法）

    return (len(reasons) == 0), reasons


# ─── 因子程序执行 ─────────────────────────────────────────

# 允许在沙箱内通过 import 语句加载的模块白名单
_SANDBOX_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "numpy",
        "pandas",
        "scipy",
        "statsmodels",
        "talib",
        "math",
        "statistics",
    }
)

# 沙箱内精确放行的 FTS 模块（全名匹配，不放开 fts 顶层）
_SANDBOX_ALLOWED_FTS_MODULES: frozenset[str] = frozenset(
    {
        "fts.factor_engine.expr_dsl.runtime",
    }
)


def _safe_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
    """沙箱安全的 __import__ — 仅允许白名单模块 + 精确放行的 FTS runtime。"""
    mod_top = name.split(".")[0] if name else ""
    if mod_top in FORBIDDEN_MODULES:
        raise ImportError(f"禁止导入模块: {name}")
    if name in _SANDBOX_ALLOWED_FTS_MODULES:
        return __import__(name, globals, locals, fromlist, level)
    if mod_top not in _SANDBOX_ALLOWED_MODULES:
        raise ImportError(f"模块不在沙箱白名单: {name}")
    return __import__(name, globals, locals, fromlist, level)


class _ArrayDataWrapper:
    """DataFrame 包装器 — 将列访问转换为 ndarray 以消除 Pandas FutureWarning。

    因子代码中 data['close'] 返回 ndarray 而非 Series，
    这样 series[i] 位置索引不会触发弃用警告。

    同时保持 .columns, .index, .shape 等属性的兼容性。
    """

    def __init__(self, df: pd.DataFrame):
        self._df = df
        self._columns = list(df.columns)

    def __getitem__(self, key: str) -> np.ndarray:
        """返回列数据为 ndarray（而非 Series）。

        pandas 3.0: .to_numpy() 可能返回只读视图，显式 .copy() 确保可写
        （legacy 模板因子常做原地赋值如 data['close'][i] = val）。
        """
        if key not in self._df.columns:
            raise KeyError(f"列 '{key}' 不存在，可用列: {self._columns}")
        return self._df[key].to_numpy().astype(np.float64).copy()

    def __getattr__(self, name: str) -> np.ndarray:
        """属性访问列（兼容 `hasattr(data, 'volume')` + `data.volume` 写法）。

        pandas DataFrame 支持 df.volume 属性访问列；本 wrapper 保持该语义，
        否则因子代码中 `hasattr(data, 'volume')` 恒为 False，导致
        volume 等列被替换为常量（如 volume_zero 消融失效）。

        pandas 3.0: 显式 .copy() 确保返回可写数组。
        """
        if name in self._df.columns:
            return self._df[name].to_numpy().astype(np.float64).copy()
        raise AttributeError(f"'data' 没有属性 '{name}'")

    def __contains__(self, key: str) -> bool:
        return key in self._df.columns

    def __len__(self) -> int:
        return len(self._df)

    @property
    def columns(self) -> list[str]:
        return self._columns

    @property
    def index(self):
        return self._df.index

    @property
    def shape(self) -> tuple[int, int]:
        return self._df.shape

    def __repr__(self) -> str:
        return f"_ArrayDataWrapper(columns={self._columns}, len={len(self._df)})"


_OPERATOR_AST_CACHE: dict[str, Any] = {}
_operator_registry: Optional[Any] = None


def _get_operator_registry():
    """延迟构建 DSL 算子注册表单例。"""
    global _operator_registry
    if _operator_registry is None:
        from .expr_dsl import build_registry

        _operator_registry = build_registry()
    return _operator_registry


class FactorExecutor:
    """因子程序执行器 — 安全沙箱内编译并执行因子代码。

    设计要点:
        1. 仅暴露 ALLOWED_IMPORTS 中的模块
        2. 禁止 __builtins__ 中的危险函数
        3. 每次执行超时 30s（由调用方实现）
        4. 输出必须为 np.ndarray
    """

    def __init__(
        self,
        program: FactorProgram,
        signal_cache: Optional["SignalCache"] = None,
    ):
        """初始化执行器。

        Args:
            program: 因子程序
            signal_cache: 可选信号缓存（GAP-070，质检链信号复用）；
                传入后 execute 对相同 (factor_id, params, 数据指纹) 命中直接返回缓存信号。
        """
        self.program = program
        self._signal_cache = signal_cache
        self._compiled: Optional[types.FunctionType] = None  # type: ignore[name-defined]
        self._validate()

    def _validate(self) -> None:
        code = self.program.get("code", "")
        if not code:
            raise FactorCompileError("因子代码为空")
        ok, reasons = validate_factor_code(code)
        if not ok:
            raise FactorCompileError(f"因子 {self.program.get('factor_id', '?')} 编译失败: {'; '.join(reasons)}")

    def compile(self) -> None:
        """编译因子代码到可执行函数。"""
        code = self.program["code"]
        # 限制的全局命名空间
        safe_globals: dict[str, Any] = {
            "__builtins__": {
                # 白名单内置函数 — 数值/类型/迭代/查询
                "abs": abs,
                "min": min,
                "max": max,
                "sum": sum,
                "len": len,
                "range": range,
                "enumerate": enumerate,
                "zip": zip,
                "sorted": sorted,
                "reversed": reversed,
                "isinstance": isinstance,
                "type": type,
                "issubclass": issubclass,
                "hasattr": hasattr,
                "callable": callable,
                "round": round,
                "divmod": divmod,
                "pow": pow,
                "int": int,
                "float": float,
                "str": str,
                "bool": bool,
                "list": list,
                "dict": dict,
                "tuple": tuple,
                "set": set,
                "frozenset": frozenset,
                "bytes": bytes,
                "map": map,
                "filter": filter,
                "iter": iter,
                "next": next,
                "any": any,
                "all": all,
                "repr": repr,
                "format": format,
                "chr": chr,
                "ord": ord,
                "print": print,
                "None": None,
                "True": True,
                "False": False,
                # 安全的 __import__ — 仅允许白名单模块
                "__import__": _safe_import,
                "__name__": "__factor_sandbox__",
                "__file__": None,
            },
            # 白名单模块
            "numpy": np,
            "np": np,
            "pandas": pd,
            "pd": pd,
            "math": __import__("math"),
            "statistics": __import__("statistics"),
        }
        try:
            local_ns: dict[str, Any] = {}
            exec(code, safe_globals, local_ns)  # noqa: S102 — 受控沙箱
            # 模块级 import 绑定（如 from fts...runtime import eval_fts_expr）落在
            # local_ns，而 factor_program 的 __globals__ 指向 safe_globals；合并后
            # 保证函数内可解析算子 runtime 桥接 (Phase C.2)。
            safe_globals.update(local_ns)
            func = local_ns.get("factor_program")
            if func is None or not callable(func):
                raise FactorCompileError("编译后未找到 factor_program 函数")
            self._compiled = func
        except FactorCompileError:
            raise
        except Exception as e:
            raise FactorCompileError(f"编译失败: {type(e).__name__}: {e}") from e

    def execute(self, data: pd.DataFrame, params: dict[str, Any]) -> np.ndarray:
        """执行因子程序，返回 np.ndarray 信号（GAP-070 支持信号缓存）。

        Args:
            data: OHLCV 数据 (columns: open/high/low/close/volume/settle/open_interest...)
            params: 因子参数

        Returns:
            np.ndarray: 信号数组（-1~+1）或评分数组，长度与 data 行数对齐
        """
        # ── 信号缓存快速路径（GAP-070）: 相同 (factor, params, 数据指纹) 直接复用 ──
        if self._signal_cache is not None:
            cached = self._signal_cache.get(self.program, data)
            if cached is not None:
                return cached
        signal = self._execute_uncached(data, params)
        if self._signal_cache is not None:
            self._signal_cache.put(self.program, data, signal)
        return signal

    def _execute_uncached(self, data: pd.DataFrame, params: dict[str, Any]) -> np.ndarray:
        """执行因子程序（无缓存路径，供 execute 内部调用）。"""
        # ── 算子因子快速路径 (Phase C.2): 直接解释 FTS-Expr，不编译沙箱代码 ──
        if self.program.get("kind") == "operator" and self.program.get("expression"):
            try:
                return self._execute_operator(data)
            except Exception as e:
                logger.warning(
                    "算子快速路径失败 (factor_id=%s), 回退沙箱: %s",
                    self.program.get("factor_id", "?"),
                    e,
                )

        if self._compiled is None:
            self.compile()

        expected_len = len(data)

        # 预处理: 将 DataFrame 列转换为 ndarray，消除 Pandas FutureWarning
        # 因子代码中 data['close'] 返回 ndarray 而非 Series
        # 使用 ArrayDataWrapper 保持 DataFrame 接口兼容性
        wrapped_data = _ArrayDataWrapper(data)

        # 抑制因子执行期间的所有警告（已通过后置处理保证数值稳定性）
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                result = self._compiled(wrapped_data, params)  # type: ignore[misc]
            except Exception:
                # 回退: 使用原始 DataFrame（因子代码可能依赖 DataFrame 方法）
                try:
                    result = self._compiled(data, params)  # type: ignore[misc]
                except Exception:
                    # 最终回退: dict[str, np.ndarray] 格式（pandas 3.0 需 .copy() 保可写）
                    data_dict: dict[str, np.ndarray] = {
                        col: data[col].to_numpy().astype(np.float64).copy() for col in data.columns
                    }
                    try:
                        result = self._compiled(data_dict, params)  # type: ignore[misc]
                    except Exception as e:
                        raise FactorCompileError(f"执行失败: {type(e).__name__}: {e}") from e

        if not isinstance(result, np.ndarray):
            raise FactorCompileError(f"因子输出必须为 np.ndarray，实际为 {type(result).__name__}")

        # 数值稳定性处理: 裁剪 inf 和 NaN，限制输出范围（先清洗，再对齐，
        # 避免前导 NaN 填充被 nan_to_num 清零，破坏"尾部有效值对齐日期"语义）
        result = np.nan_to_num(result, nan=0.0, posinf=1.0, neginf=-1.0)
        result = np.clip(result, -10.0, 10.0)

        if len(result) != expected_len:
            result = self._align_output(result, expected_len)

        return result

    def _execute_operator(self, data: pd.DataFrame) -> np.ndarray:
        """算子快速路径 — 直接解释 FTS-Expr AST，向量化执行。"""
        if not hasattr(data, "columns"):
            raise TypeError("算子快速路径需要 DataFrame 输入")
        from .expr_dsl import evaluate, parse_expression

        expression = self.program.get("expression")
        if not expression:
            raise TypeError("算子因子缺少 expression")
        node = _OPERATOR_AST_CACHE.get(expression)
        if node is None:
            node = parse_expression(expression)
            _OPERATOR_AST_CACHE[expression] = node
        series = evaluate(node, data, _get_operator_registry())
        # pandas 3.0: np.asarray(Series, dtype=float) 可能返回只读视图，
        # 显式 .copy() 确保可写（GAP-170）
        result: np.ndarray = np.asarray(series, dtype=np.float64).copy()

        expected_len = len(data)
        result = np.nan_to_num(result, nan=0.0, posinf=1.0, neginf=-1.0)
        result = np.clip(result, -10.0, 10.0)
        if len(result) != expected_len:
            result = self._align_output(result, expected_len)
        return result

    @staticmethod
    def _align_output(result: np.ndarray, expected_len: int) -> np.ndarray:
        """将因子输出对齐到期望长度。

        场景: LLM 生成的因子代码常用 rolling/shift/diff，导致输出比输入短
              (如 rolling(10) 产生 NaN 前缀或直接缩短 1 行)。
        策略: 短 => 前置 NaN 填充 (保持尾部有效值对齐日期)
              长 => 截断到期望长度
        """
        if len(result) == expected_len:
            return result
        if len(result) < expected_len:
            pad: np.ndarray = np.full(expected_len - len(result), np.nan, dtype=np.float64)
            return np.concatenate([pad, result])
        return result[:expected_len]


# ─── 因子程序工厂 ─────────────────────────────────────────


def create_factor_program(
    name: str,
    code: str,
    params: dict[str, Any],
    signature: FactorSignature,
    economic_logic: EconomicLogic,
    source: str = "manual",
    parent_id: Optional[str] = None,
    generation: int = 0,
    trace_id: Optional[str] = None,
    risk_tag: Optional[str] = None,
    market: Optional[str] = None,
    symbols: Optional[list[str]] = None,
    factor_version: str = "v2",
    kind: FactorKind = FactorKind.CODE,
) -> FactorProgram:
    """创建一个新的因子程序实例。

    自动生成 factor_id 和时间戳。
    支持多品种元数据（market/symbols），减少跨品种类型检查错误。

    Args:
        risk_tag: 风险标签，如 "vwap_approx" 用于标记高风险因子。
        market: 适用市场 (futures/stock/etf/multi)
        symbols: 适用品种列表（空列表=全品种适用）
        factor_version: 因子定义版本号
    """
    if not economic_logic.get("narrative", "").strip():
        raise ValueError("economic_logic.narrative 不能为空字符串")

    factor_id = generate_factor_id(name, code)
    normalized_symbols = symbols if symbols is not None else []
    return FactorProgram(
        factor_id=factor_id,
        name=name,
        code=code,
        params=params,
        signature=signature,
        economic_logic=economic_logic,
        source=source,  # type: ignore[typeddict-item]
        parent_id=parent_id,
        generation=generation,
        created_at=datetime.now().isoformat(),
        trace_id=trace_id or factor_id,
        risk_tag=risk_tag,
        market=market or "multi",  # type: ignore[typeddict-item]
        symbols=normalized_symbols,
        factor_version=factor_version,
        is_multi_symbol=len(normalized_symbols) > 1,
        kind=kind,
    )


__all__ = [
    "ALLOWED_IMPORTS",
    "FORBIDDEN_NAMES",
    "FORBIDDEN_MODULES",
    "FactorCompileError",
    "FactorExecutor",
    "generate_factor_id",
    "validate_factor_code",
    "create_factor_program",
]
