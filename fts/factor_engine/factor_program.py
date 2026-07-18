"""
loop_engine/factor_program.py — 因子程序接口（图灵完备代码 + 安全沙箱）

factorengine 核心约束：
    1. 因子程序必须是可执行 Python 代码（图灵完备）
    2. 仅允许 numpy/pandas/scipy/statsmodels/talib
    3. 输入为 OHLCV DataFrame，输出为 np.ndarray（-1~+1 信号 或 score）
    4. 必须可被安全沙箱编译执行，禁止 import os/sys/subprocess/open

版本: v8.10.0
"""
# pylint: disable=too-many-branches,too-many-arguments,too-many-positional-arguments,exec-used,redefined-builtin

from __future__ import annotations

import ast
import hashlib
import secrets
import types
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

from .contracts import (
    EconomicLogic,
    FactorProgram,
    FactorSignature,
)


# ─── 安全沙箱约束 ─────────────────────────────────────────

ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "numpy", "np", "pandas", "pd", "scipy", "statsmodels",
    "talib", "math", "statistics",
})

FORBIDDEN_NAMES: frozenset[str] = frozenset({
    "open", "exec", "eval", "compile", "globals", "locals",
    "vars", "dir", "getattr", "setattr", "delattr",
    "input", "breakpoint", "exit", "quit", "help",
    "memoryview", "bytearray",
})

FORBIDDEN_MODULES: frozenset[str] = frozenset({
    "os", "sys", "subprocess", "shutil", "pathlib",
    "socket", "http", "urllib", "requests",
    "ctypes", "multiprocessing", "threading", "asyncio",
    "pickle", "marshal", "importlib",
})


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
                reasons.append(
                    f"factor_program 必须接受 2 个参数 (data, params)，实际 {len(args.args)}"
                )
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
_SANDBOX_ALLOWED_MODULES: frozenset[str] = frozenset({
    "numpy", "pandas", "scipy", "statsmodels", "talib",
    "math", "statistics",
})


def _safe_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
    """沙箱安全的 __import__ — 仅允许白名单模块。

    任何尝试导入 FORBIDDEN_MODULES 或非白名单模块的请求都抛 ImportError。
    """
    mod_top = name.split(".")[0] if name else ""
    if mod_top in FORBIDDEN_MODULES:
        raise ImportError(f"禁止导入模块: {name}")
    if mod_top not in _SANDBOX_ALLOWED_MODULES:
        raise ImportError(f"模块不在沙箱白名单: {name}")
    return __import__(name, globals, locals, fromlist, level)


class FactorExecutor:
    """因子程序执行器 — 安全沙箱内编译并执行因子代码。

    设计要点:
        1. 仅暴露 ALLOWED_IMPORTS 中的模块
        2. 禁止 __builtins__ 中的危险函数
        3. 每次执行超时 30s（由调用方实现）
        4. 输出必须为 np.ndarray
    """

    def __init__(self, program: FactorProgram):
        self.program = program
        self._compiled: Optional[types.FunctionType] = None  # type: ignore[name-defined]
        self._validate()

    def _validate(self) -> None:
        code = self.program.get("code", "")
        if not code:
            raise FactorCompileError("因子代码为空")
        ok, reasons = validate_factor_code(code)
        if not ok:
            raise FactorCompileError(
                f"因子 {self.program.get('factor_id', '?')} 编译失败: {'; '.join(reasons)}"
            )

    def compile(self) -> None:
        """编译因子代码到可执行函数。"""
        code = self.program["code"]
        # 限制的全局命名空间
        safe_globals: dict[str, Any] = {
            "__builtins__": {
                # 白名单内置函数 — 数值/类型/迭代/查询
                "abs": abs, "min": min, "max": max, "sum": sum,
                "len": len, "range": range, "enumerate": enumerate,
                "zip": zip, "sorted": sorted, "reversed": reversed,
                "isinstance": isinstance, "type": type, "issubclass": issubclass,
                "hasattr": hasattr, "callable": callable,
                "round": round, "divmod": divmod, "pow": pow,
                "int": int, "float": float, "str": str, "bool": bool,
                "list": list, "dict": dict, "tuple": tuple, "set": set,
                "frozenset": frozenset, "bytes": bytes,
                "map": map, "filter": filter, "iter": iter, "next": next,
                "any": any, "all": all,
                "repr": repr, "format": format, "chr": chr, "ord": ord,
                "print": print, "None": None, "True": True, "False": False,
                # 安全的 __import__ — 仅允许白名单模块
                "__import__": _safe_import,
                "__name__": "__factor_sandbox__",
                "__file__": None,
            },
            # 白名单模块
            "numpy": np, "np": np,
            "pandas": pd, "pd": pd,
            "math": __import__("math"),
            "statistics": __import__("statistics"),
        }
        try:
            local_ns: dict[str, Any] = {}
            exec(code, safe_globals, local_ns)  # noqa: S102 — 受控沙箱
            func = local_ns.get("factor_program")
            if func is None or not callable(func):
                raise FactorCompileError("编译后未找到 factor_program 函数")
            self._compiled = func
        except FactorCompileError:
            raise
        except Exception as e:
            raise FactorCompileError(f"编译失败: {type(e).__name__}: {e}") from e

    def execute(self, data: pd.DataFrame, params: dict[str, Any]) -> np.ndarray:
        """执行因子程序，返回 np.ndarray 信号。

        Args:
            data: OHLCV 数据 (columns: open/high/low/close/volume/settle/open_interest...)
            params: 因子参数

        Returns:
            np.ndarray: 信号数组（-1~+1）或评分数组
        """
        if self._compiled is None:
            self.compile()
        try:
            result = self._compiled(data, params)  # type: ignore[misc]
        except Exception as e:
            raise FactorCompileError(f"执行失败: {type(e).__name__}: {e}") from e

        if not isinstance(result, np.ndarray):
            raise FactorCompileError(
                f"因子输出必须为 np.ndarray，实际为 {type(result).__name__}"
            )
        return result


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
) -> FactorProgram:
    """创建一个新的因子程序实例。

    自动生成 factor_id 和时间戳。
    """
    if not economic_logic.get("narrative", "").strip():
        raise ValueError("economic_logic.narrative 不能为空字符串")

    factor_id = generate_factor_id(name, code)
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
