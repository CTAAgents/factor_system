"""
fts.factor_engine.regime_crowding — 拥挤度体系化（plans/56）。

哲学依据（外部 Regime-Driven §7.2）：Beta 方向识别之后，还要识别方向是否已被
**拥挤透支**——期货多空双向下，拥挤既可能发生在多头，也可能发生在空头（如大举
做空后的逼空）。价值不在"经常对"，而在"错的时候亏得少"。

六信号（全部 Tier A 量价可算，板块/组合面板级聚合）:
  - corr_convergence : 板块内品种相关趋近 1（20 日滚动相关创新高）
  - volume_stall     : 放量滞涨 / 缩量阴跌
  - momentum_decay   : 多周期动量差走弱（短期显著弱于长期）
  - vol_structure    : realized vol 突升（最新 vs 历史分位）
  - oi_concentration : OI 天量分位（hold 缺失降级跳过）
  - turnover_overheat: 换手透支（换手率 vs 历史分位；hold 缺失降级量能比）

合成: crowding_score ∈ [0,1]（加权）+ direction（long/short/neutral）——
修复 G3：不再全 abs() 取模，区分多头拥挤（减多不抢反弹）与空头拥挤/逼空
（减空不追空）。

消费:
  - build_joint_gate_scale : 拥挤×置信度联合门控（B 模块，build_combo 乘性链）
  - apply_crowding_direction_bias : 多空方向抑制（C 模块，信号管线）

HARNESS §契约优先：CrowdingSignalConfig / CrowdingSignalResult /
compute_crowding_signals / build_joint_gate_scale / apply_crowding_direction_bias
即对外契约；灰度开关 l3.regime_crowding.enabled（默认 false）。

版本: v0.1.0（plans/56 §A）
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ─── 方向定义 ─────────────────────────────────────────────

LONG_CROWDED = "long"
SHORT_CROWDED = "short"
NEUTRAL = "neutral"


# ─── 配置契约 ─────────────────────────────────────────────


class CrowdingSignalConfig(BaseModel):
    """拥挤度 6 信号合成配置（config/settings.yaml → l3.regime_crowding，禁硬编码）。"""

    enabled: bool = Field(default=False, description="灰度开关（默认关，零行为变更）")
    days: int = Field(default=300, ge=150, description="拥挤度面板回溯天数（≥ 动量长窗 + 分位历史）")
    # 信号窗口与阈值
    corr_window: int = Field(default=20, ge=5, description="板块内品种相关滚动窗口")
    corr_percentile: float = Field(default=0.75, ge=0.5, le=0.99, description="相关历史分位（创新高 → 触发）")
    corr_min_level: float = Field(default=0.5, ge=0.0, le=1.0, description="相关绝对水平下限（低于此不触发）")
    volume_ratio_threshold: float = Field(default=1.5, ge=1.0, description="量能比阈值（volume/20日均量，放量）")
    price_stall_threshold: float = Field(default=0.01, ge=0.0, description="放量滞涨：|短期涨幅| < 阈值")
    momentum_short: int = Field(default=20, ge=5, description="动量短窗")
    momentum_long: int = Field(default=60, ge=20, description="动量长窗")
    vol_window: int = Field(default=20, ge=5, description="realized vol 窗口")
    vol_spike_percentile: float = Field(default=0.75, ge=0.5, le=0.99, description="波动突升分位")
    oi_window: int = Field(default=20, ge=5, description="OI 量能比窗口")
    oi_percentile: float = Field(default=0.75, ge=0.5, le=0.99, description="OI 天量分位")
    turnover_window: int = Field(default=20, ge=5, description="换手率窗口")
    turnover_percentile: float = Field(default=0.75, ge=0.5, le=0.99, description="换手透支分位")
    stall_trigger_ratio: float = Field(default=0.5, ge=0.0, le=1.0, description="板块内信号触发品种占比阈值")
    # 合成权重（和=1；实际按可用信号归一化）
    signal_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "corr_convergence": 0.20,
            "volume_stall": 0.15,
            "momentum_decay": 0.20,
            "vol_structure": 0.15,
            "oi_concentration": 0.15,
            "turnover_overheat": 0.15,
        }
    )
    high_crowding: float = Field(default=0.4, ge=0.0, le=1.0, description="高拥挤阈值（决策门校准：0.6→0.4）")
    direction_window: int = Field(default=20, ge=5, description="方向分解收益窗口")
    # 联合门控（B 模块）
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度高/低分界")
    high_conf_high_crowd_scale: float = Field(default=0.5, ge=0.0, le=1.0, description="高置信+高拥挤 → 减半")
    low_conf_high_crowd_scale: float = Field(default=0.0, ge=0.0, le=1.0, description="低置信+高拥挤 → 离场")
    # 多空方向抑制（C 模块）
    long_crowd_suppress: float = Field(default=0.30, ge=0.0, le=0.8, description="多头拥挤：多头信号 ×(1-抑制)")
    short_crowd_suppress: float = Field(default=0.30, ge=0.0, le=0.8, description="空头拥挤：空头信号 ×(1-抑制)")


# ─── 结果契约 ─────────────────────────────────────────────


class CrowdingSignalResult(TypedDict):
    """拥挤度 6 信号合成结果（对外契约）。"""

    crowding_score: float  # ∈ [0,1]，越高越拥挤
    direction: str  # "long" / "short" / "neutral"
    signals: dict[str, bool]  # 信号名 → 是否触发
    signal_values: dict[str, float]  # 信号名 → 原始量（诊断）
    n_signals_available: int  # 实际可用信号数（缺失降级后）
    method: str  # "rule" / "fallback"


# ─── 面板辅助 ─────────────────────────────────────────────


def _panel_index(panel: dict[str, pd.DataFrame]) -> pd.Index | None:
    """多数品种共有日期索引（至少 max(1, 品种数//2)）。"""
    dates: list[pd.Index] = []
    for df in panel.values():
        if df is None or df.empty or "close" not in df.columns:
            continue
        dates.append(pd.to_numeric(df["close"], errors="coerce").dropna().index)
    if not dates:
        return None
    common = pd.concat(dates, axis=1, keys=range(len(dates)))
    idx = common.dropna(thresh=max(1, len(dates) // 2)).index
    return idx if len(idx) > 1 else None


def _aligned_close_matrix(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """对齐后的 close 矩阵（品种 × 日期，多数对齐；单品种面板兼容）。"""
    closes = {
        s: pd.to_numeric(df["close"], errors="coerce")
        for s, df in panel.items()
        if df is not None and not df.empty and "close" in df.columns
    }
    if not closes:
        return pd.DataFrame()
    mat = pd.DataFrame(closes)
    if mat.empty:
        return pd.DataFrame()
    idx = mat.dropna(thresh=max(1, mat.shape[1] // 2)).index
    return mat.loc[idx]


def _aligned_col(panel: dict[str, pd.DataFrame], col: str) -> pd.DataFrame:
    """对齐后的指定列矩阵（缺失列品种跳过；全缺返回空）。"""
    mats = {}
    for s, df in panel.items():
        if df is None or df.empty or col not in df.columns:
            continue
        c = pd.to_numeric(df[col], errors="coerce")
        if c.dropna().empty:
            continue
        mats[s] = c
    if not mats:
        return pd.DataFrame()
    return pd.DataFrame(mats)


# ─── 6 信号 ───────────────────────────────────────────────


def _signal_corr_convergence(
    panel: dict[str, pd.DataFrame],
    cfg: CrowdingSignalConfig,
) -> tuple[bool, float]:
    """板块内品种 20 日滚动相关均值 vs 历史分位（趋近 1 创新高）。

    逐品种对计算滚动相关（DataFrame.rolling().corr() 无参调用不可用），
    截面均值序列与历史分位比较。
    """
    mat = _aligned_close_matrix(panel)
    if mat.shape[1] < 2 or len(mat) < cfg.corr_window + 2:
        return False, 0.0
    rets = mat.pct_change().dropna(how="all")
    if len(rets) < cfg.corr_window + 2:
        return False, 0.0
    cols = list(mat.columns)
    pair_corrs: list[pd.Series] = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = rets[cols[i]].rolling(cfg.corr_window).corr(rets[cols[j]])
            pair_corrs.append(r)
    if not pair_corrs:
        return False, 0.0
    cross = pd.concat(pair_corrs, axis=1).mean(axis=1, skipna=True).dropna()
    if cross.empty:
        return False, 0.0
    current = float(cross.iloc[-1])
    thr = float(cross.quantile(cfg.corr_percentile))
    # 容差 1e-6 防浮点边界（全程高相关时 current≈thr≈1.0）
    triggered = current + 1e-6 >= thr and current >= cfg.corr_min_level
    return triggered, current


def _signal_volume_stall(
    panel: dict[str, pd.DataFrame],
    cfg: CrowdingSignalConfig,
) -> tuple[bool, float]:
    """放量滞涨 / 缩量阴跌：板块内触发品种占比 ≥ stall_trigger_ratio。

    量能比 = volume / 20 日均量（ratio 口径，稳健于序列方差稀释）。
    """
    closes = _aligned_close_matrix(panel)
    volumes = _aligned_col(panel, "volume")
    if closes.empty or volumes.empty:
        return False, 0.0
    common = closes.index.intersection(volumes.index)
    closes, volumes = closes.loc[common], volumes.loc[common]
    if len(closes) < 15:
        return False, 0.0
    vol_ma = volumes.rolling(20).mean()
    vol_ratio = volumes / (vol_ma + 1e-12)
    price_chg = closes.pct_change(5)
    stall_lv = (vol_ratio.iloc[-1] > cfg.volume_ratio_threshold) & (
        price_chg.iloc[-1].abs() < cfg.price_stall_threshold
    )
    stall_sv = (vol_ratio.iloc[-1] < 1.0 / cfg.volume_ratio_threshold) & (
        price_chg.iloc[-1] < 0
    )
    ratio = float(((stall_lv | stall_sv).sum()) / max(1, closes.shape[1]))
    return ratio >= cfg.stall_trigger_ratio, ratio


def _signal_momentum_decay(
    panel: dict[str, pd.DataFrame],
    cfg: CrowdingSignalConfig,
) -> tuple[bool, float]:
    """多周期动量差走弱：短期动量显著弱于长期（板块等权指数口径）。"""
    mat = _aligned_close_matrix(panel)
    if mat.shape[1] < 1 or len(mat) < cfg.momentum_long + 2:
        return False, 0.0
    first_valid = mat.apply(lambda s: s.dropna().iloc[0])
    idx = (mat.div(first_valid, axis=1) * 100.0).mean(axis=1).dropna()
    if len(idx) < cfg.momentum_long + 2:
        return False, 0.0
    mom_s = float(idx.iloc[-1] / idx.iloc[-cfg.momentum_short - 1] - 1.0)
    mom_l = float(idx.iloc[-1] / idx.iloc[-cfg.momentum_long - 1] - 1.0)
    diff = mom_s - mom_l  # 短期相对长期的动量差
    # 历史 diff 序列（滚动）
    diffs = pd.Series(
        [
            idx.iloc[t] / idx.iloc[t - cfg.momentum_short] - 1.0
            - (idx.iloc[t] / idx.iloc[t - cfg.momentum_long] - 1.0)
            for t in range(cfg.momentum_long + 1, len(idx))
        ],
        dtype=float,
    )
    if diffs.empty:
        return False, diff
    thr = float(diffs.quantile(0.10))  # 差值为历史低 10% 分位 → 衰竭
    triggered = diff <= thr and diff < 0
    return triggered, diff


def _signal_vol_structure(
    panel: dict[str, pd.DataFrame],
    cfg: CrowdingSignalConfig,
) -> tuple[bool, float]:
    """realized vol 突升：最新 20 日年化 vol vs 历史分位（板块等权指数）。"""
    mat = _aligned_close_matrix(panel)
    if mat.shape[1] < 1 or len(mat) < cfg.vol_window + 5:
        return False, 0.0
    rets = mat.pct_change().mean(axis=1, skipna=True).dropna()
    if len(rets) < cfg.vol_window + 5:
        return False, 0.0
    vol = rets.rolling(cfg.vol_window).std() * np.sqrt(252)
    vol_h = vol.dropna()
    if vol_h.empty:
        return False, 0.0
    current = float(vol.iloc[-1])
    thr = float(vol_h.quantile(cfg.vol_spike_percentile))
    triggered = current >= thr
    return triggered, current


def _signal_oi_concentration(
    panel: dict[str, pd.DataFrame],
    cfg: CrowdingSignalConfig,
) -> tuple[bool, float]:
    """OI 天量分位：hold 量能比最新 vs 历史分位（hold 缺失 → 降级 False）。"""
    holds = _aligned_col(panel, "hold")
    if holds.empty or len(holds) < cfg.oi_window + 5:
        return False, 0.0
    ma = holds.rolling(cfg.oi_window).mean()
    ratio = holds / (ma + 1e-12)
    cross = ratio.mean(axis=1, skipna=True).dropna()  # 截面均值（单/多品种兼容）
    if cross.empty:
        return False, 0.0
    current = float(cross.iloc[-1])
    thr = float(cross.quantile(cfg.oi_percentile))
    triggered = current >= thr and current > 1.0
    return triggered, current


def _signal_turnover_overheat(
    panel: dict[str, pd.DataFrame],
    cfg: CrowdingSignalConfig,
) -> tuple[bool, float]:
    """换手透支：换手率（volume/hold）最新 vs 历史分位；hold 缺失降级量能比。"""
    volumes = _aligned_col(panel, "volume")
    holds = _aligned_col(panel, "hold")
    if volumes.empty or len(volumes) < cfg.turnover_window + 5:
        return False, 0.0
    if not holds.empty:
        common = volumes.index.intersection(holds.index)
        turnover = (volumes.loc[common] / (holds.loc[common] + 1e-12)).dropna(how="all")
    else:
        # hold 缺失：量能比代理（volume / 20 日均量）
        ma = volumes.rolling(cfg.turnover_window).mean()
        turnover = (volumes / (ma + 1e-12)).dropna(how="all")
    if turnover.empty or len(turnover) < cfg.turnover_window + 2:
        return False, 0.0
    cross = turnover.mean(axis=1, skipna=True).dropna()
    if cross.empty:
        return False, 0.0
    current = float(cross.iloc[-1])
    thr = float(cross.quantile(cfg.turnover_percentile))
    # 相对历史分位触发（真实换手率=volume/hold 通常 <1，不做绝对 >1 约束；
    # hold 缺失用量能比代理时同样按历史分位）
    triggered = current + 1e-9 >= thr
    return triggered, current


# ─── 合成 ─────────────────────────────────────────────────


def compute_crowding_signals(
    panel: dict[str, pd.DataFrame],
    config: CrowdingSignalConfig | None = None,
) -> CrowdingSignalResult:
    """合成拥挤度 6 信号（板块/组合面板级）。

    Args:
        panel: {symbol: OHLCV DataFrame}（含 close/volume，hold 可选）。
        config: CrowdingSignalConfig（None 时使用默认配置）。

    Returns:
        CrowdingSignalResult；面板数据不足时返回 score=0.0/neutral/fallback。
    """
    cfg = config or CrowdingSignalConfig()
    signals: dict[str, bool] = {}
    values: dict[str, float] = {}
    for name, fn in (
        ("corr_convergence", _signal_corr_convergence),
        ("volume_stall", _signal_volume_stall),
        ("momentum_decay", _signal_momentum_decay),
        ("vol_structure", _signal_vol_structure),
        ("oi_concentration", _signal_oi_concentration),
        ("turnover_overheat", _signal_turnover_overheat),
    ):
        try:
            trig, val = fn(panel, cfg)
            signals[name] = trig
            values[name] = round(float(val), 6)
        except Exception as e:  # noqa: BLE001 — 单信号失败降级跳过，不阻断
            logger.warning("[Crowding] 信号 %s 计算失败，降级跳过: %s", name, e)
            signals[name] = False
            values[name] = 0.0

    # 实际可用信号（值有效或已触发）；全部不可用（数据不足）→ fallback
    available = [n for n, v in values.items() if v != 0.0 or signals[n]]
    if not available:
        return CrowdingSignalResult(
            crowding_score=0.0,
            direction=NEUTRAL,
            signals=signals,
            signal_values=values,
            n_signals_available=0,
            method="fallback",
        )
    n_avail = len(available)

    weights = {k: max(0.0, float(w)) for k, w in cfg.signal_weights.items()}
    total_w = sum(weights.get(n, 0.0) for n in available) or 1.0
    score = sum(weights.get(n, 0.0) for n in available if signals[n]) / total_w
    score = float(np.clip(score, 0.0, 1.0))

    # 方向分解（修复 G3：不再全 abs 取模）——基于板块等权指数近期收益方向
    direction = NEUTRAL
    mat = _aligned_close_matrix(panel)
    if score >= cfg.high_crowding and not mat.empty and len(mat) > cfg.direction_window:
        first_valid = mat.apply(lambda s: s.dropna().iloc[0])
        idx = (mat.div(first_valid, axis=1) * 100.0).mean(axis=1).dropna()
        if len(idx) > cfg.direction_window:
            trend = float(idx.iloc[-1] / idx.iloc[-cfg.direction_window - 1] - 1.0)
            direction = LONG_CROWDED if trend > 0 else SHORT_CROWDED if trend < 0 else NEUTRAL

    return CrowdingSignalResult(
        crowding_score=round(score, 4),
        direction=direction,
        signals=signals,
        signal_values=values,
        n_signals_available=n_avail,
        method="rule",
    )


# ─── 消费 ①：联合门控（B 模块） ───────────────────────────


def build_joint_gate_scale(
    crowding_score: float,
    confidence: float,
    config: CrowdingSignalConfig | None = None,
) -> float:
    """拥挤×置信度联合门控（文档 §7.2，降档而非反手）。

    - 高置信 + 高拥挤 → high_conf_high_crowd_scale（默认 0.5 减半）
    - 低置信 + 高拥挤 → low_conf_high_crowd_scale（默认 0.0 离场）
    - 其余（低拥挤 / 中性）→ 1.0（不干预）

    Args:
        crowding_score: 拥挤度 ∈ [0,1]。
        confidence: 组合/制度置信度 ∈ [0,1]。
        config: CrowdingSignalConfig（None 时默认）。

    Returns:
        敞口倍率 ∈ [0,1]。
    """
    cfg = config or CrowdingSignalConfig()
    if crowding_score >= cfg.high_crowding:
        if confidence >= cfg.confidence_threshold:
            return cfg.high_conf_high_crowd_scale
        return cfg.low_conf_high_crowd_scale
    return 1.0


# ─── 消费 ②：多空方向抑制（C 模块） ───────────────────────


def apply_crowding_direction_bias(
    sym_scores: dict[str, float],
    direction: str,
    config: CrowdingSignalConfig | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """多空方向抑制（降档而非反手，逼空防御）。

    - long_crowded : 多头信号（正分）× (1-long_crowd_suppress)，空头不动（减多不抢反弹）
    - short_crowded: 空头信号（负分）× (1-short_crowd_suppress)，多头不动（减空不追空）
    - neutral      : 不干预（×1.0）

    Args:
        sym_scores: 品种 → 综合得分（正=多头倾向，负=空头倾向）。
        direction: 拥挤方向（long/short/neutral）。
        config: CrowdingSignalConfig（None 时默认）。

    Returns:
        (调整后得分, 偏置记录 dict)。
    """
    cfg = config or CrowdingSignalConfig()
    if direction == LONG_CROWDED:
        factor_long = 1.0 - cfg.long_crowd_suppress
        factor_short = 1.0
        label = "long_crowded"
    elif direction == SHORT_CROWDED:
        factor_long = 1.0
        factor_short = 1.0 - cfg.short_crowd_suppress
        label = "short_crowded"
    else:
        factor_long = factor_short = 1.0
        label = "neutral"
    out = {s: sc * (factor_long if sc >= 0 else factor_short) for s, sc in sym_scores.items()}
    bias = {
        "direction": label,
        "long_factor": round(factor_long, 4),
        "short_factor": round(factor_short, 4),
    }
    return out, bias


__all__ = [
    "LONG_CROWDED",
    "SHORT_CROWDED",
    "NEUTRAL",
    "CrowdingSignalConfig",
    "CrowdingSignalResult",
    "compute_crowding_signals",
    "build_joint_gate_scale",
    "apply_crowding_direction_bias",
]
