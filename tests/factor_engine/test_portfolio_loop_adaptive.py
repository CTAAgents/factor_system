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

import pandas as pd

from fts.factor_engine.contracts import (
    DEFAULT_ADAPTIVE_CONFIG,
    AdaptiveWeightConfig,
)
from fts.factor_engine.portfolio_loop import (
    PortfolioLoop,
    synthesize_signals,
)


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
