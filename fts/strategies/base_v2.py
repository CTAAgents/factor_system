"""
v2 策略可插拔框架 — 策略层插拔化重构核心。

每个策略是自包含的 compute → filter → score 三段式模块。
支持多策略并行执行、策略内验证器声明、跨策略得分融合。

与 v1 BaseStrategy 的关系：独立不继承，v1 在 v7.0 后标记 @deprecated。
v2 策略可经过 Adapter 桥接到 v1 的 score() 接口（扫盘兼容），
但推荐经 StrategyPipeline 直接在 v2 框架内运行。

版本: v0.1.0（从 FDT v8.10.0 剥离，保持原 API 不变）
"""
# pylint: disable=too-many-instance-attributes,too-many-arguments,protected-access

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


# ════════════════════════════════════════════════════════════
# 数据契约
# ════════════════════════════════════════════════════════════

@dataclass
class RawSignal:
    """策略 compute 阶段的原始信号（未过滤未打分）。"""
    symbol: str
    direction: str                    # "bull" | "bear" | "neutral"
    signal_type: str                  # 带命名空间: "{strategy}.{subtype}"
    raw_score: float                  # 策略内部原始分
    strategy_name: str                # 来源策略 name
    meta: dict = field(default_factory=dict)  # 策略自定义字段


@dataclass
class ScoredSignal:
    """策略 score 阶段的打分信号（已过滤+打分，准备交付融合）。"""

    symbol: str
    direction: str                    # "bull" | "bear" | "neutral"
    signal_type: str                  # 同 RawSignal
    strategy_name: str                # 来源策略 name
    total: float = 0.0                # 带方向总分（正=多头, 负=空头）
    abs_score: float = 0.0            # 绝对得分
    grade: str = "NOISE"              # "STRONG" | "WATCH" | "WEAK" | "NOISE"
    weight: float = 1.0               # 跨策略融合权重

    # 指标字段（从 tech_list 复制，供下游消费和验证器使用）
    price: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    adx: float = 0.0
    rsi: float = 0.0
    cci: float = 0.0
    ma_slope: float = 0.0
    macd_cross: str = "none"
    dc20_break: str = "none"
    ma_align: str = "mixed"
    z_score: float = 0.0
    stage: str = "unknown"
    atr: float = 0.0

    _tdx_patched: bool = False
    sub_scores: dict = field(default_factory=dict)  # 策略内因子明细
    extra: dict = field(default_factory=dict)        # 策略自定义扩展

    # 验证器字段
    _raw_total: Optional[float] = None
    _raw_grade: Optional[str] = None
    _validator_demoted: bool = False
    _validator_reason: str = ""
    reason: str = ""                    # 信号来源解释（子策略身份+关键条件），供辩论环节识别

    def to_dict(self) -> dict:
        """转为平铺 dict，与 v1 SignalResult.to_dict() 兼容。"""
        d = {
            "symbol": self.symbol,
            "direction": self.direction,
            "signal_type": self.signal_type,
            "strategy": self.strategy_name,
            "total": round(self.total) if isinstance(self.total, (int, float)) else self.total,
            "abs": round(self.abs_score),
            "grade": self.grade,
            "weight": self.weight,
            "price": round(self.price, 1),
            "change_pct": round(self.change_pct, 2),
            "volume": self.volume,
            "adx": round(self.adx, 1),
            "rsi": round(self.rsi, 1),
            "cci": round(self.cci, 1),
            "ma_slope": round(self.ma_slope, 2),
            "macd_cross": self.macd_cross,
            "dc20_break": self.dc20_break,
            "ma_align": self.ma_align,
            "z_score": round(self.z_score, 2),
            "stage": self.stage,
            "atr": round(self.atr, 1),
            "_tdx_patched": self._tdx_patched,
            "_validator_demoted": self._validator_demoted,
            "_validator_reason": self._validator_reason,
        }
        # reason：信号来源解释（子策略身份 + 关键条件），供辩论环节识别。
        # 若策略 score() 未显式设置，则在此按 signal_type+方向+grade+关键指标自动兜底。
        _reason = self.reason
        if not _reason:
            _metrics = {}
            if self.rsi:
                _metrics["RSI"] = round(self.rsi, 1)
            if self.cci:
                _metrics["CCI"] = round(self.cci, 1)
            if self.adx:
                _metrics["ADX"] = round(self.adx, 1)
            if self.z_score:
                _metrics["Z"] = round(self.z_score, 2)
            if self.price:
                _metrics["PX"] = round(self.price, 1)
            _reason = format_reason(
                self.signal_type, self.direction, self.grade,
                metrics=_metrics or None,
                strength=round(self.abs_score / 100, 2) if self.abs_score else None,
            )
        d["reason"] = _reason
        if self._raw_total is not None:
            d["_raw_total"] = self._raw_total
        if self._raw_grade is not None:
            d["_raw_grade"] = self._raw_grade
        for k, v in self.sub_scores.items():
            d[k] = round(v) if isinstance(v, float) else v
        d.update(self.extra)
        return d


# ════════════════════════════════════════════════════════════
# v2 策略基类
# ════════════════════════════════════════════════════════════

class BaseStrategyV2(ABC):
    """v2 策略基类 — 自包含 compute → filter → score 三阶段。

    用法:
        class MyStrategy(BaseStrategyV2):
            @property
            def name(self) -> str: return "my_strategy"
            @property
            def signal_type(self) -> str: return "my_strategy"
            @property
            def validators(self) -> list: return ["atr_vol_timing"]
            @property
            def weight(self) -> float: return 1.0

            def compute(self, tech_list, kline_data, context) -> list[RawSignal]: ...
            def score(self, filtered, tech_list, context) -> list[ScoredSignal]: ...
    """

    # ── G28（2026-07-15）：策略启用控制 ──
    # 默认启用；config.settings.DISABLED_STRATEGIES 中的策略在管线中被跳过。
    # 子类亦可在类体内声明 `enabled = False` 实现自暂停（如检测到依赖缺失）。
    enabled: bool = True

    @property
    @abstractmethod
    def name(self) -> str:
        """策略标识符，全局唯一。如 'trend_following', 'arbitrage'。"""

    @property
    def display_name(self) -> str:
        """策略中文展示名，默认同 name。"""
        return self.name

    @property
    def signal_type(self) -> str:
        """策略产出的信号类型命名空间前缀。默认 = name。"""
        return self.name

    @property
    def validators(self) -> list[str]:
        """该策略信号需要跑哪些验证器 (validator vid 列表)。
        验证器从 signals.validators.VALIDATOR_REGISTRY 查找。"""
        return []

    @property
    def weight(self) -> float:
        """跨策略融合时的权重。1.0 = 等权。"""
        return 1.0

    @property
    def depends_on(self) -> list[str]:
        """依赖的其他策略 name。空列表 = 无依赖。"""
        return []

    def compute(self, _tech_list: list[dict], _kline_data: dict,
                _context: dict | None = None) -> list[RawSignal]:
        """从技术指标列表计算原始信号。

        Args:
            tech_list: 指标引擎产出的每品种 tech dict 列表
            kline_data: {sym: (name, [bar_dict, ...])}
            context: 共享上下文（含 oi_data, basis_data 等）

        Returns:
            RawSignal 列表，每个 signal_type 须为 self.signal_type 开头
        """
        return []

    def filter(self, raw_signals: list[RawSignal],
               _context: dict | None = None) -> list[RawSignal]:
        """策略内轻量过滤（非 V1-V7，策略自定义过滤条件）。
        默认返回全部。"""
        return raw_signals

    @abstractmethod
    def score(self, filtered_signals: list[RawSignal],
              tech_list: list[dict],
              context: dict | None = None) -> list[ScoredSignal]:
        """对过滤后的信号打分。

        Args:
            filtered_signals: filter() 产出的信号
            tech_list: 指标列表（从中补充 price/adx/rsi 等字段）
            context: 共享上下文

        Returns:
            ScoredSignal 列表
        """


# ════════════════════════════════════════════════════════════
# v1 → v2 适配器（桥接兼容）
# ════════════════════════════════════════════════════════════

def _copy_fields(src: dict, dst: ScoredSignal) -> None:
    """从 v1 输出 dict 复制指标字段到 ScoredSignal。"""
    # G76：大小写兼容映射表（legacy_numpy 输出大写 ADX/RSI14，
    # TDX bridge 也输出大写，但下游统一以小写消费）
    _upper_fallback = {
        "adx": ("ADX", "ADX14"),
        "rsi": ("RSI14", "RSI"),
        "volume": ("VOL",),
        "atr": ("ATR14", "ATR"),
        "cci": ("CCI20", "CCI"),
    }
    for k in ("price", "change_pct", "volume", "adx", "rsi", "cci",
              "ma_slope", "macd_cross", "dc20_break", "ma_align",
              "z_score", "stage", "atr"):
        v = src.get(k)
        if v is None and k in _upper_fallback:
            for fk in _upper_fallback[k]:
                v = src.get(fk)
                if v is not None:
                    break
        if v is not None:
            setattr(dst, k, v)
    dst._tdx_patched = src.get("_tdx_patched", False)
    dst.sub_scores = {k: v for k, v in src.items()
                      if k.startswith("dc") or k.startswith("bb") or k.startswith("vol")}
    dst.extra = src.get("extra", {})
    for ek in ("_raw_total", "_raw_grade", "_validator_demoted", "_validator_reason",
               "_oi_surge_reversal", "_strangle_compressed", "_basis_conflict"):
        if ek in src:
            dst.extra[ek] = src[ek]


class StrategyV1Adapter(BaseStrategyV2):
    """将 v1 BaseStrategy 适配为 v2 BaseStrategyV2 接口。

    用法:
        from strategies import get_strategy
        v1 = get_strategy("channel_breakout")
        v2 = StrategyV1Adapter(v1, validators=CHANNEL_BREAKOUT_VALIDATORS)
        pipe = StrategyPipeline([v2])
    """

    def __init__(self, v1_strategy: Any,
                 signal_type: str | None = None,
                 validators: list[str] | None = None,
                 weight: float = 1.0):
        self._v1 = v1_strategy
        self._sig_type = signal_type or v1_strategy.name
        self._validators = validators or []
        self._weight = weight

    @property
    def name(self) -> str:
        return self._v1.name

    @property
    def signal_type(self) -> str:
        return self._sig_type

    @property
    def validators(self) -> list[str]:
        return self._validators

    @property
    def weight(self) -> float:
        return self._weight

    @property
    def display_name(self) -> str:
        return self._v1.display_name

    def compute(self, tech_list: list[dict], _kline_data: dict,
                _context: dict | None = None) -> list[RawSignal]:
        # v1 没有分离 compute/filter，用 pass-through 让 score 处理
        return [RawSignal(
            symbol=t.get("symbol", ""),
            direction="neutral",
            signal_type=f"{self._sig_type}.raw",
            raw_score=0,
            strategy_name=self.name,
            meta=t,
        ) for t in tech_list]

    def filter(self, raw_signals: list[RawSignal],
               _context: dict | None = None) -> list[RawSignal]:
        return raw_signals  # v1 验证器接管过滤

    def score(self, filtered_signals: list[RawSignal],
              tech_list: list[dict],
              context: dict | None = None) -> list[ScoredSignal]:
        ctx = context or {}
        result = self._v1.score(
            tech_list,
            mode=ctx.get("mode", "full"),
            kline_data=ctx.get("kline_data"),
            df_map=ctx.get("df_map"),
            period=ctx.get("period", "daily"),
            window_mode=ctx.get("window_mode", "fixed"),
        )
        signals: list[ScoredSignal] = []
        for r in result.get("all_ranked", []):
            ss = ScoredSignal(
                symbol=r.get("symbol", ""),
                direction=r.get("direction", "neutral"),
                signal_type=f"{self._sig_type}.{r.get('signal_type', 'unknown')}",
                strategy_name=self.name,
                total=r.get("total", 0),
                abs_score=r.get("abs", 0),
                grade=r.get("grade", "NOISE"),
                weight=self._weight,
            )
            _copy_fields(r, ss)
            # 自动构造 reason（子策略身份 + 关键条件），供辩论环节识别
            _metrics = {
                k: round(v, 1) for k, v in r.items()
                if k in ("dc20", "dc55", "bb", "rsi", "cci", "adx", "macd",
                         "z_score", "kf_z", "hurst", "vr_z")
                and isinstance(v, (int, float))
            }
            ss.reason = format_reason(
                ss.signal_type, ss.direction, ss.grade,
                metrics=_metrics or None,
                strength=round(ss.abs_score / 100, 2) if ss.abs_score else None,
            )
            signals.append(ss)
        return signals


def format_reason(signal_type: str, direction: str, grade: str,
                  *, metrics: Optional[dict] = None,
                  strength: Optional[float] = None, note: str = "") -> str:
    """构造结构化 reason 字符串（带 ``[signal_type]`` rule_ref 前缀）。

    辩论子 Agent 拿到后可据 ``signal_type`` 前缀在
    ``memory/knowledge/strategies/_index.json`` 定位权威规则交叉验证。

    Args:
        signal_type: 子策略命名空间（如 ``mean_reversion.rsi``）
        direction: bull/bear/neutral
        grade: STRONG/WATCH/WEAK/NOISE
        metrics: 关键条件数值（如 ``{"RSI": 18.3, "ADX": 14.2}``）
        strength: 子信号强度（0-1）
        note: 补充说明
    """
    parts = [f"[{signal_type}]", f"dir={direction}", f"grade={grade}"]
    if metrics:
        parts.append(" ".join(f"{k}={v}" for k, v in metrics.items()))
    if strength is not None:
        parts.append(f"强度={strength:.2f}")
    if note:
        parts.append(note)
    return " | ".join(parts)
