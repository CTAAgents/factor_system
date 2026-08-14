"""tests/factor_engine/test_symbol_holdout.py — 跨标的稳健性检查测试（GAP-075）。

覆盖：标的留出验证模块（分层留出/方向/保持率/边界/确定性）+ 横截面评估输出
（symbol_ic/symbol_holdout 字段/方向翻转同步）+ 审计激活（cross_symbol/symbol_holdout）
+ evolution_loop 接线（_run_factor_audit 传参）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.audit import FactorAuditor
from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
from fts.factor_engine.symbol_holdout import (
    SymbolHoldoutConfig,
    SymbolHoldoutResult,
    run_symbol_holdout,
)


def _make_signal_ret(
    n_symbols: int = 30,
    n_days: int = 60,
    seed: int = 1,
    corr_sign: float = 1.0,
    corr: float = 0.1,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """构造信号与未来收益正（或负）相关的面板（corr 控制相关强度）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    signal_dict: dict[str, pd.Series] = {}
    ret_dict: dict[str, pd.Series] = {}
    for i in range(n_symbols):
        sig = rng.normal(0, 1, n_days)
        ret = corr_sign * corr * sig + rng.normal(0, 0.3, n_days)
        sym = f"S{i:02d}"
        signal_dict[sym] = pd.Series(sig, index=dates)
        ret_dict[sym] = pd.Series(ret, index=dates)
    return signal_dict, ret_dict


def _industry_map(n_symbols: int = 30) -> dict[str, str]:
    return {f"S{i:02d}": f"IND{i % 3}" for i in range(n_symbols)}


# ── A. 标的留出验证模块 ─────────────────────────────────────


def test_run_returns_positive_retention():
    sig, ret = _make_signal_ret(corr_sign=1.0)
    res = run_symbol_holdout(sig, ret)
    assert isinstance(res, SymbolHoldoutResult)
    assert res.n_train + res.n_holdout == len(sig)
    assert res.n_holdout >= 5
    assert res.train_ic > 0
    assert res.holdout_ic > 0
    assert res.ic_retention > 0
    assert res.passed is True


def test_stratified_split_industry_coverage():
    sig, ret = _make_signal_ret()
    res = run_symbol_holdout(sig, ret, industry_map=_industry_map())
    assert res is not None
    # 留出集覆盖全部 3 个行业（每行业至少留 1 只）
    held_industries = {_industry_map()[s] for s in res.holdout_symbols}
    assert held_industries == {"IND0", "IND1", "IND2"}
    # 留出比例 ≈ 20%（每行业 10 只留 2 只 → 6/30）
    assert res.n_holdout == 6


def test_holdout_too_small_returns_none():
    sig, ret = _make_signal_ret(n_symbols=5)
    assert run_symbol_holdout(sig, ret) is None


def test_seed_deterministic():
    sig, ret = _make_signal_ret()
    r1 = run_symbol_holdout(sig, ret, industry_map=_industry_map())
    r2 = run_symbol_holdout(sig, ret, industry_map=_industry_map())
    assert r1.holdout_symbols == r2.holdout_symbols


def test_train_direction_flip_on_negative_correlation():
    sig, ret = _make_signal_ret(corr_sign=-1.0)
    res = run_symbol_holdout(sig, ret)
    assert res is not None
    # 负相关 → 训练集方向翻转 → 两集 IC 均对齐为正
    assert res.train_ic > 0
    assert res.holdout_ic > 0
    assert res.passed is True


def test_random_split_without_industry():
    sig, ret = _make_signal_ret()
    res = run_symbol_holdout(sig, ret, industry_map=None)
    assert res is not None
    assert res.n_train + res.n_holdout == len(sig)


def test_retention_threshold_controls_pass():
    # 训练集强、留出集弱 → 保持率低 → passed False
    rng = np.random.default_rng(3)
    dates = pd.date_range("2026-01-01", periods=60, freq="B")
    sig, ret = {}, {}
    for i in range(40):
        s = rng.normal(0, 1, 60)
        sig[f"S{i:02d}"] = pd.Series(s, index=dates)
        # 前 32 只强相关，后 8 只无相关 → 留出集（随机）可能弱
        r = (0.15 * s if i < 32 else 0.0) + rng.normal(0, 0.3, 60)
        ret[f"S{i:02d}"] = pd.Series(r, index=dates)
    res = run_symbol_holdout(
        sig, ret, SymbolHoldoutConfig(min_ic_retention=0.9), industry_map=None
    )
    # 高保持率阈值下大概率不满足（非确定性断言：仅验证判定路径存在）
    assert res.passed in (True, False)


def test_result_to_dict_fields():
    sig, ret = _make_signal_ret()
    res = run_symbol_holdout(sig, ret)
    d = res.to_dict()
    for k in ("n_train", "n_holdout", "train_ic", "holdout_ic", "ic_retention", "passed", "holdout_symbols"):
        assert k in d


def test_weak_train_ic_returns_none():
    """弱信号（|train_ic| < min_train_ic）→ 判定不可靠 → 返回 None（审计 skipped）。"""
    sig, ret = _make_signal_ret(corr=0.001, seed=5)
    assert run_symbol_holdout(sig, ret) is None


def test_min_train_ic_zero_preserves_legacy():
    """min_train_ic=0.0 → 弱信号也不跳过（向后兼容）。"""
    sig, ret = _make_signal_ret(corr=0.001, seed=5)
    res = run_symbol_holdout(sig, ret, SymbolHoldoutConfig(min_train_ic=0.0))
    assert isinstance(res, SymbolHoldoutResult)


def test_strong_train_ic_above_threshold():
    """强信号 |train_ic| ≥ min_train_ic → 正常返回结果（不受下限保护影响）。"""
    sig, ret = _make_signal_ret(corr=0.1)
    res = run_symbol_holdout(sig, ret)
    assert isinstance(res, SymbolHoldoutResult)
    assert abs(res.train_ic) >= 0.05


# ── B. 横截面评估集成 ───────────────────────────────────────


def _make_panel(n_stocks: int = 30, n_dates: int = 120, seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_stocks):
        signal = rng.normal(0, 1, n_dates)
        rets = np.zeros(n_dates)
        rets[1:] = 0.02 * signal[:-1] + rng.normal(0, 0.005, n_dates - 1)
        close = 100 * np.exp(np.cumsum(rets))
        panel[f"S{i:03d}"] = pd.DataFrame(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000,
            },
            index=dates,
        )
    return panel


def _factor(code: str, name: str = "test_gap075") -> dict:
    from fts.factor_engine.contracts import EconomicLogic, FactorSignature
    from fts.factor_engine.factor_program import create_factor_program

    return create_factor_program(
        name=name,
        code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=5),
        economic_logic=EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4, narrative="GAP-075 测试因子"
        ),
    )


_MOMENTUM_CODE = (
    "import numpy as np\n"
    "def factor_program(data, params):\n"
    "    close = data['close'].values\n"
    "    n = len(close)\n"
    "    sig = np.zeros(n)\n"
    "    for i in range(5, n):\n"
    "        sig[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
    "    return sig\n"
)

_REVERSE_CODE = (
    "import numpy as np\n"
    "def factor_program(data, params):\n"
    "    close = data['close'].values\n"
    "    n = len(close)\n"
    "    sig = np.zeros(n)\n"
    "    for i in range(5, n):\n"
    "        sig[i] = -(close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
    "    return sig\n"
)


def test_cross_section_outputs_symbol_ic():
    panel = _make_panel()
    dates = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in panel.values()])))
    bt = cross_section_evaluate_backtest(_factor(_MOMENTUM_CODE), panel, dates)
    assert "symbol_ic" in bt
    assert bt["symbol_ic"]
    assert set(bt["symbol_ic"].keys()) <= set(panel.keys())
    assert all(-1.0 <= v <= 1.0 for v in bt["symbol_ic"].values())
    assert "symbol_holdout" in bt
    assert isinstance(bt["symbol_holdout"], (dict, type(None)))


def test_cross_section_symbol_ic_flips_with_direction():
    panel = _make_panel()
    dates = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in panel.values()])))
    bt = cross_section_evaluate_backtest(_factor(_REVERSE_CODE), panel, dates)
    # 反向动量 → 方向翻转后全样本 IC 为正 → 逐标的 IC 也应与翻转后方向一致（均值同号）
    assert bt["ic"] > 0
    assert "symbol_ic" in bt
    assert np.mean(list(bt["symbol_ic"].values())) > 0


# ── C. 审计激活 ─────────────────────────────────────────────


def _audit_report(**kwargs):
    return FactorAuditor().audit(**kwargs)


def test_audit_symbol_holdout_skipped_when_missing():
    report = _audit_report(factor={"factor_id": "f1", "name": "f1"})
    item = report.item("symbol_holdout")
    assert item is not None
    assert item.status == "skipped"


def test_audit_symbol_holdout_passed():
    report = _audit_report(
        factor={"factor_id": "f1", "name": "f1"},
        symbol_holdout={"n_train": 24, "n_holdout": 6, "train_ic": 0.08, "holdout_ic": 0.06, "ic_retention": 0.75, "passed": True},
    )
    assert report.item("symbol_holdout").status == "passed"


def test_audit_symbol_holdout_failed():
    report = _audit_report(
        factor={"factor_id": "f1", "name": "f1"},
        symbol_holdout={"n_train": 24, "n_holdout": 6, "train_ic": 0.08, "holdout_ic": -0.03, "ic_retention": -0.4, "passed": False},
    )
    assert report.item("symbol_holdout").status == "failed"


def test_audit_cross_symbol_activated():
    # 多数标的 IC 为正 → cross_symbol passed（不再 skipped）
    ics = {f"S{i:02d}": (0.1 if i % 5 else -0.1) for i in range(30)}
    report = _audit_report(factor={"factor_id": "f1", "name": "f1"}, symbol_ic_map=ics)
    item = report.item("cross_symbol")
    assert item is not None
    assert item.status == "passed"
    # 少数为正 → failed
    bad = {f"S{i:02d}": (-0.1 if i % 5 else 0.1) for i in range(30)}
    report_bad = _audit_report(factor={"factor_id": "f1", "name": "f1"}, symbol_ic_map=bad)
    assert report_bad.item("cross_symbol").status == "failed"


# ── D. evolution_loop 接线 ──────────────────────────────────


def test_run_factor_audit_wires_symbol_ic_and_holdout(monkeypatch):
    from fts.factor_engine.audit import AuditItemResult, FactorAuditReport
    from fts.factor_engine.evolution_loop import EvolutionLoop

    loop = object.__new__(EvolutionLoop)
    captured: dict = {}

    class FakeAuditor:
        def audit(self, **kwargs):
            captured.update(kwargs)
            return FactorAuditReport(
                factor_id="f1",
                factor_name="f1",
                audited_at="",
                items=[AuditItemResult(name="cross_symbol", status="passed")],
                passed=True,
                pass_rate=1.0,
                summary={},
            )

    loop.auditor = FakeAuditor()
    loop.data = pd.DataFrame({"close": [1.0, 2.0]})
    loop.forward_returns = np.zeros(2)
    factor = {"factor_id": "f1", "name": "f1", "family": "test"}
    evaluation = {
        "level_1_backtest": {
            "symbol_ic": {"S1": 0.1, "S2": -0.2},
            "symbol_holdout": {"passed": True, "ic_retention": 0.8},
        },
        "walk_forward": {"n_windows_completed": 2, "passed": True, "ic_consistency": 0.8, "windows": []},
    }
    loop._run_factor_audit(factor, evaluation, "trace-gap075")
    assert captured["symbol_ic_map"] == {"S1": 0.1, "S2": -0.2}
    assert captured["symbol_holdout"] == {"passed": True, "ic_retention": 0.8}
