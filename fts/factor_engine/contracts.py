"""
loop_engine/contracts.py — L2 演化引擎契约层

HARNESS §契约优先：所有模块必须基于本文件的 TypedDict/常量实现。
任何字段变更必须 bump 版本号并更新 docs/harness/11-loop-engineering.md。

版本: 动态读取自 fts.__version__
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, TypedDict, cast


# ─── 市场与家族枚举 ─────────────────────────────────────────

FactorMarket = Literal["futures", "stock", "etf", "bond", "multi"]
"""因子适用的市场类型。"""

FactorFamily = Literal[
    "trend",  # 趋势跟踪
    "mean_reversion",  # 均值回归
    "carry",  # 跨期/跨品种套利
    "seasonality",  # 季节性
    "cross_section",  # 横截面
    "qlib",  # 微软 Qlib 开源因子库
    "gtja",  # 国泰君安 191 因子库
    "wq101",  # 世坤 WorldQuant 101 Alpha 因子库
    "fundamental",  # 基本面
    "technical",  # 技术指标
    "microstructure",  # 微观结构
    "macro",  # 宏观
    "behavioral",  # 行为金融
    "liquidity",  # 流动性
    "volatility",  # 波动率
    "volume",  # 成交量
    "multi_factor",  # 多因子组合
    "other",  # 其他
]
"""因子家族分类（17 大类）。"""


# ─── 因子风格枚举（A.3 原设计落地，v2.56.0）─────────────────

FactorStyle = Literal[
    "momentum",  # 趋势/动量
    "mean_reversion",  # 均值回归
    "carry",  # Carry 收益
    "value",  # 价值
    "low_vol",  # 低波动
    "high_beta",  # 高 beta
    "defensive",  # 防御
    "growth",  # 成长
    "quality",  # 质量
    "sentiment",  # 情绪
    "volatility",  # 波动率
    "open_interest",  # 持仓量
    "cross_section",  # 横截面
    "intraday",  # 日内
    "other",  # 其他
]
"""因子风格标签（15 类，A.3 自适应权重 style 维度）。"""


# ─── 版本号（HARNESS §版本号纪律）─────────────────────────


def _get_evolution_version() -> str:
    """动态获取 FTS 版本号（延迟导入避免循环依赖）。"""
    import sys

    # 延迟导入，避免循环依赖
    if "fts" in sys.modules:
        return sys.modules["fts"].__version__
    # 如果 fts 模块还未加载，读取 __init__.py 文件
    try:
        from pathlib import Path

        init_file = Path(__file__).parent.parent / "__init__.py"
        if init_file.exists():
            content = init_file.read_text(encoding="utf-8")
            # 简单解析 __version__ = "x.y.z"
            for line in content.split("\n"):
                if line.startswith("__version__"):
                    # 提取版本号
                    match = line.split("=")
                    if len(match) >= 2:
                        return match[1].strip().strip("\"'")
    except Exception:
        pass
    return "unknown"


EVOLUTION_VERSION: str = _get_evolution_version()
"""Loop Engine 版本号，动态读取自 fts.__version__。"""

# L1/L2 状态文件 schema 版本 — 仅 L1MetaLoopState / L2EvolveState TypedDict
# 字段变更时手动递增，用于冷启动决策。小版本/功能版本不触发冷启动。
STATE_SCHEMA_VERSION: str = "1"


# ─── 因子程序契约 ─────────────────────────────────────────


class FactorSignature(TypedDict, total=False):
    """因子程序的输入/输出签名。

    约束:
        - input_fields 必须包含 'close'
        - output_type 必须是 'signal'（-1~+1）或 'score'（任意 float）
        - frequency 必须是 'daily' / 'hourly' / 'minute'
    """

    input_fields: list[str]  # 必需字段，如 ["close", "volume"]
    output_type: Literal["signal", "score"]
    frequency: Literal["daily", "hourly", "minute"]
    lookback: int  # 最小回看窗口


class EconomicLogic(TypedDict, total=False):
    """经济逻辑四维评分（agentic-factor-investing 定义）。

    每个维度 score 范围 0-5，达标阈值为 3。
    """

    theory: int  # 理论支撑：与已知风险溢价的相关性
    behavioral: int  # 行为金融：捕捉过度反应/反应不足
    microstructure: int  # 市场微观结构：流动性/信息不对称
    institutional: int  # 机构约束：可执行性（换手率/成本）
    narrative: str  # LLM 生成的经济学解释（必填，非空）


from .walk_forward import WalkForwardResult  # noqa: E402 — 延迟导入规避循环依赖


class FactorKind(str, Enum):
    """因子表达类型。

    - OPERATOR: 算子表达式 (FTS-Expr DSL)，经 OperatorRegistry 解释执行
    - CODE: 代码级因子 (Python 沙箱)，现有默认类型
    - HYBRID: 算子外壳 + 代码内核 (预留)
    """

    OPERATOR = "operator"
    CODE = "code"
    HYBRID = "hybrid"


class FactorProgram(TypedDict, total=False):
    """因子程序 — 图灵完备代码表示（factorengine 核心）。

    约束:
        - factor_id 全局唯一，格式: fct_<8位hex>
        - code 必须可被安全沙箱编译执行
        - params 中的每个值必须是 optuna 可搜索的类型（int/float/str/list）
        - economic_logic.narrative 不能为空字符串
        - trace_id 必须贯穿所有衍生因子
        - market 标识因子适用市场，symbols 列出具体品种
        - factor_version 用于契约版本管理
    """

    factor_id: str  # 唯一标识: fct_<8hex>
    name: str  # 人类可读名
    code: str  # Python 可执行代码
    params: dict[str, Any]  # 可调参数空间
    signature: FactorSignature  # 输入/输出契约
    economic_logic: EconomicLogic  # 四维经济逻辑评分
    source: Literal["seed", "macro_evolution", "bootstrapping", "manual"]
    parent_id: Optional[str]  # 演化父因子 ID（用于经验链溯源）
    generation: int  # 演化代数（0 = 种子）
    created_at: str  # ISO 8601
    trace_id: str  # 全链路 trace_id
    risk_tag: Optional[str]  # 风险标签，如 "vwap_approx"
    market: FactorMarket  # 适用市场: futures/stock/etf/multi
    family: FactorFamily  # 因子家族分类
    style_tags: Optional[list[FactorStyle]]  # 风格标签（A.3，v2.56.0；缺省由分类器推断）
    symbols: list[str]  # 适用品种列表（空=全品种适用）
    factor_version: str  # 因子定义版本 (e.g., "v2")
    is_multi_symbol: bool  # 是否为多品种因子
    kind: FactorKind  # 因子表达类型 (默认 code, 向后兼容)
    expression: Optional[str]  # 算子因子表达式 (FTS-Expr DSL)
    operator_depth: Optional[int]  # 表达式 AST 深度
    operator_count: Optional[int]  # 算子个数
    max_lookback: Optional[int]  # 最大 lookback (PIT 静态分析, 防未来函数)
    evaluation: Optional[FactorEvaluation]  # 三级评估结果（晋升/质检后回填）


# ─── 评估结果契约 ─────────────────────────────────────────


class BacktestMetrics(TypedDict, total=False):
    """Level 1 — 回测验证指标（agentic-factor-investing 定义）。"""

    ic: float  # Spearman rank IC，截面均值
    icir: float  # IC 均值 / IC 标准差
    ic_pre_neutral: float  # 中性化前 IC（仅行业/市值中性化启用时存在，GAP-S01）
    sharpe: float  # 年化夏普比率
    max_drawdown: float  # 最大回撤（0~1）
    monotonicity: bool  # 十分位组合收益率严格单调
    oos_ratio: float  # 样本外比例（0~1）
    t_stat: float  # t 统计量
    turnover_monthly: float  # 月度换手率（0~1）
    decay_6m: float  # 6 个月 IC 衰减率（0~1, 越大衰减越快）
    ic_volatility: float  # 跨窗口 IC 标准差（WalkForward, 0=单窗口）
    n_walk_windows: int  # WalkForward 有效窗口数
    layer_ic: dict  # 分层 IC（GAP-S06）：{layer_name: ic_mean}
    long_only_sharpe: float  # 仅做多夏普（GAP-S07）
    multi_horizon: dict  # 多持有期 IC（GAP-060）：{horizons/ic_by_horizon/icir_by_horizon/win_rate_by_horizon/best_horizon/decay_curve/monotonic_decay}
    cs_dispersion: float  # 因子截面分散度（GAP-062）：截面因子值 std 的时间均值
    sign_flip_rate: float  # 信号翻转频率（GAP-062）：日均信号方向变化占比
    max_consecutive_losses: int  # 最大连续亏损天数（GAP-062）
    ic_t_stat: float  # IC 均值 t 统计量（GAP-062）：IC均值 / IC标准误
    win_rate: float  # 日度/块级 IC 胜率（GAP-062）：正 IC 占比
    cost_sensitivity: dict  # 可交易性压力层（GAP-061）：滑点倍数 -> 净夏普/净IC/盈亏平衡
    quintile_returns: dict  # Q1-Q5 分组收益（GAP-062）：{1..5: mean_ret, q5_q1_spread, monotonic}
    symbol_ic: dict  # 逐标的时序 IC（GAP-075）：{symbol: ic}，供审计 cross_symbol 激活
    symbol_holdout: Optional[dict]  # 标的留出验证（GAP-075）：{train_ic/holdout_ic/ic_retention/passed/...}，None=数据不足
    sign_flip_half_split: bool  # 前后半段 IC 符号反转（G4，35-gap-closure-plan）：一票否决标记
    icir_block: float  # 块级 ICIR（G4，35-gap-closure-plan）：IC均值/IC标准差，时序路径硬门槛口径


class EconomicScore(TypedDict, total=False):
    """Level 2 — 经济逻辑评分（四维）。"""

    theory: int  # 0-5
    behavioral: int  # 0-5
    microstructure: int  # 0-5
    institutional: int  # 0-5
    dimensions_passed: int  # 达标维度数（score≥3 计为达标）
    narrative: str  # LLM 评分依据


class MultipleTestResult(TypedDict, total=False):
    """Level 3 — 多重检验校正结果。"""

    bonferroni_p: float  # Bonferroni 校正后 p 值
    fdr_q: float  # False Discovery Rate
    effective_n_factors: int  # 有效因子数（考虑相关性）
    adjusted_t: float  # 调整后 t 统计量
    passed: bool  # 是否通过多重检验


class FactorEvaluation(TypedDict, total=False):
    """agentic 三级评估链输出。"""

    factor_id: str
    trace_id: str
    level_1_backtest: BacktestMetrics
    level_2_economic: EconomicScore
    level_3_multiple: MultipleTestResult
    walk_forward: Optional[WalkForwardResult]  # 可选走航验证结果
    extreme_perturbation: Optional[dict]  # 极值扰动 IC 重算（GAP-F15: ic_before/ic_after/ic_drop/n_total/n_removed）
    backtest_pipeline: Optional[dict[str, Any]]  # 端到端回测流水线结果（Phase B.2）
    ablation_check: Optional[dict[str, Any]]  # 消融实验检查结果（Phase A 集成）
    causal_validation: Optional[dict[str, Any]]  # 因果结构审查结果（Phase C 集成）
    robustness_check: Optional[dict[str, Any]]  # 鲁棒性审查结果（Phase B 集成）
    shap_analysis: Optional[dict[str, Any]]  # SHAP 可解释性分析结果（Phase B 集成）
    passed: bool  # 三级全部通过
    failure_reasons: list[str]  # 失败维度（用于经验链）
    evaluated_at: str  # ISO 8601


# ─── 经验链契约 ───────────────────────────────────────────


class ExperienceTrace(TypedDict, total=False):
    """经验链轨迹 — LLM 下一轮参考避免重复踩坑。

    约束:
        - mutation_summary 不能为空字符串
        - failure_reasons 与 success 不能同时为空
        - lessons 必须是结构化的字符串列表
    """

    trace_id: str
    factor_id: str
    parent_id: Optional[str]
    generation: int
    mutation_type: Literal["macro_logic", "micro_param", "combined"]
    mutation_summary: str  # LLM 生成的可读摘要
    evaluation: FactorEvaluation
    success: bool
    lessons: list[str]  # 失败教训 / 成功要点
    recorded_at: str  # ISO 8601
    factor_code: str  # 因子代码文本（失败时保存用于诊断）


# ─── 演化状态契约 ─────────────────────────────────────────


class EvolutionState(TypedDict, total=False):
    """演化状态文件 — Loop Engineering 状态原语。

    存储位置: memory/evolution/state.json
    备份位置: memory/evolution/state.json.backup
    """

    run_id: str  # 本次演化运行的唯一 ID
    started_at: str  # ISO 8601
    last_generation: int  # 最近完成的演化代数
    total_factors_evaluated: int  # 累计评估因子数
    total_factors_promoted: int  # 晋级 elite 池的因子数
    tokens_consumed: int  # 本次运行 LLM token 总量
    budget_limit: int  # 预算上限（熔断触发）
    status: Literal["running", "paused", "completed", "circuit_broken"]
    last_error: Optional[str]
    experience_chain_ref: list[str]  # 经验链 trace_id 列表
    evolution_method_counts: dict[str, int]  # GAP-S11: 演化方法分布（operator/gp/macro 计数）
    last_updated: str  # ISO 8601
    schema_version: str  # 状态 schema 版本（= STATE_SCHEMA_VERSION）


# ─── Verifier 契约 ────────────────────────────────────────


class VerifierConfig(TypedDict, total=False):
    """Verifier 配置 — 一旦初始化不可修改。

    HARNESS §11-loop-engineering.md §6: Verifier 协议不可修改。
    任何运行时尝试修改 _config 应抛 RuntimeError。
    """

    min_ic: float  # 最小 IC（默认 0.03）
    min_icir: float  # 最小 ICIR（默认 0.5）
    min_sharpe: float  # 最小夏普（默认 1.5）
    max_drawdown: float  # 最大回撤（默认 0.20）
    min_economic_score: int  # 最小经济逻辑维度达标数（默认 3/4）
    min_t_stat: float  # 最小 t 统计量（默认 3.0）
    max_fdr: float  # 最大 FDR（默认 0.05）
    min_oos_ratio: float  # 最小样本外比例（默认 0.30）
    max_turnover_monthly: float  # 最大月度换手率（默认 0.50）


class VerifierResult(TypedDict, total=False):
    """Verifier 判定结果。"""

    passed: bool
    failure_reasons: list[str]  # 失败维度（人类/LLM 可读）
    checked_against: VerifierConfig  # 使用的 Verifier 配置快照
    checked_at: str  # ISO 8601


# ─── 预算配置 ─────────────────────────────────────────────


class BudgetConfig(TypedDict, total=False):
    """预算配置 — Loop Engineering 熔断原语。"""

    nightly_token_limit: int  # 单夜 token 上限（默认 200,000）
    monthly_token_limit: int  # 月度 token 上限（默认 6,000,000）
    max_generation: int  # 最大演化代数（默认 50）
    max_tokens_per_factor: int  # 单因子 token 上限（默认 10,000）
    circuit_breaker_token_ratio: float  # 熔断 token 比例（默认 2.0）
    circuit_breaker_consecutive_low_ic: int  # 连续低 IC 熔断（默认 3）
    circuit_breaker_low_ic_threshold: float  # 低 IC 阈值（默认 0.01）
    circuit_breaker_failure_rate: float  # 失败率熔断（默认 0.95）
    max_per_family: int  # 单一家族最大精英因子数（默认 3）
    evolution_stop_enabled: bool  # P1-3 (Phase 3): 提前达标停止开关（默认 False 保守）
    evolution_stop_consecutive_empty_generations: int  # P1-3: 连续零晋升 K 代 → 提前结束（默认 5）


# ─── 默认配置常量 ─────────────────────────────────────────

DEFAULT_VERIFIER_CONFIG: VerifierConfig = VerifierConfig(
    min_ic=0.03,
    min_icir=0.5,
    min_sharpe=1.5,
    max_drawdown=0.50,
    min_economic_score=3,
    min_t_stat=3.0,
    max_fdr=0.05,
    min_oos_ratio=0.30,
    max_turnover_monthly=5.0,
)
"""v1.1.0 锁定的 Verifier 默认配置 — 不可在运行时修改。"""

FUTURES_VERIFIER_CONFIG: VerifierConfig = VerifierConfig(
    min_ic=0.03,
    min_icir=0.3,
    min_sharpe=1.0,
    max_drawdown=0.50,
    min_economic_score=3,
    min_t_stat=2.0,
    max_fdr=0.05,
    min_oos_ratio=0.30,
    max_turnover_monthly=5.0,
)
"""期货市场 Verifier 配置 — 适配期货更低夏普、更高噪声的特性。
放宽阈值: min_sharpe=1.0, min_icir=0.3, min_t_stat=2.0"""


DEFAULT_BUDGET_CONFIG: BudgetConfig = BudgetConfig(
    nightly_token_limit=200_000,
    monthly_token_limit=6_000_000,
    max_generation=50,
    max_tokens_per_factor=10_000,
    circuit_breaker_token_ratio=2.0,
    circuit_breaker_consecutive_low_ic=5,
    circuit_breaker_low_ic_threshold=0.005,
    circuit_breaker_failure_rate=0.95,
    max_per_family=15,
)
"""v1.1.0 默认预算配置 — 熔断触发后必须人类介入恢复。"""


# ─── 演化来源标签 ─────────────────────────────────────────

FactorSource = Literal["seed", "macro_evolution", "bootstrapping", "manual"]
MutationType = Literal["macro_logic", "micro_param", "combined"]
EvolutionStatus = Literal["running", "paused", "completed", "circuit_broken"]


# ══════════════════════════════════════════════════════════
# ─── L1 Meta-Loop 契约（Phase 2 — v1.1.0 新增，后与 FTS 同步）
# ══════════════════════════════════════════════════════════

L1BootstrappingSource = Literal[
    "l1_bootstrapping",  # factorengine Bootstrapping Agent 链产出
    "l1_web_discovery",  # f10/web_collector 感知模块直接发现
    "l1_debate_gap",  # debate_round 薄弱维度反向生成
    "l1_manual",  # 人类手动注入
    "l1_extractor_pipeline",  # 三源提取器管道（研报/论文/天软文档）
]
"""L1 种子候选来源标签。"""

MetaLoopStatus = Literal["running", "paused", "completed", "circuit_broken"]
"""L1 Meta-Loop 状态枚举。"""


class SeedCandidate(TypedDict, total=False):
    """L1 Bootstrapping 产出的种子候选 — 注入 L2 种子池入口。

    约束:
        - candidate_id 全局唯一，格式: cand_<8hex>
        - code 必须可被安全沙箱编译执行（满足 FactorExecutor 约束）
        - economic_logic.narrative 不能为空字符串
        - economic_logic 四维中至少 2 维 score>=3（L1 Verifier 宽松阈值）
        - trace_id 必须贯穿从感知到注入的全链路

    存储: memory/knowledge/factors/l1_injected/<candidate_id>.json
    """

    candidate_id: str  # 唯一标识: cand_<8hex>
    name: str  # 人类可读名
    code: str  # Python 可执行代码
    params: dict[str, Any]  # 可调参数空间
    signature: FactorSignature  # 输入/输出契约
    economic_logic: EconomicLogic  # 四维经济逻辑评分
    source: L1BootstrappingSource  # 来源标签
    market: Optional[str]  # 所属市场 (futures/stock)，GAP-031 起注入时写入
    parent_topic: str  # 触发 Bootstrapping 的市场主题/研报
    debate_round_ref: Optional[int]  # 关联的 debate_round（质量信号源）
    debate_gap: Optional[str]  # 从 debate_round 推断的论证缺口
    web_snapshot_ref: Optional[str]  # f10/web_collector 快照 trace_id
    is_executable: bool  # 是否通过沙箱编译验证
    is_duplicate: bool  # 是否与现有种子重复
    passed_l1_verifier: bool  # 是否通过 L1 Verifier
    failure_reasons: list[str]  # 失败维度
    trace_id: str  # 全链路 trace_id
    created_at: str  # ISO 8601
    injected_to_l2: bool  # 是否已注入 L2 种子池
    injected_at: Optional[str]  # 注入时间 ISO 8601


class L1MetaLoopState(TypedDict, total=False):
    """L1 Meta-Loop 状态文件 — Loop Engineering 状态原语。

    存储位置: memory/meta_loop/state.json
    备份位置: memory/meta_loop/state.json.backup
    """

    run_id: str  # 本次 L1 运行的唯一 ID
    started_at: str  # ISO 8601
    last_bootstrap_topic: str  # 最近一次 Bootstrapping 主题
    total_candidates_generated: int  # 累计生成候选数
    total_candidates_injected: int  # 累计注入 L2 种子池数
    total_debate_gaps_detected: int  # 累计识别的辩论缺口数
    tokens_consumed: int  # 本次运行 LLM token 总量
    budget_limit: int  # 预算上限（熔断触发）
    status: MetaLoopStatus
    last_error: Optional[str]
    candidates_ref: list[str]  # 候选 ID 列表
    last_updated: str  # ISO 8601
    schema_version: str  # 状态 schema 版本（= STATE_SCHEMA_VERSION）


class FactorPoolEntry(TypedDict, total=False):
    """factor_pool.json 单条记录 — L1 种子池索引。

    存储位置: memory/knowledge/factors/factor_pool.json
    """

    factor_id: str  # fct_<8hex> 或 cand_<8hex>
    name: str
    source: FactorSource | L1BootstrappingSource  # 来源标签
    parent_topic: Optional[str]  # 触发主题
    debate_round_ref: Optional[int]  # 关联辩论轮次
    debate_gap: Optional[str]  # 论证缺口
    economic_logic: EconomicLogic  # 四维评分
    priority: Literal["high", "medium", "low"]  # 优先级（基于 debate_gap + 经济逻辑）
    status: Literal["pending", "injected", "decayed", "rejected"]
    market: Optional[str]  # 市场标识（futures/stock），GAP-I306 新增；缺失=历史记录（Step 2.5 去重纳入）
    evaluation_status: Literal["pending", "evaluated"]  # pending=未评估（无 IC/Sharpe），evaluated=已评估
    trace_id: str
    created_at: str
    updated_at: str


class FactorPool(TypedDict, total=False):
    """factor_pool.json 顶层结构 — L1 种子池。

    存储位置: memory/knowledge/factors/factor_pool.json
    """

    version: str  # 契约版本
    updated_at: str  # ISO 8601
    factors: list[FactorPoolEntry]  # 种子因子列表
    total_count: int  # 总数（= len(factors)）
    pending_count: int  # 待注入 L2 数


# ─── L1 Verifier 配置 ─────────────────────────────────────


class L1VerifierConfig(TypedDict, total=False):
    """L1 Verifier 配置 — 比 L2 宽松，注重发现而非严格筛选。

    HARNESS §11-loop-engineering.md §15: L1 Verifier 锁定值。
    任何运行时尝试修改 _config 应抛 RuntimeError。
    """

    min_economic_score: int  # 最小经济逻辑维度达标数（默认 2/4）
    require_executable: bool  # 必须可执行（默认 True）
    require_not_duplicate: bool  # 必须不重复（默认 True）
    min_narrative_length: int  # narrative 最小长度（默认 20 字符）


class L1VerifierResult(TypedDict, total=False):
    """L1 Verifier 判定结果。"""

    passed: bool
    failure_reasons: list[str]
    checked_against: L1VerifierConfig
    checked_at: str


# ─── L1 预算配置 ──────────────────────────────────────────


class L1BudgetConfig(TypedDict, total=False):
    """L1 预算配置 — Loop Engineering 熔断原语。

    每日 50K token，月度 1.5M token（远低于 L2 的 200K/夜）。
    """

    daily_token_limit: int  # 单日 token 上限（默认 50,000）
    monthly_token_limit: int  # 月度 token 上限（默认 1,500,000）
    max_bootstraps_per_run: int  # 单次运行最大 Bootstrapping 数（默认 5）
    max_tokens_per_candidate: int  # 单候选 token 上限（默认 5,000）
    circuit_breaker_token_ratio: float  # 熔断 token 比例（默认 2.0）
    circuit_breaker_failure_rate: float  # 失败率熔断（默认 0.95）
    circuit_breaker_consecutive_low_quality: int  # 连续低质量熔断（默认 5）


# ─── 默认配置常量 ─────────────────────────────────────────

DEFAULT_L1_VERIFIER_CONFIG: L1VerifierConfig = L1VerifierConfig(
    min_economic_score=2,
    require_executable=True,
    require_not_duplicate=True,
    min_narrative_length=20,
)
"""v1.1.0 锁定的 L1 Verifier 默认配置 — 不可在运行时修改。"""

DEFAULT_L1_BUDGET_CONFIG: L1BudgetConfig = L1BudgetConfig(
    daily_token_limit=50_000,
    monthly_token_limit=1_500_000,
    max_bootstraps_per_run=20,  # 增加至 20: 容纳三源提取器(13) + LLM(7) 候选
    max_tokens_per_candidate=5_000,
    circuit_breaker_token_ratio=2.0,
    circuit_breaker_failure_rate=0.95,
    circuit_breaker_consecutive_low_quality=5,
)
"""v1.1.0 默认 L1 预算配置 — 熔断触发后必须人类介入恢复。"""


# ══════════════════════════════════════════════════════════
# L3 Portfolio Loop 契约（Phase 3 — v1.1.0 同步）
# ══════════════════════════════════════════════════════════


class FactorCorrelation(TypedDict, total=False):
    """因子间相关性矩阵条目。"""

    factor_id_a: str
    factor_id_b: str
    pearson: float  # Pearson 相关系数
    spearman: float  # Spearman 秩相关系数


class PortfolioSignal(TypedDict, total=False):
    """L3 信号合成输出。"""

    factor_id: str
    name: str
    weight: float  # 组合权重（0~1，归一化后）
    sharpe: float  # 样本外夏普比率
    ic: float  # 截面 IC
    turnover: float  # 月度换手率
    decay_6m: float  # 6 个月衰减率（>0.3 表示需剔除）
    orthogonalized: bool  # 是否已正交化
    retained: bool  # 是否保留在组合中
    _sharpe_raw: float  # 截断前的原始夏普（P0 过拟合修复）
    _ic_raw: float  # 截断前的原始 IC（P0 过拟合修复）
    correlation_flags: list[dict[str, Any]]  # 相关性诊断标记（L2 先验/分层正交化）
    orthogonalized_against: Optional[str]  # 正交化参照因子 ID（GAP-I206 透传）
    orthogonalized_pearson: float  # 与参照因子的 Pearson 相关（GAP-I206 透传）


class PortfolioCombo(TypedDict, total=False):
    """L3 组合构建输出 — 信号合成结果。

    存储位置: memory/portfolio/current_combo.json
    """

    version: str
    updated_at: str
    combo_id: str  # cmb_<8hex>
    trace_id: str
    synthesis_mode: Literal["equal_weight", "sharpe_weight", "lightgbm"]
    signals: list[PortfolioSignal]
    combo_sharpe: float  # 组合整体夏普（风控后净暴露口径：含 exposure_scale 仓位缩放）
    signal_sharpe: Optional[float]  # 缩放前信号质量夏普（风控约束前，方案③；None=未计算）
    net_combo_sharpe: Optional[float]  # 扣除交易成本后的净夏普（GAP-L305，None=未启用成本模型）
    combo_turnover: float  # 组合整体换手率
    max_correlation: float  # 组合内最大因子间相关性
    n_factors: int  # 最终保留因子数
    status: Literal["pending", "active", "decayed"]
    created_at: str
    sharpe_warning: Optional[str]  # 夏普警戒提示（None=正常, str=警戒原因）
    sharpe_randomization_passed: Optional[bool]  # 随机化测试是否通过
    metrics_source: Optional[str]  # 组合指标来源（"measured"=因子收益矩阵 w×R 实测, "estimated"=估算回退，GAP-L301）
    qc_standards: dict  # 组合质检三标准（GAP-063）：synthesis_gain/diversification_gain/drawdown_control_ratio + passed 标记
    exposure_scale: Optional[float]  # 置信度仓位缩放因子（28-T6，None=未启用）
    regime_meta: Optional[dict]  # regime 元信息 {regime, confidence, exposure_scale, entropy_norm}（28-T6）


class AgentOptimizationProposal(TypedDict, total=False):
    """L3 输出的 Agent 优化建议。

    存储位置: memory/portfolio/agent_proposals/<proposal_id>.json
    """

    proposal_id: str  # prop_<8hex>
    trace_id: str
    created_at: str
    agent_name: str  # 目标 Agent 名
    current_prompt_summary: str  # 当前 prompt 摘要
    suggested_changes: str  # 建议变更内容
    debate_round_ref: Optional[int]
    rationale: str  # 变更理由（基于 debate_round 数据）
    priority: Literal["high", "medium", "low"]
    status: Literal["draft", "applied", "rejected"]
    description: str  # 建议描述（漂移重平衡等场景）
    confidence: float  # 建议置信度（0~1）
    source: str  # 建议来源（如 "drift_monitor"）


class L3VerifierConfig(TypedDict, total=False):
    """L3 Verifier 配置 — 组合构建判定标准。

    HARNESS §11-loop-engineering.md §6: Verifier 协议不可修改。
    """

    min_sharpe: float  # 最小组合夏普（默认 2.0）
    max_correlation: float  # 最大因子间相关性（默认 0.3）
    max_turnover: float  # 最大组合换手率（默认 0.50）
    max_decay_rate: float  # 最大衰减率（默认 0.30）
    min_n_factors: int  # 最少因子数（默认 3）
    max_sharpe: float  # 最大组合夏普上限（默认 3.5，防止过拟合虚高）


class L3MetaLoopState(TypedDict, total=False):
    """L3 Portfolio Loop 状态文件。

    存储位置: memory/portfolio/state.json
    """

    run_id: str
    started_at: str
    last_synthesis_mode: str
    total_signals_processed: int
    total_signals_retained: int
    total_proposals_generated: int
    tokens_consumed: int
    budget_limit: int
    status: Literal["running", "paused", "completed", "circuit_broken", "frozen"]
    last_error: Optional[str]
    combo_ref: list[str]  # 组合 ID 列表
    last_updated: str
    schema_version: str  # 状态 schema 版本（= STATE_SCHEMA_VERSION）
    drift_alerted: bool  # 组合漂移告警已触发（GAP-F13）
    drift_alert_info: dict[str, Any]  # 漂移告警详情（GAP-F13）
    attribution_report: str  # 归因报告路径（GAP-L307）
    walk_forward_report: str  # 组合层走航报告路径（GAP-L306）
    risk_alerts: dict[str, Any]  # 组合风控告警详情（Step 7.8）


# ─── L3 默认配置 ───────────────────────────────────────────

DEFAULT_L3_VERIFIER_CONFIG: L3VerifierConfig = L3VerifierConfig(
    min_sharpe=2.0,
    max_correlation=0.5,
    max_turnover=0.50,
    max_decay_rate=0.30,
    min_n_factors=3,
    max_sharpe=3.5,
)
"""v1.1.0 锁定的 L3 Verifier 默认配置 — 不可在运行时修改。"""

DEFAULT_L3_BUDGET = 100_000
"""L3 每周 token 预算 100K。"""


# ─── L3 组合粘性约束 ──────────────────────────────────────


class StickyConfig(TypedDict, total=False):
    """L3 组合粘性约束配置 — 平滑组合换血，防止策略漂移。

    约束时机: build_combo 权重归一化之前。
    约束规则:
        - 存量因子: 权重相对上次组合变动 clamp 在 ±max_delta
        - 新因子: 首日权重封顶 new_factor_cap
    """

    enabled: bool  # 是否启用粘性约束（默认 True）
    max_delta: float  # 单因子权重相对上次组合变动上限（默认 0.30）
    new_factor_cap: float  # 新因子首日权重封顶（默认 0.10）


DEFAULT_STICKY_CONFIG: StickyConfig = StickyConfig(
    enabled=False,
    max_delta=0.30,
    new_factor_cap=0.10,
)
"""v2.11.0 锁定的 L3 粘性约束默认配置。"""


# ─── L3 自适应权重配置（A.3 / v2.56.0）──────────────────────


class AdaptiveWeightConfig(TypedDict, total=False):
    """L3 自适应权重配置 — Regime 倍率维度与平滑参数。

    调整维度（dimension）:
        - "family": 仅按 FactorFamily 倍率（既有 REGIME_FAMILY_MULTIPLIERS）
        - "style":  仅按 FactorStyle 倍率（REGIME_STYLE_MULTIPLIERS，v2.56.0）
        - "both":   family × style 双倍率乘积（默认，乘积后 clamp 到 ±50%）

    机构级优化（28-T4）:
        - probability_mix: 制度概率混合（regime blend，默认 True）
        - confidence_scale: 置信度仓位缩放（默认 True）
        - confidence_scale_min: 熵标定后缩放下限（默认 0.3）
        - confidence_entropy_penalty: 熵标定惩罚系数（默认 0.5）

    P2 远期（GAP-094/095）:
        - blend_power: 制度概率混合幂次调节（默认 1.0 线性；
          >1 锐化大概率权重、<1 钝化趋平；plans/28 §5 风险表）
        - calibration_path: 统计概率校准文件（GAP-094 isotonic/Platt 拟合产物
          JSON 路径；默认空 = 使用熵标定，校准文件存在且有效时优先统计校准）
    """

    enabled: bool  # 是否启用（默认 True）
    dimension: str  # "family" | "style" | "both"（默认 "both"）
    smoother: dict[str, float]  # RegimeSmoother 参数 {alpha, min_days}
    min_weight: float  # 调整后权重下限比例（默认 0.01）
    min_clamp: float  # 双维度乘积下限 clamp 倍率（默认 0.5）
    max_clamp: float  # 双维度乘积上限 clamp 倍率（默认 1.5）
    probability_mix: bool  # 制度概率混合开关（regime blend，默认 True；28-T4）
    confidence_scale: bool  # 置信度仓位缩放开关（默认 True；28-T4）
    confidence_scale_min: float  # 熵标定缩放下限（默认 0.3；28-T4）
    confidence_entropy_penalty: float  # 熵标定惩罚系数（默认 0.5；28-T4）
    blend_power: float  # 制度概率混合幂次（默认 1.0 线性；GAP-095）
    calibration_path: str  # 统计概率校准 JSON 路径（默认 ""；GAP-094）


DEFAULT_ADAPTIVE_CONFIG: AdaptiveWeightConfig = AdaptiveWeightConfig(
    enabled=True,
    dimension="both",
    smoother={"alpha": 0.5, "min_days": 2},
    min_weight=0.01,
    min_clamp=0.5,
    max_clamp=1.5,
    probability_mix=True,
    confidence_scale=True,
    confidence_scale_min=0.3,
    confidence_entropy_penalty=0.5,
    blend_power=1.0,
    calibration_path="",
)
"""v2.56.0 锁定的 L3 自适应权重默认配置（更灵敏平滑档；28-T4 扩展概率混合与置信度缩放；
GAP-094/095 远期参数默认值：blend_power=1.0 线性、calibration_path="" 熵标定）。"""


# ─── 多源数据交叉验证契约 ──────────────────────────────────


class MultiSourceDisagreement(TypedDict, total=False):
    """多源数据交叉验证分歧记录 — 用于数据质量监控。

    字段:
        symbol: 品种代码（如 "RB0"）
        date: ISO 日期 "2026-08-04"
        prices: dict[源名称, close 价格]
        median: 各源 close 中位数
        outliers: 偏离中位数超过阈值的源名称列表
        max_diff_pct: 最大偏离百分比
        threshold: 偏离阈值
        trace_id: 链路追踪 ID（HARNESS §5.5）
        detected_at: 检测时间 ISO 8601
    """

    symbol: str
    date: str
    prices: dict[str, float]
    median: float
    outliers: list[str]
    max_diff_pct: float
    threshold: float
    trace_id: str
    detected_at: str


# ─── L3 组合漂移监控 ──────────────────────────────────────

# ─── L3 组合漂移告警配置（GAP-F13，v2.67.0）──────────────────


class DriftAlertConfig(TypedDict, total=False):
    """L3 组合漂移告警配置 — 超阈值告警 + 可选自动重平衡。

    阈值:
        - overlap_threshold: 成员重合率下限（低于该值触发告警，默认 0.50）
        - weight_l1_threshold: 权重 L1 变化率上限（高于该值触发告警，默认 0.40）
    重平衡:
        - trigger_rebalance: 超阈值时是否自动触发粘性约束重平衡（默认 False）
    """

    overlap_threshold: float  # 成员重合率下限（默认 0.50）
    weight_l1_threshold: float  # 权重 L1 变化率上限（默认 0.40）
    trigger_rebalance: bool  # 是否自动触发粘性约束重平衡（默认 False）


DEFAULT_DRIFT_ALERT_CONFIG: DriftAlertConfig = DriftAlertConfig(
    overlap_threshold=0.50,
    weight_l1_threshold=0.40,
    trigger_rebalance=False,
)
"""v2.67.0 锁定的 L3 漂移告警默认配置。"""


class DriftMetrics(TypedDict, total=False):
    """L3 组合漂移监控指标 — 每次组合构建后记录。

    存储位置: memory/portfolio/drift_history/YYYY-MM-DD.json
    对比基准: 上一次组合（current_combo.json 归档到 combo_history/）。
    """

    date: str  # 记录日期 YYYY-MM-DD
    combo_id: str  # 本次组合 ID
    prev_combo_id: str  # 上次组合 ID（首次为空）
    trace_id: str  # 本次运行 trace_id
    member_overlap_rate: float  # 成员重合率 Jaccard = |A∩B| / |A∪B|（0~1）
    weight_l1_change: float  # 权重 L1 变化率 Σ|w_new - w_prev| / 2（0~1）
    n_prev_members: int  # 上次组合因子数
    n_new_members: int  # 本次组合因子数
    n_common_members: int  # 两期共有因子数
    added: list[str]  # 新增因子名列表
    removed: list[str]  # 移除因子名列表


__all__ = [
    # 版本
    "EVOLUTION_VERSION",
    "STATE_SCHEMA_VERSION",
    # 枚举类型
    "FactorMarket",
    "FactorFamily",
    # 因子契约
    "FactorProgram",
    "FactorSignature",
    "EconomicLogic",
    # 评估契约
    "BacktestMetrics",
    "EconomicScore",
    "MultipleTestResult",
    "FactorEvaluation",
    # 经验链
    "ExperienceTrace",
    # 状态
    "EvolutionState",
    # Verifier
    "VerifierConfig",
    "VerifierResult",
    # 预算
    "BudgetConfig",
    # 默认配置
    "DEFAULT_VERIFIER_CONFIG",
    "FUTURES_VERIFIER_CONFIG",
    "DEFAULT_BUDGET_CONFIG",
    # Literal 类型
    "FactorSource",
    "MutationType",
    "EvolutionStatus",
    # ─── L1 Meta-Loop（Phase 2 v1.1.0 同步）─────────────
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
    # ─── L3 Portfolio Loop（Phase 3 v1.1.0 同步）────────
    "FactorCorrelation",
    "PortfolioSignal",
    "PortfolioCombo",
    "AgentOptimizationProposal",
    "L3VerifierConfig",
    "L3MetaLoopState",
    "DEFAULT_L3_VERIFIER_CONFIG",
    "DEFAULT_L3_BUDGET",
    # ─── L3 粘性约束 + 漂移监控（v2.11.0 / v2.67.0 GAP-F13）─────────────
    "StickyConfig",
    "DEFAULT_STICKY_CONFIG",
    "DriftMetrics",
    "DriftAlertConfig",
    "DEFAULT_DRIFT_ALERT_CONFIG",
    # ─── L3 自适应权重（A.3 / v2.56.0）────────────────
    "AdaptiveWeightConfig",
    "DEFAULT_ADAPTIVE_CONFIG",
    # 多源数据交叉验证
    "MultiSourceDisagreement",
    # 规范化工具
    "normalize_factor_program",
    "normalize_factor_signature",
    "detect_factor_market",
]


# ─── 因子规范化工具 ─────────────────────────────────────────


def normalize_factor_signature(signature: dict[str, Any]) -> FactorSignature:
    """规范化因子签名，兼容旧版和新版定义。

    旧版格式（测试中常见）:
        {"inputs": ["close"], "outputs": ["signal"], "feature_dim": 1}
    新版格式（标准契约）:
        {"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 10}

    Args:
        signature: 原始签名字典

    Returns:
        FactorSignature 规范化后的签名
    """
    if not signature:
        return FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=10,
        )

    # 旧版字段映射
    input_fields = signature.get("input_fields") or signature.get("inputs", ["close"])
    output_type = signature.get("output_type") or _map_output_type(signature.get("outputs", ["signal"]))
    frequency = signature.get("frequency", "daily")
    lookback = signature.get("lookback", 10)

    return FactorSignature(
        input_fields=input_fields if isinstance(input_fields, list) else [input_fields],
        output_type=output_type,
        frequency=frequency,
        lookback=lookback,
    )


def _map_output_type(outputs: list[str]) -> Literal["signal", "score"]:
    """将旧版 outputs 格式映射到 output_type。"""
    if outputs and "signal" in outputs:
        return "signal"
    return "score"


def detect_factor_market(symbols: list[str] | None, market_hint: str | None = None) -> FactorMarket:
    """检测因子适用的市场类型。

    Args:
        symbols: 品种列表
        market_hint: 市场提示

    Returns:
        FactorMarket 市场类型
    """
    if market_hint and market_hint in ("futures", "stock", "etf", "bond", "multi"):
        return cast(FactorMarket, market_hint)

    if not symbols:
        return "multi"

    # 根据品种代码格式判断
    futures_patterns = (
        "RB",
        "M",
        "CU",
        "AU",
        "AG",
        "AL",
        "ZN",
        "PB",
        "NI",
        "SN",
        "SC",
        "FU",
        "BU",
        "TA",
        "MA",
        "PP",
        "L",
        "V",
        "EB",
        "EG",
        "PG",
        "SA",
        "UR",
        "RU",
        "NR",
        "SP",
        "LU",
        "EC",
        "BC",
        "LC",
    )
    stock_patterns = ("SH", "SZ", "BJ", "6", "0", "3")

    has_futures = any(sym.startswith(p) for sym in symbols for p in futures_patterns)
    has_stock = any(sym.startswith(p) for sym in symbols for p in stock_patterns)

    if has_futures and has_stock:
        return "multi"
    if has_futures:
        return "futures"
    if has_stock:
        return "stock"
    return "multi"


def normalize_factor_program(factor: FactorProgram, market_hint: str | None = None) -> FactorProgram:
    """规范化因子定义，确保符合最新契约。

    处理内容:
    1. 规范化 signature 格式
    2. 自动检测/补全 market/family/symbols 字段
    3. 填充默认值

    Args:
        factor: 原始因子定义
        market_hint: 市场提示

    Returns:
        FactorProgram 规范化后的因子定义
    """
    normalized: FactorProgram = cast(FactorProgram, dict(factor))

    # 规范化 signature
    if "signature" in normalized and isinstance(normalized["signature"], dict):
        normalized["signature"] = normalize_factor_signature(normalized["signature"])
    elif "signature" not in normalized:
        normalized["signature"] = FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=10,
        )

    # 补全 market
    if "market" not in normalized or not normalized.get("market"):
        symbols = normalized.get("symbols", [])
        normalized["market"] = detect_factor_market(symbols, market_hint)

    # 补全 family（非标准值也需要重新推断）
    family = normalized.get("family", "")
    standard_families = {
        "trend",
        "mean_reversion",
        "carry",
        "seasonality",
        "cross_section",
        "qlib",
        "gtja",
        "wq101",
        "fundamental",
        "technical",
        "microstructure",
        "macro",
        "behavioral",
        "liquidity",
        "volatility",
        "volume",
        "multi_factor",
        "other",
    }
    if not family or family not in standard_families:
        normalized["family"] = _infer_factor_family(normalized)

    # 补全 symbols
    if "symbols" not in normalized:
        normalized["symbols"] = []

    # 补全版本
    if "factor_version" not in normalized:
        normalized["factor_version"] = "v2"

    # 补全 is_multi_symbol
    symbols = normalized.get("symbols", [])
    normalized["is_multi_symbol"] = len(symbols) > 1 if symbols else False

    # 因子表达类型推断: 有 expression 视为算子因子, 否则视为代码因子
    if "kind" not in normalized or not normalized.get("kind"):
        normalized["kind"] = FactorKind.OPERATOR if normalized.get("expression") else FactorKind.CODE

    return normalized


def _infer_factor_family(factor: FactorProgram) -> FactorFamily:
    """根据因子特征推断其家族分类。

    规则:
    - name 包含 trend/momentum → trend
    - name 包含 reversion/mean/regression → mean_reversion
    - name 包含 carry/spread/arbitrage → carry
    - name 包含 volume/flow → volume
    - name 包含 volatility/vol → volatility
    - name 包含 fundamental → fundamental
    - name 包含 liquidity → liquidity
    - code 包含 open_interest → microstructure
    - name 以 qlib_ 开头 → qlib（微软 Qlib 因子库）
    - name 以 gtja_ 开头 → gtja（国泰君安 191 因子库）
    - name 以 alpha_/wq_ 开头 → wq101（世坤 WorldQuant 101 Alpha）
    - name 以 fut_ 开头 → trend（期货传统量价因子多为趋势类）
    """
    name = (factor.get("name", "") or "").lower()
    code = (factor.get("code", "") or "").lower()
    sig = factor.get("signature", {})
    input_fields = sig.get("input_fields", []) if sig else []

    if any(kw in name for kw in ("trend", "momentum", "follow", "breakout")):
        return "trend"
    if any(kw in name for kw in ("reversion", "mean", "regression", "bounce")):
        return "mean_reversion"
    if any(kw in name for kw in ("carry", "spread", "arbitrage", "basis")):
        return "carry"
    if any(kw in name for kw in ("volume", "flow", "money", "capital")):
        return "volume"
    if any(kw in name for kw in ("volatility", "vol_", "garch", "variance")):
        return "volatility"
    if any(kw in name for kw in ("fundamental", "value", "quality", "growth")):
        return "fundamental"
    if any(kw in name for kw in ("liquidity", "liquid", "depth")):
        return "liquidity"
    # 因子库来源前缀：qlib/gtja/wq101 归入各自子家族
    if name.startswith("qlib_"):
        return "qlib"
    if name.startswith("gtja_"):
        return "gtja"
    if name.startswith(("alpha_", "wq_")):
        return "wq101"
    if name.startswith("fut_"):
        return "trend"
    if any(kw in code for kw in ("open_interest", "order_flow", "tick")):
        return "microstructure"
    if any(kw in str(input_fields).lower() for kw in ("macro", "gdp", "cpi", "pmi")):
        return "macro"
    return "other"
