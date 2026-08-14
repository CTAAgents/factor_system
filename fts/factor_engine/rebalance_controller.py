"""
fts.factor_engine.rebalance_controller — 组合五层调仓控制器（手册阶段7）。

对照《期货CTA多因子策略标准化作业手册》阶段7「信号生成与五层调仓机制」实现：

    第一层 缓冲带（Buffer Zone）
        核心持仓区 / 缓冲区 / 不持仓区。缓冲区内已持有则继续持有、未持有则不新开，
        消除 TopN 边界抖动导致的过度换手。缓冲带宽度 k 按品种池规模差异化。
    第二层 调仓触发条件（混合模式）
        周期性再平衡（距上次 ≥ T 日）/ 边界突破 / 风控触发 / 交割月移仓 / 强制再平衡，
        任一满足即评估；否则维持现有持仓。
    第三层 换手阈值拦截（成本收益门控）
        预期收益提升 = Σ|Δw_i| × |Score_i| × 因子收益系数；
        调仓成本     = Σ|Δw_i| × (手续费 + 滑点 + 冲击成本)；
        仅当 预期收益 > 成本 × λ 才执行，否则跳过并记录「成本拦截」日志。
    第四层 防僵尸持仓 + 强制再平衡
        单品种最大持仓天数超限强制重新评估（因子失效则平仓）；
        每 force_rebalance_period 日强制执行一次全量排名对齐。
    第五层 调仓执行时点优化
        单次调仓品种数较多时按批次拆分执行，降低集中下单冲击。

设计约束:
    - 纯函数 / 零未来函数：仅用当日排名 + 持仓状态，不使用未来信息
    - 方向值约定：+1 多头 / -1 空头 / 0 空仓
    - 输出调仓决策（目标持仓 / 变更明细 / 拦截日志），不直接下单，执行交由下游
    - NaN 兜底：缺失得分品种不参与排名，视为不持仓区

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np

logger = logging.getLogger(__name__)

# 缓冲区方向
ZoneCode = Literal["long_core", "long_buffer", "flat", "short_buffer", "short_core"]
# 触发类型
TriggerType = Literal["periodic", "boundary", "risk", "rollover", "forced", None]


# ─── 配置契约 ─────────────────────────────────────────────


@dataclass
class RebalanceConfig:
    """五层调仓控制器配置。

    Attributes:
        n_long: 多头核心持仓数 N
        n_short: 空头核心持仓数 N
        buffer_k: 缓冲带宽度 k（None=按品种池规模自动计算）
        pool_size: 品种池规模 M（auto k 使用）
        rebalance_period: 周期性再平衡间隔 T（交易日，量价因子 3 / 期限结构 5 / 基本面 10）
        force_rebalance_period: 强制全量再平衡周期（默认 20 日）
        max_hold_days: 防僵尸单品种最大持仓天数（量价 20 / 基本面 40）
        cost_lambda: 换手阈值拦截安全边际 λ（默认 1.5）
        factor_return_coeff: 因子收益系数（预期收益公式用）
        commission_rate: 手续费率（单边）
        slippage_rate: 滑点率（单边）
        impact_rate: 冲击成本率（单边）
        batch_threshold: 单次调仓品种数 ≥ 该值时分批
        batch_count: 分批数
        asymmetric_buffer: 是否启用多空不对称缓冲（趋势 Regime 多头缓冲 k+1、空头 k-1；震荡反之）
    """

    n_long: int = 8
    n_short: int = 8
    buffer_k: Optional[int] = None
    pool_size: int = 40
    rebalance_period: int = 3
    force_rebalance_period: int = 20
    max_hold_days: int = 20
    cost_lambda: float = 1.5
    factor_return_coeff: float = 1.0
    commission_rate: float = 0.0002
    slippage_rate: float = 0.0005
    impact_rate: float = 0.0010
    batch_threshold: int = 5
    batch_count: int = 3
    asymmetric_buffer: bool = False


def auto_buffer_k(pool_size: int, n: int) -> int:
    """按品种池规模自动计算缓冲带宽度 k（手册阶段7 差异化公式）。

    Args:
        pool_size: 品种池规模 M
        n: 多/空核心持仓数量 N

    Returns:
        缓冲带宽度 k。
    """
    if pool_size < 30:
        return 2
    if pool_size < 60:
        return max(2, round(n * 0.25))
    return round(n * 0.3)


# ─── 持仓状态 ─────────────────────────────────────────────


@dataclass
class RebalanceState:
    """调仓控制器状态（跨日持久）。

    Attributes:
        last_rebalance_days_ago: 距上次完整再平衡的交易日数
        last_forced_days_ago: 距上次强制全量再平衡的交易日数
        hold_days: symbol → 连续持仓天数（防僵尸）
        positions: symbol → 当前方向（+1/-1/0）
    """

    last_rebalance_days_ago: int = 0
    last_forced_days_ago: int = 0
    hold_days: dict[str, int] = field(default_factory=dict)
    positions: dict[str, int] = field(default_factory=dict)


# ─── 调仓决策输出 ─────────────────────────────────────────


@dataclass
class RebalanceDecision:
    """单次调仓评估决策输出。"""

    triggered: bool  # 是否触发调仓评估
    trigger: Optional[str] = None  # 触发类型
    target_positions: dict[str, int] = field(default_factory=dict)  # symbol → 目标方向
    current_positions: dict[str, int] = field(default_factory=dict)
    changes: dict[str, tuple[int, int]] = field(default_factory=dict)  # symbol → (旧方向, 新方向)
    cost_intercepted: bool = False  # 是否被换手阈值拦截
    cost_benefit: dict = field(default_factory=dict)  # 预期收益/成本明细
    zombie_forced: list[str] = field(default_factory=list)  # 防僵尸强制平仓品种
    batches: list[list[str]] = field(default_factory=list)  # 分批执行计划
    logs: list[str] = field(default_factory=list)  # 拦截/决策日志

    def to_dict(self) -> dict:
        return {
            "triggered": self.triggered,
            "trigger": self.trigger,
            "target_positions": self.target_positions,
            "current_positions": self.current_positions,
            "changes": {k: list(v) for k, v in self.changes.items()},
            "cost_intercepted": self.cost_intercepted,
            "cost_benefit": self.cost_benefit,
            "zombie_forced": self.zombie_forced,
            "batches": self.batches,
            "logs": self.logs,
        }


# ─── 五层调仓控制器 ───────────────────────────────────────


class RebalanceController:
    """组合五层调仓控制器。

    Usage:
        ctrl = RebalanceController()
        decision = ctrl.step(scores={"RB": 1.2, "CU": -0.8, ...}, event=None)
    """

    def __init__(self, config: Optional[RebalanceConfig] = None) -> None:
        """初始化。

        Args:
            config: 调仓配置（None=默认）
        """
        self.config = config or RebalanceConfig()
        if self.config.buffer_k is None:
            self.config.buffer_k = auto_buffer_k(self.config.pool_size, self.config.n_long)
        self.state = RebalanceState()

    # ─── 第一层：缓冲带 ──────────────────────────────────

    def build_zone(
        self,
        scores: dict[str, float],
        k_long: Optional[int] = None,
        k_short: Optional[int] = None,
    ) -> dict[str, ZoneCode]:
        """按当日得分划分排名缓冲带区域。

        得分从高到低排名：前 n_long 为核心多头，其后 k_long 为多头缓冲区，
        尾部 n_short 为核心空头，其前 k_short 为空头缓冲区，其余为不持仓区。
        支持多空不对称缓冲（手册阶段7 可选：趋势 Regime 多头缓冲更宽、空头更窄）。

        Args:
            scores: symbol → 综合得分（缺失/NaN 视为不参与排名）
            k_long: 多头缓冲带宽度（None=用配置 buffer_k）
            k_short: 空头缓冲带宽度（None=用配置 buffer_k）

        Returns:
            symbol → 区域编码。
        """
        valid = {s: float(v) for s, v in scores.items() if v is not None and not (isinstance(v, float) and np.isnan(v))}
        cfg = self.config
        n_long, n_short = cfg.n_long, cfg.n_short
        k_l = int(k_long if k_long is not None else cfg.buffer_k or 0)
        k_s = int(k_short if k_short is not None else cfg.buffer_k or 0)
        ranked = sorted(valid.items(), key=lambda kv: kv[1], reverse=True)
        m = len(ranked)
        zone: dict[str, ZoneCode] = {}
        for idx, (sym, _) in enumerate(ranked):
            r = idx + 1
            if r <= n_long:
                zone[sym] = "long_core"
            elif r <= n_long + k_l:
                zone[sym] = "long_buffer"
            elif r <= m - n_short - k_s:
                zone[sym] = "flat"
            elif r <= m - n_short:
                zone[sym] = "short_buffer"
            else:
                zone[sym] = "short_core"
        return zone

    @staticmethod
    def zone_to_target(zone: ZoneCode, current_dir: int) -> int:
        """区域编码 → 目标方向（缓冲区内已持有则继续持有，未持有则不新开）。

        Args:
            zone: 区域编码
            current_dir: 当前方向（+1/-1/0）

        Returns:
            目标方向（+1/-1/0）。
        """
        if zone == "long_core":
            return 1
        if zone == "short_core":
            return -1
        if zone == "long_buffer":
            return 1 if current_dir == 1 else 0
        if zone == "short_buffer":
            return -1 if current_dir == -1 else 0
        return 0

    # ─── 第二层：混合触发 ────────────────────────────────

    def evaluate_trigger(
        self,
        current_positions: dict[str, int],
        target_positions: dict[str, int],
        event: Optional[str] = None,
    ) -> Optional[str]:
        """判断是否触发调仓评估（混合模式，任一满足即触发）。

        Args:
            current_positions: 当前持仓
            target_positions: 目标持仓（含缓冲带保持）
            event: 外部事件（"risk" 风控 / "rollover" 交割月移仓，None=无）

        Returns:
            触发类型，未触发返回 None。
        """
        if event in ("risk", "rollover"):
            return event
        if self.state.last_forced_days_ago >= self.config.force_rebalance_period:
            return "forced"
        if self.state.last_rebalance_days_ago >= self.config.rebalance_period:
            return "periodic"
        # 边界突破：任一品种目标方向与当前方向不同
        all_syms = set(current_positions) | set(target_positions)
        for sym in all_syms:
            if target_positions.get(sym, 0) != current_positions.get(sym, 0):
                return "boundary"
        return None

    # ─── 第三层：换手阈值拦截 ────────────────────────────

    def _weight_of(self, direction: int) -> float:
        """方向 → 等权单位权重（多/空分别 1/N）。"""
        if direction == 1:
            return 1.0 / max(1, self.config.n_long)
        if direction == -1:
            return 1.0 / max(1, self.config.n_short)
        return 0.0

    def cost_benefit_gate(
        self,
        current_positions: dict[str, int],
        target_positions: dict[str, int],
        scores: dict[str, float],
    ) -> tuple[bool, dict]:
        """第三层：换手阈值拦截（成本收益门控）。

        预期收益提升 = Σ|Δw_i| × |Score_i| × 因子收益系数
        调仓成本     = Σ|Δw_i| × (手续费 + 滑点 + 冲击成本)
        仅当 预期收益 > 成本 × λ 时放行。

        Args:
            current_positions: 当前持仓
            target_positions: 目标持仓
            scores: symbol → 综合得分

        Returns:
            (是否放行, 明细 dict)。
        """
        cfg = self.config
        delta_weight: dict[str, float] = {}
        all_syms = set(current_positions) | set(target_positions)
        for sym in all_syms:
            old_dir = current_positions.get(sym, 0)
            new_dir = target_positions.get(sym, 0)
            if old_dir == new_dir:
                continue
            dw = abs(self._weight_of(new_dir) - self._weight_of(old_dir))
            if dw > 0:
                delta_weight[sym] = dw
        if not delta_weight:
            return True, {}
        unit_cost = cfg.commission_rate + cfg.slippage_rate + cfg.impact_rate
        benefit = sum(
            dw * abs(float(scores.get(sym, 0.0))) * cfg.factor_return_coeff for sym, dw in delta_weight.items()
        )
        cost = sum(dw * unit_cost for dw in delta_weight.values())
        execute = benefit > cost * cfg.cost_lambda
        detail = {
            "benefit": benefit,
            "cost": cost,
            "delta_weight": delta_weight,
            "unit_cost": unit_cost,
            "lambda": cfg.cost_lambda,
        }
        return execute, detail

    # ─── 第四层：防僵尸持仓 ──────────────────────────────

    def zombie_guard(
        self,
        target_positions: dict[str, int],
        zone: dict[str, ZoneCode],
    ) -> tuple[dict[str, int], list[str]]:
        """第四层：防僵尸持仓。

        单品种连续持仓天数超限且已不在核心/缓冲区 → 强制平仓（因子失效）。

        Args:
            target_positions: 目标持仓（就地修正）
            zone: 缓冲带区域

        Returns:
            (修正后目标持仓, 强制平仓品种列表)。
        """
        cfg = self.config
        forced: list[str] = []
        keep_zones = {"long_core", "long_buffer", "short_core", "short_buffer"}
        for sym, days in list(self.state.hold_days.items()):
            if days > cfg.max_hold_days and self.state.positions.get(sym, 0) != 0:
                if zone.get(sym, "flat") not in keep_zones:
                    if target_positions.get(sym, 0) != 0:
                        target_positions[sym] = 0
                        forced.append(sym)
                        self.state.hold_days.pop(sym, None)
                        logger.debug("[防僵尸] %s 持仓超 %d 日且已失效，强制平仓", sym, cfg.max_hold_days)
        return target_positions, forced

    # ─── 第五层：分批执行 ────────────────────────────────

    def plan_batches(self, changes: dict[str, tuple[int, int]]) -> list[list[str]]:
        """第五层：调仓执行时点优化。

        单次调仓品种数较多时拆分为多批，降低集中下单冲击。

        Args:
            changes: symbol → (旧方向, 新方向)

        Returns:
            分批计划（list[list[symbol]]）；品种数不足阈值时返回单批。
        """
        syms = list(changes.keys())
        if not syms:
            return []
        cfg = self.config
        if len(syms) < cfg.batch_threshold or cfg.batch_count <= 1:
            return [syms]
        batch_size = int(np.ceil(len(syms) / cfg.batch_count))
        return [syms[i : i + batch_size] for i in range(0, len(syms), batch_size)]

    # ─── 主入口 ──────────────────────────────────────────

    def step(
        self,
        scores: dict[str, float],
        event: Optional[str] = None,
        regime: Optional[str] = None,
    ) -> RebalanceDecision:
        """逐日执行五层调仓评估。

        Args:
            scores: 当日各品种综合得分（横截面排名输入）
            event: 外部触发事件（"risk" / "rollover"，None=无）
            regime: 当前 Regime（"trend" / "oscillation" / "transition"，启用
                asymmetric_buffer 时应用多空不对称缓冲；None=对称缓冲）

        Returns:
            RebalanceDecision（触发/目标持仓/变更/拦截/分批计划/日志）。
        """
        decision = RebalanceDecision(triggered=False, current_positions=dict(self.state.positions))
        # 多空不对称缓冲（手册阶段7 可选）：趋势 Regime 多头缓冲 k+1、空头 k-1；震荡反之
        k_long: Optional[int] = None
        k_short: Optional[int] = None
        if self.config.asymmetric_buffer and regime in ("trend", "oscillation"):
            k_base = int(self.config.buffer_k or 0)
            if regime == "trend":
                k_long = max(0, k_base + 1)
                k_short = max(0, k_base - 1)
            else:
                k_long = max(0, k_base - 1)
                k_short = max(0, k_base + 1)
        zone = self.build_zone(scores, k_long=k_long, k_short=k_short)
        decision.target_positions = {
            sym: self.zone_to_target(z, self.state.positions.get(sym, 0)) for sym, z in zone.items()
        }
        # 状态日期推进
        self.state.last_rebalance_days_ago += 1
        self.state.last_forced_days_ago += 1

        trigger = self.evaluate_trigger(self.state.positions, decision.target_positions, event)
        decision.trigger = trigger
        if trigger is None:
            self._update_hold_days(decision.target_positions)
            decision.triggered = False
            return decision
        decision.triggered = True

        # 第四层：防僵尸
        decision.target_positions, zombie_forced = self.zombie_guard(decision.target_positions, zone)
        decision.zombie_forced = zombie_forced

        # 变更集（排除无变化品种）
        changes: dict[str, tuple[int, int]] = {}
        for sym in set(self.state.positions) | set(decision.target_positions):
            old_dir = self.state.positions.get(sym, 0)
            new_dir = decision.target_positions.get(sym, 0)
            if old_dir != new_dir:
                changes[sym] = (old_dir, new_dir)
        decision.changes = changes

        # 第三层：换手阈值拦截（强制再平衡不拦截）
        if trigger != "forced" and changes:
            execute, detail = self.cost_benefit_gate(self.state.positions, decision.target_positions, scores)
            decision.cost_benefit = detail
            if not execute:
                decision.cost_intercepted = True
                decision.logs.append(
                    f"成本拦截: 预期收益 {detail['benefit']:.6f} ≤ 成本×λ {detail['cost'] * self.config.cost_lambda:.6f}"
                )
                logger.info("[换手拦截] 触发=%s 跳过调仓: %s", trigger, decision.logs[-1])
                self._update_hold_days(decision.target_positions)
                return decision

        # 执行调仓：更新持仓状态
        for sym, (old_dir, new_dir) in changes.items():
            if new_dir == 0:
                self.state.positions.pop(sym, None)
                self.state.hold_days.pop(sym, None)
            else:
                self.state.positions[sym] = new_dir
                self.state.hold_days[sym] = self.state.hold_days.get(sym, 0) + 1

        # 第五层：分批计划
        decision.batches = self.plan_batches(changes)

        # 状态刷新
        self.state.last_rebalance_days_ago = 0
        if trigger == "forced":
            self.state.last_forced_days_ago = 0
        decision.logs.append(f"触发类型: {trigger}, 调仓品种 {len(changes)} 个")
        logger.info("[调仓] 触发=%s 变更=%d 分批=%d", trigger, len(changes), len(decision.batches))
        return decision

    # ─── 内部：持仓天数更新 ──────────────────────────────

    def _update_hold_days(self, target_positions: dict[str, int]) -> None:
        """未调仓日更新连续持仓天数（新开仓置 1，已持仓 +1）。"""
        for sym, direction in target_positions.items():
            if direction == 0:
                self.state.hold_days.pop(sym, None)
            else:
                self.state.hold_days[sym] = self.state.hold_days.get(sym, 0) + 1


# ─── 换手率超限自动控制（CTA 手册阶段7） ──────────────────


@dataclass
class TurnoverControlResult:
    """换手率超限自动调整建议。"""

    buffer_k: int  # 调整后缓冲带宽度
    rebalance_period: int  # 调整后再平衡周期
    action: str  # ok / widen_buffer / extend_period / both / both_aggressive


def plan_turnover_control(
    turnover_annual: float,
    turnover_target: float = 50.0,
    base_buffer_k: int = 2,
    base_period: int = 3,
) -> TurnoverControlResult:
    """换手率超限自动调整（手册阶段7：年化换手率 ≤ 50 倍，超标按优先级调整）。

    调整优先级（手册）: ①扩大缓冲带 → ②延长再平衡周期 T → ③提高换手阈值 λ。

    Args:
        turnover_annual: 实际年化换手率
        turnover_target: 目标年化换手率上限（默认 50 倍）
        base_buffer_k: 基准缓冲带宽度 k
        base_period: 基准再平衡周期 T

    Returns:
        TurnoverControlResult（调整后 k/T/动作）。
    """
    ratio = float(turnover_annual) / max(float(turnover_target), 1e-9)
    if ratio <= 1.0:
        return TurnoverControlResult(base_buffer_k, base_period, "ok")
    if ratio <= 1.5:
        return TurnoverControlResult(base_buffer_k + 1, base_period, "widen_buffer")
    if ratio <= 2.0:
        return TurnoverControlResult(base_buffer_k + 2, base_period + 1, "both")
    return TurnoverControlResult(base_buffer_k + 3, base_period + 2, "both_aggressive")


__all__ = [
    "RebalanceConfig",
    "RebalanceState",
    "RebalanceDecision",
    "RebalanceController",
    "TurnoverControlResult",
    "auto_buffer_k",
    "plan_turnover_control",
]
