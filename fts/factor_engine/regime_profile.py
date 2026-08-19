"""
fts.factor_engine.regime_profile — Regime 条件化因子画像（plans/53 §A/§D）

背景
----
业务目标（plans/53 §1.1）：在识别到的市场制度（Regime）下，通过因子进行胜率和
回报率更高的交易。本模块为每个因子产出"因子×5 制度 × {IC, ICIR, 胜率, n}"的
资产化画像，供：
  - A 模块：`regime_ic_profile` 落库（factor_catalog.metadata），作为组合层条件化
    与晋升门槛的数据地基；
  - D 模块：`RegimeSeriesBuilder` 由市场面板构建历史制度标签序列，供画像计算与
    制度预测力验证（Kruskal-Wallis 决策门）复用。

与 subchain_profile.py（plans/47 §A）同构：子链画像管"因子×子链"，本模块管
"因子×Regime"，复用其已验证的落库/接线模板。

§A3 判定链（三门槛 AND，任一不过 → effective=False）
    ① n ≥ min_regime_samples(=20)：制度内有效样本数
    ② ICIR > 0：制度内块状 IC 的 mean/std（复用 regime_validation._regime_icir，
       方向为正才视为该制度下"有效"）
    ③ |ic| ≥ min_abs_ic(=0.03)：IC 幅度门槛（对齐评估链 min_ic）

胜率（win_rate）：该制度下前向收益为正的样本占比——对应业务目标"胜率"维度。

保守性设计（与子链画像一致）：误标（把全制度因子裁成部分制度）损失 alpha 与
多样性 > 漏标（维持现状）——护栏偏向漏标；画像不显著 → 保持全制度。

HARNESS §契约优先：RegimeProfileConfig / RegimeStat / RegimeProfile /
compute_regime_profile / build_regime_metadata / RegimeSeriesBuilder 即对外契约。

版本: v0.1.0（plans/53 草案）
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 复用 G7 的制度内 ICIR 计算（单一实现，防双实现漂移）
from .regime_validation import _regime_icir  # noqa: E402

try:
    from scipy.stats import spearmanr

    _SCIPY = True
except ImportError:  # pragma: no cover
    spearmanr = None
    _SCIPY = False

# 制度适用性 scope 类型：部分制度 = 制度名列表；"all"/"unknown" 为语义标记
RegimeScope = Union[list[str], str]


class RegimeProfileConfig(BaseModel):
    """Regime 画像护栏参数（config/settings.yaml → regime_profile.*，禁硬编码）。"""

    min_regime_samples: int = Field(default=20, ge=1, description="制度内最小样本数（门槛①）")
    min_positive_regimes: int = Field(default=2, ge=1, description="晋升门槛：最少正向制度数（plans/53 §C）")
    min_abs_ic: float = Field(default=0.05, ge=0.0, description="|IC| 幅度门槛（门槛③）。"
        "v0.1.0 实现校准：制度内样本（默认 20~60）远小于评估全样本（500），0.03 门槛下"
        "独立噪声制度实测误报率高（60 样本 Spearman 偶然相关可达 0.1）；0.05 为护栏滤噪声的"
        "合理折中（plans/47 子链 min_chain_ic=0.10 对应 3 品种更小样本）")
    all_regimes_effective_min: int = Field(default=3, ge=1, description="≥ 此制度数 effective 时 scope='all'")


class RegimeStat(BaseModel):
    """单制度统计画像（对外契约，含审计留痕字段）。"""

    n: int
    ic: Optional[float] = None
    icir: Optional[float] = None
    win_rate: Optional[float] = None  # 该制度下前向收益为正的样本占比
    effective: bool


class RegimeProfile(BaseModel):
    """因子 Regime 适用性画像（对外契约，plans/53 §A1）。"""

    factor_id: str
    regime_stats: dict[str, RegimeStat]
    regime_scope: RegimeScope = "unknown"
    regime_dependent: bool = False


def _regime_stat(signal: np.ndarray, fwd: np.ndarray, cfg: RegimeProfileConfig) -> RegimeStat:
    """单制度有效性判定（plans/53 §A3，三门槛 AND，任一不过 → effective=False）。

    Args:
        signal: 因子信号数组（与 fwd 对齐，可含 NaN）
        fwd: 前向收益数组
        cfg: 护栏参数

    Returns:
        RegimeStat — 含 n/ic/icir/win_rate/effective；门槛①拦截时统计字段为 None。
    """
    sig = np.asarray(signal, dtype=float)
    fwd = np.asarray(fwd, dtype=float)
    m = np.isfinite(sig) & np.isfinite(fwd)
    n = int(m.sum())
    if n < cfg.min_regime_samples:  # 门槛①：样本不足，不给机会
        return RegimeStat(n=n, ic=None, icir=None, win_rate=None, effective=False)
    s, f = sig[m], fwd[m]
    if _SCIPY and spearmanr is not None:
        ic = float(spearmanr(s, f)[0])
    else:  # pragma: no cover — scipy 不可用时 Pearson 兜底
        ic = float(np.corrcoef(s, f)[0, 1])
    if not np.isfinite(ic):
        ic = 0.0
    icir = _regime_icir(sig, fwd)  # 复用 G7 块状 ICIR（单一实现）
    win_rate = float((f > 0).mean())
    icir_positive = bool(np.isfinite(icir) and icir > 0)  # 门槛②
    ic_ok = abs(ic) >= cfg.min_abs_ic  # 门槛③
    return RegimeStat(
        n=n,
        ic=round(float(ic), 6),
        icir=(round(float(icir), 6) if np.isfinite(icir) else None),
        win_rate=round(win_rate, 6),
        effective=bool(icir_positive and ic_ok),
    )


def compute_regime_profile(
    factor_id: str,
    signal: np.ndarray,
    fwd_returns: np.ndarray,
    regime_series: Union[pd.Series, list[str], np.ndarray],
    cfg: Optional[RegimeProfileConfig] = None,
) -> RegimeProfile:
    """计算因子 Regime 适用性画像（plans/53 §A1）。

    Args:
        factor_id: 因子 ID（画像/落库标识）
        signal: 因子信号数组（长度 = n_dates）
        fwd_returns: 前向收益数组（与 signal 对齐）
        regime_series: 制度标签序列（与 signal 对齐；pd.Series 取 values，
            仅长度对齐即可，不要求 index 一致——调用方负责时间对齐）
        cfg: 护栏参数（None → 默认）

    Returns:
        RegimeProfile — 各制度 RegimeStat + 派生 scope/dependent：
          - effective ≥ all_regimes_effective_min → scope="all"
          - 1~2 制度 effective → scope=effective 制度列表
          - 无制度 effective → scope="unknown"（保守，保持全制度）
    """
    cfg = cfg or RegimeProfileConfig()
    if len(signal) == 0 or len(signal) != len(fwd_returns) or len(signal) != len(regime_series):
        return RegimeProfile(factor_id=factor_id, regime_stats={}, regime_scope="unknown", regime_dependent=False)

    sig = np.asarray(signal, dtype=float)
    fwd = np.asarray(fwd_returns, dtype=float)
    labels = np.asarray(list(regime_series.values if isinstance(regime_series, pd.Series) else regime_series), dtype=object)

    stats: dict[str, RegimeStat] = {}
    for regime in sorted({str(r) for r in labels}):
        mask = labels == regime
        if not bool(mask.sum()):
            continue
        stats[str(regime)] = _regime_stat(sig[mask], fwd[mask], cfg)

    effective_chains = [r for r, st in stats.items() if st.effective]
    if len(effective_chains) >= cfg.all_regimes_effective_min:
        scope: RegimeScope = "all"
    elif effective_chains:
        scope = effective_chains
    else:
        scope = "unknown"
    regime_dependent = any(
        st.icir is not None and st.icir < -0.5 for st in stats.values()
    )
    logger.info(
        "[regime_profile] factor=%s scope=%s effective_regimes=%s dependent=%s",
        factor_id, scope, effective_chains, regime_dependent,
    )
    return RegimeProfile(
        factor_id=factor_id,
        regime_stats=stats,
        regime_scope=scope,
        regime_dependent=regime_dependent,
    )


def build_regime_metadata(
    factor_id: str,
    signal: np.ndarray,
    fwd_returns: np.ndarray,
    regime_series: Union[pd.Series, list[str], np.ndarray],
    cfg: Optional[RegimeProfileConfig] = None,
) -> dict[str, Any]:
    """构建可写入 factor_catalog.metadata 的 Regime 画像字段（plans/53 §A2 落库）。

    Args:
        factor_id: 因子 ID
        signal: 因子信号数组
        fwd_returns: 前向收益数组
        regime_series: 制度标签序列
        cfg: 护栏参数（None → 默认）

    Returns:
        {"regime_ic_profile": {regime: RegimeStat dict}, "regime_scope": ...,
         "regime_dependent": bool}
    """
    prof = compute_regime_profile(factor_id, signal, fwd_returns, regime_series, cfg)
    profile_dict: dict[str, Any] = {}
    for regime, st in prof.regime_stats.items():
        stat = st.model_dump()
        if isinstance(stat.get("icir"), float) and math.isinf(stat["icir"]):
            stat["icir"] = None  # JSON/DuckDB 安全（effective 已承载判定结论）
        profile_dict[regime] = stat
    return {
        "regime_ic_profile": profile_dict,
        "regime_scope": prof.regime_scope,
        "regime_dependent": prof.regime_dependent,
    }


# ─── D 模块：RegimeSeriesBuilder（历史制度标签序列构建）───────────────────


class RegimeSeriesBuilder:
    """由市场面板构建历史制度标签序列（plans/53 §D1，供验证/画像复用）。

    流程：
        1. 品种面板 → 市场合成 OHLCV（复用 SectorRegimeSelector._build_sector_ohlcv，
           等权收益率指数 + 真实波幅）；
        2. 滚动窗口调用 RegimeAwareSelector.detect 输出制度标签序列
           （标签日期 = 窗口末端，与 evaluate 窗口语义一致）。

    Args:
        lookback_days: 滚动检测窗口大小（默认 60，plans/53 §D 实测结论：energy 面板
            规则检测 + 60 天窗口的制度标签对前向收益有显著区分力 K-W p=0.030；
            120 天 + HMM 的 5 制度在该面板上样本过稀不可检验）
        step_days: 滚动步长（默认 1 逐日，样本量与检测成本折中）
        selector: 可注入的检测器（None → 默认 RegimeAwareSelector）
        use_hmm: selector 为 None 时是否启用 HMM（默认 False=规则检测，
            plans/53 §D 实测规则检测有区分力且快；HMM 5 制度样本稀疏）
    """

    def __init__(
        self,
        lookback_days: int = 60,
        step_days: int = 1,
        selector: Optional[Any] = None,
        use_hmm: bool = False,
    ) -> None:
        self.lookback_days = max(20, int(lookback_days))
        self.step_days = max(1, int(step_days))
        if selector is not None:
            self._selector = selector
        else:
            from .regime import RegimeAwareSelector

            self._selector = RegimeAwareSelector(use_hmm=use_hmm, use_multi_hmm=use_hmm)

    def build_from_panel(self, panel: dict[str, pd.DataFrame], symbols: list[str]) -> pd.Series:
        """由品种面板构建制度序列（合成 OHLCV 路径）。

        Args:
            panel: 品种行情面板 {symbol: DataFrame(OHLCV)}
            symbols: 参与合成指数的品种列表（energy 用 ENERGY_CHAIN_SYMBOLS ∪ HOLDOUT）

        Returns:
            制度标签 Series（DatetimeIndex）；面板不足时返回空 Series。
        """
        from .regime import SectorRegimeSelector

        ohlcv = SectorRegimeSelector._build_sector_ohlcv(panel, symbols)
        if ohlcv is None or ohlcv.empty or len(ohlcv) <= self.lookback_days:
            logger.warning("[RegimeSeriesBuilder] 合成 OHLCV 不足（≤%d 行），返回空序列", self.lookback_days)
            return pd.Series(dtype=object)
        return self.build_from_ohlcv(ohlcv)

    def build_from_ohlcv(self, ohlcv: pd.DataFrame) -> pd.Series:
        """滚动窗口检测制度，返回制度标签序列（索引 = 窗口末端时点）。

        Args:
            ohlcv: 合成 OHLCV DataFrame（DatetimeIndex）

        Returns:
            pd.Series(name="regime", index=DatetimeIndex)；数据不足时返回空 Series。
        """
        if ohlcv is None or ohlcv.empty or len(ohlcv) <= self.lookback_days:
            return pd.Series(dtype=object)
        regimes: list[str] = []
        idx: list[pd.Timestamp] = []
        n = len(ohlcv)
        for i in range(self.lookback_days, n, self.step_days):
            window = ohlcv.iloc[i - self.lookback_days : i]
            det = self._selector.detect(window)
            regimes.append(str(det.get("regime", "oscillate")))
            idx.append(ohlcv.index[i - 1])
        return pd.Series(regimes, index=pd.DatetimeIndex(idx), name="regime")


def regime_gate_passed(
    regime_report: Optional[dict[str, Any]],
    min_positive_regimes: int = 2,
) -> bool:
    """晋升门槛判定（plans/53 §C1）：regime_ic_report 是否满足晋升要求。

    Args:
        regime_report: 评估链产出的 regime_ic_report（build_regime_metadata 输出）；
            None/空 → 放行（无画像不拦截，向后兼容）。
        min_positive_regimes: 最少有效制度数（默认 2，settings.yaml regime_profile.min_positive_regimes）。

    Returns:
        True=放行晋升；False=拦截（有效制度数不足）。
    """
    if not regime_report:
        return True
    scope = regime_report.get("regime_scope")
    # scope="all"/"unknown"（非 list）→ 放行（保守性设计：不误杀全制度/数据不足因子）
    if not isinstance(scope, list) or not scope:
        return True
    return len(scope) >= max(1, int(min_positive_regimes))


__all__ = [
    "RegimeProfileConfig",
    "RegimeStat",
    "RegimeProfile",
    "compute_regime_profile",
    "build_regime_metadata",
    "regime_gate_passed",
    "RegimeSeriesBuilder",
]
