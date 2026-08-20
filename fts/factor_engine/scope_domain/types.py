"""
fts/factor_engine/scope_domain/types.py — scope 域评估契约（P0 方案，Pydantic V2）

定义因子有效域（全链/子链/品种）与域内统计量的对外契约，供评估链产出、
评审门禁、退化监控、信号契约（schema_version=2）统一消费。

设计原则（与 subchain_profile 保守性一致）：
  - 域内统计量独立于全链口径：特异因子不再"先被全链稀释再走放行特例"；
  - 品种级"特异"必须过真伪鉴别护栏（guard_passed），宁漏标不误标。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# scope 类型：all=全链 / chain=产业链（可多链）/ symbol=品种级（单品种）
ScopeKind = Literal["all", "chain", "symbol"]


class FactorScope(BaseModel):
    """因子有效域定义（对外契约，信号契约 v2 消费）。

    - kind="all"：全链适用（默认，兼容现状）；
    - kind="chain"：chains 为有效产业链列表（空列表视作全链）；
    - kind="symbol"：symbols 为有效品种列表（单元素，品种级特异）。
    """

    kind: ScopeKind = "all"
    chains: list[str] = Field(default_factory=list, description="有效链列表（kind=chain）")
    symbols: list[str] = Field(default_factory=list, description="有效品种列表（kind=symbol）")
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="画像证据（evaluated_at/trace_id/护栏结论）"
    )


class DomainStats(BaseModel):
    """域内统计量（scope 域内口径，非全链）。"""

    scope: FactorScope = Field(default_factory=FactorScope)
    n_symbols: int = 0  # 域内有效品种数
    n_dates: int = 0  # 域内有效交易日数
    ic: Optional[float] = None  # 域内 IC（逐品种时序 IC 均值，方向翻转同步）
    sharpe: Optional[float] = None  # 域内 Sharpe（域内 IC 均值 / IC 标准差 × 年化系数）
    ic_positive_ratio: float = 0.0  # 域内同向品种占比
    subperiod_consistency: float = 0.0  # 跨不重叠子期符号一致率（真伪护栏维度）
    permutation_p: Optional[float] = None  # 域内置换检验 p 值
    extreme_ic: Optional[float] = None  # 极端窗口域内 IC
    half_life_days: Optional[float] = None  # 域内 IC 半衰期
    guard_passed: bool = False  # 真伪鉴别护栏是否通过
    valid: bool = False  # 统计有效性（样本窗/品种数达标）


class ScopeGuardResult(BaseModel):
    """真伪鉴别护栏结论（品种级专用）。"""

    passed: bool = False
    reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


__all__ = ["FactorScope", "DomainStats", "ScopeGuardResult", "ScopeKind"]
