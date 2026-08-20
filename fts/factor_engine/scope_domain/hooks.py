"""
fts/factor_engine/scope_domain/hooks.py — 接入桩（评估/评审/退化/信号/L1 注入）

各接入点懒加载本模块，避免顶层循环依赖：
  - attach_evaluation_domain_stats：评估链产出 domain_stats；
  - domain_gate_decision：评审域内门禁（域内 IC/Sharpe 达标 → approved）；
  - chain_focus_batches：L1 注入按链分批（futures 17 链 / energy 子链）。

开关统一读 FTS_SCOPE_DOMAIN_ENABLED（默认 "1"，=0 即时回退全链口径）。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fts.factor_engine.scope_domain.evaluator import compute_domain_stats
from fts.factor_engine.scope_domain.resolver import resolve_chain_map
from fts.factor_engine.scope_domain.types import DomainStats, FactorScope

logger = logging.getLogger(__name__)

_ENV_ENABLED = "FTS_SCOPE_DOMAIN_ENABLED"


def scope_domain_enabled() -> bool:
    """域内评估总开关（默认开启；FTS_SCOPE_DOMAIN_ENABLED=0 回退全链口径）。"""
    return os.getenv(_ENV_ENABLED, "1") == "1"


def load_scope_config() -> Optional[Any]:
    """懒加载 scope_domain 配置（settings.scope_domain；缺失 → None 用默认值）。"""
    try:
        from fts.config import get_config

        cfg = get_config()
        return getattr(cfg, "scope_domain", None) or getattr(cfg, "scope_domain_settings", None)
    except Exception:  # noqa: BLE001
        return None


def attach_evaluation_domain_stats(
    evaluation: dict[str, Any],
    symbol_ic: dict[str, float],
    market: str = "futures",
) -> dict[str, Any]:
    """评估链接入：写 evaluation["domain_stats"]（全链口径聚合）。

    仅 scope_domain_enabled() 时写入；symbol_ic 为空 → 跳过（不污染评估产物）。
    """
    if not scope_domain_enabled() or not symbol_ic:
        return evaluation
    try:
        scope = FactorScope(kind="all")
        stats = compute_domain_stats(symbol_ic=symbol_ic, scope=scope, market=market)
        evaluation["domain_stats"] = stats.model_dump()
    except Exception as e:  # noqa: BLE001 — 域内统计失败不阻断评估
        logger.warning("[scope] 评估域内统计产出失败（跳过）: %s", e)
    return evaluation


def domain_gate_decision(
    *,
    ic: Any,
    sharpe: Any,
    domain_stats: Optional[dict[str, Any]],
    min_ic: float,
    min_sharpe: float,
) -> Optional[str]:
    """评审域内门禁：域内 IC/Sharpe 达标 → "approved"（域内口径优先）。

    仅传入有效 domain_stats（valid=True）时生效；未达标或缺失 → None（走全链逻辑）。
    """
    if not scope_domain_enabled() or not domain_stats:
        return None
    try:
        stats = DomainStats(**domain_stats)
    except Exception:  # noqa: BLE001
        return None
    if not stats.valid or stats.ic is None:
        return None
    d_ic, d_sharpe = abs(float(stats.ic)), stats.sharpe
    if d_ic < min_ic:
        return None
    if d_sharpe is not None and d_sharpe < min_sharpe:
        return None
    logger.info(
        "[scope] 评审域内门禁放行: domain_ic=%.4f domain_sharpe=%s (全链 ic=%s sharpe=%s)",
        d_ic,
        None if d_sharpe is None else round(d_sharpe, 3),
        ic,
        sharpe,
    )
    return "approved"


def chain_focus_batches(market: str, max_candidates: int) -> list[tuple[str, int]]:
    """L1 注入按链分批：futures 按 sector_map 17 链、energy 按子链。

    Returns:
        [(focus, per_batch), ...]；无链映射 → 空（调用方回退单批）。
    """
    chain_map = resolve_chain_map(market)
    if not chain_map:
        return []
    chains = list(chain_map.keys())
    n = max(1, len(chains))
    per = max(1, max_candidates // n)
    return [(c, per) for c in chains]


def symbol_scope_guard(
    *,
    signal: Any,
    forward_returns: Any,
    symbol: str,
) -> dict[str, Any]:
    """品种级特异候选真伪鉴别（P2 完整接线）：域内评估 + 三门槛护栏。

    护栏不过（样本窗/跨子期/显著性任一）→ guard_passed=False，标记"疑似噪声"，
    调用方不得落库为"品种特异"画像（宁漏标不误标）。

    Returns:
        {stats: DomainStats.model_dump(), guard: ScopeGuardResult.model_dump()}。
    """
    from fts.factor_engine.scope_domain.evaluator import evaluate_symbol_scope
    from fts.factor_engine.scope_domain.guard import run_scope_guard

    cfg = load_scope_config()
    stats = evaluate_symbol_scope(signal, forward_returns, symbol, cfg=cfg)
    guard = run_scope_guard(stats=stats, daily_ic=[], cfg=cfg)
    if guard.passed:
        stats.guard_passed = True
        stats.scope.evidence.update(guard.evidence)
    return {"stats": stats.model_dump(), "guard": guard.model_dump()}


__all__ = [
    "attach_evaluation_domain_stats",
    "chain_focus_batches",
    "domain_gate_decision",
    "load_scope_config",
    "scope_domain_enabled",
    "symbol_scope_guard",
]
