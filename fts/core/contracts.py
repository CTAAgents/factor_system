"""
fts.core.contracts — FTS 核心契约入口

Re-export factor_engine 的核心契约，提供统一的导入入口。
因子引擎的完整契约定义在 fts.factor_engine.contracts 中。

HARNESS §契约优先：所有模块必须基于本文件的 TypedDict/常量实现。
任何字段变更必须 bump 版本号。
"""

from __future__ import annotations

from typing import Optional, TypedDict

from typing_extensions import NotRequired  # Python 3.10+ 兼容（mypy 识别 typing_extensions 版本）

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


class OHLCVBase(TypedDict, total=False):
    """公共 OHLCV 字段（无市场形状，共享层）。

    股票/期货两市场 K 线的公共字段。字段集合锁定，禁止任意加减。
    """

    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trace_id: str


class FusionMeta(TypedDict, total=False):
    """多源融合元数据（两市场共用，共享层）。"""

    contributing_sources: list[str]
    fusion_strategy: str
    disagreement_pct: float


class StockOHLCV(TypedDict, total=False):
    """股票/ETF 单条 K 线契约（股票特有形状：复权因子）。

    必填字段: symbol/date/open/high/low/close/volume/trace_id
    可选字段: amount/adjust_factor/source/fetched_at/融合元数据

    HARNESS §契约优先: 字段集合锁定，禁止任意加减。
    """

    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trace_id: str
    amount: NotRequired[float]
    adjust_factor: NotRequired[float]  # 复权因子（股票特有）
    source: NotRequired[str]
    fetched_at: NotRequired[str]
    contributing_sources: NotRequired[list[str]]
    fusion_strategy: NotRequired[str]
    disagreement_pct: NotRequired[float]


class FusedOHLCV(TypedDict, total=False):
    """多源融合 OHLCV 数据契约（通用兼容契约）。

    必填字段:
        symbol, date, open, high, low, close, volume,
        trace_id, contributing_sources, fusion_strategy, source

    可选字段:
        amount, settle, disagreement_pct
        hold, oi_change, pre_settle, vwap（期货扩展，Phase 14.4 兼容）

    ⚠️ 兼容说明（F.1 契约拆分）: 新代码优先使用市场契约 `StockOHLCV` /
    `FuturesOHLCV`；本契约保留以兼容旧调用方，字段集合冻结不再演进。
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
    hold: Optional[float]
    oi_change: Optional[float]
    pre_settle: Optional[float]
    vwap: Optional[float]
    trace_id: str
    contributing_sources: list[str]
    fusion_strategy: str
    source: str
    disagreement_pct: Optional[float]


# ─── 期货 K 线契约（Phase 14.4，v2.3.0 起）──────────────────


class FuturesOHLCV(TypedDict, total=False):
    """期货单条 K 线数据契约。

    必填字段(8): symbol/date/open/high/low/close/volume/trace_id
    可选字段(8+): amount/hold/settle/pre_settle/oi_change/vwap/source/fetched_at
                  + 融合元数据（contributing_sources/fusion_strategy/disagreement_pct，F.1 补入）

    HARNESS §契约优先: 字段集合锁定，禁止任意加减。
    """

    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trace_id: str
    amount: NotRequired[float]
    hold: NotRequired[float]
    settle: NotRequired[float]
    pre_settle: NotRequired[float]
    oi_change: NotRequired[float]
    vwap: NotRequired[float]
    source: NotRequired[str]
    fetched_at: NotRequired[str]
    contributing_sources: NotRequired[list[str]]
    fusion_strategy: NotRequired[str]
    disagreement_pct: NotRequired[float]


class FuturesDataLineage(TypedDict, total=False):
    """期货数据同步血缘追踪契约（Phase 14.4）。

    必填字段(5): trace_id/started_at/finished_at/symbols/rows_written
    可选字段(3): sources_used/sources_failed/disagreements
    """

    trace_id: str
    started_at: str
    finished_at: str
    symbols: list[str]
    rows_written: int
    sources_used: NotRequired[dict[str, int]]
    sources_failed: NotRequired[dict[str, int]]
    disagreements: NotRequired[int]


class FusionReport(TypedDict, total=False):
    """多源融合报告契约（Phase 14.4，v2.3.0+）。

    必填字段(6): trace_id/symbol/strategy/rows/sources_used/rows_count
    可选字段(4): started_at/finished_at/disagreements/avg_disagreement_pct

    CLI `fts data fuse` 的 JSON 输出、联调脚本报告落盘均使用本契约。
    """

    trace_id: str
    symbol: str
    strategy: str
    rows: list[FusedOHLCV]
    sources_used: list[str]
    rows_count: int
    started_at: NotRequired[str]
    finished_at: NotRequired[str]
    disagreements: NotRequired[list[MultiSourceDisagreement]]
    avg_disagreement_pct: NotRequired[float]


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
    # F.1 契约拆分：公共基契约 + 市场契约
    "OHLCVBase",
    "FusionMeta",
    "StockOHLCV",
    # 期货 K 线 / 血缘 / 融合报告契约（Phase 14.4）
    "FuturesOHLCV",
    "FuturesDataLineage",
    "FusionReport",
]
