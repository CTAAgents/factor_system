"""
fts.factor_engine.factor_returns — 因子收益序列构建器（GAP-L301，v2.61.0）。

机构级组合构建的第一块地基：将每个因子在横截面上构建多空组合（top/bottom
分位），得到因子收益时间序列，供 L3 组合层做协方差估计、组合优化与指标实测。

设计:
    输入: signal_matrix (n_dates × n_stocks × n_factors) + forward_returns (n_dates × n_stocks)
    输出: factor_returns (DataFrame, index=dates, columns=factor_ids)

    对每个 (日期 t, 因子 j):
        - 取信号与收益均有效的股票集合
        - 按信号横截面排序，取 top/bottom quantile 分位
        - 因子收益 = mean(前向收益[top]) − mean(前向收益[bottom])

用法:
    builder = FactorReturnsBuilder()
    fr = builder.build_from_panel(
        signal_matrix, forward_returns, dates=dates, factor_ids=fids,
    )

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 契约 ─────────────────────────────────────────────────


@dataclass
class FactorReturnsConfig:
    """因子收益序列构建配置。"""

    quantile: float = 0.2            # 多空分位比例（top/bottom 各取 20%）
    min_stocks: int = 10             # 每日最少有效股票数（不足该日该因子置 NaN）
    min_dates: int = 20              # 有效日期数下限（不足抛 ValueError）
    annualize_factor: float = 252.0  # 年化因子（夏普/波动率年化）
    directional: bool = False        # 是否按全样本平均收益符号校准因子方向（False=保持原始多空方向）


@dataclass
class FactorReturnsResult:
    """因子收益序列构建结果。"""

    returns: pd.DataFrame            # 因子收益矩阵 (T × N)，index=dates, columns=factor_ids
    coverage: dict[str, float]       # {factor_id: 有效日期占比}
    config: FactorReturnsConfig = field(default_factory=FactorReturnsConfig)

    def to_dict(self) -> dict[str, Any]:
        """序列化摘要（供报告/日志）。"""
        return {
            "n_dates": int(self.returns.shape[0]),
            "n_factors": int(self.returns.shape[1]),
            "coverage": {k: round(v, 4) for k, v in self.coverage.items()},
            "annualize_factor": self.config.annualize_factor,
        }


class FactorReturnsBuilder:
    """因子收益序列构建器（横截面多空组合法）。

    Args:
        config: 构建配置（None 用默认）
    """

    def __init__(self, config: Optional[FactorReturnsConfig] = None) -> None:
        self._config = config or FactorReturnsConfig()
        if not (0 < self._config.quantile < 0.5):
            raise ValueError(f"quantile 必须在 (0, 0.5) 内，收到 {self._config.quantile}")
        if self._config.min_stocks < 2:
            raise ValueError(f"min_stocks 必须 ≥ 2，收到 {self._config.min_stocks}")

    # ─── 主入口 ──────────────────────────────────────────

    def build_from_panel(
        self,
        signal_matrix: np.ndarray,
        forward_returns: np.ndarray,
        dates: Sequence[Any],
        factor_ids: Sequence[str],
    ) -> FactorReturnsResult:
        """从横截面面板构建因子收益序列。

        Args:
            signal_matrix: 因子信号矩阵 (n_dates, n_stocks, n_factors)，NaN 表示缺失
            forward_returns: 前向收益矩阵 (n_dates, n_stocks)，与 signal_matrix 对齐
            dates: 日期序列（长度 n_dates）
            factor_ids: 因子 ID 序列（长度 n_factors）

        Returns:
            FactorReturnsResult（returns 为 T×N DataFrame，index=dates, columns=factor_ids）

        Raises:
            ValueError: 维度不匹配 / 有效日期不足
        """
        sig = np.asarray(signal_matrix, dtype=float)
        fwd = np.asarray(forward_returns, dtype=float)
        n_dates, n_stocks, n_factors = sig.shape
        if fwd.shape != (n_dates, n_stocks):
            raise ValueError(
                f"forward_returns 维度 {fwd.shape} 与 signal_matrix 股票维度不一致 "
                f"({n_dates}, {n_stocks})"
            )
        if len(dates) != n_dates:
            raise ValueError(f"dates 长度 {len(dates)} != n_dates {n_dates}")
        if len(factor_ids) != n_factors:
            raise ValueError(f"factor_ids 长度 {len(factor_ids)} != n_factors {n_factors}")

        cfg = self._config
        ret = np.full((n_dates, n_factors), np.nan)
        q = cfg.quantile

        for t in range(n_dates):
            fwd_t = fwd[t]
            for j in range(n_factors):
                sig_t = sig[t, :, j]
                valid = ~np.isnan(sig_t) & ~np.isnan(fwd_t)
                n_valid = int(valid.sum())
                if n_valid < cfg.min_stocks:
                    continue
                sig_v = sig_t[valid]
                fwd_v = fwd_t[valid]
                order = np.argsort(sig_v)
                n_leg = max(1, int(round(q * n_valid)))
                # 多头: 信号 top（rank 高）; 空头: 信号 bottom（rank 低）
                long_idx = order[-n_leg:]
                short_idx = order[:n_leg]
                long_ret = float(np.mean(fwd_v[long_idx]))
                short_ret = float(np.mean(fwd_v[short_idx]))
                ret[t, j] = long_ret - short_ret

        # 方向校准（可选）：全样本平均收益为负 → 翻转符号，保证因子收益正偏
        if cfg.directional:
            for j in range(n_factors):
                col = ret[:, j]
                if np.nanmean(col) < 0:
                    ret[:, j] = -col

        df = pd.DataFrame(ret, index=list(dates), columns=list(factor_ids))

        # 覆盖率
        coverage = {
            fid: float(np.isfinite(df[fid]).mean()) for fid in factor_ids
        }
        n_valid_dates = int(np.isfinite(df.values).sum(axis=0).max()) if n_factors else 0
        if n_valid_dates < cfg.min_dates:
            raise ValueError(
                f"有效日期不足（{n_valid_dates} < {cfg.min_dates}），"
                f"请检查 panel 数据或降低 min_dates"
            )

        logger.info(
            "[FactorReturns] 构建完成: %d 因子 × %d 交易日 (quantile=%.2f, min_stocks=%d)",
            n_factors, n_dates, q, cfg.min_stocks,
        )
        return FactorReturnsResult(returns=df, coverage=coverage, config=cfg)

    # ─── 组合层辅助 ──────────────────────────────────────

    @staticmethod
    def align_to_factors(
        factor_returns: pd.DataFrame,
        factor_ids: Sequence[str],
    ) -> pd.DataFrame:
        """将因子收益矩阵对齐到指定因子子集并剔除缺失行。

        Args:
            factor_returns: 因子收益矩阵 (T × N)
            factor_ids: 需要保留的因子列

        Returns:
            对齐后的收益矩阵（仅保留 factor_ids 列且行无缺失）；无交集返回空 DataFrame
        """
        cols = [c for c in factor_ids if c in factor_returns.columns]
        if not cols:
            return pd.DataFrame()
        aligned = factor_returns.reindex(columns=cols).dropna(how="any")
        return aligned

    @staticmethod
    def portfolio_returns(
        factor_returns: pd.DataFrame,
        weights: Sequence[float],
    ) -> pd.Series:
        """组合收益序列 = w·R（逐期加权）。

        Args:
            factor_returns: 对齐后的因子收益矩阵 (T × N)
            weights: 权重序列（长度 N，与列序一致）

        Returns:
            组合收益 Series（index=因子收益行索引）
        """
        w = np.asarray(weights, dtype=float)
        total = float(np.sum(w))
        if total <= 0:
            w = np.ones(len(w)) / max(len(w), 1)
        else:
            w = w / total
        return pd.Series(
            factor_returns.values @ w,
            index=factor_returns.index,
            name="portfolio_returns",
        )

    @staticmethod
    def annualized_sharpe(returns: pd.Series, annualize_factor: float = 252.0) -> float:
        """年化夏普比率（mean/std × sqrt(annualize)）。"""
        r = returns.dropna().values
        if len(r) < 2:
            return 0.0
        std = float(np.std(r, ddof=1))
        if std <= 0:
            return 0.0
        return float(np.mean(r) / std * np.sqrt(annualize_factor))

    @staticmethod
    def max_abs_correlation(factor_returns: pd.DataFrame) -> float:
        """组合内最大因子间 |相关性|（对角线剔除）。"""
        if factor_returns.shape[1] < 2:
            return 0.0
        corr = factor_returns.corr().abs().values
        np.fill_diagonal(corr, 0.0)
        return float(np.nanmax(corr)) if corr.size else 0.0


__all__ = [
    "FactorReturnsConfig",
    "FactorReturnsResult",
    "FactorReturnsBuilder",
]
