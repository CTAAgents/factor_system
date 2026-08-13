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

import numpy as np
import pandas as pd

try:
    from scipy.stats import kruskal, spearmanr

    _SCIPY = True
except ImportError:  # pragma: no cover
    kruskal = None
    spearmanr = None
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


# ─── G7: 5-Regime 因子拆分检验（35-gap-closure-plan §4.4）─────


def _regime_icir(signal: np.ndarray, forward_returns: np.ndarray, block_size: int = 20) -> float:
    """制度内块状 IC 序列的 ICIR（mean/std）。

    IC 跨块恒定（零波动）时视为充分稳定返回 999.0；块数 <2 返回 NaN。
    """
    sig = np.asarray(signal, dtype=float)
    fwd = np.asarray(forward_returns, dtype=float)
    n = len(sig)
    ics: list[float] = []
    for s in range(0, n - block_size + 1, block_size):
        e = s + block_size
        m = np.isfinite(sig[s:e]) & np.isfinite(fwd[s:e])
        if int(np.sum(m)) < 5:
            continue
        if not _SCIPY or spearmanr is None:
            ic = float(np.corrcoef(sig[s:e][m], fwd[s:e][m])[0, 1])
        else:
            ic = float(spearmanr(sig[s:e][m], fwd[s:e][m])[0])
        if np.isfinite(ic):
            ics.append(ic)
    if len(ics) < 2:
        return float("nan")
    mu = float(np.mean(ics))
    sd = float(np.std(ics, ddof=1))
    if sd < 1e-10:
        return 999.0 if mu != 0 else 0.0
    return float(mu / sd)


def validate_factor_across_regimes(
    factor_signal: pd.Series,
    forward_returns: pd.Series,
    regime_series: pd.Series,
    min_positive_regimes: int = 3,
    min_regime_samples: int = 20,
) -> dict:
    """按 5 制度拆分检验因子（G7，替代 WF 正占比近似）。

    与既有 ``validate_regime_predictive_power`` 互补：
      - 前者检验「Regime 标签能否区分前向收益/波动」；
      - 本函数检验「因子在每个 Regime 下是否都有效」。

    判定规则:
        - 覆盖制度数 = 样本 ≥ min_regime_samples 的制度数；
        - 正向制度数 = 其中 ICIR > 0 的制度数；
        - passed = 覆盖 ≥3 制度 且 正向制度数 ≥ min_positive_regimes；
        - regime_dependent = 任一覆盖制度 ICIR < -0.5（环境依赖标记，不否决——
          避免误杀区间型因子，入库标记 regime_dependent）。

    Args:
        factor_signal: 因子信号序列（index 与 regime_series 对齐）
        forward_returns: 前向收益序列
        regime_series: 制度标签序列（bull/bear/oscillate/high_vol/low_vol）
        min_positive_regimes: 最少正向制度数（默认 3）
        min_regime_samples: 制度最小样本数（默认 20）

    Returns:
        {passed, regime_dependent, n_regimes_covered, n_positive,
         per_regime: {regime: {ic, icir, n}}}
    """
    df = pd.DataFrame({"regime": regime_series, "signal": factor_signal, "fwd": forward_returns}).dropna()
    if df.empty:
        return {
            "passed": False,
            "regime_dependent": False,
            "n_regimes_covered": 0,
            "n_positive": 0,
            "per_regime": {},
        }

    per_regime: dict[str, dict] = {}
    for r, g in df.groupby("regime"):
        if len(g) < min_regime_samples:
            continue
        if not _SCIPY or spearmanr is None:
            ic = float(np.corrcoef(g["signal"], g["fwd"])[0, 1])
        else:
            ic = float(spearmanr(g["signal"], g["fwd"])[0])
        icir = _regime_icir(g["signal"].to_numpy(dtype=float), g["fwd"].to_numpy(dtype=float))
        per_regime[str(r)] = {"ic": ic, "icir": icir, "n": int(len(g))}

    covered = [r for r in per_regime if per_regime[r]["n"] >= min_regime_samples]
    positive = [r for r in covered if np.isfinite(per_regime[r]["icir"]) and per_regime[r]["icir"] > 0]
    regime_dependent = any(
        np.isfinite(per_regime[r]["icir"]) and per_regime[r]["icir"] < -0.5 for r in covered
    )
    passed = len(covered) >= 3 and len(positive) >= min_positive_regimes
    return {
        "passed": bool(passed),
        "regime_dependent": bool(regime_dependent),
        "n_regimes_covered": len(covered),
        "n_positive": len(positive),
        "per_regime": per_regime,
    }
