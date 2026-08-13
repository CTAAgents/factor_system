"""
fts.factor_engine.macro_regime — Bridgewater 增长×通胀四象限宏观制度层（GAP-092，28 计划 §6 远期）。

在现有量价 Regime 体系（HMM 后验/规则软投票，regime.py）之上，叠加宏观制度判定维度：
以「增长 × 通胀」两维水平阈值划分四象限，输出制度状态 + 置信度 + 联合软概率，
供宏观制度报告/跨周期制度认知使用（不改变既有量价 regime 链路，向后兼容）。

四象限（Bridgewater All Weather 框架）:
  - overheat    增长↑ 通胀↑：过热 → 周期/商品、能源/工业金属偏强
  - goldilocks  增长↑ 通胀↓：金发女孩 → 风险偏好强，权益/商品均衡偏多
  - stagflation 增长↓ 通胀↑：滞胀 → 黄金/能源偏强，权益/利率品承压
  - recession   增长↓ 通胀↓：衰退 → 防御/利率品偏强，商品承压

数据输入（已闭环宏观基础设施）:
  - 通胀维度: CPI 当月同比 —— EastmoneyMacroSource（东财 RPT_ECONOMY_CPI，GAP-088 闭环，edb_cache）
  - 增长维度: 制造业 PMI —— akshare macro_china_pmi「制造业-指数」（GAP-087 同款，时序完整）
  - 月度数据天然发布滞后（当月数据次月可得），检测器不做额外 lag（数据可得性即滞后约束）

判定口径（水平阈值，GAP-092 设计）:
  - growth_score = clip((PMI − growth_threshold) / growth_scale, −1, 1)
  - inflation_score = clip((CPI − inflation_target) / inflation_scale, −1, 1)
  - 得分 ≥ 0 归"高"侧（边界语义），符号组合定象限
  - 联合软概率（独立假设）: p_g=(1+gs)/2, p_i=(1+is)/2
    P(overheat)=p_g·p_i / P(goldilocks)=p_g·(1−p_i) / P(stagflation)=(1−p_g)·p_i / P(recession)=(1−p_g)·(1−p_i)
  - confidence = 主象限概率（[0,1]，四象限均分时 0.25）

版本: v0.1.0
"""

from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict

import numpy as np
import pandas as pd

# ─── 四象限定义 ───────────────────────────────────────────


class MacroQuadrant(str, Enum):
    """Bridgewater 增长×通胀四象限。"""

    OVERHEAT = "overheat"  # 增长↑ 通胀↑
    GOLDILOCKS = "goldilocks"  # 增长↑ 通胀↓
    STAGFLATION = "stagflation"  # 增长↓ 通胀↑
    RECESSION = "recession"  # 增长↓ 通胀↓


QUADRANT_PROFILES: dict[str, dict[str, Any]] = {
    "overheat": {
        "label": "过热",
        "description": "增长↑ 通胀↑",
        "favored": ["周期/商品", "能源/工业金属"],
        "hedged": ["利率品/久期"],
        "narrative": "经济偏热、通胀上行，顺周期与商品资产占优，利率品承压。",
    },
    "goldilocks": {
        "label": "金发女孩",
        "description": "增长↑ 通胀↓",
        "favored": ["权益/风险资产", "成长/周期均衡"],
        "hedged": ["防御/黄金"],
        "narrative": "增长扩张且通胀温和回落，风险偏好最强，权益与商品均衡偏多。",
    },
    "stagflation": {
        "label": "滞胀",
        "description": "增长↓ 通胀↑",
        "favored": ["黄金", "能源"],
        "hedged": ["权益/利率品"],
        "narrative": "增长收缩而通胀高企，实际购买力受损，黄金/能源避险占优。",
    },
    "recession": {
        "label": "衰退",
        "description": "增长↓ 通胀↓",
        "favored": ["利率品/久期", "防御性资产"],
        "hedged": ["商品/周期"],
        "narrative": "增长收缩且通胀回落，政策宽松预期升温，利率品与防御资产占优。",
    },
}


# ─── 契约 ─────────────────────────────────────────────────


class MacroRegimeConfig(TypedDict, total=False):
    """四象限检测器配置（水平阈值口径）。

    growth_threshold: 增长荣枯线（默认 50.0，PMI 口径）。
    inflation_target: 通胀目标中枢（默认 2.0，%）。
    inflation_band:   通胀带宽（默认 2.0，% —— 超出目标±band 判高/低通胀）。
    growth_scale:     增长得分归一化尺度（默认 5.0 —— 超出阈值 ±5 个点打满）。
    inflation_scale:  通胀得分归一化尺度（默认 2.0 —— 超出中枢 ±2 个点打满）。
    """

    growth_threshold: float
    inflation_target: float
    inflation_band: float
    growth_scale: float
    inflation_scale: float


DEFAULT_MACRO_REGIME_CONFIG: MacroRegimeConfig = MacroRegimeConfig(
    growth_threshold=50.0,
    inflation_target=2.0,
    inflation_band=2.0,
    growth_scale=5.0,
    inflation_scale=2.0,
)
"""v2.104.0+3 锁定的四象限默认配置（PMI 50 荣枯线 / CPI 目标 2%±2%）。"""


class MacroRegimeResult(TypedDict):
    """四象限检测输出（对齐 MarketRegime 契约风格）。

    quadrant:        主象限（overheat/goldilocks/stagflation/recession）。
    confidence:      主象限概率（[0,1]，频率语义由联合软概率保证）。
    growth_score:    增长维度得分（[-1,1]，≥0 为高增长）。
    inflation_score: 通胀维度得分（[-1,1]，≥0 为高通胀）。
    growth_value:    增长原始值（最新有效 PMI）。
    inflation_value: 通胀原始值（最新有效 CPI）。
    quadrant_probs:  四象限联合软概率（和=1）。
    """

    quadrant: str
    confidence: float
    growth_score: float
    inflation_score: float
    growth_value: float
    inflation_value: float
    quadrant_probs: dict[str, float]


# ─── 检测器 ───────────────────────────────────────────────


class MacroRegimeDetector:
    """Bridgewater 增长×通胀四象限检测器（水平阈值判定）。

    输入为增长/通胀两条时序（任取最新有效值），输出主象限 + 置信度 + 联合软概率。
    数据缺失（空/全 NaN）时 detect 返回 None（无法判定，调用方降级）。

    Args:
        config: MacroRegimeConfig（None 时使用默认配置）。
    """

    def __init__(self, config: MacroRegimeConfig | None = None) -> None:
        cfg = dict(DEFAULT_MACRO_REGIME_CONFIG)
        if config:
            cfg.update({k: v for k, v in config.items() if v is not None})
        self.config: MacroRegimeConfig = cfg

    def detect(
        self,
        growth: pd.Series,
        inflation: pd.Series,
    ) -> MacroRegimeResult | None:
        """检测当前宏观四象限状态。

        Args:
            growth: 增长指标时序（如制造业 PMI，月度索引）。
            inflation: 通胀指标时序（如 CPI 当月同比，月度索引）。

        Returns:
            MacroRegimeResult；任一时序为空/全 NaN 时返回 None。
        """
        growth_v = _latest_valid(growth)
        inflation_v = _latest_valid(inflation)
        if growth_v is None or inflation_v is None:
            return None

        growth_score = _clip_score(
            (growth_v - float(self.config["growth_threshold"])) / float(self.config["growth_scale"])
        )
        inflation_score = _clip_score(
            (inflation_v - float(self.config["inflation_target"])) / float(self.config["inflation_scale"])
        )

        growth_high = growth_score >= 0.0  # 边界归"高"侧
        inflation_high = inflation_score >= 0.0
        if growth_high and inflation_high:
            quadrant = MacroQuadrant.OVERHEAT
        elif growth_high and not inflation_high:
            quadrant = MacroQuadrant.GOLDILOCKS
        elif not growth_high and inflation_high:
            quadrant = MacroQuadrant.STAGFLATION
        else:
            quadrant = MacroQuadrant.RECESSION

        # 联合软概率（独立假设：增长↑ 与 通胀↑ 的概率乘积）
        p_g = (1.0 + growth_score) / 2.0
        p_i = (1.0 + inflation_score) / 2.0
        probs: dict[str, float] = {
            MacroQuadrant.OVERHEAT.value: p_g * p_i,
            MacroQuadrant.GOLDILOCKS.value: p_g * (1.0 - p_i),
            MacroQuadrant.STAGFLATION.value: (1.0 - p_g) * p_i,
            MacroQuadrant.RECESSION.value: (1.0 - p_g) * (1.0 - p_i),
        }

        return MacroRegimeResult(
            quadrant=quadrant.value,
            confidence=float(probs[quadrant.value]),
            growth_score=float(growth_score),
            inflation_score=float(inflation_score),
            growth_value=float(growth_v),
            inflation_value=float(inflation_v),
            quadrant_probs={k: float(v) for k, v in probs.items()},
        )

    @staticmethod
    def quadrant_profile(quadrant: str) -> dict[str, Any] | None:
        """查询象限画像（板块偏好/宏观含义）；未知象限返回 None。"""
        return QUADRANT_PROFILES.get(quadrant)


def _latest_valid(series: pd.Series) -> float | None:
    """返回时序最新有效值（dropna 后尾部）；空/全 NaN 返回 None。"""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    return float(s.iloc[-1])


def _clip_score(x: float) -> float:
    """得分 clip 到 [-1, 1]。"""
    return float(np.clip(x, -1.0, 1.0))
