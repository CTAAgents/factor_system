"""
fts.factor_engine — 因子引擎（L1 Meta-Loop + L2 Evolution Loop + L3 Portfolio Loop）

从 FDT loop_engine 剥离的独立因子演化引擎。
整合 agentic-factor-investing + factorengine + Loop Engineering 三层架构。

核心模块：
    - contracts: TypedDict 契约层（L1 + L2 + L3 三层契约）
    - factor_program: 因子程序接口（图灵完备代码 + 安全沙箱）
    - seed_pool: 种子池（12 个内置因子 + L1 注入接口）
    - macro_evolution: 宏观演化（LLM 改逻辑）
    - micro_evolution: 微观演化（optuna 贝叶斯调参）
    - evaluation_chain: agentic 三级评估链
    - experience_chain: 经验链存储
    - verifier: Verifier 协议（锁定评估机制）
    - state: 演化状态 + trace_id 全链路
    - evolution_loop: L2 主循环（夜间因子演化）
    - meta_loop: L1 主循环（每日知识补给 + Bootstrapping + debate_round 分析）
    - portfolio_loop: L3 主循环（组合构建 + 正交化 + 衰减检验 + 信号产出）

版本: v1.1.0（与 FTS 项目版本同步）
"""

from .contracts import (
    FactorProgram,
    FactorSignature,
    EconomicLogic,
    EconomicScore,
    BacktestMetrics,
    MultipleTestResult,
    FactorEvaluation,
    ExperienceTrace,
    EvolutionState,
    VerifierConfig,
    VerifierResult,
    BudgetConfig,
    EVOLUTION_VERSION,
    DEFAULT_VERIFIER_CONFIG,
    DEFAULT_BUDGET_CONFIG,
    # L1 契约（Phase 2 v1.1.0 同步）
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
)
from .verifier import (
    FactorVerifier,
    VerifierAlreadyLockedError,
    VerifierNotLockedError,
    get_global_verifier,
)
from .factor_program import (
    FactorExecutor,
    FactorCompileError,
    create_factor_program,
    generate_factor_id,
    validate_factor_code,
)
from .seed_pool import SeedPool, get_default_seed_pool
from .experience_chain import (
    ExperienceChain,
    ExperienceChainError,
    create_trace_from_evaluation,
)
from .state import (
    EvolutionStateManager,
    generate_trace_id,
    generate_run_id,
)
from .evaluation_chain import EvaluationChain
from .macro_evolution import MacroEvolver, MockLLMClient, get_default_llm_client
from .micro_evolution import evolve_micro, optimize_params
from .evolution_loop import EvolutionLoop, EvolutionRunResult
from .meta_loop import (
    MetaLoopError,
    MetaStateManagerError,
    L1VerifierLocked,
    FactorPoolError,
    L1Verifier,
    MetaStateManager,
    FactorPoolManager,
    DebateQualityAnalyzer,
    BootstrappingChain,
    MetaLoop,
    MetaRunResult,
)
from .portfolio_loop import (
    L3Error,
    L3Verifier,
    PortfolioStateManager,
    PortfolioManager,
    synthesize_signals,
    orthogonalize_factors,
    decay_test,
    build_combo,
    generate_agent_proposals,
    load_elite_factors,
    inject_to_fdt,
    PortfolioRunResult,
    PortfolioLoop,
)
from .program import (
    ProgramConfig,
    parse_program_md,
    load_program,
    init_program,
    get_llm_env_overrides,
)
from .monitor import (
    LoopStatus,
    AllStatus,
    check_loop,
    check_all,
)

__version__ = "1.1.0"
__all__ = [
    # 版本
    "EVOLUTION_VERSION",
    # 契约
    "FactorProgram",
    "FactorSignature",
    "EconomicLogic",
    "EconomicScore",
    "BacktestMetrics",
    "MultipleTestResult",
    "FactorEvaluation",
    "ExperienceTrace",
    "EvolutionState",
    "VerifierConfig",
    "VerifierResult",
    "BudgetConfig",
    "DEFAULT_VERIFIER_CONFIG",
    "DEFAULT_BUDGET_CONFIG",
    # Verifier
    "FactorVerifier",
    "VerifierAlreadyLockedError",
    "VerifierNotLockedError",
    "get_global_verifier",
    # 因子程序
    "FactorExecutor",
    "FactorCompileError",
    "create_factor_program",
    "generate_factor_id",
    "validate_factor_code",
    # 种子池
    "SeedPool",
    "get_default_seed_pool",
    # 经验链
    "ExperienceChain",
    "ExperienceChainError",
    "create_trace_from_evaluation",
    # 状态
    "EvolutionStateManager",
    "generate_trace_id",
    "generate_run_id",
    # 评估链
    "EvaluationChain",
    # 宏观演化
    "MacroEvolver",
    "MockLLMClient",
    "get_default_llm_client",
    # 微观演化
    "evolve_micro",
    "optimize_params",
    # L2 主循环
    "EvolutionLoop",
    "EvolutionRunResult",
    # ─── L1 Meta-Loop（Phase 2 v1.1.0 同步）─────────────────
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
    "MetaLoopError",
    "MetaStateManagerError",
    "L1VerifierLocked",
    "FactorPoolError",
    "L1Verifier",
    "MetaStateManager",
    "FactorPoolManager",
    "DebateQualityAnalyzer",
    "BootstrappingChain",
    "MetaLoop",
    "MetaRunResult",
    # ─── L3 Portfolio Loop（Phase 3 v1.1.0 同步）────────────────
    "L3Error",
    "L3Verifier",
    "PortfolioStateManager",
    "PortfolioManager",
    "synthesize_signals",
    "orthogonalize_factors",
    "decay_test",
    "build_combo",
    "generate_agent_proposals",
    "load_elite_factors",
    "inject_to_fdt",
    "PortfolioRunResult",
    "PortfolioLoop",
]
