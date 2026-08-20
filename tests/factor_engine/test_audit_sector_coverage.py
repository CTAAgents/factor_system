"""GAP-160 (v3.0.0+7): 盲测池 symbol_holdout + cross_symbol 板块覆盖率通道测试。

覆盖:
- _cs_sector_coverage 板块覆盖率统计（正 IC 比例 ≥50% 板块计数）
- _cs_blind_holdout_metrics 盲测池 retention 判定（含弱信号 skipped）
- cross_section_evaluate_backtest 盲测池模式产出（blind_pool 标记）/ 回退旧路径
- FactorAuditor._check_cross_symbol 机制4（板块覆盖率）通过/不通过/缺失降级
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fts.factor_engine.audit import FactorAuditConfig, FactorAuditor
from fts.factor_engine.evaluation_chain import (
    _cs_blind_holdout_metrics,
    _cs_sector_coverage,
    _cs_symbol_ts_ics,
    cross_section_evaluate_backtest,
)
from fts.factor_engine.symbol_holdout import SymbolHoldoutConfig


def _panel(n_stocks: int, n_dates: int, seed: int = 7) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    """合成 OHLCV panel（与 test_evaluation_chain 同款）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-09-01", periods=n_dates, freq="D")
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_stocks):
        close = 100 + i * 5.0 + np.cumsum(rng.standard_normal(n_dates) * 0.5)
        panel[f"SYM_{i}"] = pd.DataFrame(
            {
                "open": close + rng.standard_normal(n_dates) * 0.1,
                "high": close + np.abs(rng.standard_normal(n_dates)) * 0.3,
                "low": close - np.abs(rng.standard_normal(n_dates)) * 0.3,
                "close": close,
                "volume": rng.integers(1000, 10000, n_dates).astype(float),
            },
            index=dates,
        )
    return panel, dates


def _make_factor() -> Any:
    from fts.factor_engine.contracts import EconomicLogic, FactorSignature
    from fts.factor_engine.factor_program import create_factor_program

    return create_factor_program(
        name="cs_blind_mom",
        code=(
            "import numpy as np\n"
            "def factor_program(data, params):\n"
            "    close = data['close'].values\n"
            "    n = len(close)\n"
            "    sig = np.zeros(n)\n"
            "    for i in range(5, n):\n"
            "        sig[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
            "    return np.clip(sig * 10, -1.0, 1.0)\n"
        ),
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        economic_logic=EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4, narrative="GAP-160 盲测池测试"
        ),
        source="manual",
    )


# ─── _cs_sector_coverage ─────────────────────────────────


class TestCsSectorCoverage:
    def test_basic_counting(self):
        """板块内品种 IC 正比例 ≥50% 计数为 covered。"""
        symbol_ic = {
            "RB0": 0.05, "I0": 0.03, "J0": -0.02,   # 黑色 2/3 正 → covered
            "CU0": 0.04, "ZN0": -0.01, "NI0": 0.02,  # 有色 2/3 正 → covered
            "TA0": -0.03, "MA0": -0.02, "SC0": 0.01,  # 化工 1/3 正 → 不 covered
            "AU0": 0.02, "AG0": -0.01,               # 贵金属 1/2 正 → covered
            "IF0": 0.03, "IC0": -0.02, "IH0": 0.01,  # 金融 2/3 正 → covered
        }
        industry_map = {
            "RB0": "黑色", "I0": "黑色", "J0": "黑色",
            "CU0": "有色", "ZN0": "有色", "NI0": "有色",
            "TA0": "化工", "MA0": "化工", "SC0": "化工",
            "AU0": "贵金属", "AG0": "贵金属",
            "IF0": "金融", "IC0": "金融", "IH0": "金融",
        }
        res = _cs_sector_coverage(symbol_ic, industry_map)
        assert res == {"covered_sectors": 4, "total_sectors": 5}

    def test_no_industry_map_returns_none(self):
        assert _cs_sector_coverage({"RB0": 0.1}, None) is None

    def test_empty_symbol_ic_returns_none(self):
        assert _cs_sector_coverage({}, {"RB0": "黑色"}) is None

    def test_all_negative_sector_not_covered(self):
        symbol_ic = {"RB0": -0.1, "I0": -0.1, "J0": -0.1}
        industry_map = {"RB0": "黑色", "I0": "黑色", "J0": "黑色"}
        res = _cs_sector_coverage(symbol_ic, industry_map)
        assert res == {"covered_sectors": 0, "total_sectors": 1}


# ─── _cs_blind_holdout_metrics ──────────────────────────


class TestBlindHoldoutMetrics:
    def test_pass_when_retention_high(self):
        cfg = SymbolHoldoutConfig()
        res = _cs_blind_holdout_metrics(
            train_ics=[0.06, 0.10, 0.08],
            holdout_ics=[0.04, 0.06, 0.05],
            cfg=cfg,
            holdout_symbols=["JM0", "AL0", "PB0"],
        )
        assert res is not None
        assert res["passed"] is True
        assert res["n_holdout"] == 3
        assert res["detail"]["blind_pool"] is True

    def test_fail_when_retention_low(self):
        cfg = SymbolHoldoutConfig()
        res = _cs_blind_holdout_metrics(
            train_ics=[0.10, 0.11, 0.12],
            holdout_ics=[0.01, 0.02, 0.03],
            cfg=cfg,
            holdout_symbols=["JM0", "AL0"],
        )
        assert res is not None
        assert res["passed"] is False
        assert res["ic_retention"] < 0.5

    def test_fail_when_holdout_ic_negative(self):
        cfg = SymbolHoldoutConfig()
        res = _cs_blind_holdout_metrics(
            train_ics=[0.08, 0.09],
            holdout_ics=[-0.01, 0.01],
            cfg=cfg,
            holdout_symbols=["JM0"],
        )
        assert res is not None
        assert res["passed"] is False

    def test_weak_train_ic_skipped(self):
        """弱信号（|train_ic| < min_train_ic）→ None（审计 skipped，GAP-116）。"""
        cfg = SymbolHoldoutConfig()
        res = _cs_blind_holdout_metrics(
            train_ics=[0.02, 0.03],
            holdout_ics=[0.05, 0.06],
            cfg=cfg,
            holdout_symbols=["JM0"],
        )
        assert res is None

    def test_empty_ics_returns_none(self):
        cfg = SymbolHoldoutConfig()
        assert _cs_blind_holdout_metrics([], [0.1], cfg, ["JM0"]) is None
        assert _cs_blind_holdout_metrics([0.1], [], cfg, ["JM0"]) is None


# ─── _cs_symbol_ts_ics ──────────────────────────────────


class TestSymbolTsIcs:
    def test_direction_flip(self):
        """direction=-1 时全部 IC 取反。"""
        s = pd.Series(np.linspace(0, 1, 100))
        r = pd.Series(np.linspace(0, 1, 100))  # 与信号强正相关
        ics = _cs_symbol_ts_ics({"A": s}, {"A": r}, direction=1.0)
        ics_flip = _cs_symbol_ts_ics({"A": s}, {"A": r}, direction=-1.0)
        assert ics[0] > 0.9
        assert abs(ics_flip[0] + ics[0]) < 1e-9

    def test_no_common_window_needed(self):
        """不同长度序列各自独立算 IC（晚上市品种仅用自身有效期）。"""
        s_short = pd.Series(np.linspace(0, 1, 30))
        r_short = pd.Series(np.linspace(0, 1, 30))
        s_long = pd.Series(np.linspace(0, 1, 100))
        r_long = pd.Series(np.linspace(0, 1, 100))
        ics = _cs_symbol_ts_ics({"SHORT": s_short, "LONG": s_long}, {"SHORT": r_short, "LONG": r_long})
        assert len(ics) == 2
        assert all(v > 0.9 for v in ics)


# ─── cross_section_evaluate_backtest 盲测池模式 ─────────


class TestEvaluateBlindPool:
    def test_blind_pool_mode_outputs(self):
        """传 holdout_panel_data → symbol_holdout 带 blind_pool 标记 + 板块覆盖率产出。"""
        panel, dates = _panel(6, n_dates=120)
        holdout_panel, _ = _panel(3, n_dates=120, seed=21)
        industry_map = {sym: f"SEC_{i % 3}" for i, sym in enumerate(panel)}
        bt = cross_section_evaluate_backtest(
            _make_factor(),
            panel,
            dates,
            industry_map=industry_map,
            holdout_panel_data=holdout_panel,
        )
        # 板块覆盖率指标恒产出（sector_coverage 供 cross_symbol 软门控D）
        assert "cross_symbol_sector_coverage" in bt
        sc = bt["cross_symbol_sector_coverage"]
        assert sc is None or sc["total_sectors"] >= 1
        # symbol_holdout：盲测池模式（弱信号/数据不足时 None，但不走旧路径留出）
        ho = bt.get("symbol_holdout")
        assert ho is None or ho.get("detail", {}).get("blind_pool") is True

    def test_fallback_without_holdout_panel(self):
        """不传 holdout_panel_data → 回退训练池内留出路径（无 blind_pool 标记）。"""
        panel, dates = _panel(12, n_dates=200)
        bt = cross_section_evaluate_backtest(_make_factor(), panel, dates)
        ho = bt.get("symbol_holdout")
        # 旧路径 run_symbol_holdout 产出无 blind_pool 字段（或 None 若样本不足）
        assert ho is None or "blind_pool" not in (ho.get("detail") or {})

    def test_use_blind_pool_false_fallback(self):
        """SymbolHoldoutConfig.use_blind_pool=false 时即使传盲测池也走旧路径。"""
        panel, dates = _panel(12, n_dates=200)
        holdout_panel, _ = _panel(3, n_dates=200, seed=33)
        old = SymbolHoldoutConfig.use_blind_pool
        SymbolHoldoutConfig.use_blind_pool = False
        try:
            bt = cross_section_evaluate_backtest(
                _make_factor(), panel, dates, holdout_panel_data=holdout_panel
            )
        finally:
            SymbolHoldoutConfig.use_blind_pool = old
        ho = bt.get("symbol_holdout")
        assert ho is None or "blind_pool" not in (ho.get("detail") or {})


# ─── FactorAuditor._check_cross_symbol 机制4 ────────────


class TestCrossSymbolSectorChannel:
    def _weak_symbol_ic_map(self) -> dict[str, float]:
        """11/19 品种 IC 为正（0.579）——主防线/软门控A/binomial 均不过。"""
        ics: dict[str, float] = {}
        for i in range(11):
            ics[f"S{i}"] = 0.02
        for i in range(8):
            ics[f"N{i}"] = -0.02
        return ics

    def test_sector_channel_passes(self):
        """主防线不足但板块覆盖率 ≥5/7 → 通过（机制4）。"""
        auditor = FactorAuditor(FactorAuditConfig())
        res = auditor._check_cross_symbol(
            self._weak_symbol_ic_map(),
            {"covered_sectors": 5, "total_sectors": 7},
        )
        assert res.status == "passed"
        assert "sector_coverage" in res.details["mechanisms"]

    def test_sector_channel_insufficient(self):
        """板块覆盖不足（3/7 < 5）且无其他通道 → failed。"""
        auditor = FactorAuditor(FactorAuditConfig())
        res = auditor._check_cross_symbol(
            self._weak_symbol_ic_map(),
            {"covered_sectors": 3, "total_sectors": 7},
        )
        assert res.status == "failed"

    def test_sector_channel_missing_degrades(self):
        """sector_coverage 缺失 → 不启用机制4，行为与旧版一致。"""
        auditor = FactorAuditor(FactorAuditConfig())
        res = auditor._check_cross_symbol(self._weak_symbol_ic_map(), None)
        assert res.status == "failed"

    def test_audit_entrypoint_passes_sector(self):
        """audit() 入口透传 sector_coverage。"""
        auditor = FactorAuditor(FactorAuditConfig())
        report = auditor.audit(
            factor={"factor_id": "fct_t", "name": "t"},
            symbol_ic_map=self._weak_symbol_ic_map(),
            sector_coverage={"covered_sectors": 6, "total_sectors": 7},
        )
        cross = next(i for i in report.items if i.name == "cross_symbol")
        assert cross.status == "passed"

    def test_sector_channel_respects_config_threshold(self):
        """min_sector_coverage=6 时 5/7 不通过；=5 时通过。"""
        strict = FactorAuditor(FactorAuditConfig(min_sector_coverage=6))
        res_strict = strict._check_cross_symbol(
            self._weak_symbol_ic_map(),
            {"covered_sectors": 5, "total_sectors": 7},
        )
        assert res_strict.status == "failed"

        loose = FactorAuditor(FactorAuditConfig(min_sector_coverage=5))
        res_loose = loose._check_cross_symbol(
            self._weak_symbol_ic_map(),
            {"covered_sectors": 5, "total_sectors": 7},
        )
        assert res_loose.status == "passed"
