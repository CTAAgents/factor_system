"""
fts.factor_engine.regime_validation — 制度有效性样本外验证（28 计划 T9）。

对标:
  - 学术通则（知识库 VAE 市场画像一文局限性）: Regime 标签需样本外验证——
    制度标签须能区分前向收益/前向波动，否则不应驱动仓位。

当前包含:
  - validate_regime_predictive_power: 按制度分桶统计前向收益/前向波动，
    输出 Kruskal-Wallis 组间差异检验与各制度条件均值/波动差异。

版本: v0.1.0
"""

from __future__ import annotations

import pandas as pd

try:
    from scipy.stats import kruskal

    _SCIPY = True
except ImportError:  # pragma: no cover
    kruskal = None
    _SCIPY = False


def validate_regime_predictive_power(
    regime_series: pd.Series,
    forward_returns: pd.Series,
    forward_vol: pd.Series,
) -> dict:
    """按制度分桶统计前向收益/波动，输出区分度指标。

    检验制度标签对前向收益/前向波动的区分能力：
      - Kruskal-Wallis 非参数检验（组间前向收益分布差异，scipy 可用且分组 ≥2 时）；
      - 各制度条件均值/波动差异（mean_fwd_return / mean_fwd_vol / fwd_return_std）。

    参数:
        regime_series:   制度标签序列（索引与 forward_returns/forward_vol 对齐）。
        forward_returns: 前向收益序列（与 regime_series 等长/对齐）。
        forward_vol:     前向波动序列（与 regime_series 等长/对齐）。

    返回:
        dict —
            "n": 有效样本数（dropna 后）；
            "kruskal_stat"/"kruskal_p": Kruskal-Wallis 统计量与 p 值
              （_SCIPY 且分组数 ≥2 且每组样本数 >1 时输出）；
            每个制度名: {count, mean_fwd_return, mean_fwd_vol, fwd_return_std}。
        空数据返回 {"n": 0, "error": "empty"}。
    """
    df = pd.DataFrame({"regime": regime_series, "fwd": forward_returns, "fwd_vol": forward_vol}).dropna()
    if df.empty:
        return {"n": 0, "error": "empty"}
    groups = {r: g for r, g in df.groupby("regime")["fwd"]}
    out: dict = {"n": int(len(df))}
    if _SCIPY and len(groups) >= 2 and all(len(g) > 1 for g in groups.values()):
        stat, p = kruskal(*groups.values())
        out["kruskal_stat"] = float(stat)
        out["kruskal_p"] = float(p)
    for r, g in df.groupby("regime"):
        out[str(r)] = {
            "count": int(len(g)),
            "mean_fwd_return": float(g["fwd"].mean()),
            "mean_fwd_vol": float(g["fwd_vol"].mean()),
            "fwd_return_std": float(g["fwd"].std()),
        }
    return out
