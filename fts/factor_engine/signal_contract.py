"""
fts.factor_engine.signal_contract — 实盘信号契约（C.2）。

定义标准信号 JSON Schema 对应的 Python 类型（TypedDict）与验证器：
    - ``SignalDetail``: 单个品种的信号详情
    - ``FactorContribution``: 因子贡献详情
    - ``FactorSignal``: 完整因子信号包
    - ``SignalMeta``: 信号元数据
    - ``SignalValidator``: 格式验证器（必填字段/方向枚举/置信度范围）

FTS 角色边界: 本模块只负责信号格式契约与验证，交易执行由下游系统负责。

用法:
    from fts.factor_engine.signal_contract import FactorSignal, SignalValidator

    validator = SignalValidator()
    errors = validator.validate(signal_dict)
    if not errors:
        # 信号格式正确，可进入风控层

版本: v1.0.0
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

# 信号方向枚举
DIRECTIONS: tuple[str, ...] = ("long", "short", "flat")
# 信号频率枚举
FREQUENCIES: tuple[str, ...] = ("tick", "1m", "5m", "15m", "30m", "1h", "4h", "1d")


def to_lots(
    position_pct: float,
    equity: float,
    price: float,
    multiplier: float,
    max_lots: int = 0,
) -> int:
    """资金占比 → 目标手数（G12，v2.103.0+15）。

    Args:
        position_pct: 目标资金占比 [0,1]
        equity: 总权益
        price: 最新价
        multiplier: 合约乘数
        max_lots: 手数上限（0=不截断）

    Returns:
        目标手数；资金不足 → 0 手，超上限 → 截断。
    """
    if position_pct <= 0 or equity <= 0 or price <= 0 or multiplier <= 0:
        return 0
    lots = int(round(position_pct * equity / (price * multiplier)))
    if max_lots > 0:
        lots = min(lots, max_lots)
    return lots


class FactorContribution(TypedDict, total=False):
    """因子贡献详情。"""

    factor_id: str
    weight: float
    signal: float


class SignalDetail(TypedDict, total=False):
    """单个品种的信号详情。"""

    symbol: str
    direction: str  # Literal['long', 'short', 'flat']
    position: float  # 目标资金占比
    target_lots: int  # G12: 目标手数
    current_lots: int  # G12: 当前持仓手数
    delta_lots: int  # G12: 调仓手数 = target - current
    score: float  # G12: 触发因子得分（Composite Score）
    regime: str  # G12: 市场状态标签
    risk_usage: float  # G12: 风险占用比例 ∈ [0,1]
    confidence: float
    price: float
    stop_loss: float
    take_profit: float
    contributing_factors: list[FactorContribution]


class SignalMeta(TypedDict, total=False):
    """信号元数据。"""

    trace_id: str
    factor_count: int
    regime: str
    source_version: str


class FactorSignal(TypedDict, total=False):
    """完整因子信号包。"""

    signal_id: str
    portfolio_id: str
    timestamp: str
    frequency: str
    universe: list[str]
    signals: list[SignalDetail]
    meta: SignalMeta


class SignalValidator:
    """信号格式验证器（C.2）。

    验证信号包的必填字段、方向枚举、置信度范围。
    """

    # ─── 主入口 ──────────────────────────────────────────

    def validate(self, signal: dict[str, Any]) -> list[str]:
        """验证信号格式，返回错误列表（空列表 = 通过）。

        Args:
            signal: FactorSignal 字典

        Returns:
            错误消息列表。为空表示验证通过。
        """
        errors: list[str] = []

        # 1. 必填字段
        required = ["signal_id", "timestamp", "signals"]
        for field in required:
            if field not in signal or signal.get(field) in (None, "", []):
                errors.append(f"Missing required field: {field}")

        # 2. signal_id 非空
        if signal.get("signal_id") is None:
            errors.append("signal_id must not be null")

        # 3. portfolio_id 建议存在（非必填但推荐）
        if not signal.get("portfolio_id"):
            errors.append("Missing recommended field: portfolio_id")

        # 4. 频率枚举
        freq = signal.get("frequency", "1d")
        if freq not in FREQUENCIES:
            errors.append(f"Invalid frequency: {freq}")

        # 5. 逐项校验 signals
        errors.extend(self._validate_signal_details(signal.get("signals") or []))

        # 6. 元数据
        meta = signal.get("meta") or {}
        if "trace_id" not in meta:
            errors.append("Missing recommended field: meta.trace_id")

        return errors

    # ─── 内部校验 ────────────────────────────────────────

    @staticmethod
    def _validate_signal_details(signals: list[Any]) -> list[str]:
        """校验信号详情列表。"""
        errors: list[str] = []
        for i, sig in enumerate(signals):
            if not isinstance(sig, dict):
                errors.append(f"signals[{i}] must be an object")
                continue

            # 必填字段
            for field in ("symbol", "direction", "position", "confidence"):
                if field not in sig:
                    errors.append(f"signals[{i}] missing '{field}'")

            # 方向枚举
            direction = sig.get("direction")
            if direction not in DIRECTIONS:
                errors.append(f"signals[{i}] invalid direction: {direction} (expected {list(DIRECTIONS)})")

            # 置信度范围
            confidence = sig.get("confidence")
            if confidence is not None and not (0 <= confidence <= 1):
                errors.append(f"signals[{i}] confidence out of range: {confidence}")

            # 仓位非负
            position = sig.get("position")
            if position is not None and position < 0:
                errors.append(f"signals[{i}] position must be >= 0: {position}")

            # G12: 手数字段（可选，提供时校验非负整数）
            for lots_field in ("target_lots", "current_lots", "delta_lots"):
                lots_val = sig.get(lots_field)
                if lots_val is None:
                    continue
                if not isinstance(lots_val, int) or lots_val < 0:
                    errors.append(f"signals[{i}] {lots_field} must be a non-negative int: {lots_val}")

            # G12: 调仓手数一致性 delta = target - current（三者齐备时校验）
            tgt, cur, delta = sig.get("target_lots"), sig.get("current_lots"), sig.get("delta_lots")
            if tgt is not None and cur is not None and delta is not None and delta != tgt - cur:
                errors.append(f"signals[{i}] delta_lots {delta} != target - current = {tgt - cur}")

            # G12: 风险占用比例 ∈ [0,1]
            risk_usage = sig.get("risk_usage")
            if risk_usage is not None and not (0 <= risk_usage <= 1):
                errors.append(f"signals[{i}] risk_usage out of range [0,1]: {risk_usage}")

            # G12: score 数值化（可选）
            score = sig.get("score")
            if score is not None:
                try:
                    float(score)
                except (TypeError, ValueError):
                    errors.append(f"signals[{i}] score must be numeric: {score}")

            # 贡献因子
            for f in sig.get("contributing_factors") or []:
                if not isinstance(f, dict) or not f.get("factor_id"):
                    errors.append(f"signals[{i}] invalid contributing factor")
        return errors

    # ─── ScoredSignal → FactorSignal ─────────────────────

    @staticmethod
    def to_factor_signal(
        scored_signals: list[Any],
        portfolio_id: str = "default",
        trace_id: str = "",
        frequency: str = "1d",
        regime: str = "",
        source_version: str = "",
    ) -> FactorSignal:
        """将 ``ScoredSignal`` 列表转换为 ``FactorSignal`` 契约。

        方向映射: bull → long, bear → short, neutral → flat。
        置信度: 由 grade 映射（STRONG=0.9/WATCH=0.7/WEAK=0.5/NOISE=0.3），
        可被 extra 中的 confidence 覆盖。

        Args:
            scored_signals: ScoredSignal 列表（含 symbol/direction/total/weight 等）
            portfolio_id: 组合 ID
            trace_id: trace_id（缺省自动生成）
            frequency: 信号频率
            regime: 市场状态
            source_version: 信号源版本

        Returns:
            FactorSignal 字典。
        """
        from .state import generate_trace_id

        trace_id = trace_id or generate_trace_id()
        signals: list[SignalDetail] = []
        for s in scored_signals:
            direction = {
                "bull": "long",
                "bear": "short",
                "neutral": "flat",
            }.get(getattr(s, "direction", "neutral"), "flat")
            confidence = {
                "STRONG": 0.9,
                "WATCH": 0.7,
                "WEAK": 0.5,
                "NOISE": 0.3,
            }.get(getattr(s, "grade", "NOISE"), 0.3)
            position = float(getattr(s, "position", 0.0) or 0.0)
            if position <= 0:
                position = abs(float(getattr(s, "total", 0.0) or 0.0))
            signals.append(
                SignalDetail(
                    symbol=getattr(s, "symbol", ""),
                    direction=direction,
                    position=round(position, 6),
                    confidence=confidence,
                    price=float(getattr(s, "price", 0.0) or 0.0),
                )
            )

        return FactorSignal(
            signal_id=generate_trace_id(),
            portfolio_id=portfolio_id,
            timestamp=datetime.now().isoformat(),
            frequency=frequency,
            universe=list({s.get("symbol", "") for s in signals}),
            signals=signals,
            meta=SignalMeta(
                trace_id=trace_id,
                factor_count=len(scored_signals),
                regime=regime,
                source_version=source_version,
            ),
        )


def signal_map_to_factor_signal(
    signal_map: dict[str, Any],
    signal_id: str = "",
    timestamp: str = "",
    frequency: str = "1d",
    portfolio_id: str = "default",
    trace_id: str = "",
    regime: str = "",
    source_version: str = "",
    equity: float = 0.0,
    price_multiplier: float = 1.0,
    default_position_pct: float = 0.0,
    confidence: float = 0.7,
) -> FactorSignal:
    """信号映射 {symbol: {direction, score, price, ...}} → FactorSignal 契约（G12）。

    统一 mhf_signal_pipeline / tqsdk_mhf_executor 等管道输出为
    FactorSignal 结构，经 `SignalValidator` 校验后供风控层/FDT 消费。

    Args:
        signal_map: {symbol: {direction, score, price, position, target_lots, ...}}。
            direction 支持 int（+1 多 / -1 空 / 0 平）或 str（long/short/flat）。
        signal_id: 信号 ID（缺省自动生成）
        timestamp: 信号时间戳（缺省当前 UTC ISO）
        frequency: 信号频率
        portfolio_id: 组合 ID
        trace_id: 全链路 trace_id
        regime: 市场状态标签（写入每条 SignalDetail.regime）
        source_version: 信号源版本
        equity: 总权益（提供且含 position 时计算 target_lots）
        price_multiplier: 合约乘数（计算 target_lots 用）
        default_position_pct: 无 position 时的兜底目标占比
        confidence: 默认置信度

    Returns:
        FactorSignal 字典。
    """
    from .state import generate_trace_id

    trace_id = trace_id or generate_trace_id()
    signal_id = signal_id or generate_trace_id()
    timestamp = timestamp or datetime.now().isoformat()

    details: list[SignalDetail] = []
    for sym, data in signal_map.items():
        if not isinstance(data, dict):
            continue
        raw_dir = data.get("direction", 0)
        if isinstance(raw_dir, str):
            direction = raw_dir.lower() if raw_dir.lower() in DIRECTIONS else "flat"
        else:
            direction = {1: "long", -1: "short"}.get(int(raw_dir), "flat")
        price = float(data.get("price") or data.get("last_close") or 0.0)
        position = float(data.get("position", default_position_pct) or 0.0)
        detail: SignalDetail = {
            "symbol": sym,
            "direction": direction,
            "position": round(position, 6),
            "confidence": float(data.get("confidence", confidence) or confidence),
            "price": price,
        }
        if data.get("score") is not None:
            try:
                detail["score"] = float(data["score"])
            except (TypeError, ValueError):
                detail["score"] = data["score"]  # 非数值原样保留，交 validator 拒绝
        if regime:
            detail["regime"] = regime
        if data.get("target_lots") is not None:
            detail["target_lots"] = int(data["target_lots"])
        if data.get("current_lots") is not None:
            detail["current_lots"] = int(data["current_lots"])
        if not detail.get("target_lots") and equity > 0 and price > 0 and position > 0:
            detail["target_lots"] = to_lots(position, equity, price, price_multiplier or 1.0)
        if data.get("delta_lots") is not None:
            detail["delta_lots"] = int(data["delta_lots"])
        elif detail.get("target_lots") is not None and detail.get("current_lots") is not None:
            detail["delta_lots"] = detail["target_lots"] - detail["current_lots"]
        if data.get("risk_usage") is not None:
            detail["risk_usage"] = float(data["risk_usage"])
        details.append(detail)

    return FactorSignal(
        signal_id=signal_id,
        portfolio_id=portfolio_id,
        timestamp=timestamp,
        frequency=frequency,
        universe=list(signal_map.keys()),
        signals=details,
        meta=SignalMeta(
            trace_id=trace_id,
            factor_count=len(details),
            regime=regime,
            source_version=source_version,
        ),
    )


__all__ = [
    "FactorContribution",
    "SignalDetail",
    "SignalMeta",
    "FactorSignal",
    "SignalValidator",
    "to_lots",
    "signal_map_to_factor_signal",
    "DIRECTIONS",
    "FREQUENCIES",
]
