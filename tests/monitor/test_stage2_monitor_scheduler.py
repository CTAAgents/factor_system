"""Stage 2 测试 — A.2/A.3/B.1 监控与调度补齐。"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fts.factor_engine.adaptive_weight import (  # noqa: E402
    AdaptiveWeightManager,
    RegimeSmoother,
)
from fts.monitor.data_quality_monitor import (  # noqa: E402
    compute_coverage_ratio,
    compute_cross_source_deviation,
    compute_data_drift_rate,
    compute_freshness,
    compute_jump_detection,
    compute_missing_ratio,
    compute_outlier_ratio,
    compute_timestamp_continuity,
    evaluate_source_data,
)
from fts.monitor.prometheus_metrics import MetricsRegistry  # noqa: E402
from fts.scheduler.tasks import get_task, list_tasks  # noqa: E402


# ─── A.3 自适应权重 ─────────────────────────────────────────


class TestAdaptiveWeightManager:
    """AdaptiveWeightManager 封装与热更新。"""

    def test_adjust_delegates_to_portfolio_loop(self):
        """adjust() 委托 portfolio_loop 的 regime_adaptive_weight_adjustment。"""
        manager = AdaptiveWeightManager()
        signals = [
            {"factor_id": "fct_a", "weight": 0.5, "decay_6m": 0.05},
            {"factor_id": "fct_b", "weight": 0.5, "decay_6m": 0.10},
        ]
        factors = [
            {"factor_id": "fct_a", "name": "ts_momentum", "family": "momentum"},
            {"factor_id": "fct_b", "name": "ts_mean_reversion", "family": "mean_reversion"},
        ]
        regime = {"regime": "oscillate", "confidence": 0.9}
        adjusted = manager.adjust(signals, regime, factors)
        assert isinstance(adjusted, list)
        assert len(adjusted) == 2
        # 返回列表（就地更新权重）
        assert all("weight" in s for s in adjusted)

    def test_compute_weights(self):
        manager = AdaptiveWeightManager()
        factors = [
            {"factor_id": "fct_a", "name": "ts_momentum", "family": "momentum"},
            {"factor_id": "fct_b", "name": "carry_1", "family": "carry"},
        ]
        weights = manager.compute_weights(factors, {"regime": "bull"})
        assert set(weights.keys()) == {"fct_a", "fct_b"}
        # 调整后权重均为正数（不要求归一化，原函数就地乘倍率）
        assert all(w > 0 for w in weights.values())

    def test_update_config_and_list(self):
        manager = AdaptiveWeightManager(multipliers={"bull": {"momentum": 1.3}})
        manager.update_config("bull", {"carry": 1.2})
        config = manager.get_current_config("bull")
        assert config["momentum"] == 1.3
        assert config["carry"] == 1.2
        configs = manager.list_configs()
        assert "bull" in configs


class TestRegimeSmoother:
    """RegimeSmoother 指数平滑。"""

    def test_same_regime_returns_new_weights(self):
        smoother = RegimeSmoother(alpha=0.3, min_days=0)
        # min_days=0 → 稳定期 → 直接返回新权重
        out = smoother.should_apply(
            "bull", {"a": 0.5, "b": 0.5}, {"a": 0.8, "b": 0.2}
        )
        assert out["a"] == pytest.approx(0.8)

    def test_regime_change_smooths_weights(self):
        smoother = RegimeSmoother(alpha=0.5, min_days=30)
        # 首次检测 Regime → 过渡期 → 平滑
        out = smoother.should_apply(
            "bear", {"a": 0.5, "b": 0.5}, {"a": 0.2, "b": 0.8}
        )
        total = sum(out.values())
        assert total == pytest.approx(1.0, abs=1e-6)
        # 平滑后 a 在 0.2 与 0.5 之间
        assert 0.2 < out["a"] < 0.5


# ─── B.1 三维指标 ───────────────────────────────────────────


def _make_ohlcv(n: int = 50, missing: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    df = pd.DataFrame({
        "symbol": ["RB"] * n,
        "timestamp": dates,
        "open": close * 0.99,
        "close": close,
        "volume": rng.integers(1000, 5000, n),
    })
    if missing > 0:
        mask = rng.random(n) < missing
        df.loc[mask, "close"] = np.nan
    return df


class TestCompletenessMetrics:
    """完整性指标。"""

    def test_coverage_ratio(self):
        df = _make_ohlcv()
        assert compute_coverage_ratio(df, {"RB", "HC"}) == pytest.approx(0.5)
        assert compute_coverage_ratio(df, {"RB"}) == pytest.approx(1.0)
        assert compute_coverage_ratio(df, set()) == 0.0

    def test_timestamp_continuity(self):
        df = _make_ohlcv()
        # 连续日频 → 连续率 1.0
        assert compute_timestamp_continuity(df, "D") == pytest.approx(1.0)
        # 删除中间一行 → 连续性 < 1.0
        df2 = pd.concat([df.iloc[:25], df.iloc[26:]]).reset_index(drop=True)
        assert compute_timestamp_continuity(df2, "D") < 1.0

    def test_missing_ratio(self):
        df = _make_ohlcv(missing=0.1)
        ratio = compute_missing_ratio(df)
        assert 0 < ratio < 0.5


class TestAccuracyMetrics:
    """准确性指标。"""

    def test_cross_source_deviation(self):
        a = pd.Series([1.0, 2.0, 3.0])
        b = pd.Series([1.02, 2.04, 3.06])
        dev = compute_cross_source_deviation(a, b)
        assert dev > 0.01

    def test_outlier_ratio(self):
        # 95 个 1.0 + 5 个 100（占比 5%，远大于 3σ）
        s = pd.Series([1.0] * 95 + [100.0] * 5)
        assert compute_outlier_ratio(s, threshold=3.0) > 0.04

    def test_jump_detection(self):
        close = np.array([100.0] * 10 + [130.0] * 10)  # 单次 30% 跳变
        df = pd.DataFrame({"close": close})
        assert compute_jump_detection(df, threshold=0.15) >= 1

    def test_data_drift_rate(self):
        ref = pd.Series(np.random.default_rng(1).normal(0, 1, 1000))
        curr = pd.Series(np.random.default_rng(2).normal(3, 1, 1000))  # 均值漂移
        psi = compute_data_drift_rate(ref, curr)
        assert psi > 0.25  # 严重漂移


class TestTimelinessMetrics:
    """及时性指标。"""

    def test_freshness(self):
        df = _make_ohlcv(n=50)  # 数据到 2026-02-19
        now = pd.Timestamp("2026-03-01")
        fresh = compute_freshness(df, now=now.to_pydatetime())
        assert fresh > 0

    def test_evaluate_source_data_summary(self):
        df = _make_ohlcv()
        result = evaluate_source_data(df, expected_symbols={"RB", "HC"})
        assert "completeness" in result
        assert "accuracy" in result
        assert "timeliness" in result
        assert result["completeness"]["coverage_ratio"] == pytest.approx(0.5)
        assert "jump_detection_count" in result["accuracy"]


# ─── A.2 Prometheus 指标 ────────────────────────────────────


class TestMetricsRegistry:
    """MetricsRegistry 渲染。"""

    def test_render_decay_metrics(self):
        reg = MetricsRegistry()
        reg.update_decay_counts(active=10, decaying=2, critical=1, deprecated=3)
        reg.record_decay_evaluation("active", "decaying")
        reg.set_regime("bull")
        reg.record_rebalance("bull")

        text = "\n".join(reg.render())
        assert "fts_factor_decay_active_count 10" in text
        assert "fts_factor_decay_decaying_count 2" in text
        assert "fts_factor_decay_critical_count 1" in text
        assert "fts_factor_decay_deprecated_count 3" in text
        assert 'status_before="active"' in text
        assert 'fts_regime_current{regime="bull"} 1' in text
        assert 'fts_weight_rebalance_total{regime="bull"} 1' in text

    def test_render_empty(self):
        reg = MetricsRegistry()
        text = "\n".join(reg.render())
        assert "fts_factor_decay_active_count 0" in text
        assert "fts_regime_current" in text


# ─── 调度任务注册 ───────────────────────────────────────────


class TestSchedulerTasks:
    """新增定时任务注册。"""

    def test_monthly_decay_task_registered(self):
        task = get_task("monthly_decay_eval")
        assert task is not None
        assert task.cron_expression == "0 2 1 * *"
        assert "monthly_decay_eval_job" in task.callable_path

    def test_data_quality_task_registered(self):
        task = get_task("data_quality_eval")
        assert task is not None
        assert task.cron_expression == "*/5 * * * *"

    def test_default_tasks_include_new_ones(self):
        names = {t.name for t in list_tasks()}
        assert "monthly_decay_eval" in names
        assert "data_quality_eval" in names
