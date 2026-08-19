"""tests/factor_engine/test_regime_profile.py — Regime 画像与制度序列构建测试（plans/53 §A/§D）。

HARNESS §测试随重构。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.regime_profile import (
    RegimeProfileConfig,
    RegimeSeriesBuilder,
    build_regime_metadata,
    compute_regime_profile,
    regime_gate_passed,
)

REGIMES = ("bull", "bear", "oscillate", "high_vol", "low_vol")
_N_PER_REGIME = 60  # ≥ 2×block_size=40，确保 ICIR 有 ≥2 个块


def _make_factor(active: tuple[str, ...] = REGIMES, seed: int = 7) -> pd.DataFrame:
    """构造因子面板：active 制度内 signal 与 fwd 正相关，其余制度为噪声。

    Returns:
        DataFrame[regime, signal, fwd]
    """
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for r in REGIMES:
        fwd = rng.normal(0.02, 1.0, _N_PER_REGIME)  # 正偏移，win_rate > 0.5
        if r in active:
            sig = fwd * 0.8 + rng.normal(0, 0.4, _N_PER_REGIME)
        else:
            # 确定性零方差信号（Spearman/相关系数 → NaN → ic=0）：
            # 避免随机序列在 60 样本下偶然相关（实测可达 0.1）导致测试脆弱
            sig = np.zeros(_N_PER_REGIME)
        for i in range(_N_PER_REGIME):
            rows.append({"regime": r, "signal": sig[i], "fwd": fwd[i]})
    return pd.DataFrame(rows)


def test_single_regime_effective():
    """仅 1 制度 effective → scope=[该制度]。"""
    df = _make_factor(active=("bull",))
    prof = compute_regime_profile("f1", df["signal"].to_numpy(), df["fwd"].to_numpy(), df["regime"].to_numpy())
    assert prof.regime_scope == ["bull"]
    assert prof.regime_stats["bull"].effective is True
    # 其余制度噪声 → 不 effective
    assert prof.regime_stats["oscillate"].effective is False


def test_multi_regime_scope():
    """2 制度 effective → scope 含 2 制度。"""
    df = _make_factor(active=("bull", "bear"))
    prof = compute_regime_profile("f1", df["signal"].to_numpy(), df["fwd"].to_numpy(), df["regime"].to_numpy())
    assert sorted(prof.regime_scope) == ["bear", "bull"]
    assert prof.regime_stats["bull"].effective is True
    assert prof.regime_stats["bear"].effective is True
    assert prof.regime_stats["low_vol"].effective is False


def test_scope_all():
    """全 5 制度 effective → scope='all'。"""
    df = _make_factor()
    prof = compute_regime_profile("f1", df["signal"].to_numpy(), df["fwd"].to_numpy(), df["regime"].to_numpy())
    assert prof.regime_scope == "all"
    assert all(st.effective for st in prof.regime_stats.values())


def test_scope_unknown():
    """全部噪声 → scope='unknown'（不误标）。"""
    df = _make_factor(active=())
    prof = compute_regime_profile("f1", df["signal"].to_numpy(), df["fwd"].to_numpy(), df["regime"].to_numpy())
    assert prof.regime_scope == "unknown"
    assert all(not st.effective for st in prof.regime_stats.values())


def test_win_rate_computed():
    """胜率口径：fwd>0 样本占比。"""
    df = _make_factor(active=("bull",))
    prof = compute_regime_profile("f1", df["signal"].to_numpy(), df["fwd"].to_numpy(), df["regime"].to_numpy())
    fwd = df[df["regime"] == "bull"]["fwd"].to_numpy()
    expected = float((fwd > 0).mean())
    assert prof.regime_stats["bull"].win_rate is not None
    assert abs(prof.regime_stats["bull"].win_rate - expected) < 1e-6


def test_regime_dependent_flag():
    """bear 制度 ICIR 明显为负 → regime_dependent=True。"""
    df = _make_factor(active=("bull", "bear", "oscillate", "high_vol"))
    mask = df["regime"] == "bear"
    df.loc[mask, "signal"] = -df.loc[mask, "signal"]  # 反转 bear 关系
    prof = compute_regime_profile("f1", df["signal"].to_numpy(), df["fwd"].to_numpy(), df["regime"].to_numpy())
    assert prof.regime_dependent is True
    # 其余 4 制度仍 effective → scope='all'
    assert prof.regime_scope == "all"


def test_min_abs_ic_threshold():
    """min_abs_ic 抬高 → 弱相关制度不 effective（门槛③生效）。"""
    df = _make_factor(active=("bull", "high_vol", "oscillate", "low_vol"))
    df = df[df["regime"] != "bear"].reset_index(drop=True)
    prof_loose = compute_regime_profile("f1", df["signal"].to_numpy(), df["fwd"].to_numpy(), df["regime"].to_numpy())
    assert prof_loose.regime_scope == "all"
    prof_strict = compute_regime_profile(
        "f1",
        df["signal"].to_numpy(),
        df["fwd"].to_numpy(),
        df["regime"].to_numpy(),
        RegimeProfileConfig(min_abs_ic=0.99),  # 门槛抬到超过真实 |IC| → 全制度不通过
    )
    assert prof_strict.regime_scope == "unknown"


def test_mismatched_lengths_unknown():
    """长度不一致 → 安全返回 unknown（不抛异常）。"""
    prof = compute_regime_profile("f1", np.array([1.0, 2.0]), np.array([1.0]), np.array(["bull"]))
    assert prof.regime_scope == "unknown"
    assert prof.regime_stats == {}


def test_metadata_persisted():
    """build_regime_metadata 输出落库字段（JSON/DuckDB 安全）。"""
    df = _make_factor(active=("bull",))
    md = build_regime_metadata("f1", df["signal"].to_numpy(), df["fwd"].to_numpy(), df["regime"].to_numpy())
    assert "regime_ic_profile" in md
    assert "regime_scope" in md
    assert "regime_dependent" in md
    assert md["regime_scope"] == ["bull"]
    bull = md["regime_ic_profile"]["bull"]
    assert {"n", "ic", "icir", "win_rate", "effective"} <= set(bull.keys())
    assert bull["effective"] is True
    # t 极端值序列化为 None（JSON 安全）
    assert all(
        (v.get("icir") is None or isinstance(v.get("icir"), float))
        for v in md["regime_ic_profile"].values()
    )


def test_series_builder_synthetic():
    """RegimeSeriesBuilder 滚动检测：合成双制度 OHLCV → 输出序列与窗口语义一致。"""
    rng = np.random.default_rng(42)
    n = 300
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    drift = np.concatenate([np.full(150, 0.003), np.full(150, -0.003)])
    close = 100.0 * np.cumprod(1.0 + drift + rng.normal(0.0, 0.008, n))
    ohlcv = pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.full(n, 1e6),
        },
        index=dates,
    )
    builder = RegimeSeriesBuilder(lookback_days=60, step_days=10)
    series = builder.build_from_ohlcv(ohlcv)
    assert len(series) == (n - 60) // 10
    assert isinstance(series.index, pd.DatetimeIndex)
    assert set(series.unique()) <= {"bull", "bear", "oscillate", "high_vol", "low_vol"}
    # 前段上涨 → bull/oscillate 类，后段下跌 → bear 类（至少出现两种标签）
    assert len(series.unique()) >= 2


def test_series_builder_insufficient():
    """OHLCV 不足窗口 → 空序列。"""
    ohlcv = pd.DataFrame(
        {"open": [1.0] * 10, "high": [1.0] * 10, "low": [1.0] * 10, "close": [1.0] * 10, "volume": [1.0] * 10}
    )
    builder = RegimeSeriesBuilder(lookback_days=60)
    assert builder.build_from_ohlcv(ohlcv).empty


def test_gate_missing_report_passes():
    """无 regime_ic_report → 放行（向后兼容）。"""
    assert regime_gate_passed(None) is True
    assert regime_gate_passed({}) is True


def test_gate_single_regime_rejected():
    """仅 1 制度有效（<min_positive_regimes=2）→ 拦截。"""
    report = {"regime_scope": ["bull"], "regime_ic_profile": {"bull": {}}}
    assert regime_gate_passed(report, 2) is False


def test_gate_two_regimes_passes():
    """2 制度有效（≥min_positive_regimes=2）→ 放行。"""
    report = {"regime_scope": ["bull", "bear"], "regime_ic_profile": {}}
    assert regime_gate_passed(report, 2) is True


def test_gate_scope_all_passes():
    """scope='all' → 放行。"""
    assert regime_gate_passed({"regime_scope": "all"}, 2) is True


def test_gate_scope_unknown_passes():
    """scope='unknown' → 放行（保守性设计，不误杀数据不足因子）。"""
    assert regime_gate_passed({"regime_scope": "unknown"}, 2) is True


def test_gate_min_positive_regimes_param():
    """min_positive_regimes 参数化生效。"""
    report = {"regime_scope": ["bull", "bear"], "regime_ic_profile": {}}
    assert regime_gate_passed(report, 3) is False  # 2 < 3 → 拦截
    assert regime_gate_passed(report, 2) is True


class TestRegimeReportInEvalChain:
    """plans/53 §A2：评估链 regime_ic_report 报告段（开关控制 + energy 面板限定）。"""

    @staticmethod
    def _energy_panel(n_dates: int = 300, seed: int = 5):
        """构造 symbol 命中 ENERGY_CHAIN_SYMBOLS 的合成面板。"""
        from fts.data_futures import ENERGY_CHAIN_SYMBOLS

        syms = list(ENERGY_CHAIN_SYMBOLS)[:6]  # ≥5 品种，满足评估链 signal_dict ≥5 门槛
        rng = np.random.default_rng(seed)
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
        panel: dict[str, pd.DataFrame] = {}
        for s in syms:
            close = 100 + np.cumsum(rng.normal(0, 0.5, n_dates))
            panel[s] = pd.DataFrame(
                {
                    "open": close + rng.normal(0, 0.1, n_dates),
                    "high": close + np.abs(rng.normal(0, 0.3, n_dates)),
                    "low": close - np.abs(rng.normal(0, 0.3, n_dates)),
                    "close": close,
                    "volume": rng.integers(1000, 10000, n_dates).astype(float),
                },
                index=dates,
            )
        return panel, dates

    @staticmethod
    def _make_factor(name: str):
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.factor_program import create_factor_program

        return create_factor_program(
            name=name,
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
                theory=4, behavioral=3, microstructure=3, institutional=4, narrative="regime 画像测试"
            ),
            source="manual",
        )

    def test_disabled_no_regime_report(self):
        """开关默认关闭 → 输出不含 regime_ic_report（零行为变更）。"""
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

        panel, dates = self._energy_panel()
        bt = cross_section_evaluate_backtest(self._make_factor("rp_off"), panel, dates)
        assert "regime_ic_report" not in bt

    def test_enabled_energy_produces_report(self, monkeypatch):
        """开关开启 + energy 面板 → 输出含 regime_ic_report（含三个落库字段）。"""
        from fts.config.settings import get_config
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

        _cfg = get_config()
        monkeypatch.setattr(_cfg, "l3_regime_ic_report_enabled", True)
        panel, dates = self._energy_panel()
        bt = cross_section_evaluate_backtest(self._make_factor("rp_on"), panel, dates)
        report = bt.get("regime_ic_report")
        assert report is not None
        assert "regime_ic_profile" in report
        assert "regime_scope" in report
        assert "regime_dependent" in report
