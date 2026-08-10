"""
fts.factor_engine.stock_regime — A 股行业轮动与风格轮动制度检测（GAP-S03）。

检测两个维度的股票市场制度:
  1. 行业轮动维度: 基于申万一级行业收益面板的动量离散度与集中度，
     判定「行业集中 / 行业轮动 / 均衡」三种状态。
  2. 风格切换维度: 基于大小盘、成长价值指数比值的动量方向，
     判定「large_cap / small_cap」「growth / value」风格状态。

多周期集成复用 `regime_hmm.MultiHorizonHMMDetector`（P1.2），
风格比值序列构造合成 OHLCV 后送入 HMM 获取趋势态与置信度；
hmmlearn 不可用时自动回退规则动量判定。

输出 `StockRegime` 契约兼容 `MarketRegime`（regime/confidence/detected_at/
features/method），其中 `regime` 字段映射 `REGIME_STYLE_MULTIPLIERS`
新增的股票风格键（large_cap/small_cap/growth/value/
sector_concentrated/sector_rotating），驱动 L3 风格自适应权重。

版本: v2.63.0（GAP-S03）
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, TypedDict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# hmmlearn 是可选依赖，HMM 集成失败时回退规则动量判定
_HMM_AVAILABLE: bool = False
try:
    from hmmlearn import hmm  # noqa: F401

    _HMM_AVAILABLE = True
except ImportError:
    pass

from fts.factor_engine.regime_hmm import MultiHorizonHMMDetector  # noqa: E402


# ─── 默认参数 ─────────────────────────────────────────────

_DEFAULT_MOMENTUM_DAYS = 20  # 行业/风格动量回看窗口（交易日）
_DEFAULT_LOOKBACK_DAYS = 60  # 轮动强度回看窗口
_DEFAULT_TOP_N = 5  # 集中度计算取前 N 个行业
_CONCENTRATION_THRESHOLD = 0.5  # top5 动量占比 > 阈值 → 行业集中
_ROTATION_STD_THRESHOLD = 0.005  # 行业动量横截面 std > 阈值 → 强轮动/分化
_MIN_SAMPLES = 30  # 最少样本数，不足返回 fallback


# ─── 契约 ─────────────────────────────────────────────────


class StockIndustryState(TypedDict, total=False):
    """行业轮动状态。"""

    state: str  # "concentrated" / "rotating" / "balanced"
    rotation_strength: float  # 行业动量横截面离散度（std）
    concentration: float  # top-N 行业动量占比（0~1）
    top_industries: list[str]  # 动量最强的 N 个行业
    industry_momentum: dict[str, float]  # 各行业动量
    features: dict[str, Any]


class StockStyleState(TypedDict, total=False):
    """风格切换状态。"""

    size_state: str  # "large_cap" / "small_cap" / "unknown"
    growth_state: str  # "growth" / "value" / "unknown"
    size_ratio_momentum: float  # 大盘/小盘 比值动量（>0 大盘占优）
    growth_ratio_momentum: float  # 成长/价值 比值动量（>0 成长占优）
    confidence: float  # 风格置信度 0~1
    features: dict[str, Any]


class StockRegime(TypedDict):
    """股票市场制度检测结果（兼容 MarketRegime + 扩展字段）。"""

    regime: str  # REGIME_STYLE_MULTIPLIERS 键（风格优先，行业兜底）
    confidence: float  # 置信度 0~1
    detected_at: str  # ISO 8601
    features: dict[str, Any]  # 检测特征（含 industry/style 子状态）
    method: str  # "stock_hmm" / "stock_rule" / "fallback"
    industry: StockIndustryState  # 行业轮动子状态
    style: StockStyleState  # 风格切换子状态


# ─── 检测器 ───────────────────────────────────────────────


class StockRegimeSelector:
    """A 股行业轮动 + 风格轮动制度检测器。

    输入:
        industry_panel: 行业收益面板 {行业名 → pd.Series(收益率)}，
                        或 {行业名 → OHLCV DataFrame}（自动取 close pct_change）。
        style_panel:    风格指数面板 {风格名 → pd.Series(收盘价)}，
                        需包含 large/small/growth/value 四类（大小写不敏感，
                        前缀匹配 "large"/"small"/"growth"/"value"）。

    输出:
        detect() → StockRegime（regime 字段可驱动 REGIME_STYLE_MULTIPLIERS）。
    """

    def __init__(
        self,
        momentum_days: int = _DEFAULT_MOMENTUM_DAYS,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
        top_n: int = _DEFAULT_TOP_N,
        concentration_threshold: float = _CONCENTRATION_THRESHOLD,
        rotation_std_threshold: float = _ROTATION_STD_THRESHOLD,
        use_hmm: bool = True,
    ) -> None:
        self.momentum_days = momentum_days
        self.lookback_days = lookback_days
        self.top_n = top_n
        self.concentration_threshold = concentration_threshold
        self.rotation_std_threshold = rotation_std_threshold
        self._use_hmm = use_hmm and _HMM_AVAILABLE
        self._multi_hmm: MultiHorizonHMMDetector | None = None
        if self._use_hmm:
            try:
                self._multi_hmm = MultiHorizonHMMDetector()
            except Exception:
                self._multi_hmm = None

    # ── 主入口 ────────────────────────────────────────────

    def detect(
        self,
        industry_panel: dict[str, pd.Series | pd.DataFrame] | None = None,
        style_panel: dict[str, pd.Series] | None = None,
    ) -> StockRegime:
        """综合检测行业轮动 + 风格切换状态。

        参数:
            industry_panel: 行业收益面板（见类 docstring）。
            style_panel:    风格指数面板（需含 large/small/growth/value）。

        返回:
            StockRegime — regime 字段 = 风格主状态（无风格数据时取行业状态，
            均无数据返回 "oscillate" fallback）。
        """
        industry_state = self.detect_industry(industry_panel)
        style_state = self.detect_style(style_panel)

        if style_state.get("size_state") != "unknown" or style_state.get("growth_state") != "unknown":
            regime_name = self._style_regime_key(style_state)
            method = "stock_hmm" if self._multi_hmm is not None else "stock_rule"
        elif industry_state.get("state") != "unknown":
            regime_name = self._industry_regime_key(industry_state)
            method = "stock_rule"
        else:
            regime_name = "oscillate"
            method = "fallback"

        confidence = float(style_state.get("confidence", 0.5)) if style_state.get("confidence", 0.0) > 0 else 0.5

        return StockRegime(
            regime=regime_name,
            confidence=round(max(0.3, min(0.99, confidence)), 4),
            detected_at=datetime.now().isoformat(),
            features={
                "industry": dict(industry_state),
                "style": dict(style_state),
                "momentum_days": self.momentum_days,
                "lookback_days": self.lookback_days,
            },
            method=method,
            industry=industry_state,
            style=style_state,
        )

    # ── 行业轮动维度 ──────────────────────────────────────

    def detect_industry(
        self,
        industry_panel: dict[str, pd.Series | pd.DataFrame] | None,
    ) -> StockIndustryState:
        """行业轮动状态检测。

        逻辑:
          - 各行业动量 = 最近 momentum_days 累计收益（收益面板直接取尾部，
            OHLCV 面板取 close.pct_change 尾部累计）。
          - rotation_strength = 行业动量横截面 std（离散度）。
          - concentration = top_n 行业动量占比（动量绝对值归一化）。
          - state: 样本不足 → unknown；集中度高且动量分化大 → concentrated；
            否则按离散度阈值区分 rotating / balanced。
        """
        if not industry_panel:
            return StockIndustryState(
                state="unknown",
                rotation_strength=0.0,
                concentration=0.0,
                top_industries=[],
                industry_momentum={},
                features={},
            )

        momentum: dict[str, float] = {}
        for name, panel in industry_panel.items():
            rets = self._to_returns(panel)
            if rets is None or len(rets) < self.momentum_days + 2:
                continue
            # 尾部 momentum_days 累计收益
            m = float((1.0 + rets.tail(self.momentum_days)).prod() - 1.0)
            if np.isfinite(m):
                momentum[name] = m

        if len(momentum) < 2:
            return StockIndustryState(
                state="unknown",
                rotation_strength=0.0,
                concentration=0.0,
                top_industries=[],
                industry_momentum=momentum,
                features={},
            )

        values = np.array(list(momentum.values()), dtype=float)
        rotation_strength = float(np.std(values))
        total_abs = float(np.sum(np.abs(values))) or 1.0
        top_industries = sorted(momentum, key=momentum.get, reverse=True)[: self.top_n]
        concentration = float(np.sum(np.abs(values[np.argsort(-np.abs(values))[: self.top_n]])) / total_abs)

        if rotation_strength > self.rotation_std_threshold and concentration > self.concentration_threshold:
            state = "concentrated"  # 主线集中：少数行业强势
        elif rotation_strength > self.rotation_std_threshold:
            state = "rotating"  # 强分化但无绝对主线
        else:
            state = "balanced"  # 行业间动量均衡

        return StockIndustryState(
            state=state,
            rotation_strength=round(rotation_strength, 6),
            concentration=round(concentration, 4),
            top_industries=top_industries,
            industry_momentum={k: round(v, 6) for k, v in momentum.items()},
            features={"n_industries": len(momentum)},
        )

    # ── 风格切换维度 ──────────────────────────────────────

    def detect_style(
        self,
        style_panel: dict[str, pd.Series | pd.DataFrame] | None,
    ) -> StockStyleState:
        """风格切换状态检测。

        逻辑:
          - 从风格面板提取 large/small/growth/value 收盘序列（前缀匹配）。
          - 大小盘比值 = large / small；成长价值比值 = growth / value。
          - 比值动量 = 尾部 momentum_days 累计变化率（>0 表示前者占优）。
          - 多周期集成: 比值序列构造合成 OHLCV 送入 MultiHorizonHMMDetector，
            获取趋势态（bull→占优方持续，bear→切换）与置信度；
            HMM 不可用/失败时回退规则动量判定，置信度 = min(0.7, 0.3 + |动量|)。
        """
        if not style_panel:
            return StockStyleState(
                size_state="unknown",
                growth_state="unknown",
                size_ratio_momentum=0.0,
                growth_ratio_momentum=0.0,
                confidence=0.0,
                features={},
            )

        large = self._extract_style_series(style_panel, ("large",))
        small = self._extract_style_series(style_panel, ("small",))
        growth = self._extract_style_series(style_panel, ("growth",))
        value = self._extract_style_series(style_panel, ("value",))

        size_state, size_mom, size_conf, size_feats = self._detect_pair(
            large,
            small,
            "large_cap",
            "small_cap",
            "size",
        )
        growth_state, growth_mom, growth_conf, growth_feats = self._detect_pair(
            growth,
            value,
            "growth",
            "value",
            "growth_value",
        )

        # 风格置信度 = 两维度中较高者（有数据维度）
        confs = [c for c in (size_conf, growth_conf) if c > 0]
        confidence = float(max(confs)) if confs else 0.0

        return StockStyleState(
            size_state=size_state,
            growth_state=growth_state,
            size_ratio_momentum=round(size_mom, 6),
            growth_ratio_momentum=round(growth_mom, 6),
            confidence=round(confidence, 4),
            features={"size": size_feats, "growth_value": growth_feats},
        )

    # ── 内部工具 ──────────────────────────────────────────

    def _detect_pair(
        self,
        a: pd.Series | None,
        b: pd.Series | None,
        pos_label: str,
        neg_label: str,
        key: str,
    ) -> tuple[str, float, float, dict[str, Any]]:
        """检测一对风格指数的相对占优状态。

        返回:
            (state, ratio_momentum, confidence, features)
        """
        if a is None or b is None:
            return "unknown", 0.0, 0.0, {"available": False}

        idx = a.index.intersection(b.index)
        if len(idx) < _MIN_SAMPLES:
            return "unknown", 0.0, 0.0, {"available": False, "samples": len(idx)}

        a_s = a.loc[idx].dropna()
        b_s = b.loc[idx].dropna()
        common = a_s.index.intersection(b_s.index)
        if len(common) < _MIN_SAMPLES:
            return "unknown", 0.0, 0.0, {"available": False, "samples": len(common)}

        ratio = a_s.loc[common] / b_s.loc[common].replace(0, np.nan)
        ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
        if len(ratio) < _MIN_SAMPLES:
            return "unknown", 0.0, 0.0, {"available": False, "samples": len(ratio)}

        # 规则动量：方向主判定（ratio 尾部 momentum_days 累计变化率）
        mom = (
            float(ratio.tail(self.momentum_days).iloc[-1] / ratio.tail(self.momentum_days).iloc[0] - 1.0)
            if len(ratio) > self.momentum_days + 1
            else float(ratio.iloc[-1] / ratio.iloc[0] - 1.0)
        )
        rule_pos = mom >= 0
        state = pos_label if rule_pos else neg_label

        features: dict[str, Any] = {"available": True, "ratio_momentum": round(mom, 6), "n_samples": len(ratio)}

        # 多周期 HMM 集成（P1.2）：比值序列 → 合成 OHLCV → 趋势态
        # 用于校正置信度：HMM 与规则动量方向一致时置信度提升，冲突时折减
        if self._multi_hmm is not None:
            try:
                synth = self._build_synth_ohlcv(ratio)
                regime, conf, hmm_feats = self._multi_hmm.predict(synth)
                if regime in ("bull", "bear") and conf >= 0.3:
                    hmm_pos = regime == "bull"  # bull = 比值上升 = pos 占优
                    consistency = 1.0 if (hmm_pos == rule_pos) else 0.5
                    confidence = float(conf * consistency)
                    features["hmm_regime"] = regime
                    features["hmm_confidence"] = round(conf, 4)
                    features["hmm_consistent"] = consistency == 1.0
                    return state, mom, confidence, features
            except Exception as e:
                logger.debug("StockRegime HMM 检测失败（%s），回退规则动量", e)

        # 规则回退置信度
        confidence = min(0.7, 0.3 + abs(mom) * 2.0) if state != "unknown" else 0.0
        return state, mom, confidence, features

    @staticmethod
    def _extract_style_series(
        style_panel: dict[str, pd.Series | pd.DataFrame],
        prefixes: tuple[str, ...],
    ) -> pd.Series | None:
        """按前缀匹配风格面板（大小写不敏感），返回收盘序列。

        支持两类输入:
          - pd.Series: 价格序列（自动检测收益率形态并累乘）或已收益率序列。
          - pd.DataFrame: 含 close 列（取 close）。
        """
        for name, series in style_panel.items():
            lower = name.lower()
            if not any(lower.startswith(p) for p in prefixes):
                continue
            s: pd.Series
            if isinstance(series, pd.DataFrame):
                if "close" not in series.columns:
                    continue
                s = series["close"].dropna()
            else:
                s = series
            # 若为收益率序列（值域小且含负值），累乘为价格
            if len(s) > 1 and s.dropna().abs().max() < 1.0 and (s < 0).any():
                s = (1.0 + s).cumprod()
            return s
        return None

    @staticmethod
    def _to_returns(panel: pd.Series | pd.DataFrame) -> pd.Series | None:
        """归一化输入为收益序列。"""
        if isinstance(panel, pd.Series):
            # 已收益序列：直接返回；若为价格序列（无负值、幅度大）则转收益
            s = panel.dropna()
            if len(s) < 2:
                return None
            if (s < 0).any() and s.abs().max() < 1.0:
                return s
            return s.pct_change()
        if isinstance(panel, pd.DataFrame):
            if "close" not in panel.columns:
                return None
            return panel["close"].dropna().pct_change()
        return None

    @staticmethod
    def _build_synth_ohlcv(series: pd.Series) -> pd.DataFrame:
        """将比值序列构造为合成 OHLCV（open/high/low 用 close 近似）。"""
        close = series
        df = pd.DataFrame(
            {
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": np.ones(len(close)),
            }
        )
        df.index = pd.DatetimeIndex(df.index)
        return df

    @staticmethod
    def _style_regime_key(style: StockStyleState) -> str:
        """风格状态 → REGIME_STYLE_MULTIPLIERS 键（动量幅度更大的维度优先）。"""
        g_state = style.get("growth_state")
        s_state = style.get("size_state")
        g_mom = abs(style.get("growth_ratio_momentum", 0.0))
        s_mom = abs(style.get("size_ratio_momentum", 0.0))
        if g_state in ("growth", "value") and g_mom >= s_mom:
            return str(g_state)
        if s_state in ("large_cap", "small_cap"):
            return str(s_state)
        if g_state in ("growth", "value"):
            return str(g_state)
        return "oscillate"

    @staticmethod
    def _industry_regime_key(industry: StockIndustryState) -> str:
        """行业状态 → REGIME_STYLE_MULTIPLIERS 键。"""
        state = industry.get("state")
        if state == "concentrated":
            return "sector_concentrated"
        if state == "rotating":
            return "sector_rotating"
        return "oscillate"
