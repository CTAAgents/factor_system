"""tests/factor_engine/test_portfolio_risk_controls.py — 组合级风控测试（GAP-067 + G1/G2）。

覆盖: 回撤止损 / 相关性熔断 / 综合检查 / 边界降级 / G1 同向敞口惩罚 / G2 踩踏规避。
HARNESS §测试随重构。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from fts.factor_engine.portfolio_risk_controls import (
    AlignedExposureConfig,
    ExitStampedeConfig,
    check_aligned_exposure,
    check_correlation_circuit_breaker,
    check_drawdown_stop,
    run_portfolio_risk_controls,
    throttle_exit_stampede,
    DEFAULT_CORR_THRESHOLD,
)


# ─── 回撤止损 ─────────────────────────────────────────────


def test_drawdown_stop_triggers_on_decline():
    """持续亏损序列回撤超阈值触发。"""
    returns = np.full(100, -0.002)  # 累计 -18%
    res = check_drawdown_stop(returns, threshold=0.10)
    assert res["triggered"] is True
    assert res["max_drawdown"] > 0.10


def test_drawdown_stop_not_triggered_on_rise():
    """稳步上升序列不触发。"""
    returns = np.full(100, 0.002)
    res = check_drawdown_stop(returns, threshold=0.10)
    assert res["triggered"] is False


def test_drawdown_threshold_configurable():
    """阈值可配置：大幅回撤在宽松阈值下不触发。"""
    returns = np.concatenate([np.full(50, 0.001), np.full(50, -0.004)])  # 峰值后 -18%
    assert check_drawdown_stop(returns, threshold=0.30)["triggered"] is False
    assert check_drawdown_stop(returns, threshold=0.10)["triggered"] is True


# ─── 相关性熔断 ───────────────────────────────────────────


def _member_returns(correlated: bool, seed: int = 8) -> pd.DataFrame:
    """构造成员收益面板：强相关（共同驱动）或独立。"""
    rng = np.random.default_rng(seed)
    n = 120
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    if correlated:
        common = rng.normal(0, 0.02, n)
        return pd.DataFrame(
            {"a": common + rng.normal(0, 0.002, n), "b": common + rng.normal(0, 0.002, n)},
            index=idx,
        )
    return pd.DataFrame(
        {"a": rng.normal(0, 0.02, n), "b": rng.normal(0, 0.02, n), "c": rng.normal(0, 0.02, n)},
        index=idx,
    )


def test_corr_breaker_triggers_on_crisis():
    """危机模式（成员高度联动）触发熔断。"""
    res = check_correlation_circuit_breaker(_member_returns(correlated=True))
    assert res["triggered"] is True
    assert res["mean_corr"] > DEFAULT_CORR_THRESHOLD


def test_corr_breaker_not_triggered_independent():
    """独立成员不触发熔断。"""
    res = check_correlation_circuit_breaker(_member_returns(correlated=False))
    assert res["triggered"] is False


def test_corr_breaker_short_window_no_trigger():
    """窗口样本不足不触发。"""
    returns = _member_returns(correlated=True).tail(3)
    res = check_correlation_circuit_breaker(returns)
    assert res["triggered"] is False


# ─── 综合检查 ─────────────────────────────────────────────


def test_run_risk_controls_both_alerts():
    """同时触发回撤止损与相关性熔断，notes 非空。"""
    returns = np.concatenate([np.full(60, 0.001), np.full(60, -0.003)])  # 峰值后约 -16%
    members = _member_returns(correlated=True)
    alert = run_portfolio_risk_controls(returns, members, drawdown_threshold=0.10, corr_threshold=0.8)
    assert alert.drawdown_stop is True
    assert alert.correlation_breaker is True
    assert len(alert.notes) == 2


def test_run_risk_controls_no_alerts():
    """正常组合无告警。"""
    returns = np.full(120, 0.002)
    members = _member_returns(correlated=False)
    alert = run_portfolio_risk_controls(returns, members)
    assert alert.drawdown_stop is False
    assert alert.correlation_breaker is False
    assert alert.notes == []


def test_run_risk_controls_none_inputs_no_crash():
    """combo_returns/member_returns 为 None 时不崩溃且不触发。"""
    alert = run_portfolio_risk_controls(None, None)
    assert alert.drawdown_stop is False
    assert alert.correlation_breaker is False


def test_to_dict_serializable():
    """to_dict 可 JSON 序列化。"""
    returns = np.concatenate([np.full(60, 0.001), np.full(60, -0.003)])
    alert = run_portfolio_risk_controls(returns, _member_returns(correlated=True))
    json.dumps(alert.to_dict())


# ─── G1 同向敞口惩罚（35-gap-closure-plan）────────────────


def _signals(ics: list[float], weights: list[float] | None = None) -> list[dict]:
    """构造 PortfolioSignal 风格的信号列表（ic + weight）。"""
    n = len(ics)
    weights = weights if weights is not None else [1.0 / n] * n
    return [{"factor_id": f"f{i}", "ic": ic, "weight": w} for i, (ic, w) in enumerate(zip(ics, weights))]


def test_aligned_exposure_all_same_direction_full_compress():
    """全部因子同向（看多占比 100%）→ 最大压缩至 max_compress（0.5）。"""
    res = check_aligned_exposure(_signals([0.05, 0.03, 0.06, 0.04, 0.02]))
    assert res["triggered"] is True
    assert abs(float(res["compress_scale"]) - 0.5) < 1e-9
    assert float(res["long_ratio"]) == 1.0


def test_aligned_exposure_no_trigger_on_split():
    """多空分歧（2 正 2 负 1 中性）→ 不触发（scale=1.0）。"""
    res = check_aligned_exposure(_signals([0.05, 0.03, -0.04, -0.02, 0.0]))
    assert res["triggered"] is False
    assert float(res["compress_scale"]) == 1.0


def test_aligned_exposure_ratio_at_threshold_triggers():
    """同向占比恰为 0.6（阈值）→ 触发但压缩为 1.0（刚过阈值的自然衔接）。"""
    res = check_aligned_exposure(_signals([0.05, 0.03, 0.02, -0.04, -0.02]))
    assert res["triggered"] is True
    assert abs(float(res["compress_scale"]) - 1.0) < 1e-9


def test_aligned_exposure_below_threshold_no_trigger():
    """同向占比 0.5 < 0.6 → 不触发。"""
    res = check_aligned_exposure(_signals([0.05, 0.03, -0.04, -0.02]))
    assert res["triggered"] is False


def test_aligned_exposure_partial_compress():
    """4 正 1 负（占比 0.8）→ scale=0.75（线性）。"""
    res = check_aligned_exposure(_signals([0.05, 0.03, 0.06, 0.04, -0.02]))
    assert res["triggered"] is True
    assert abs(float(res["compress_scale"]) - 0.75) < 1e-9


def test_aligned_exposure_weighted_ratio():
    """权重加权：大权重因子主导方向占比。"""
    # f0 权重 0.8 看多，其余 4 个权重共 0.2 看空 → 同向占比 0.8 → 触发
    ics = [0.05, -0.04, -0.03, -0.02, -0.01]
    weights = [0.8, 0.05, 0.05, 0.05, 0.05]
    res = check_aligned_exposure(_signals(ics, weights))
    assert res["triggered"] is True
    assert abs(float(res["long_ratio"]) - 0.8) < 1e-9


def test_aligned_exposure_curve_sqrt_gentler():
    """sqrt 曲线压缩比 linear 更温和（scale 更大）。"""
    ics = [0.05, 0.03, 0.06, 0.04, 0.02]
    linear = check_aligned_exposure(_signals(ics), AlignedExposureConfig(compress_curve="linear"))
    sqrt = check_aligned_exposure(_signals(ics), AlignedExposureConfig(compress_curve="sqrt"))
    assert float(sqrt["compress_scale"]) >= float(linear["compress_scale"])


def test_aligned_exposure_disabled():
    """enabled=False → 不触发。"""
    res = check_aligned_exposure(_signals([0.05, 0.03, 0.06, 0.04, 0.02]), AlignedExposureConfig(enabled=False))
    assert res["triggered"] is False
    assert float(res["compress_scale"]) == 1.0


def test_aligned_exposure_empty_no_crash():
    """空信号 → 不触发不崩溃。"""
    res = check_aligned_exposure([])
    assert res["triggered"] is False
    assert float(res["compress_scale"]) == 1.0


# ─── G2 集中踩踏止损规避（35-gap-closure-plan）────────────


def _exit_matrix(n_days: int = 5, n_syms: int = 10) -> pd.DataFrame:
    """第 0 日全部合约触发，其余日无触发。"""
    idx = pd.date_range("2024-01-01", periods=n_days, freq="D")
    df = pd.DataFrame(0, index=idx, columns=[f"c{i}" for i in range(n_syms)])
    df.iloc[0] = 1
    return df


def _exposures(n_syms: int = 10) -> pd.Series:
    """敞口序列：c0 最大，依次递减。"""
    return pd.Series({f"c{i}": float(n_syms - i) for i in range(n_syms)})


def test_exit_stampede_throttles_same_day():
    """单日 10 合约触发 → 每天 ≤3，优先最大敞口当日执行。"""
    out = throttle_exit_stampede(_exit_matrix(), _exposures())
    day_counts = out.sum(axis=1)
    assert (day_counts <= 3).all()
    assert out.sum().sum() == 10  # 无合约丢失
    assert out.iloc[0].sum() == 3
    assert out.loc[out.index[0], "c0"] == 1  # 最大敞口当日优先
    assert out.loc[out.index[0], "c1"] == 1
    assert out.loc[out.index[0], "c2"] == 1


def test_exit_stampede_no_throttle_under_cap():
    """单日触发 ≤3 → 不重排。"""
    df = _exit_matrix(n_syms=3)
    out = throttle_exit_stampede(df, _exposures(3))
    assert (out == df).all().all()


def test_exit_stampede_disabled_passthrough():
    """enabled=False → 原样返回。"""
    df = _exit_matrix()
    out = throttle_exit_stampede(df, _exposures(), ExitStampedeConfig(enabled=False))
    assert (out == df).all().all()


def test_exit_stampede_empty_no_crash():
    """空输入 → 不崩溃。"""
    out = throttle_exit_stampede(pd.DataFrame(), pd.Series(dtype=float))
    assert out.empty


def test_exit_stampede_multi_day_distributed():
    """多日分散触发：每日配额内不相互挤压。"""
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    df = pd.DataFrame(0, index=idx, columns=[f"c{i}" for i in range(4)])
    df.iloc[0, :2] = 1  # day0 触发 2 个
    df.iloc[1, :2] = 1  # day1 触发 2 个
    out = throttle_exit_stampede(df, _exposures(4))
    assert out.sum().sum() == 4
    assert (out.sum(axis=1) <= 3).all()


def test_exit_stampede_pool_exhausted_keeps_original_day():
    """计划日耗尽（3 天 × 3 配额 = 9 < 10）→ 最后一个保留原触发日，不丢弃止损。"""
    df = _exit_matrix(n_days=3, n_syms=10)
    out = throttle_exit_stampede(df, _exposures())
    assert out.sum().sum() == 10  # 全部保留
    # 配额 9 < 10：前 9 个分批执行，最后一个回填触发日（day0）——纪律优先，允许单日超配额
    assert out.iloc[0].sum() == 4
    assert out.iloc[1].sum() == 3
    assert out.iloc[2].sum() == 3
