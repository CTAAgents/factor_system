"""
fts.factor_engine.specific_observe — 特异因子观察期与 OOS 前瞻复核（plans/59 OPT-03 / GAP-163）。

子链/品种特异画像"一次判定终身"存在两类风险：
  - 误标（护栏漏检的噪声被当特异固化，长期污染组合）；
  - 误杀（真特异因子无观察窗口，一次判定后无真实样本外二次确认）。

本模块提供特异画像观察期机制：
  - 晋升时特异因子写入观察期标记（promoted_at / observe_until / baseline_domain_ic）；
  - 观察期内画像保持"观察态"（不固化）；
  - 观察期满用晋升后的真实 OOS 域内 IC 复核：
        |current| >= confirm_min_ic                 → confirm（固化画像）
        衰减较基线 > revoke_ic_decay               → revoke（撤销画像，回退全链口径）
        数据不可得 / 未显著衰减但未达固化线         → hold（观察期顺延，宁缺毋滥方向）
  - 小样本（n_symbols<3）基线向全链均值收缩（shrink_scope_ic），防止小样本基线虚高
    导致误固化。

纯函数 / 日期用自然日近似交易日 / 不判失败不崩溃 / 可单测。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 观察态 status 常量
STATUS_OBSERVING = "observing"
STATUS_CONFIRMED = "confirmed"
STATUS_REVOKED = "revoked"

# 复核阶段
PHASE_OBSERVING = "observing"
PHASE_REVIEW_DUE = "review_due"


class SpecificObserveConfig(BaseModel):
    """特异因子观察期配置。

    enabled=True（默认）时晋升特异因子写观察期标记；=0 关闭（回退旧行为）。
    """

    enabled: bool = Field(default=True, description="总开关；False=关闭观察期机制")
    observe_days: int = Field(default=20, description="观察期天数（自然日，近似 20 交易日）")
    confirm_min_ic: float = Field(default=0.02, description="观察期满域内 |IC| 固化下限")
    revoke_ic_decay: float = Field(default=0.50, description="域内 IC 较基线衰减>该比例 → 撤销")
    hold_grace_days: int = Field(default=10, description="复核数据不可得时观察期顺延天数")
    shrink_k: float = Field(default=0.5, description="小样本基线收缩强度（0=不收缩，1=完全向全链）")

    @classmethod
    def from_env(cls) -> "SpecificObserveConfig":
        """从环境变量读取（FTS_SPECIFIC_OBSERVE_ENABLED / FTS_SPECIFIC_OBSERVE_DAYS 等）。"""
        import os

        def _i(key: str, default: int) -> int:
            try:
                return int(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        enabled = os.getenv("FTS_SPECIFIC_OBSERVE_ENABLED", "1").lower() in {"1", "true", "yes"}
        return cls(
            enabled=enabled,
            observe_days=_i("FTS_SPECIFIC_OBSERVE_DAYS", 20),
            confirm_min_ic=_f("FTS_SPECIFIC_CONFIRM_MIN_IC", 0.02),
            revoke_ic_decay=_f("FTS_SPECIFIC_REVOKE_IC_DECAY", 0.50),
        )


def _to_date(v: Any) -> Optional[date]:
    """解析日期/ISO 字符串为 date；失败返回 None。"""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v).date()
        except ValueError:
            return None
    return None


def build_observe_marker(
    promoted_at: Any,
    baseline_domain_ic: Optional[float],
    config: Optional[SpecificObserveConfig] = None,
) -> dict[str, Any]:
    """构造特异因子观察期标记（晋升落库时调用）。

    Args:
        promoted_at: 晋升时间（datetime / date / ISO 字符串）
        baseline_domain_ic: 晋升时域内 IC（基线，用于观察期满衰减对比）
        config: SpecificObserveConfig（None → 默认）

    Returns:
        dict: {promoted_at, observe_days, observe_until, status, baseline_domain_ic}
    """
    cfg = config or SpecificObserveConfig()
    p_date = _to_date(promoted_at)
    if p_date is None:
        p_date = date.today()
    until = p_date + timedelta(days=max(1, cfg.observe_days))
    return {
        "promoted_at": p_date.isoformat(),
        "observe_days": cfg.observe_days,
        "observe_until": until.isoformat(),
        "status": STATUS_OBSERVING,
        "baseline_domain_ic": float(baseline_domain_ic) if baseline_domain_ic is not None else None,
    }


def observe_phase(
    marker: dict[str, Any],
    today: Any = None,
    config: Optional[SpecificObserveConfig] = None,
) -> str:
    """观察期阶段判定（纯函数）。

    Args:
        marker: build_observe_marker 输出
        today: 当前日期（None → date.today()）
        config: 配置（None → 默认）

    Returns:
        str: "observing" | "review_due" | "confirmed" | "revoked"
    """
    if not isinstance(marker, dict):
        return PHASE_OBSERVING
    status = marker.get("status")
    if status == STATUS_CONFIRMED:
        return STATUS_CONFIRMED
    if status == STATUS_REVOKED:
        return STATUS_REVOKED
    today = _to_date(today) or date.today()
    until = _to_date(marker.get("observe_until"))
    if until is None:
        return PHASE_OBSERVING  # 标记损坏，保守保持观察
    return PHASE_REVIEW_DUE if today >= until else PHASE_OBSERVING


def shrink_scope_ic(
    domain_ic: Optional[float],
    n_symbols: int,
    global_ic: Optional[float],
    config: Optional[SpecificObserveConfig] = None,
) -> Optional[float]:
    """小样本域内 IC 向全链均值收缩（贝叶斯收缩评分）。

    单链/双链候选（n_symbols<3）的域内 IC 为小样本估计，噪声大；向全链均值
    收缩后作为观察期复核基线，防止小样本基线虚高导致误固化。

    Args:
        domain_ic: 域内 IC（小样本）
        n_symbols: 域内有效品种数
        global_ic: 全链 IC（收缩目标）
        config: 配置（None → 默认）

    Returns:
        float: 收缩后 IC；输入缺失 → None。
    """
    if domain_ic is None or n_symbols < 1:
        return domain_ic
    cfg = config or SpecificObserveConfig()
    if n_symbols >= 3 or global_ic is None or cfg.shrink_k <= 0:
        return float(domain_ic)
    k = min(1.0, cfg.shrink_k * (3 - n_symbols) / 2.0)  # n=1 → k, n=2 → k/2
    return float(domain_ic * (1 - k) + global_ic * k)


def review_specific_oos(
    marker: dict[str, Any],
    current_domain_ic: Optional[float],
    config: Optional[SpecificObserveConfig] = None,
) -> tuple[str, dict[str, Any]]:
    """观察期满 OOS 复核判定（纯函数）。

    注意：调用方须先经 ``observe_phase(marker, today) == "review_due"`` 确认观察期满；
    未到期时本函数恒返回 ("hold", ...)（由阶段判定拦截）。

    Args:
        marker: 观察期标记
        current_domain_ic: 晋升后新数据域内 IC（None=数据不可得 → hold 顺延）
        config: 配置（None → 默认）

    Returns:
        (decision, detail): decision ∈ {"confirm", "revoke", "hold"}
    """
    cfg = config or SpecificObserveConfig()
    if not isinstance(marker, dict):
        return "hold", {"reason": "观察期标记缺失"}
    if marker.get("status") == STATUS_CONFIRMED:
        return "confirm", {"reason": "已确认固化"}
    if marker.get("status") == STATUS_REVOKED:
        return "revoke", {"reason": "已撤销"}
    if observe_phase(marker) != PHASE_REVIEW_DUE:
        return "hold", {"reason": "观察期未到期"}

    if current_domain_ic is None:
        return (
            "hold",
            {"reason": "OOS 域内 IC 不可得，观察期顺延", "grace_days": cfg.hold_grace_days},
        )

    cur_abs = abs(float(current_domain_ic))
    base = marker.get("baseline_domain_ic")
    if cur_abs >= cfg.confirm_min_ic and base is None:
        return "confirm", {"reason": f"OOS 域内 |IC|={cur_abs:.4f} >= {cfg.confirm_min_ic}（无基线，按达标固化）"}
    if base is None:
        return "hold", {"reason": "基线缺失且未达固化线，维持观察"}

    base_abs = abs(float(base))
    decay = 1.0 - (cur_abs / base_abs) if base_abs > 1e-12 else 0.0
    if cur_abs >= cfg.confirm_min_ic:
        return "confirm", {"reason": f"OOS 域内 |IC|={cur_abs:.4f} >= {cfg.confirm_min_ic}，固化特异画像"}
    if decay > cfg.revoke_ic_decay:
        return "revoke", {"reason": f"域内 IC 衰减 {decay:.1%} > {cfg.revoke_ic_decay:.0%}，撤销特异画像回退全链"}
    return "hold", {"reason": f"衰减 {decay:.1%} 未达撤销线且 |IC| 未达固化线，维持观察"}


__all__ = [
    "STATUS_OBSERVING",
    "STATUS_CONFIRMED",
    "STATUS_REVOKED",
    "PHASE_OBSERVING",
    "PHASE_REVIEW_DUE",
    "SpecificObserveConfig",
    "build_observe_marker",
    "observe_phase",
    "shrink_scope_ic",
    "review_specific_oos",
]
