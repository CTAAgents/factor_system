"""
fts.core.contracts — FTS 核心契约入口

Re-export factor_engine 的核心契约，提供统一的导入入口。
因子引擎的完整契约定义在 fts.factor_engine.contracts 中。

HARNESS §契约优先：所有模块必须基于本文件的 TypedDict/常量实现。
任何字段变更必须 bump 版本号。
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

# Re-export factor_engine 契约（从 loop_engine/contracts.py 迁移）
from fts.factor_engine.contracts import (
    # 版本号
    EVOLUTION_VERSION,
    # 因子程序契约
    FactorProgram,
    FactorSignature,
    EconomicLogic,
    # 评估契约
    BacktestMetrics,
    EconomicScore,
    MultipleTestResult,
    FactorEvaluation,
    # 经验链
    ExperienceTrace,
    # 演化状态
    EvolutionState,
    # Verifier
    VerifierConfig,
    VerifierResult,
    # 预算
    BudgetConfig,
    # 默认配置
    DEFAULT_VERIFIER_CONFIG,
    DEFAULT_BUDGET_CONFIG,
    # L1 Meta-Loop
    L1BootstrappingSource,
    MetaLoopStatus,
    SeedCandidate,
    L1MetaLoopState,
    FactorPoolEntry,
    FactorPool,
    L1VerifierConfig,
    L1VerifierResult,
    L1BudgetConfig,
    DEFAULT_L1_VERIFIER_CONFIG,
    DEFAULT_L1_BUDGET_CONFIG,
    # L3 Portfolio Loop
    FactorCorrelation,
    PortfolioSignal,
    PortfolioCombo,
    AgentOptimizationProposal,
    L3VerifierConfig,
    L3MetaLoopState,
    DEFAULT_L3_VERIFIER_CONFIG,
    DEFAULT_L3_BUDGET,
    # 多源数据交叉验证
    MultiSourceDisagreement,
)

# ─── 数据融合契约 ──────────────────────────────────────────

class FusedOHLCV(TypedDict, total=False):
    """多源融合 OHLCV 数据契约。

    必填字段:
        symbol, date, open, high, low, close, volume,
        trace_id, contributing_sources, fusion_strategy, source

    可选字段:
        amount, settle, disagreement_pct
    """
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: Optional[float]
    settle: Optional[float]
    trace_id: str
    contributing_sources: list[str]
    fusion_strategy: str
    source: str
    disagreement_pct: Optional[float]


__all__ = [
    "EVOLUTION_VERSION",
    "FactorProgram",
    "FactorSignature",
    "EconomicLogic",
    "BacktestMetrics",
    "EconomicScore",
    "MultipleTestResult",
    "FactorEvaluation",
    "ExperienceTrace",
    "EvolutionState",
    "VerifierConfig",
    "VerifierResult",
    "BudgetConfig",
    "DEFAULT_VERIFIER_CONFIG",
    "DEFAULT_BUDGET_CONFIG",
    "L1BootstrappingSource",
    "MetaLoopStatus",
    "SeedCandidate",
    "L1MetaLoopState",
    "FactorPoolEntry",
    "FactorPool",
    "L1VerifierConfig",
    "L1VerifierResult",
    "L1BudgetConfig",
    "DEFAULT_L1_VERIFIER_CONFIG",
    "DEFAULT_L1_BUDGET_CONFIG",
    "FactorCorrelation",
    "PortfolioSignal",
    "PortfolioCombo",
    "AgentOptimizationProposal",
    "L3VerifierConfig",
    "L3MetaLoopState",
    "DEFAULT_L3_VERIFIER_CONFIG",
    "DEFAULT_L3_BUDGET",
    "MultiSourceDisagreement",
    # 数据融合契约
    "FusedOHLCV",
]
