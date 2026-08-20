"""
fts/factor_engine/scope_domain — 品种/产业链特异因子域评估模块（P0 方案）

把"全链为主、特异放行特例"倒转为"scope 域内评估为主"：
因子有效域（全链/子链/品种）内的 IC、Sharpe、置换、极端行情、半衰期、
退化、评审全部在域内计算。品种级"特异"必须过真伪鉴别护栏（guard_passed）。

公共 API：
  - FactorScope / DomainStats / ScopeGuardResult（types.py 契约）
  - resolve_chain_map / resolve_scope（resolver.py 链映射 SSOT）
  - compute_domain_stats / aggregate_domain（evaluator.py 域内统计量）
  - run_scope_guard（guard.py 真伪鉴别护栏）
  - attach_evaluation_domain_stats / domain_gate_decision / chain_focus_batches
    （hooks.py 接入桩）

开关：FTS_SCOPE_DOMAIN_ENABLED 默认 "1"，=0 即时回退全链口径。
"""

from __future__ import annotations

from fts.factor_engine.scope_domain.evaluator import (
    aggregate_domain,
    compute_domain_stats,
    domain_sharpe,
    estimate_domain_half_life,
    evaluate_symbol_scope,
    resolve_domain_symbols,
    subperiod_consistency,
)
from fts.factor_engine.scope_domain.guard import permutation_p, run_scope_guard
from fts.factor_engine.scope_domain.hooks import (
    attach_evaluation_domain_stats,
    chain_focus_batches,
    domain_gate_decision,
    scope_domain_enabled,
    symbol_scope_guard,
)
from fts.factor_engine.scope_domain.resolver import resolve_chain_map, resolve_scope, symbols_for_chain
from fts.factor_engine.scope_domain.types import DomainStats, FactorScope, ScopeGuardResult

__all__ = [
    "FactorScope",
    "DomainStats",
    "ScopeGuardResult",
    "aggregate_domain",
    "attach_evaluation_domain_stats",
    "chain_focus_batches",
    "compute_domain_stats",
    "domain_gate_decision",
    "domain_sharpe",
    "estimate_domain_half_life",
    "evaluate_symbol_scope",
    "permutation_p",
    "resolve_chain_map",
    "resolve_domain_symbols",
    "resolve_scope",
    "run_scope_guard",
    "scope_domain_enabled",
    "subperiod_consistency",
    "symbol_scope_guard",
    "symbols_for_chain",
]
