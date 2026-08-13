"""
tests/factor_engine/test_portfolio_loop_adaptive.py — L3 adaptive 权重集成测试（A.3 / v2.56.0）。

覆盖:
    - synthesize_signals mode="adaptive" 基权重 = sharpe_weight
    - PortfolioLoop synthesis_mode="adaptive" 端到端（含 Step 2.5 regime 调整）
    - adaptive_config 默认值 / 自定义 dimension / smoother 参数透传
    - PortfolioLoop.__init__ adaptive_config 缺省回退 DEFAULT_ADAPTIVE_CONFIG
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.adaptive_weight import RegimeSmoother
from fts.factor_engine.contracts import (
    DEFAULT_ADAPTIVE_CONFIG,
    AdaptiveWeightConfig,
)
from fts.factor_engine.portfolio_loop import (
    PortfolioLoop,
    _compute_exposure_scale,
    _power_normalize_probs,
    regime_adaptive_weight_adjustment,
    synthesize_signals,
)
from fts.factor_engine.regime_calibration import StatisticalRegimeCalibrator


@pytest.fixture(autouse=True)
def _no_real_data_source(monkeypatch):
    """统一隔离真实数据源（TqSdk/DuckDB），防止 run() 内 Step 0.5 触碰网络。

    与 test_portfolio_loop_market_ohlcv.py 同模式：拦截 fts.data.FTSDataProvider
    返回合成期货面板（空面板即可，本文件 run() 测试显式传 market_ohlcv）。
    """
    panel = {}
    mock_provider = MagicMock()
    mock_provider.get_futures_panel.return_value = (panel, pd.DatetimeIndex([]))
    mock_cls = MagicMock(return_value=mock_provider)
    monkeypatch.setattr("fts.data.FTSDataProvider", mock_cls)
    # 双保险：任何直接实例化 FuturesDataProvider 的路径也返回 mock
    monkeypatch.setattr("fts.data_futures.FuturesDataProvider", MagicMock())
    return mock_provider


def _factor(fid: str, name: str, sharpe: float = 1.8) -> dict:
    return {
        "factor_id": fid,
        "name": name,
        "sharpe": sharpe,
        "ic": 0.05,
        "turnover": 0.3,
        "decay_6m": 0.05,
        "family": "trend",
        "style_tags": ["momentum"],
        "code": "def f(data, params):\n    return data['close']",
    }


def _factors() -> list[dict]:
    return [_factor("f1", "fut_trend_a", 2.0), _factor("f2", "fut_carry_b", 1.5)]


# ─── synthesize_signals adaptive 模式 ─────────────────────


def test_synthesize_adaptive_base_is_sharpe_weight() -> None:
    """adaptive 模式基权重等于 sharpe_weight（f1 sharpe 2.0 / total 3.5）。"""
    factors = _factors()
    sig_adaptive, _, _ = synthesize_signals(factors, "adaptive")
    sig_sharpe, _, _ = synthesize_signals(factors, "sharpe_weight")

    w_a = {s["factor_id"]: s["weight"] for s in sig_adaptive}
    w_s = {s["factor_id"]: s["weight"] for s in sig_sharpe}
    for fid in w_a:
        assert abs(w_a[fid] - w_s[fid]) < 1e-6
    assert abs(w_a["f1"] - 2.0 / 3.5) < 1e-6
    assert abs(w_a["f2"] - 1.5 / 3.5) < 1e-6


def test_synthesize_adaptive_retains_all() -> None:
    signals, _, _ = synthesize_signals(_factors(), "adaptive")
    assert all(s["retained"] for s in signals)
    assert all(s["orthogonalized"] is False for s in signals)


def test_synthesize_adaptive_empty() -> None:
    assert synthesize_signals([], "adaptive") == ([], 0.0, 0.0)


# ─── AdaptiveWeightConfig 契约 ────────────────────────────


def test_default_adaptive_config() -> None:
    """默认配置: both 维度 + 灵敏平滑档 + clamp [0.5,1.5]。"""
    assert DEFAULT_ADAPTIVE_CONFIG["enabled"] is True
    assert DEFAULT_ADAPTIVE_CONFIG["dimension"] == "both"
    assert DEFAULT_ADAPTIVE_CONFIG["smoother"] == {"alpha": 0.5, "min_days": 2}
    assert DEFAULT_ADAPTIVE_CONFIG["min_clamp"] == 0.5
    assert DEFAULT_ADAPTIVE_CONFIG["max_clamp"] == 1.5


def test_default_adaptive_config_has_probability_mix() -> None:
    """默认配置: 制度概率混合 + 置信度仓位缩放（28-T4）。"""
    cfg = DEFAULT_ADAPTIVE_CONFIG
    assert cfg.get("probability_mix", False) is True
    assert 0.0 < cfg.get("confidence_scale_min", 0.3) <= 1.0


def test_custom_adaptive_config_override() -> None:
    cfg: AdaptiveWeightConfig = AdaptiveWeightConfig(
        enabled=True,
        dimension="style",
        smoother={"alpha": 0.3, "min_days": 5},
    )
    assert cfg["dimension"] == "style"
    assert cfg["smoother"]["alpha"] == 0.3


# ─── PortfolioLoop 端到端 ─────────────────────────────────


def _mock_run_deps(loop: PortfolioLoop) -> None:
    """mock 数据面板与 regime 检测，避免真实数据依赖。"""
    loop._regime_selector = MagicMock()
    loop._regime_selector.detect.return_value = {
        "regime": "bull",
        "confidence": 0.8,
        "detected_at": "2026-08-09T00:00:00",
        "features": {},
        "method": "mock",
    }


def test_portfolio_loop_adaptive_mode_end_to_end(tmp_path) -> None:
    """adaptive 模式端到端: 合成 → regime 调整 → 组合构建。"""
    memory_dir = tmp_path / "portfolio"
    elite_dir = tmp_path / "elite"
    elite_dir.mkdir(parents=True)

    loop = PortfolioLoop(
        memory_dir=str(memory_dir),
        elite_dir=str(elite_dir),
        synthesis_mode="adaptive",
        use_duckdb=False,
        enable_regime_adaptation=True,
    )
    _mock_run_deps(loop)

    # mock load_elite_factors 返回两个因子
    with patch(
        "fts.factor_engine.portfolio_loop.load_elite_factors",
        return_value=_factors(),
    ):
        result = loop.run(
            market_ohlcv=pd.DataFrame(
                {
                    "close": [1.0 + i * 0.01 for i in range(100)],
                    "high": [1.0 + i * 0.01 for i in range(100)],
                    "low": [1.0 + i * 0.005 for i in range(100)],
                    "open": [1.0 + i * 0.01 for i in range(100)],
                    "volume": [1000] * 100,
                },
                index=pd.date_range("2026-01-01", periods=100),
            )
        )

    assert result.status in ("passed", "verifier_warning")
    assert result.n_factors_input == 2


def test_portfolio_loop_adaptive_config_default_fallback() -> None:
    """未传 adaptive_config 时回退 DEFAULT_ADAPTIVE_CONFIG。"""
    loop = PortfolioLoop(synthesis_mode="adaptive")
    assert loop.adaptive_config == DEFAULT_ADAPTIVE_CONFIG


def test_portfolio_loop_custom_adaptive_config() -> None:
    """自定义 adaptive_config 生效。"""
    cfg: AdaptiveWeightConfig = AdaptiveWeightConfig(
        enabled=True,
        dimension="family",
        smoother={"alpha": 0.2, "min_days": 5},
    )
    loop = PortfolioLoop(synthesis_mode="adaptive", adaptive_config=cfg)
    assert loop.adaptive_config["dimension"] == "family"
    assert loop.adaptive_config["smoother"]["alpha"] == 0.2


# ─── regime blend（28-T3：制度概率混合权重）──────────────────


def _make_signals(specs: list[tuple[str, str, float]]) -> list[dict]:
    """构造最小信号列表（regime blend 测试用）。"""
    return [
        {
            "factor_id": fid,
            "name": fid,
            "weight": weight,
            "sharpe": 1.5,
            "ic": 0.05,
            "turnover": 0.3,
            "decay_6m": 0.05,
            "retained": True,
        }
        for fid, _family, weight in specs
    ]


def _make_factors(specs: list[tuple[str, str]]) -> list[dict]:
    """构造最小因子列表（regime blend 测试用）。"""
    return [{"factor_id": fid, "name": fid, "family": family} for fid, family in specs]


def test_regime_blend_mixes_probability_weighted_multipliers() -> None:
    """regime_probs 存在时按概率混合倍率，而非硬查表。"""
    signals = _make_signals([("f1", "trend", 0.10)])
    regime = {
        "regime": "oscillate",
        "confidence": 0.5,
        "regime_probs": {"bull": 0.6, "oscillate": 0.4, "bear": 0.0, "high_vol": 0.0, "low_vol": 0.0},
    }
    adjusted = regime_adaptive_weight_adjustment(signals, regime, _make_factors([("f1", "trend")]))
    # bull trend 倍率 1.3 × 0.6 + oscillate trend 倍率 0.8 × 0.4 = 1.10
    assert abs(adjusted[0]["weight"] - 0.10 * 1.10) < 1e-6


def test_regime_blend_fallback_hardcoded() -> None:
    """无 regime_probs 时回退硬查表（向后兼容）。"""
    signals = _make_signals([("f1", "trend", 0.10)])
    regime = {"regime": "bull", "confidence": 0.8}
    adjusted = regime_adaptive_weight_adjustment(signals, regime, _make_factors([("f1", "trend")]))
    assert abs(adjusted[0]["weight"] - 0.10 * 1.3) < 1e-6  # bull/trend=1.3


# ─── GAP-095: regime blend 幂次调节（blend_power）────────────


def test_default_adaptive_config_has_blend_power() -> None:
    """默认配置含 blend_power=1.0（线性，向后兼容）与 calibration_path=""。"""
    cfg = DEFAULT_ADAPTIVE_CONFIG
    assert cfg.get("blend_power", 1.0) == 1.0
    assert cfg.get("calibration_path", "") == ""


def test_power_normalize_probs_identity() -> None:
    """power=1.0 原样返回；非法 power（≤0）不调整。"""
    probs = {"bull": 0.6, "oscillate": 0.4}
    assert _power_normalize_probs(probs, 1.0) is probs
    assert _power_normalize_probs(probs, 0.0) is probs
    assert _power_normalize_probs(probs, -2.0) is probs
    assert _power_normalize_probs({}, 3.0) == {}


def test_power_normalize_probs_sharpens() -> None:
    """power>1 锐化：大概率制度权重放大，分布更尖锐且和仍为 1。"""
    probs = {"bull": 0.6, "oscillate": 0.4, "bear": 0.0}
    sharp = _power_normalize_probs(probs, 3.0)
    assert abs(sum(sharp.values()) - 1.0) < 1e-9
    assert sharp["bull"] > 0.6  # 0.7714
    assert sharp["oscillate"] < 0.4  # 0.2286
    assert sharp["bear"] < 1e-9  # 零概率仍为零（1e-12^3 归一化后≈0）


def test_power_normalize_probs_flattens() -> None:
    """power<1 钝化：概率趋平（锐化反向）。"""
    probs = {"bull": 0.9, "oscillate": 0.1}
    flat = _power_normalize_probs(probs, 0.5)
    assert abs(sum(flat.values()) - 1.0) < 1e-9
    assert flat["bull"] < 0.9  # sqrt(0.9)/(sqrt(0.9)+sqrt(0.1)) ≈ 0.75
    assert flat["oscillate"] > 0.1


def test_blend_power_default_matches_linear_mix() -> None:
    """默认 blend_power=1.0 时结果与既有线性概率混合一致（回归保护）。"""
    regime = {
        "regime": "oscillate",
        "confidence": 0.5,
        "regime_probs": {"bull": 0.6, "oscillate": 0.4, "bear": 0.0, "high_vol": 0.0, "low_vol": 0.0},
    }
    factors = _make_factors([("f1", "trend")])
    # 注：adjustment 原地修改 signals，每次调用须用独立列表
    w_default = regime_adaptive_weight_adjustment(_make_signals([("f1", "trend", 0.10)]), regime, factors)[0]["weight"]
    w_explicit = regime_adaptive_weight_adjustment(
        _make_signals([("f1", "trend", 0.10)]), regime, factors, blend_power=1.0
    )[0]["weight"]
    assert abs(w_default - w_explicit) < 1e-9
    assert abs(w_default - 0.10 * (1.3 * 0.6 + 0.8 * 0.4)) < 1e-6


def test_blend_power_sharpens_toward_high_prob_regime() -> None:
    """blend_power>1 时权重向大概率（高倍率）制度靠拢（锐化）。

    线性混合 mult=1.3×0.6+0.8×0.4=1.10；power=3 归一化后 bull≈0.771/osc≈0.229
    → mult≈1.186，最终权重高于线性混合（锐化生效）。
    """
    regime = {
        "regime": "oscillate",
        "confidence": 0.5,
        "regime_probs": {"bull": 0.6, "oscillate": 0.4, "bear": 0.0, "high_vol": 0.0, "low_vol": 0.0},
    }
    factors = _make_factors([("f1", "trend")])
    w_sharp = regime_adaptive_weight_adjustment(
        _make_signals([("f1", "trend", 0.10)]), regime, factors, blend_power=3.0
    )[0]["weight"]
    w_linear = regime_adaptive_weight_adjustment(_make_signals([("f1", "trend", 0.10)]), regime, factors)[0]["weight"]
    assert w_sharp > w_linear  # 锐化后更靠近 bull（倍率 1.3 > oscillate 0.8）
    expected = 0.10 * (1.3 * (0.6**3) / (0.6**3 + 0.4**3) + 0.8 * (0.4**3) / (0.6**3 + 0.4**3))
    assert abs(w_sharp - expected) < 1e-6


# ─── GAP-094: 统计校准接入 _compute_exposure_scale ─────────


def _synth_calibration_samples(n: int = 240, seed: int = 7):
    """合成可校准样本：真实命中率 p = 0.1 + 0.8×conf。"""
    rng = np.random.default_rng(seed)
    conf = rng.uniform(0.05, 0.95, size=n)
    p = 0.1 + 0.8 * conf
    return conf.tolist(), (rng.random(n) < p).astype(int).tolist()


def _regime_with(confidence: float, probs: dict | None = None) -> dict:
    return {"regime": "bull", "confidence": confidence, "regime_probs": probs}


def test_exposure_scale_statistical_calibration_used(tmp_path) -> None:
    """配置 calibration_path 且校准有效时，exposure_scale 用统计校准值（频率语义）。"""
    conf, hits = _synth_calibration_samples()
    path = tmp_path / "cal.json"
    StatisticalRegimeCalibrator().fit(conf, hits).save(path)
    scale = _compute_exposure_scale(_regime_with(0.8), calibration_path=str(path))
    # 0.8 置信度真实命中率 ≈ 0.74，统计校准值接近 0.74（熵标定 0.8 被概率熵折扣后远低）
    assert 0.5 < scale <= 1.0
    # 与默认熵标定路径不同（统计校准替换生效）
    default_scale = _compute_exposure_scale(_regime_with(0.8))
    assert abs(scale - default_scale) > 1e-6


def test_exposure_scale_fallback_when_no_calibration_path() -> None:
    """无 calibration_path → 熵标定（默认行为不变）。"""
    sharp = {"bull": 0.95, "bear": 0.01, "oscillate": 0.02, "high_vol": 0.01, "low_vol": 0.01}
    scale = _compute_exposure_scale(_regime_with(0.9, sharp))
    assert 0.3 <= scale <= 1.0
    assert scale > 0.8  # 尖锐分布熵标定几乎不折扣


def test_exposure_scale_missing_calibration_file_falls_back(tmp_path) -> None:
    """calibration_path 指向不存在/损坏文件 → 安全回退熵标定（不抛异常）。"""
    missing = str(tmp_path / "nope.json")
    scale = _compute_exposure_scale(_regime_with(0.8), calibration_path=missing)
    assert 0.3 <= scale <= 1.0


def test_exposure_scale_disabled_returns_one() -> None:
    """enabled=False 返回 1.0（不缩放）。"""
    assert _compute_exposure_scale(_regime_with(0.8), enabled=False) == 1.0


# ─── RegimeSmoother 不对称切换（28-T7：de-risk 快 / re-risk 慢）───────


def test_smoother_asymmetric_de_risk_faster() -> None:
    """进入风险制度快速降权（de-risk），离开风险制度缓慢加仓（re-risk）。

    对标 Man AHL / PIMCO 战术配置：进入 bear/high_vol 用大 alpha 快速向新权重
    靠拢，回归安全制度用小 alpha 缓慢回升，滞后确认防震荡。
    注：min_days=1 保证 stable_days(0) < min_days 走过渡期分支（min_days=0
    会命中稳定期直接采用分支，见 monitor 既有用例语义）。
    """
    smoother = RegimeSmoother(alpha=0.3, min_days=1, de_risk_alpha=0.8, re_risk_alpha=0.1)
    prev = {"a": 1.0}
    # 进入风险制度：快速下降（0.8×0.2 + 0.2×1.0 = 0.36）
    w1 = smoother.should_apply("high_vol", prev, {"a": 0.2})
    assert w1["a"] < 0.5  # 大幅向新权重靠拢
    # 回归安全制度：缓慢上升（0.1×1.0 + 0.9×0.36 = 0.424）
    w2 = smoother.should_apply("bull", w1, {"a": 1.0})
    assert w2["a"] < 0.9  # 仅小幅回升
    assert w2["a"] > w1["a"]
