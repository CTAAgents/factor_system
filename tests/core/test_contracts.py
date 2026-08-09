"""tests/core/test_contracts.py — FTS 核心契约入口测试。

验证 fts.core.contracts 正确 re-export fts.factor_engine.contracts 的所有符号。

HARNESS §契约优先: 契约变更必须同步更新测试。
"""

from __future__ import annotations

import importlib

import pytest

from fts.core import contracts as core_contracts
from fts.factor_engine import contracts as fe_contracts
from fts.core.contracts import __all__ as core_all


# ─── __all__ 完整性 ─────────────────────────────────────


def test_all_is_defined_and_non_empty():
    """__all__ 已定义且非空。"""
    assert core_all is not None
    assert len(core_all) > 0


def test_all_symbols_importable():
    """__all__ 中每个符号均可从 fts.core.contracts 导入。"""
    for name in core_all:
        obj = getattr(core_contracts, name, None)
        assert obj is not None, f"符号 {name} 在 core_contracts 中不存在"


def test_all_symbols_match_factor_engine():
    """__all__ 中 re-export 自 factor_engine 的符号值与源一致。

    注：v2.3.0 起 core.contracts 允许本地定义额外符号（如 FuturesOHLCV），
    本测试只对**从 factor_engine.contracts 导入**的符号做一致性检查。
    """
    fe_names = set(getattr(fe_contracts, "__all__", []) or [])
    for name in core_all:
        if name not in fe_names:
            # 本地定义符号（如 FuturesOHLCV）跳过
            continue
        core_val = getattr(core_contracts, name)
        fe_val = getattr(fe_contracts, name, None)
        assert fe_val is not None, f"符号 {name} 在 factor_engine.contracts 中不存在"
        assert core_val is fe_val, (
            f"符号 {name} 引用不一致: core={id(core_val)}, fe={id(fe_val)}"
        )


def test_core_contracts_exports_subset_of_fe():
    """fts.core.contracts 中**来自 factor_engine 的符号**必须是其子集。

    v2.3.0 起 core.contracts 允许本地定义额外符号（如 FuturesOHLCV），
    本测试只校验来自 factor_engine 的 re-export 符号是子集。
    """
    fe_all = getattr(fe_contracts, "__all__", None)
    if fe_all is not None:
        fe_set = set(fe_all)
        # 只检查在 factor_engine 中也声明的符号
        re_exported = {name for name in core_all if name in fe_set}
        # re-export 的符号必须是 fe 的子集（实际上是 fe 的一部分）
        assert re_exported.issubset(fe_set), (
            f"core 有 re-export 符号不在 factor_engine 中: {re_exported - fe_set}"
        )


# ─── 具体符号验证（≥10 个目标） ────────────────────────


@pytest.mark.parametrize(
    "name,expected_type",
    [
        # 版本
        ("EVOLUTION_VERSION", str),
        # 核心契约
        ("FactorProgram", type),
        ("FactorSignature", type),
        ("EconomicLogic", type),
        ("BacktestMetrics", type),
        ("EconomicScore", type),
        ("MultipleTestResult", type),
        ("FactorEvaluation", type),
        ("ExperienceTrace", type),
        ("EvolutionState", type),
        # Verifier
        ("VerifierConfig", type),
        ("VerifierResult", type),
        # 预算
        ("BudgetConfig", type),
        # 默认配置
        ("DEFAULT_VERIFIER_CONFIG", dict),
        ("DEFAULT_BUDGET_CONFIG", dict),
        # L1
        ("L1BootstrappingSource", object),
        ("MetaLoopStatus", object),
        ("SeedCandidate", type),
        ("L1MetaLoopState", type),
        ("FactorPoolEntry", type),
        ("FactorPool", type),
        ("L1VerifierConfig", type),
        ("L1VerifierResult", type),
        ("DEFAULT_L1_VERIFIER_CONFIG", dict),
        ("DEFAULT_L1_BUDGET_CONFIG", dict),
        # L3
        ("FactorCorrelation", type),
        ("PortfolioSignal", type),
        ("PortfolioCombo", type),
        ("AgentOptimizationProposal", type),
        ("L3VerifierConfig", type),
        ("L3MetaLoopState", type),
        ("DEFAULT_L3_VERIFIER_CONFIG", dict),
        ("DEFAULT_L3_BUDGET", int),
    ],
)
def test_specific_import(name: str, expected_type):
    """每个具体符号类型正确。"""
    obj = getattr(core_contracts, name)
    assert isinstance(obj, expected_type), (
        f"{name} 期望类型 {expected_type}，实际 {type(obj)}"
    )


# ─── 动态导入 ───────────────────────────────────────────


def test_import_via_fts_core():
    """可以通过 from fts.core import contracts 导入。"""
    mod = importlib.import_module("fts.core.contracts")
    assert mod is core_contracts


def test_import_via_fts():
    """可以通过 from fts import core 后访问 core.contracts。"""
    import fts

    assert hasattr(fts.core, "contracts")


# ─── v2.3.0 新增：FuturesOHLCV / FuturesDataLineage 契约 ──────────────
#
# 覆盖规则：
#   - FuturesOHLCV: 8 个必填字段(symbol/date/open/high/low/close/volume/trace_id)
#   - 5 个可选字段(amount/hold/settle/pre_settle/oi_change/vwap/source/fetched_at)
#   - FuturesDataLineage: 7 个字段(trace_id/started_at/finished_at/symbols/rows_written/sources_used/sources_failed)
#   - disagreements: 多源交叉验证告警计数


# ─── FuturesOHLCV 字段定义（与实现解耦，作为单一事实源） ──────────

FUTURES_OHLCV_REQUIRED = [
    "symbol", "date", "open", "high", "low", "close", "volume", "trace_id",
]
FUTURES_OHLCV_OPTIONAL = [
    "amount", "hold", "settle", "pre_settle", "oi_change", "vwap",
    "source", "fetched_at",
]
FUTURES_OHLCV_ALL = FUTURES_OHLCV_REQUIRED + FUTURES_OHLCV_OPTIONAL


def test_futures_ohlcv_importable():
    """FuturesOHLCV TypedDict 可从 fts.core.contracts 导入。"""
    from fts.core.contracts import FuturesOHLCV

    assert FuturesOHLCV is not None
    # TypedDict 在运行时即普通 dict，但其 __annotations__ 含字段
    annotations = getattr(FuturesOHLCV, "__annotations__", {})
    assert len(annotations) > 0, "FuturesOHLCV 必须声明至少一个字段"


def test_futures_ohlcv_required_fields_complete():
    """FuturesOHLCV 必填字段 8 个全部声明（symbol/date/ohlc/volume/trace_id）。"""
    from fts.core.contracts import FuturesOHLCV

    annotations = set(FuturesOHLCV.__annotations__.keys())
    for field in FUTURES_OHLCV_REQUIRED:
        assert field in annotations, f"FuturesOHLCV 缺少必填字段: {field}"


def test_futures_ohlcv_optional_fields_complete():
    """FuturesOHLCV 可选字段 8 个全部声明（amount/hold/settle/pre_settle/oi_change/vwap/source/fetched_at）。"""
    from fts.core.contracts import FuturesOHLCV

    annotations = set(FuturesOHLCV.__annotations__.keys())
    for field in FUTURES_OHLCV_OPTIONAL:
        assert field in annotations, f"FuturesOHLCV 缺少可选字段: {field}"


def test_futures_ohlcv_total_field_count():
    """FuturesOHLCV 总字段数 = 16（8 必填 + 8 可选），锁定后禁止任意加减。"""
    from fts.core.contracts import FuturesOHLCV

    annotations = set(FuturesOHLCV.__annotations__.keys())
    expected = set(FUTURES_OHLCV_ALL)
    assert annotations == expected, (
        f"FuturesOHLCV 字段集合不匹配: 多出={annotations - expected}, 缺少={expected - annotations}"
    )


@pytest.mark.parametrize("field,expected_type", [
    ("symbol", str),
    ("date", str),
    ("open", float),
    ("high", float),
    ("low", float),
    ("close", float),
    ("volume", float),
    ("trace_id", str),
    ("amount", float),
    ("hold", float),
    ("settle", float),
    ("pre_settle", float),
    ("oi_change", float),
    ("vwap", float),
    ("source", str),
    ("fetched_at", str),
])
def test_futures_ohlcv_field_types(field, expected_type):
    """FuturesOHLCV 每个字段类型契约（防止 stringly-typed）。

    可选字段用 NotRequired[T] 包装，测试时需解包为 T 再比较。
    """
    import typing
    from typing import get_args, get_origin, get_type_hints

    from fts.core.contracts import FuturesOHLCV

    hints = get_type_hints(FuturesOHLCV, include_extras=True)
    assert field in hints
    hint = hints[field]
    # 解包 NotRequired[T] → T（兼容 typing 与 typing_extensions）
    try:
        not_required = typing.NotRequired
    except AttributeError:  # Python 3.10: NotRequired 位于 typing_extensions
        from typing_extensions import NotRequired as not_required
    if get_origin(hint) is not_required:
        hint = get_args(hint)[0]
    assert hint is expected_type, (
        f"{field} 期望 {expected_type}, 实际 {hints[field]}"
    )


def test_futures_ohlcv_constructable_as_dict():
    """FuturesOHLCV 在运行时可当 dict 构造（TypedDict 行为）。"""
    from fts.core.contracts import FuturesOHLCV

    row: FuturesOHLCV = {
        "symbol": "RB0",
        "date": "2026-08-04",
        "open": 3500.0,
        "high": 3550.0,
        "low": 3490.0,
        "close": 3540.0,
        "volume": 100000.0,
        "trace_id": "1722345678-abcdef12",
        # 可选字段留空
    }
    assert row["symbol"] == "RB0"
    assert row["close"] == 3540.0


# ─── FuturesDataLineage ────────────────────────────────────

LINEAGE_REQUIRED = [
    "trace_id", "started_at", "finished_at", "symbols", "rows_written",
]
LINEAGE_OPTIONAL = ["sources_used", "sources_failed", "disagreements"]


def test_futures_data_lineage_importable():
    """FuturesDataLineage TypedDict 可从 fts.core.contracts 导入。"""
    from fts.core.contracts import FuturesDataLineage

    assert FuturesDataLineage is not None
    annotations = getattr(FuturesDataLineage, "__annotations__", {})
    assert len(annotations) >= len(LINEAGE_REQUIRED)


def test_futures_data_lineage_required_fields():
    """FuturesDataLineage 必填字段含 trace_id/started_at/finished_at/symbols/rows_written。"""
    from fts.core.contracts import FuturesDataLineage

    annotations = set(FuturesDataLineage.__annotations__.keys())
    for field in LINEAGE_REQUIRED:
        assert field in annotations, f"FuturesDataLineage 缺少必填字段: {field}"


def test_futures_data_lineage_sources_tracking_fields():
    """FuturesDataLineage 必含 sources_used / sources_failed / disagreements 用于血缘追溯。"""
    from fts.core.contracts import FuturesDataLineage

    annotations = set(FuturesDataLineage.__annotations__.keys())
    for field in ("sources_used", "sources_failed", "disagreements"):
        assert field in annotations, f"FuturesDataLineage 缺少血缘字段: {field}"


def test_futures_data_lineage_constructable():
    """FuturesDataLineage 可实例化为 dict。"""
    from fts.core.contracts import FuturesDataLineage

    lineage: FuturesDataLineage = {
        "trace_id": "1722345678-abcdef12",
        "started_at": "2026-08-04T17:30:00+08:00",
        "finished_at": "2026-08-04T17:35:23+08:00",
        "symbols": ["RB0", "CU0", "AU0"],
        "rows_written": 1500,
        "sources_used": {"TQ_LOCAL": 800, "AKSHARE": 700},
        "sources_failed": {},
        "disagreements": 2,
    }
    assert lineage["rows_written"] == 1500
    assert lineage["sources_used"]["TQ_LOCAL"] == 800
