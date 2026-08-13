"""
fts/factor_engine/cost_sensitivity.py — 可交易性压力层（GAP-061，v2.91.0）

对照《期货因子质检六层框架》Layer 6 可交易性验证补齐：
    - 滑点放大压力测试：1/2/4/8 倍滑点下净夏普/净 IC 的退化
    - 盈亏平衡倍数：净夏普转负的滑点倍数（可交易性上限）

设计约束:
    - 复用 TransactionCostModel 成本引擎（同口径，不重复实现成本模型）
    - 持仓滞后一期（t 日信号赚 t+1 收益），零未来函数
    - NaN 兜底：信号/价格含 NaN 时剔除无效样本
    - 独立模块、无循环依赖；evaluation_chain 可选集成（配置开关）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, cast

import numpy as np
from scipy import stats as _sp

from .cost_model import CostConfig, TransactionCostModel, _DEFAULT_FUTURES

DEFAULT_SLIPPAGE_MULTS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)


@dataclass
class CostSensitivityResult:
    """成本敏感性 / 滑点压力测试结果。"""

    slippage_mults: list[float] = field(default_factory=lambda: list(DEFAULT_SLIPPAGE_MULTS))
    gross_sharpe: float = 0.0  # 无成本毛夏普
    gross_ic: float = 0.0  # 无成本毛 IC
    net_sharpe_by_mult: dict[float, float] = field(default_factory=dict)  # 滑点倍数 -> 净夏普
    net_ic_by_mult: dict[float, float] = field(default_factory=dict)  # 滑点倍数 -> 成本调整后 IC
    total_cost_bps_by_mult: dict[float, float] = field(default_factory=dict)
    breakeven_mult: Optional[float] = None  # 净夏普转负的滑点倍数（线性插值）
    positive_at_max_stress: bool = False  # 最大倍数下净夏普仍为正

    def to_dict(self) -> dict:
        """序列化为 dict（供 FactorEvaluation / JSON 输出）。"""
        return {
            "slippage_mults": self.slippage_mults,
            "gross_sharpe": self.gross_sharpe,
            "gross_ic": self.gross_ic,
            "net_sharpe_by_mult": self.net_sharpe_by_mult,
            "net_ic_by_mult": self.net_ic_by_mult,
            "total_cost_bps_by_mult": self.total_cost_bps_by_mult,
            "breakeven_mult": self.breakeven_mult,
            "positive_at_max_stress": self.positive_at_max_stress,
        }


def _gross_metrics(
    signal: np.ndarray,
    close: np.ndarray,
    periods_per_year: int = 252,
) -> tuple[float, float]:
    """计算毛夏普与 1 日持有 IC（持仓滞后一期）。

    Returns:
        (sharpe, ic)；样本不足返回 (0.0, 0.0)。
    """
    sig = np.asarray(signal, dtype=float)
    close_arr = np.asarray(close, dtype=float)
    n = min(len(sig), len(close_arr))
    if n < 30:
        return 0.0, 0.0
    sig = sig[:n]
    close_arr = close_arr[:n]
    rets = np.full(n, np.nan)
    rets[1:] = (close_arr[1:] - close_arr[:-1]) / np.maximum(np.abs(close_arr[:-1]), 1e-10)

    # 持仓：zscore 后 clip [-1,1]，滞后一期（t 日信号赚 t+1 收益）
    mu = np.nanmean(sig)
    sd = np.nanstd(sig)
    if not np.isfinite(mu) or not np.isfinite(sd) or sd < 1e-12:
        return 0.0, 0.0
    pos = np.clip((sig - mu) / max(sd, 1e-10), -1.0, 1.0)

    # 组合收益：pos[t-1] * rets[t]
    gross_ret = np.full(n, np.nan)
    gross_ret[1:] = pos[:-1] * rets[1:]
    mask = np.isfinite(gross_ret) & np.isfinite(pos)
    if int(mask.sum()) < 30:
        return 0.0, 0.0
    gr = gross_ret[mask]
    sharpe = float(np.mean(gr) / max(np.std(gr, ddof=1), 1e-10) * np.sqrt(periods_per_year))

    # 1 日持有 IC（信号 vs 未来 1 日收益）
    valid = np.isfinite(pos) & np.isfinite(rets)
    if int(valid.sum()) < 30 or np.std(rets[valid]) < 1e-12:
        ic = 0.0
    else:
        ic_val, _ = _sp.spearmanr(pos[valid], rets[valid])
        ic = 0.0 if np.isnan(ic_val) else float(ic_val)
    return sharpe, ic


def _scaled_config(base: CostConfig, mult: float) -> CostConfig:
    """按倍数缩放滑点，其余成本参数不变。"""
    scaled: dict[str, Any] = dict(base)
    scaled["slippage_bps"] = float(base.get("slippage_bps", 0.5)) * float(mult)
    return cast(CostConfig, scaled)


def run_slippage_stress(
    signal: np.ndarray,
    close: np.ndarray,
    mults: tuple[float, ...] = DEFAULT_SLIPPAGE_MULTS,
    market: str = "futures",
    periods_per_year: int = 252,
) -> Optional[CostSensitivityResult]:
    """滑点放大压力测试：1/2/4/8 倍滑点下净夏普 / 净 IC / 盈亏平衡倍数。

    Args:
        signal: 因子信号
        close: 收盘价序列
        mults: 滑点放大倍数
        market: 市场（futures/stock/etf），决定基础成本参数
        periods_per_year: 年化系数

    Returns:
        CostSensitivityResult；样本不足返回 None。
    """
    gross_sharpe, gross_ic = _gross_metrics(signal, close, periods_per_year)
    if abs(gross_sharpe) < 1e-9 and abs(gross_ic) < 1e-9:
        # 无法计算毛指标（样本不足/常数），返回 None 表示不可用
        sig = np.asarray(signal, dtype=float)
        if len(sig) < 30:
            return None

    base_cfg = TransactionCostModel().get_cost_bps(market) or _DEFAULT_FUTURES
    result = CostSensitivityResult(
        slippage_mults=[float(m) for m in mults],
        gross_sharpe=gross_sharpe,
        gross_ic=gross_ic,
    )

    metrics: dict = {"sharpe": gross_sharpe, "ic": gross_ic}
    for m in mults:
        cfg = _scaled_config(base_cfg, m)
        model = TransactionCostModel(config=cfg)
        try:
            adj = model.adjust(metrics, signal, market=market)
            result.net_sharpe_by_mult[float(m)] = float(adj["net_sharpe"])
            result.net_ic_by_mult[float(m)] = float(adj["cost_adjusted_ic"])
            result.total_cost_bps_by_mult[float(m)] = float(adj["total_cost_bps"])
        except Exception:  # noqa: BLE001 — 单倍数失败降级（记录该倍数不可用）
            continue

    # 盈亏平衡倍数：净夏普跨零的线性插值
    order = [float(m) for m in mults if m in result.net_sharpe_by_mult]
    if len(order) >= 2:
        for i in range(1, len(order)):
            m_prev, m_cur = order[i - 1], order[i]
            s_prev = result.net_sharpe_by_mult[m_prev]
            s_cur = result.net_sharpe_by_mult[m_cur]
            if s_prev > 0 and s_cur <= 0:
                result.breakeven_mult = float(m_prev + (m_cur - m_prev) * s_prev / max(s_prev - s_cur, 1e-12))
                break

    if order:
        result.positive_at_max_stress = bool(result.net_sharpe_by_mult.get(order[-1], 0.0) > 0)
    return result


def run_cost_sensitivity(
    signal: np.ndarray,
    close: np.ndarray,
    mults: tuple[float, ...] = DEFAULT_SLIPPAGE_MULTS,
    market: str = "futures",
    periods_per_year: int = 252,
) -> Optional[CostSensitivityResult]:
    """成本敏感性扫描（与 run_slippage_stress 同口径，对外统一入口）。"""
    return run_slippage_stress(signal, close, mults=mults, market=market, periods_per_year=periods_per_year)


__all__ = [
    "CostSensitivityResult",
    "run_slippage_stress",
    "run_cost_sensitivity",
    "DEFAULT_SLIPPAGE_MULTS",
]
