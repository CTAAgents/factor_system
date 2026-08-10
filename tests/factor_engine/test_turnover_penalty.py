"""tests/factor_engine/test_turnover_penalty.py — 组合换手惩罚项（GAP-I303，v2.85.0）。

覆盖：
- apply_turnover_penalty 单元：λ=0 不变 / λ>0 收缩 / 新因子不惩罚 / 无 prev 直返
- 换手惩罚生效断言（GAP-I303 测试方案）：惩罚后 Σ|Δw| 严格更小，且 λ 单调递减
- build_combo 集成：turnover_penalty 生效（权重介于 prev 与无惩罚之间）
- PortfolioLoop 配置读取：从 FTSConfig.l3_turnover_penalty 读取 / 默认 0
"""

from __future__ import annotations

import pytest

from fts.factor_engine.portfolio_loop import (
    PortfolioLoop,
    apply_turnover_penalty,
    build_combo,
)
from fts.factor_engine.contracts import PortfolioSignal


def _signal(fid: str, weight: float) -> PortfolioSignal:
    return PortfolioSignal(
        factor_id=fid,
        name=fid,
        weight=weight,
        sharpe=2.0,
        ic=0.04,
        turnover=0.3,
        decay_6m=0.1,
        orthogonalized=False,
        retained=True,
    )


def _abs_delta(signals: list[PortfolioSignal], prev: dict[str, float]) -> float:
    return sum(abs(s["weight"] - prev.get(s["factor_id"], 0.0)) for s in signals)


PREV = {"fct_001": 0.40, "fct_002": 0.35, "fct_003": 0.25}


def _make_signals() -> list[PortfolioSignal]:
    """每次构造新信号列表（apply_turnover_penalty 就地修改，避免跨用例污染）。"""
    return [_signal("fct_001", 0.70), _signal("fct_002", 0.20), _signal("fct_003", 0.10)]


class TestApplyTurnoverPenalty:
    def test_zero_penalty_no_change(self) -> None:
        out = apply_turnover_penalty(_make_signals(), PREV, 0.0)
        assert [s["weight"] for s in out] == [0.70, 0.20, 0.10]

    def test_positive_penalty_shrinks_movement(self) -> None:
        out = apply_turnover_penalty(_make_signals(), PREV, 1.0)
        # w' = prev + (w - prev) / 2
        assert out[0]["weight"] == pytest.approx(0.40 + (0.70 - 0.40) / 2)
        assert out[1]["weight"] == pytest.approx(0.35 + (0.20 - 0.35) / 2)

    def test_new_factor_not_penalized(self) -> None:
        signals = _make_signals() + [_signal("fct_new", 0.05)]
        out = apply_turnover_penalty(signals, PREV, 2.0)
        new_sig = next(s for s in out if s["factor_id"] == "fct_new")
        assert new_sig["weight"] == pytest.approx(0.05)

    def test_no_prev_weights_returns_unchanged(self) -> None:
        out = apply_turnover_penalty(_make_signals(), {}, 2.0)
        assert [s["weight"] for s in out] == [0.70, 0.20, 0.10]


class TestTurnoverPenaltyEffectiveness:
    """GAP-I303 测试方案：换手惩罚生效断言。"""

    def test_penalty_reduces_turnover(self) -> None:
        base_delta = _abs_delta(_make_signals(), PREV)
        penalized = apply_turnover_penalty(_make_signals(), PREV, 1.0)
        assert _abs_delta(penalized, PREV) < base_delta

    def test_lambda_monotonic(self) -> None:
        deltas = [
            _abs_delta(apply_turnover_penalty(_make_signals(), PREV, lam), PREV)
            for lam in (0.0, 1.0, 3.0, 10.0)
        ]
        assert deltas[0] > deltas[1] > deltas[2] > deltas[3]

    def test_large_lambda_keeps_prev_weights(self) -> None:
        out = apply_turnover_penalty(_make_signals(), PREV, 1000.0)
        # shrink=1/1001，权重接近但不等同于 prev（容许 1% 内偏差）
        assert all(abs(s["weight"] - PREV[s["factor_id"]]) < 1e-2 for s in out)


class TestBuildComboIntegration:
    def test_turnover_penalty_affects_final_weights(self, tmp_path) -> None:
        no_pen = build_combo(
            [_signal("fct_001", 0.70), _signal("fct_002", 0.20), _signal("fct_003", 0.10)],
            prev_weights=PREV,
            turnover_penalty=0.0,
        )
        with_pen = build_combo(
            [_signal("fct_001", 0.70), _signal("fct_002", 0.20), _signal("fct_003", 0.10)],
            prev_weights=PREV,
            turnover_penalty=2.0,
        )
        w_no = {s["factor_id"]: s["weight"] for s in no_pen["signals"]}
        w_pen = {s["factor_id"]: s["weight"] for s in with_pen["signals"]}
        # 惩罚后 fct_001 更接近旧权重 0.40
        assert abs(w_pen["fct_001"] - PREV["fct_001"]) < abs(w_no["fct_001"] - PREV["fct_001"])
        # 权重仍归一化
        assert sum(w_pen.values()) == pytest.approx(1.0)

    def test_default_zero_penalty_unchanged(self, tmp_path) -> None:
        combo = build_combo(
            [_signal("fct_001", 0.70), _signal("fct_002", 0.20), _signal("fct_003", 0.10)],
            prev_weights=PREV,
        )
        w = {s["factor_id"]: s["weight"] for s in combo["signals"]}
        assert w["fct_001"] == pytest.approx(0.70 / 1.0)  # 未归一化前之和=1.0


class TestPortfolioLoopConfig:
    def test_reads_config_turnover_penalty(self, tmp_path, monkeypatch) -> None:
        from fts.config.settings import FTSConfig

        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(l3_turnover_penalty=3.0),
        )
        loop = PortfolioLoop(memory_dir=str(tmp_path / "mem"))
        assert loop.turnover_penalty == 3.0

    def test_default_zero(self, tmp_path, monkeypatch) -> None:
        from fts.config.settings import FTSConfig

        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(),
        )
        loop = PortfolioLoop(memory_dir=str(tmp_path / "mem"))
        assert loop.turnover_penalty == 0.0

    def test_explicit_parameter_overrides_config(self, tmp_path, monkeypatch) -> None:
        from fts.config.settings import FTSConfig

        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(l3_turnover_penalty=3.0),
        )
        loop = PortfolioLoop(memory_dir=str(tmp_path / "mem"), turnover_penalty=5.0)
        assert loop.turnover_penalty == 5.0
