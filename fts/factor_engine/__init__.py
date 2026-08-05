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
    - standardizer: 标准化模块（6 种标准化方法）
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
    FactorCorrelation,
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
    # L3 契约
    L3VerifierConfig,
    L3MetaLoopState,
    DEFAULT_L3_VERIFIER_CONFIG,
    DEFAULT_L3_BUDGET,
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
from .seed_pool import SeedPool, get_default_seed_pool, compute_seed_correlations, compute_cross_section_correlations
from .seed_loader import (
    load_all_yaml_seeds,
    load_factors_from_dir,
    load_factors_from_yaml,
    verify_yaml_integrity,
)
from .standardizer import (
    StandardizeMethod,
    SUPPORTED_METHODS,
    StandardizerConfig,
    Standardizer,
    standardize,
)
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
from .ablation import (
    AblationExperiment,
    AblationResult,
    SingleAblation,
    ABLATION_MODES,
)
from .shap_analyzer import (
    ShapAnalyzer,
    ShapAnalysisResult,
    ShapSampleAnalysis,
    ShapFeatureImportance,
)
from .robustness import (
    RobustnessTester,
    RobustnessTestResult,
    AdversarialTestResult,
    MissingValueTestResult,
    OODTestResult,
)
from .causal_validator import (
    CausalValidator,
    CausalValidationResult,
    EventPredictionError,
)
from .monitor import (
    LoopStatus,
    AllStatus,
    check_loop,
    check_all,
)
from .factor_quality_card import (
    FactorQualityCard,
    FactorQualityCardConfig,
    FactorQualityScore,
    DimensionScore,
    compute_total_score,
    determine_grade,
)
from .audit import (
    FactorAuditor,
    FactorAuditConfig,
    FactorAuditReport,
    AuditItemResult,
    AuditItemStatus,
)
from .feature_ops import (
    OperatorInfo,
    OperatorRegistry,
    TimeSeriesOps,
    PriceOps,
    RollingOps,
    TechnicalOps,
    CrossSectionOps,
    CrossSymbolOps,
    CompositeOps,
    FeatureOpsEngine,
)
from .gp_evolver import (
    TreeNode,
    ExpressionTree,
    FitnessResult,
    GPEvolverConfig,
    GenerationSnapshot,
    GPEvolveResult,
    GPEvolver,
    tree_to_factor_program,
)
from .feature_importance import (
    FeatureImportanceResult,
    FeatureImportanceAnalyzer,
)
from .backtest_pipeline import (
    BacktestPipeline,
    BacktestInput,
    BacktestReport,
    BacktestResult,
    BacktestPipelineBuilder,
    PipelineResult,
    PipelineStage,
    PerformanceMetrics,
    FactorOutput,
    PipelineConfig,
)
from .factor_screener import FactorScreener
from .signal_generator import SignalGenerator
from .portfolio_constructor import PortfolioConstructor, PortfolioResult
from .cost_simulator import CostSimulator, CostResult
from .risk_attributor import RiskAttributor, RiskAttributionReport
from .report_generator import ReportGenerator
from .capital_allocator import CapitalAllocator, AllocationResult
from .signal_contract import (
    FactorContribution,
    SignalDetail,
    SignalMeta,
    FactorSignal,
    SignalValidator,
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
    "FactorCorrelation",
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
    "compute_seed_correlations",
    "compute_cross_section_correlations",
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
    # 标准化
    "StandardizeMethod",
    "SUPPORTED_METHODS",
    "StandardizerConfig",
    "Standardizer",
    "standardize",
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
    "L3VerifierConfig",
    "L3MetaLoopState",
    "DEFAULT_L3_VERIFIER_CONFIG",
    "DEFAULT_L3_BUDGET",
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
    # ─── 消融实验（Phase A v1.10.0 逻辑审查）────────────────
    "AblationExperiment",
    "AblationResult",
    "SingleAblation",
    "ABLATION_MODES",
    # ─── SHAP 分析（Phase B v1.11.0 逻辑审查）────────────────
    "ShapAnalyzer",
    "ShapAnalysisResult",
    "ShapSampleAnalysis",
    "ShapFeatureImportance",
    # ─── 鲁棒性审查（Phase B v1.11.0 逻辑审查）────────────────
    "RobustnessTester",
    "RobustnessTestResult",
    "AdversarialTestResult",
    "MissingValueTestResult",
    "OODTestResult",
    # ─── 因果结构审查（Phase C v2.0.0 逻辑审查）────────────────
    "CausalValidator",
    "CausalValidationResult",
    "EventPredictionError",
    # ─── 因子质量评分卡（Phase A.1 v1.0.0）────────────────
    "FactorQualityCard",
    "FactorQualityCardConfig",
    "FactorQualityScore",
    "DimensionScore",
    "compute_total_score",
    "determine_grade",
    # ─── 因子审计流程 (Phase B.3 v0.1.0)────────────────
    "FactorAuditor",
    "FactorAuditConfig",
    "FactorAuditReport",
    "AuditItemResult",
    "AuditItemStatus",
    # ─── 特征工程中台 (Phase C.1 v0.1.0)────────────────
    "OperatorInfo",
    "OperatorRegistry",
    "TimeSeriesOps",
    "PriceOps",
    "RollingOps",
    "TechnicalOps",
    "CrossSectionOps",
    "CrossSymbolOps",
    "CompositeOps",
    "FeatureOpsEngine",
    "TreeNode",
    "ExpressionTree",
    "FitnessResult",
    "GPEvolverConfig",
    "GenerationSnapshot",
    "GPEvolveResult",
    "GPEvolver",
    "tree_to_factor_program",
    "FeatureImportanceResult",
    "FeatureImportanceAnalyzer",
    # ─── B.2 回测流水线（v2.9.0 增强）────────────────
    "BacktestPipeline",
    "BacktestInput",
    "BacktestReport",
    "BacktestResult",
    "BacktestPipelineBuilder",
    "PipelineResult",
    "PipelineStage",
    "PerformanceMetrics",
    "FactorOutput",
    "PipelineConfig",
    "FactorScreener",
    "SignalGenerator",
    "PortfolioConstructor",
    "PortfolioResult",
    "CostSimulator",
    "CostResult",
    "RiskAttributor",
    "RiskAttributionReport",
    "ReportGenerator",
    "CapitalAllocator",
    "AllocationResult",
    # ─── C.2 实盘信号契约（v2.9.0）────────────────
    "FactorContribution",
    "SignalDetail",
    "SignalMeta",
    "FactorSignal",
    "SignalValidator",
]
