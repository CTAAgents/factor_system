"""
fts.factor_engine.regime_beta_layer — L0 宏观 Beta 层（plans/55）。

识别市场 Beta 方向（RISK_ON / RISK_OFF / RANGE_BOUND）并输出顺 β 方向配置敞口
所需的缩放/偏置系数。哲学依据（外部 Regime-Driven 文档 §1.3 Beta 优先 / §5.1 L1）：
组合收益 = β × 市场收益 + α + 噪声，期货多空双向下 Beta 的意义是"顺 β 方向
配置敞口"——正 Beta 做多进攻、负 Beta 反向做空进攻、状态不明防御，Alpha 仅作
识别错误的缓冲。

信号（全部日频，金融期货 CFFEX 品种池内可算）:
  - trend_score : 金融期货合成指数 MA20 - MA60 归一化趋势（趋势有惯性）
  - vol_score   : 20 日 realized vol 年化（历史分位阈值，波动有状态）
  - risk_pref   : IF0/TF0 股债比 20 日滚动 z-score（资金信念，风险偏好上行 = 正）

判定（软投票）:
  - RISK_ON : trend 向上 + risk_pref 上行 + 波动不高（≥ min_votes 且多于 OFF 票）
  - RISK_OFF : trend 向下 + (risk_pref 下行 或 高波动)
  - RANGE_BOUND : 其余 / 置信度 < min_confidence（不偏置）
  - unknown : 数据不足（不偏置，scale=1.0，零行为变更）

消费（顺 β 方向配置敞口）:
  - compute_beta_scale : 组合总敞口倍率（RISK_OFF → off_scale 压缩）
  - apply_beta_bias    : 多空不对称方向偏置（RISK_OFF 降多头/放空头，期货反向进攻）

HARNESS §契约优先：BetaLayerConfig / BetaState / BetaDetector / compute_beta_scale /
apply_beta_bias 即对外契约；灰度开关 l3.regime_beta_layer.enabled（默认 false）。

版本: v0.1.0（plans/55 §A）
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ─── 状态定义 ─────────────────────────────────────────────

RISK_ON = "RISK_ON"
RISK_OFF = "RISK_OFF"
RANGE_BOUND = "RANGE_BOUND"
UNKNOWN = "unknown"

_BETA_STATES = (RISK_ON, RISK_OFF, RANGE_BOUND, UNKNOWN)


class BetaState(TypedDict):
    """L0 宏观 Beta 状态检测结果（对外契约）。"""

    state: str  # RISK_ON / RISK_OFF / RANGE_BOUND / unknown
    confidence: float  # 软投票置信度 0~1
    trend_score: float  # 合成指数归一化趋势（MA20-MA60 相对值）
    vol_score: float  # 最新年化 realized vol
    vol_ok: bool  # 波动是否低于历史分位阈值
    risk_pref: float  # 最新股债比（IF0/TF0）
    risk_pref_z: float  # 股债比 20 日滚动 z-score（缺失时 NaN）
    method: str  # "rule" / "fallback"


# ─── 配置契约 ─────────────────────────────────────────────


class BetaLayerConfig(BaseModel):
    """L0 宏观 Beta 层配置（config/settings.yaml → l3.regime_beta_layer，禁硬编码）。

    信号源: 金融期货合成指数（CFFEX 品种池）+ IF0/TF0 股债比（FTS 品种池内无 T0，
    以 TF0 五年期国债作为避险锚）。缩放系数为保守初始值，按回测/灰度观察校准。
    """

    enabled: bool = Field(default=False, description="灰度开关（默认关，零行为变更）")
    days: int = Field(default=130, ge=70, description="金融期货数据回溯天数（≥ 趋势长窗+余量）")
    fin_symbols: list[str] = Field(
        default_factory=lambda: ["IF0", "IH0", "IC0", "IM0", "TF0", "TS0"],
        description="金融期货合成指数成分（FTS 连续合约格式）",
    )
    trend_window_short: int = Field(default=20, ge=5, description="趋势短窗（MA20）")
    trend_window_long: int = Field(default=60, ge=20, description="趋势长窗（MA60）")
    vol_window: int = Field(default=20, ge=5, description="realized vol 窗口")
    vol_threshold_percentile: float = Field(
        default=0.8, ge=0.5, le=0.95, description="高波动历史分位阈值（vol 高于此 → OFF 倾向）"
    )
    risk_pref_pair: tuple[str, str] = Field(
        default=("IF0", "TF0"), description="股债比：风险资产 / 避险资产"
    )
    risk_pref_window: int = Field(default=20, ge=5, description="股债比滚动 z-score 窗口")
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度门槛：低于 → RANGE_BOUND（不偏置）")
    min_votes: int = Field(default=2, ge=1, le=3, description="软投票最少一致信号数")
    # 敞口缩放（C 模块 build_combo 乘性合并）
    on_scale: float = Field(default=1.0, ge=0.0, le=2.0, description="RISK_ON 总敞口倍率")
    off_scale: float = Field(default=0.5, ge=0.0, le=1.0, description="RISK_OFF 总敞口倍率（压缩）")
    # 多空不对称方向偏置（B 模块信号管线）
    on_long_boost: float = Field(default=0.10, ge=0.0, le=0.5, description="RISK_ON 多头加分")
    on_short_suppress: float = Field(default=0.10, ge=0.0, le=0.5, description="RISK_ON 空头减分")
    off_long_suppress: float = Field(default=0.40, ge=0.0, le=0.8, description="RISK_OFF 多头抑制")
    off_short_boost: float = Field(default=0.20, ge=0.0, le=0.5, description="RISK_OFF 空头放大（反向进攻）")


# ─── 金融期货合成指数 ─────────────────────────────────────


def _build_financial_index(panel: dict[str, pd.DataFrame]) -> pd.Series | None:
    """CFFEX 品种等权收益率指数（close 归一化首值=100 后截面均值）。

    对齐 `SectorRegimeSelector._build_sector_ohlcv` 的等权归一化思想
    （避免高价品种主导），但仅需 close 序列（Beta 层趋势/波动/股债比均
    基于收盘价，无需真实波幅）。

    Args:
        panel: {symbol: OHLCV DataFrame}。

    Returns:
        等权收益率指数 Series；有效品种 < 2 或数据不足时返回 None。
    """
    closes: dict[str, pd.Series] = {}
    for sym, df in panel.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        c = pd.to_numeric(df["close"], errors="coerce")
        if c.dropna().empty:
            continue
        closes[sym] = c
    if len(closes) < 2:
        return None
    df = pd.DataFrame(closes).dropna(how="all")
    if df.empty:
        return None
    first_valid = df.apply(lambda s: s.dropna().iloc[0])
    norm = df.div(first_valid, axis=1) * 100.0
    idx = norm.mean(axis=1).dropna()
    if len(idx) < 2:
        return None
    return idx


# ─── 检测器 ───────────────────────────────────────────────


class BetaDetector:
    """L0 宏观 Beta 层检测器（规则法，三信号软投票）。

    输入金融期货面板（CFFEX 日线，FTS 数据层可得），输出 BetaState。
    数据不足（品种 < 2 / 指数 < 2 行 / 无 risk_pref 且趋势波动不可算）→
    unknown（调用方不偏置，scale=1.0）。
    """

    def __init__(self, config: BetaLayerConfig | None = None) -> None:
        self.config = config or BetaLayerConfig()

    # ── 主入口 ──────────────────────────────────────────

    def detect(self, panel: dict[str, pd.DataFrame]) -> BetaState:
        """检测当前宏观 Beta 状态。

        Args:
            panel: 金融期货 OHLCV 面板 {symbol: DataFrame}。

        Returns:
            BetaState；数据不足时返回 unknown/0.0/fallback。
        """
        idx = _build_financial_index(panel)
        if idx is None:
            return self._unknown()
        try:
            rets = idx.pct_change().dropna()
            if len(rets) < max(self.config.trend_window_long + 2, 10):
                return self._unknown()

            # ── 信号 1: 趋势（MA20 - MA60 归一化） ──
            ma_short = idx.rolling(self.config.trend_window_short).mean()
            ma_long = idx.rolling(self.config.trend_window_long).mean()
            trend_series = (ma_short - ma_long) / (ma_long + 1e-9)
            trend_score = float(trend_series.iloc[-1])

            # ── 信号 2: 波动（年化 realized vol + 历史分位） ──
            vol_series = rets.rolling(self.config.vol_window).std() * np.sqrt(252)
            vol_hist = vol_series.dropna()
            if vol_hist.empty:
                return self._unknown()
            vol_now = float(vol_series.iloc[-1])
            vol_thr = float(vol_hist.quantile(self.config.vol_threshold_percentile))
            vol_ok = vol_now < vol_thr

            # ── 信号 3: 股债比 risk_pref（IF0/TF0 滚动 z-score） ──
            risk_pref_z = float("nan")
            risk_pref_val = float("nan")
            try:
                risk_sym, hedge_sym = self.config.risk_pref_pair
                if risk_sym in panel and hedge_sym in panel:
                    r_close = pd.to_numeric(panel[risk_sym]["close"], errors="coerce")
                    h_close = pd.to_numeric(panel[hedge_sym]["close"], errors="coerce")
                    ratio = r_close / h_close
                    ratio = ratio.reindex(idx.index)
                    # 换月/停牌日缺口前向填充（比值慢变，ffill 失真可忽略）；
                    # min_periods 兜底头部/极端缺口，仍不足 → z=NaN 由调用方降级。
                    ratio = ratio.ffill()
                    _min_p = max(5, self.config.risk_pref_window // 2)
                    mu = ratio.rolling(self.config.risk_pref_window, min_periods=_min_p).mean()
                    sd = ratio.rolling(self.config.risk_pref_window, min_periods=_min_p).std()
                    z = (ratio - mu) / (sd + 1e-12)
                    risk_pref_z = float(z.iloc[-1])
                    risk_pref_val = float(ratio.iloc[-1])
            except Exception as e:  # noqa: BLE001 — 股债比异常不阻断，信号缺失降级
                logger.warning("[BetaLayer] risk_pref 计算失败，信号缺失降级: %s", e)

            return self._decide(trend_score, vol_ok, risk_pref_z, risk_pref_val, vol_now)
        except Exception as e:  # noqa: BLE001 — 任何异常不阻断，回退 unknown
            logger.warning("[BetaLayer] 检测异常，回退 unknown: %s", e)
            return self._unknown()

    # ── 判定（文档语义：趋势方向投票 + 波动门控 + 股债比佐证） ──
    # vol 是"门控"而非独立投票：低波动是 RISK_ON 的必要条件（不单独给票），
    # 高波动需配合趋势向下才判 RISK_OFF（vol 高 ≠ 熊，防"横盘高波"误判）。

    def _decide(
        self,
        trend_score: float,
        vol_ok: bool,
        risk_pref_z: float,
        risk_pref_val: float,
        vol_now: float,
    ) -> BetaState:
        trend_on = trend_score > 0
        trend_off = trend_score < 0
        vol_high = not vol_ok
        risk_available = not (np.isnan(risk_pref_z) or np.isinf(risk_pref_z))
        risk_on = risk_available and risk_pref_z > 0
        risk_off = risk_available and risk_pref_z < 0

        # 置信度软投票：trend + vol（门控计票）+ risk_pref 方向，最多 3 票
        n_signals = 3 if risk_available else 2
        on_score = int(trend_on) + int(vol_ok) + (int(risk_on) if risk_available else 0)
        off_score = int(trend_off) + int(vol_high) + (int(risk_off) if risk_available else 0)

        # 判定（文档 §5.1 语义）
        if trend_on and vol_ok and (not risk_available or risk_on):
            state = RISK_ON
        elif trend_off and (vol_high or risk_off):
            state = RISK_OFF
        else:
            state = RANGE_BOUND

        if state == RISK_ON:
            confidence = on_score / n_signals
        elif state == RISK_OFF:
            confidence = off_score / n_signals
        else:
            confidence = max(on_score, off_score) / n_signals

        # 置信度门槛：不达标 → 视为 RANGE_BOUND（不偏置）
        if state in (RISK_ON, RISK_OFF) and confidence < self.config.min_confidence:
            state = RANGE_BOUND

        return BetaState(
            state=state,
            confidence=round(confidence, 4),
            trend_score=round(float(trend_score), 6),
            vol_score=round(float(vol_now), 6),
            vol_ok=bool(vol_ok),
            risk_pref=round(float(risk_pref_val), 6),
            risk_pref_z=round(float(risk_pref_z), 6),
            method="rule",
        )

    @staticmethod
    def _unknown() -> BetaState:
        return BetaState(
            state=UNKNOWN,
            confidence=0.0,
            trend_score=0.0,
            vol_score=0.0,
            vol_ok=False,
            risk_pref=float("nan"),
            risk_pref_z=float("nan"),
            method="fallback",
        )


# ─── 顺 β 方向配置敞口 ────────────────────────────────────


def compute_beta_scale(state: str, config: BetaLayerConfig | None = None) -> float:
    """组合总敞口倍率（C 模块 build_combo 乘性合并）。

    RISK_OFF → off_scale（压缩总敞口）；RISK_ON → on_scale；其余 → 1.0。
    注意：detect 已保证三态置信度达门槛，此处不再乘 confidence。

    Args:
        state: Beta 状态（RISK_ON/RISK_OFF/RANGE_BOUND/unknown）。
        config: BetaLayerConfig（None 时使用默认配置）。

    Returns:
        敞口倍率 ∈ [0, 2]；非三态返回 1.0。
    """
    cfg = config or BetaLayerConfig()
    if state == RISK_ON:
        return cfg.on_scale
    if state == RISK_OFF:
        return cfg.off_scale
    return 1.0


def alpha_buffer_scale(
    confidence: float,
    low_threshold: float = 0.3,
    mid_threshold: float = 0.6,
    mid_scale: float = 0.5,
) -> float:
    """Alpha 缓冲（plans/54 P2-2，文档 §7.3 动态 Beta-Alpha 分配）。

    Beta 识别不可靠时，组合从"高 Beta 策略"自动切换到"低 Beta 高 Alpha 模式"：
    - 高置信（≥ mid_threshold）→ 1.0（纯 Beta 重仓，让 Alpha 休息）
    - 中置信（low~mid）→ mid_scale（Beta 减半 + 保留 Alpha）
    - 低置信（< low_threshold）→ 0.0（切 Alpha 模式：中性/均值回归）

    Args:
        confidence: Beta 识别置信度 ∈ [0,1]。
        low_threshold: 低置信阈值（默认 0.3）。
        mid_threshold: 中置信阈值（默认 0.6）。
        mid_scale: 中置信档位（默认 0.5）。

    Returns:
        Beta 敞口倍率 ∈ [0,1]（乘性作用于 Beta 层敞口）。
    """
    c = max(0.0, min(1.0, float(confidence)))
    if c >= mid_threshold:
        return 1.0
    if c >= low_threshold:
        return float(mid_scale)
    return 0.0


def apply_beta_bias(
    sym_scores: dict[str, float],
    state: str,
    config: BetaLayerConfig | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """多空不对称方向偏置（B 模块信号管线，顺 β 方向配置敞口）。

    - RISK_ON : 多头 ×(1+on_long_boost)、空头 ×(1-on_short_suppress)（顺正 β 进攻）
    - RISK_OFF: 多头 ×(1-off_long_suppress)、空头 ×(1+off_short_boost)（顺负 β：降多、反向做空进攻）
    - 其余    : 不干预（×1.0）

    Args:
        sym_scores: 品种 → 综合得分（正=多头倾向，负=空头倾向）。
        state: Beta 状态。
        config: BetaLayerConfig（None 时使用默认配置）。

    Returns:
        (调整后得分, 偏置记录 dict) — 偏置记录含 state / long_factor / short_factor。
    """
    cfg = config or BetaLayerConfig()
    if state == RISK_ON:
        f_long = 1.0 + cfg.on_long_boost
        f_short = 1.0 - cfg.on_short_suppress
    elif state == RISK_OFF:
        f_long = 1.0 - cfg.off_long_suppress
        f_short = 1.0 + cfg.off_short_boost
    else:
        f_long = f_short = 1.0
    out = {s: sc * (f_long if sc >= 0 else f_short) for s, sc in sym_scores.items()}
    bias = {"state": state, "long_factor": round(f_long, 4), "short_factor": round(f_short, 4)}
    return out, bias


__all__ = [
    "RISK_ON",
    "RISK_OFF",
    "RANGE_BOUND",
    "UNKNOWN",
    "BetaState",
    "BetaLayerConfig",
    "BetaDetector",
    "compute_beta_scale",
    "apply_beta_bias",
    "alpha_buffer_scale",
]
