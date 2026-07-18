"""tests/factor_engine/test_factor_program.py — 因子程序接口测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import EconomicLogic, FactorSignature
from fts.factor_engine.factor_program import (
    ALLOWED_IMPORTS,
    FORBIDDEN_MODULES,
    FORBIDDEN_NAMES,
    FactorCompileError,
    FactorExecutor,
    create_factor_program,
    generate_factor_id,
    validate_factor_code,
)


# ─── 因子 ID 生成 ─────────────────────────────────────────

def test_generate_factor_id_format():
    """因子 ID 必须符合 fct_<8hex> 格式。"""
    fid = generate_factor_id("test", "def f(): pass")
    assert fid.startswith("fct_")
    assert len(fid) == 12  # fct_ + 8 hex


def test_generate_factor_id_uniqueness():
    """同名同代码的两次调用应产生不同 ID（时间戳参与哈希）。"""
    id1 = generate_factor_id("test", "code")
    id2 = generate_factor_id("test", "code")
    # 高概率不同（依赖 time.time_ns）
    assert id1 != id2


# ─── 代码安全沙箱 ─────────────────────────────────────────

def test_validate_valid_code():
    code = """
import numpy as np
def factor_program(data, params):
    close = data['close'].values
    return np.zeros(len(close))
"""
    ok, reasons = validate_factor_code(code)
    assert ok, f"应通过: {reasons}"


def test_validate_missing_factor_function():
    code = "x = 1"
    ok, reasons = validate_factor_code(code)
    assert not ok
    assert any("factor_program" in r for r in reasons)


def test_validate_wrong_signature():
    code = "def factor_program(data): return data"
    ok, reasons = validate_factor_code(code)
    assert not ok
    assert any("参数" in r for r in reasons)


def test_validate_forbidden_import_os():
    code = """
import os
def factor_program(data, params):
    return data['close'].values
"""
    ok, reasons = validate_factor_code(code)
    assert not ok
    assert any("os" in r for r in reasons)


def test_validate_forbidden_import_subprocess():
    code = """
import subprocess
def factor_program(data, params):
    return data['close'].values
"""
    ok, reasons = validate_factor_code(code)
    assert not ok
    assert any("subprocess" in r for r in reasons)


def test_validate_forbidden_eval_call():
    code = """
def factor_program(data, params):
    eval("1+1")
    return data['close'].values
"""
    ok, reasons = validate_factor_code(code)
    assert not ok
    assert any("eval" in r for r in reasons)


def test_validate_forbidden_open_call():
    code = """
def factor_program(data, params):
    f = open('/etc/passwd')
    return data['close'].values
"""
    ok, reasons = validate_factor_code(code)
    assert not ok
    assert any("open" in r for r in reasons)


def test_validate_syntax_error():
    code = "def factor_program(data, params\n  return"
    ok, reasons = validate_factor_code(code)
    assert not ok
    assert any("语法" in r for r in reasons)


# ─── 因子执行 ─────────────────────────────────────────────

def test_executor_compile_and_run(sample_ohlcv):
    """可执行因子程序应能编译并返回 ndarray。"""
    code = """
import numpy as np
def factor_program(data, params):
    return np.zeros(len(data['close']))
"""
    fp = create_factor_program(
        name="test_zero",
        code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="测试"),
        source="manual",
    )
    executor = FactorExecutor(fp)
    result = executor.execute(sample_ohlcv, {})
    assert isinstance(result, np.ndarray)
    assert len(result) == len(sample_ohlcv)


def test_executor_reject_invalid_code():
    """无效代码应抛 FactorCompileError。"""
    fp = create_factor_program(
        name="invalid",
        code="def wrong_name(data, params): return None",
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="测试"),
        source="manual",
    )
    with pytest.raises(FactorCompileError):
        FactorExecutor(fp).compile()


def test_executor_reject_non_ndarray_output(sample_ohlcv):
    """输出非 ndarray 应抛 FactorCompileError。"""
    code = """
def factor_program(data, params):
    return [0, 0, 0]
"""
    fp = create_factor_program(
        name="bad_output",
        code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="测试"),
        source="manual",
    )
    executor = FactorExecutor(fp)
    with pytest.raises(FactorCompileError):
        executor.execute(sample_ohlcv, {})


def test_create_factor_program_rejects_empty_narrative():
    """economic_logic.narrative 不能为空。"""
    with pytest.raises(ValueError):
        create_factor_program(
            name="bad",
            code="def factor_program(d,p): return d['close'].values",
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative=""),
            source="manual",
        )


def test_allowed_imports_includes_numpy():
    assert "numpy" in ALLOWED_IMPORTS
    assert "np" in ALLOWED_IMPORTS


def test_forbidden_modules_includes_os_subprocess():
    assert "os" in FORBIDDEN_MODULES
    assert "subprocess" in FORBIDDEN_MODULES
    assert "sys" in FORBIDDEN_MODULES


def test_forbidden_names_includes_eval_open():
    assert "eval" in FORBIDDEN_NAMES
    assert "open" in FORBIDDEN_NAMES
    assert "exec" in FORBIDDEN_NAMES


# ─── factor_program 额外覆盖 ────────────────────────────

class TestFactorProgramCoverage:
    """补齐 factor_program.py 覆盖率缺口。"""

    def test_allowed_imports_list_property(self):
        """ALLOWED_IMPORTS 应包含所有常见白名单模块。"""
        for mod in ("numpy", "np", "pandas", "pd", "scipy", "math", "statistics"):
            assert mod in ALLOWED_IMPORTS, f"缺少 {mod}"

    def test_forbidden_modules_list_property(self):
        """FORBIDDEN_MODULES 应包含所有常见危险模块。"""
        for mod in ("os", "sys", "subprocess", "shutil", "socket", "ctypes"):
            assert mod in FORBIDDEN_MODULES, f"缺少 {mod}"

    def test_forbidden_names_list_property(self):
        """FORBIDDEN_NAMES 应包含所有危险内置函数。"""
        for name in ("eval", "exec", "compile", "open", "globals", "locals"):
            assert name in FORBIDDEN_NAMES, f"缺少 {name}"

    def test_generate_factor_id_8hex(self):
        """生成 ID 的 hex 部分应正好 8 个字符。"""
        fid = generate_factor_id("test", "some code")
        hex_part = fid.split("_")[1]
        assert len(hex_part) == 8
        # 应为十六进制字符
        int(hex_part, 16)

    def test_validate_code_with_allowed_pandas_import(self):
        """from pandas import ... 应通过。"""
        code = """
import pandas as pd
import numpy as np
def factor_program(data, params):
    close = data['close']
    return np.zeros(len(close))
"""
        ok, reasons = validate_factor_code(code)
        assert ok, f"应通过: {reasons}"

    def test_validate_code_forbidden_from_import(self):
        """from os import ... 应被拒绝。"""
        code = """
from os import path
def factor_program(data, params):
    return data['close'].values
"""
        ok, reasons = validate_factor_code(code)
        assert not ok
        assert any("os" in r for r in reasons)

    def test_validate_code_forbidden_subprocess_from_import(self):
        """from subprocess import ... 应被拒绝。"""
        code = """
from subprocess import run
def factor_program(data, params):
    return data['close'].values
"""
        ok, reasons = validate_factor_code(code)
        assert not ok
        assert any("subprocess" in r for r in reasons)

    def test_validate_code_dunder_attribute_access(self):
        """访问 __import__ 等属性应被拒绝。"""
        code = """
def factor_program(data, params):
    x = something.__import__
    return data['close'].values
"""
        ok, reasons = validate_factor_code(code)
        assert not ok
        assert any("__import__" in r for r in reasons)

    def test_validate_code_dunder_builtins_attribute(self):
        """访问 __builtins__ 属性应被拒绝。"""
        code = """
def factor_program(data, params):
    x = something.__builtins__
    return data['close'].values
"""
        ok, reasons = validate_factor_code(code)
        assert not ok
        assert any("__builtins__" in r for r in reasons)

    def test_create_factor_program_valid_path(self):
        """完整创建 FactorProgram（含 trace_id）。"""
        fp = create_factor_program(
            name="test_factor",
            code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
            params={"window": 10},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="完整测试"),
            source="manual",
            parent_id="fct_parent1234",
            generation=1,
            trace_id="l2_trace_test",
        )
        assert fp["factor_id"].startswith("fct_")
        assert fp["name"] == "test_factor"
        assert fp["params"] == {"window": 10}
        assert fp["parent_id"] == "fct_parent1234"
        assert fp["generation"] == 1
        assert fp["trace_id"] == "l2_trace_test"
        assert fp["source"] == "manual"

    def test_executor_empty_code(self):
        """空代码应抛 FactorCompileError。"""
        from fts.factor_engine.contracts import FactorProgram
        fp = FactorProgram(
            factor_id="fct_empty",
            name="empty",
            code="",
            params={},
        )
        with pytest.raises(FactorCompileError, match="为空"):
            FactorExecutor(fp)

    def test_executor_code_no_factor_function(self):
        """无 factor_program 函数的代码应抛编译错误。"""
        from fts.factor_engine.contracts import FactorProgram
        fp = FactorProgram(
            factor_id="fct_no_func",
            name="no_func",
            code="x = 42",
            params={},
        )
        with pytest.raises(FactorCompileError):
            FactorExecutor(fp)

    def test_executor_execute_auto_compile(self, sample_ohlcv):
        """未手动 compile 时应自动编译。"""
        code = """
import numpy as np
def factor_program(data, params):
    return np.zeros(len(data['close']))
"""
        fp = create_factor_program(
            name="auto_compile",
            code=code,
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="auto"),
            source="manual",
        )
        executor = FactorExecutor(fp)
        # 不调 compile()，直接 execute 应自动编译
        result = executor.execute(sample_ohlcv, {})
        assert isinstance(result, np.ndarray)

    def test_executor_compile_non_callable_output(self):
        """编译后输出类型非 callable 应抛异常。"""
        from fts.factor_engine.contracts import FactorProgram
        # 代码需通过 _validate() 的 AST 检查（含 factor_program 函数定义），
        # 但 exec 后 factor_program 被覆盖为非 callable 值
        code = """
def factor_program(data, params):
    return 42

factor_program = 1
"""
        fp = FactorProgram(
            factor_id="fct_non_call",
            name="non_callable",
            code=code,
            params={},
        )
        executor = FactorExecutor(fp)
        with pytest.raises(FactorCompileError):
            executor.compile()

    def test_executor_execute_runtime_error(self, sample_ohlcv):
        """运行时异常应包装为 FactorCompileError。"""
        code = """
def factor_program(data, params):
    return 1 / 0
"""
        fp = create_factor_program(
            name="runtime_err",
            code=code,
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="runtime error"),
            source="manual",
        )
        executor = FactorExecutor(fp)
        with pytest.raises(FactorCompileError):
            executor.execute(sample_ohlcv, {})

    def test_executor_allows_numpy_scipy(self, sample_ohlcv):
        """执行器应允许 numpy/scipy 白名单模块。"""
        code = """
import numpy as np
import scipy.stats as sp_stats
def factor_program(data, params):
    close = data['close'].values
    # 简单统计
    z = (close - np.mean(close)) / max(np.std(close), 1e-10)
    return np.clip(z, -1.0, 1.0)
"""
        fp = create_factor_program(
            name="scipy_factor",
            code=code,
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="scipy test"),
            source="manual",
        )
        executor = FactorExecutor(fp)
        result = executor.execute(sample_ohlcv, {})
        assert isinstance(result, np.ndarray)
        # 信号应在 [-1, 1] 范围内
        assert np.all(result >= -1.0)
        assert np.all(result <= 1.0)
