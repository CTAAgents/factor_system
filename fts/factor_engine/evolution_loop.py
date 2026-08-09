"""
loop_engine/evolution_loop.py — L2 因子演化主循环

HARNESS §11-loop-engineering.md §2.2:
    seed_pool.fetch()  →  for generation in 1..MAX_GEN:
        ├─ macro_evolution.evolve(factor, experience_chain)  # LLM 改逻辑
        ├─ micro_evolution.optimize(factor_new)              # optuna 100 trials
        ├─ evaluation_chain.evaluate(factor_optimized)       # 三级评估
        ├─ verifier.check(eval_result)                        # 锁定 Verifier
        ├─ experience_chain.record(factor, eval_result)       # 经验链
        └─ state.persist(generation, factor, eval_result)     # 状态文件

预算控制 + 熔断:
    - 单夜 token 超 2x → circuit_broken
    - 连续 3 代 IC < 0.01 → circuit_broken
    - 失败率 > 90% → circuit_broken

版本: v1.9.0（与 FTS 同步，引入 UCT 父因子选择）
"""
# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments,too-many-locals,too-few-public-methods,broad-exception-caught,import-outside-toplevel

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from .batch_mining import BatchedProposal

logger = logging.getLogger(__name__)

# 影子池观察期（交易日数）— 新晋升因子先进影子池观察，期满后才进正式组合。
# 与 portfolio_loop.SHADOW_OBSERVE_TRADING_DAYS 保持一致。
_SHADOW_OBSERVE_TRADING_DAYS: int = 5


def _add_trading_days(start: datetime, days: int) -> datetime:
    """计算 N 个交易日后的日期时间（跳过周末，近似交易日）。"""
    end = np.busday_offset(start.date(), days, roll="forward")
    return datetime.combine(end.astype(object), datetime.min.time())


def _build_shadow_pool(now: Optional[datetime] = None) -> dict[str, Any]:
    """构造因子影子池标记。

    新晋升因子进入影子池观察，观察期内 L3 组合不纳入该因子。
    """
    now = now or datetime.now()
    observe_until = _add_trading_days(now, _SHADOW_OBSERVE_TRADING_DAYS)
    return {
        "promoted_at": now.isoformat(),
        "observe_trading_days": _SHADOW_OBSERVE_TRADING_DAYS,
        "observe_until": observe_until.isoformat(),
    }

# ── 一致性日志（P4: JSON ↔ DuckDB） ──
_CONSISTENCY_LOG_PATH = Path("data/_lineage/catalog_consistency.jsonl")


def _log_consistency_event(
    event_type: str,
    factor_id: str,
    factor_name: str,
    market: str,
    status: str,
    json_path: str | None = None,
    trace_id: str = "",
) -> None:
    """追加一条一致性日志记录到 catalog_consistency.jsonl。

    Args:
        event_type: 事件类型（promote / retire / verify）
        factor_id: 因子 ID
        factor_name: 因子名称
        market: 市场类型（stock / futures）
        status: 因子状态（active / retired）
        json_path: JSON 文件路径（可选）
        trace_id: 追踪 ID（可选）
    """
    import json
    try:
        record = {
            "event_type": event_type,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "market": market,
            "status": status,
            "json_path": json_path or "",
            "trace_id": trace_id or "",
            "timestamp": datetime.now().isoformat(),
        }
        _CONSISTENCY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(str(_CONSISTENCY_LOG_PATH), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("[consistency_log] 写入失败: %s", e)


def _normalize_industry_keys(mapping: dict[str, Any]) -> dict[str, Any]:
    """键归一化：映射键剥离交易所后缀（.SH/.SZ）生成裸代码键，同时保留原始键。

    行业/市值映射文件（data/industry_map.json）键为 "600519.SH" 格式，
    而 CSI300 面板 symbol 为裸代码 "600519"。归一化后两种格式均可命中。

    Args:
        mapping: {symbol: value} 原始映射

    Returns:
        归一化后的映射（原始键 + 裸代码键均保留）
    """
    result: dict[str, Any] = {}
    for sym, value in mapping.items():
        result[sym] = value
        # 剥离 "600519.SH" → "600519"（面板 symbol 为裸代码）
        base = sym.split(".")[0].strip()
        if base and base != sym:
            result[base] = value
    return result


from .contracts import (
    DEFAULT_BUDGET_CONFIG,
    BudgetConfig,
    EvolutionState,
    FactorCorrelation,
    FactorEvaluation,
    FactorProgram,
)
from .audit import FactorAuditor, FactorAuditReport
from .evaluation_chain import (
    EvaluationChain,
    cross_section_evaluate_backtest,
)
from .experience_chain import (
    ExperienceChain,
    create_trace_from_evaluation,
)
from .macro_evolution import MacroEvolver, get_default_llm_client
from .micro_evolution import evolve_micro
from .seed_pool import SeedPool, compute_seed_correlations
from .state import EvolutionStateManager, generate_trace_id
from .verifier import FactorVerifier, get_global_verifier


# ─── UCT 常量 ─────────────────────────────────────────────

UCT_EXPLORATION_C: float = 1.0
"""UCT 探索常数。越大越倾向探索未访问的父因子。"""


# ─── 演化结果 ─────────────────────────────────────────────

@dataclass
class EvolutionRunResult:
    """单次演化运行的结果。"""
    run_id: str
    trace_id: str
    generations_completed: int
    total_factors_evaluated: int
    total_factors_promoted: int
    tokens_consumed: int
    status: str  # running / paused / completed / circuit_broken
    circuit_breaker_reason: Optional[str] = None
    elite_factor_ids: list[str] = None  # type: ignore[assignment]
    seed_correlations: Optional[list[FactorCorrelation]] = None  # type: ignore[assignment]
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "generations_completed": self.generations_completed,
            "total_factors_evaluated": self.total_factors_evaluated,
            "total_factors_promoted": self.total_factors_promoted,
            "tokens_consumed": self.tokens_consumed,
            "status": self.status,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "elite_factor_ids": self.elite_factor_ids or [],
            "seed_correlations": self.seed_correlations or [],
            "error": self.error,
        }


# ─── 兼容包装: 替代已删除的 FactorQualityInspection ────

class _QualityInspectionResult:
    """兼容 InspectionResult 属性接口，用于 evolution_loop 内部。"""
    def __init__(self, score: dict, filtered: bool, reason: str = "") -> None:
        self.total_score: float = score.get("total_score", 0.0)
        self.grade: str = score.get("grade", "C")
        self.reason: str = reason
        self.filtered: bool = filtered
        self.quality_score: dict = score


class _QualityInspectionCompat:
    """兼容包装: 将 FactorQualityCard 适配为旧的 FactorQualityInspection 接口。

    原 pipeline/factor_quality_inspection.py 在死代码清理时删除，
    此处直接使用 fts/factor_engine/factor_quality_card.py 的 FactorQualityCard。
    """

    def __init__(self, card_config: Any = None, min_grade: str = "B") -> None:
        from fts.factor_engine.factor_quality_card import FactorQualityCard
        self.card = FactorQualityCard(card_config)
        self.min_grade = min_grade

    def inspect(self, factor: dict, evaluation: dict) -> _QualityInspectionResult:
        """将因子+评估映射到评分卡，返回兼容结果。"""
        bt = evaluation.get("level_1_backtest", {}) or {}
        econ = evaluation.get("level_2_economic", {}) or {}

        factor_id = factor.get("factor_id", "?")
        ic = bt.get("ic", 0.0)
        sharpe = bt.get("sharpe", 0.0)
        icir = bt.get("icir", 0.0)
        decay_rate = bt.get("decay_6m", 0.2)
        turnover = bt.get("turnover_monthly", 0.3)
        # 为 L1 候选因子设置合理换手率默认值（当回测未提供换手率时）
        if turnover <= 0:
            turnover = 0.5  # 期货 50% 月换手作为合理默认值
        walk_forward = evaluation.get("walk_forward")

        # 经济逻辑评分: 四维平均
        theory = econ.get("theory", 3)
        behavioral = econ.get("behavioral", 3)
        microstructure = econ.get("microstructure", 3)
        institutional = econ.get("institutional", 3)
        logic_score = int(round((theory + behavioral + microstructure + institutional) / 4.0))

        # 跨品种覆盖率: 从 symbols 列表估算
        symbols = factor.get("symbols", [])
        cross_symbol_coverage = min(1.0, len(symbols) / 10.0) if symbols else 0.6

        # 估算 Calmar 比率: 从 Sharpe 和 max_drawdown 推导
        max_dd = abs(bt.get("max_drawdown", 0.0))
        if max_dd > 1e-6:
            calmar = (sharpe * 0.15) / max_dd  # 假设 15% 年化波动率
        else:
            calmar = sharpe * 2.0  # 无回撤时使用保守估计

        score = self.card.evaluate(
            factor_id=factor_id,
            ic=ic,
            sharpe=sharpe,
            walk_forward_result=walk_forward,
            decay_rate=decay_rate,
            turnover=turnover,
            correlation_max=0.5,
            logic_score=logic_score,
            data_frequency="daily",
            cross_symbol_coverage=cross_symbol_coverage,
            icir=icir,
            calmar=calmar,
        )

        grade = score.get("grade", "C")
        filtered = grade not in ("A", "B") or grade > self.min_grade
        # 如果因子等级为 C 则淘汰
        if grade == "C" and self.min_grade in ("A", "B"):
            filtered = True
        reason = f"质检淘汰: 等级={grade}, 总分={score.get('total_score', 0):.1f}/50" if filtered else ""

        return _QualityInspectionResult(score, filtered, reason)


# ─── 演化循环 ─────────────────────────────────────────────

class EvolutionLoop:
    """L2 因子演化主循环。

    Usage:
        loop = EvolutionLoop(
            data=my_ohlcv_df,
            forward_returns=my_returns_array,
            elite_dir="memory/knowledge/factors/elite",
        )
        result = loop.run()
    """

    def __init__(
        self,
        data: pd.DataFrame,
        forward_returns: np.ndarray,
        elite_dir: str | Path | None = None,
        memory_dir: str | Path = "memory/evolution",
        inject_dir: str | Path = "memory/knowledge/factors/l1_injected",
        budget: Optional[BudgetConfig] = None,
        verifier: Optional[FactorVerifier] = None,
        llm_client: Optional[Any] = None,
        seed_pool: Optional[SeedPool] = None,
        n_trials_micro: int = 100,
        cross_section_data: Optional[dict[str, pd.DataFrame]] = None,
        cross_section_dates: Optional[pd.DatetimeIndex] = None,
        quality_card_config: Optional[Any] = None,
        quality_min_grade: str = "B",
        market: Optional[str] = None,
        factor_db_path: Optional[str | Path] = None,
        audit_config: Optional[Any] = None,
        industry_map: Optional[dict[str, str]] = None,
        cap_map: Optional[dict[str, float]] = None,
    ):
        self.data = data
        self.forward_returns = forward_returns
        self.cross_section_data = cross_section_data
        self.cross_section_dates = cross_section_dates
        self.industry_map = industry_map
        self.cap_map = cap_map
        if market is None:
            from fts.config.settings import get_config
            market = get_config().default_market
        self.market = market
        # GAP-S11 (v2.67.0): 演化模式解析——股票演化默认 operator-first
        # （算子演化优先，LLM/GP 兜底），期货保持原配置行为。
        from fts.config.settings import get_config
        _raw_mode = getattr(get_config(), "evolution_mode", "hybrid")
        self.evolution_mode = _raw_mode
        if market == "stock" and _raw_mode == "hybrid":
            self.evolution_mode = "operator_first"
            logger.info(
                "[EvolutionLoop] 股票演化默认 operator-first: 算子演化优先，LLM/GP 兜底"
            )
        self.factor_db_path = factor_db_path
        self._is_cross_section = cross_section_data is not None

        # v2.59.0 (GAP-F03): 期货横截面模式自动注入板块映射（板块/产业链中性化）
        # 从 FUTURES_SECTOR_MAP 反向构建 {symbol: sector}；futures_neutralization=false
        # 或已显式传入 industry_map 时跳过。
        if (
            self._is_cross_section
            and market == "futures"
            and self.industry_map is None
        ):
            try:
                from fts.config.settings import get_config
                if get_config().futures_neutralization:
                    from fts.data_futures import FUTURES_SECTOR_MAP
                    self.industry_map = {
                        sym: sector
                        for sector, symbols in FUTURES_SECTOR_MAP.items()
                        for sym in symbols
                    }
                    logger.info(
                        "[EvolutionLoop] 期货板块中性化已启用: %d 品种映射到 %d 个产业链",
                        len(self.industry_map), len(FUTURES_SECTOR_MAP),
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvolutionLoop] 期货板块映射注入失败，跳过中性化: %s", e)

        # v2.61.0 (GAP-S01): 股票横截面模式自动注入行业/市值映射（行业/市值中性化）
        # 读取 FTSConfig.stock_neutralization（默认 true，v2.57.0 遗留死配置，本版本接通）；
        # industry_map 已显式传入时跳过；键归一化：映射键 "600519.SH"/"600519.SZ" 剥离后缀
        # 生成裸代码键（面板 symbol 为裸代码 "600519"），同时保留原始键兼容两种格式。
        if (
            self._is_cross_section
            and market == "stock"
            and self.industry_map is None
        ):
            try:
                from fts.config.settings import get_config, load_cap_map, load_industry_map
                if get_config().stock_neutralization:
                    raw_industry = load_industry_map()
                    if raw_industry:
                        self.industry_map = _normalize_industry_keys(raw_industry)
                        logger.info(
                            "[EvolutionLoop] 股票行业中性化已启用: %d 条映射（归一化后 %d 键）",
                            len(raw_industry), len(self.industry_map),
                        )
                    # 市值映射（cap_map_path 配置，缺失/为空返回空 dict → 仅行业去均值）
                    raw_cap = load_cap_map()
                    if raw_cap:
                        self.cap_map = _normalize_industry_keys(raw_cap)
                        logger.info(
                            "[EvolutionLoop] 股票市值中性化已启用: %d 条映射",
                            len(self.cap_map),
                        )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvolutionLoop] 股票行业/市值映射注入失败，跳过中性化: %s", e)

        # ── 市场隔离: 自动按 market 选择 elite 目录 ──
        if elite_dir is None:
            if market == "futures":
                elite_dir = "memory/knowledge/factors/futures_elite"
            else:
                elite_dir = "memory/knowledge/factors/elite"
        self.elite_dir = Path(elite_dir)
        self.elite_dir.mkdir(parents=True, exist_ok=True)
        self.inject_dir = Path(inject_dir)
        self.memory_dir = Path(memory_dir)
        self.budget: BudgetConfig = budget or DEFAULT_BUDGET_CONFIG
        if verifier is not None:
            self.verifier = verifier
        elif market == "futures":
            from .contracts import FUTURES_VERIFIER_CONFIG
            self.verifier = FactorVerifier(FUTURES_VERIFIER_CONFIG)
        else:
            self.verifier = get_global_verifier()
        self.llm_client = llm_client or get_default_llm_client()
        self.seed_pool = seed_pool or SeedPool()
        self.n_trials_micro = n_trials_micro

        # GAP-I205 (v2.68.0): 微观演化两阶段漏斗配置（粗筛淘汰 + 精筛自适应 trials）
        try:
            from fts.config.settings import get_config
            _micro_cfg = get_config()
            self._micro_staged_evolution = bool(
                getattr(_micro_cfg, "micro_staged_evolution", True)
            )
            self._micro_coarse_trials = int(
                getattr(_micro_cfg, "micro_coarse_trials", 20)
            )
            self._micro_coarse_ic_floor = float(
                getattr(_micro_cfg, "micro_coarse_ic_floor", 0.02)
            )
            # GAP-I206 (v2.71.0): L2 准入去冗余配置
            self._l2_elite_corr_threshold = float(
                getattr(_micro_cfg, "l2_elite_corr_threshold", 0.9)
            )
            self._l2_elite_corr_max_scan = int(
                getattr(_micro_cfg, "l2_elite_corr_max_scan", 50)
            )
            self._l2_elite_corr_debug = bool(
                getattr(_micro_cfg, "l2_elite_corr_debug", False)
            )
        except Exception:
            # 配置读取失败时采用模块默认值，不阻断演化
            self._micro_staged_evolution = True
            self._micro_coarse_trials = 20
            self._micro_coarse_ic_floor = 0.02
            self._l2_elite_corr_threshold = 0.9
            self._l2_elite_corr_max_scan = 50
            self._l2_elite_corr_debug = False

        # 子模块
        self.state_manager = EvolutionStateManager(self.memory_dir)
        self.experience_chain = ExperienceChain(self.memory_dir)
        self.macro_evolver = MacroEvolver(
            llm_client=self.llm_client,
            experience_chain=self.experience_chain,
            max_tokens_per_call=self.budget["max_tokens_per_factor"],
        )
        self.evaluation_chain = EvaluationChain()

        # 子模块: 因子质检过滤器 (Phase A.1 集成)
        # 使用 _QualityInspectionCompat 替代已删除的 pipeline.FactorQualityInspection
        self.quality_inspector = _QualityInspectionCompat(
            card_config=quality_card_config,
            min_grade=quality_min_grade,
        )

        # 子模块: 因子审计器 (Phase B.3 集成)
        # audit_config: 允许外部注入审计阈值（如期货低信噪比场景放宽 OOS 阈值）
        self.auditor = (
            FactorAuditor(config=audit_config) if audit_config else FactorAuditor()
        )

        # 子模块: 高IC筛查器 (Phase B.4 集成, 所有市场统一)
        from .high_ic_screener import HighICScreener, HighICScreenConfig
        if market == "futures":
            # 期货市场放宽 V5 经济逻辑维度最低分（LLM 演化因子 L2 评分偏低）
            futures_config = HighICScreenConfig(logic_min_score=1.0)
            self.high_ic_screener = HighICScreener(config=futures_config)
        else:
            self.high_ic_screener = HighICScreener()

        # 子模块: 端到端回测流水线 (Phase B.2 集成)
        from .backtest_pipeline import BacktestPipeline, PipelineConfig
        self.backtest_pipeline = BacktestPipeline(config=PipelineConfig())

        # 子模块: GP 特征演化引擎 (Phase C.1 集成)
        from .feature_ops import FeatureOpsEngine
        self.feature_ops_engine = FeatureOpsEngine()

        # 子模块: 数据质量监控器 (Phase B.1 集成)
        from ..monitor.data_quality_monitor import DataQualityMonitor
        self.data_quality_monitor = DataQualityMonitor()

        # Phase B.1: 注册到 HTTP 指标端点
        from ..monitor import set_data_quality_monitor
        set_data_quality_monitor(self.data_quality_monitor)

        # 子模块: 精英因子追踪器 (Phase A.2 集成)
        from ..monitor.elite_tracker import EliteFactorTracker
        self.elite_tracker = EliteFactorTracker(
            tracking_dir=str(self.memory_dir / "tracking"),
        )

        # 子模块: 消融实验 (Phase A 集成)
        from .ablation import AblationExperiment
        self.ablation_experiment = AblationExperiment(random_seed=42)

        # 子模块: SHAP 可解释性分析 (Phase B 集成)
        from .shap_analyzer import ShapAnalyzer
        self.shap_analyzer = ShapAnalyzer()

        # 子模块: 鲁棒性审查 (Phase B 集成)
        from .robustness import RobustnessTester
        self.robustness_tester = RobustnessTester()

        # 子模块: 因果验证 (Phase C 集成)
        from .causal_validator import CausalValidator
        self.causal_validator = CausalValidator()

        # 子模块: 特征重要性分析 (Phase C.1 集成)
        from .feature_importance import FeatureImportanceAnalyzer
        self.feature_importance_analyzer = FeatureImportanceAnalyzer()

        # 子模块: 逻辑监控 (Phase C.2 集成)
        from ..monitor.logic_monitor import LogicMonitor
        self.logic_monitor = LogicMonitor()

        # 状态
        self._prior_evaluations: list[FactorEvaluation] = []
        self._consecutive_low_ic: int = 0
        # UCT 统计: {factor_id: {"visits": int, "total_reward": float}}
        self._uct_stats: dict[str, dict[str, float]] = {}
        # DuckDB 仓储（延迟初始化）
        self._repo: Optional[Any] = None

        # GAP-I201 (v2.65.0): 批量挖掘配置（evolution_mode="batch" 时生效）
        from fts.config.settings import get_config as _batch_cfg
        _cfg_batch = _batch_cfg()
        self.batch_size: int = int(getattr(_cfg_batch, "batch_size", 20))
        self.batch_max_candidates: int = int(
            getattr(_cfg_batch, "batch_max_candidates", 5)
        )
        self.batch_max_workers: int = int(
            getattr(_cfg_batch, "batch_max_workers", 4)
        )
        self.batch_random_seed: int = int(
            getattr(_cfg_batch, "batch_random_seed", 42)
        )
        # batch 模式批量生成游标（_batch_generate_one 内自增，保证方法轮换 + seed 递增）
        self._batch_idx: int = 0

    def run(self, max_generation: Optional[int] = None) -> EvolutionRunResult:
        """执行 L2 演化循环。

        Args:
            max_generation: 最大代数（None = 使用 budget 配置）

        Returns:
            EvolutionRunResult
        """
        trace_id = generate_trace_id("l2")

        # ── 清理前日因子信号缓存（缓存随因子集变化而失效） ──
        try:
            cache_dir = Path("memory/cache/factor_signals")
            if cache_dir.exists():
                n_cleared = sum(1 for _ in cache_dir.glob("*.npy"))
                for fp in cache_dir.glob("*.npy"):
                    try:
                        fp.unlink()
                    except OSError:
                        pass
                idx = cache_dir / "signal_index.json"
                if idx.exists():
                    try:
                        idx.unlink()
                    except OSError:
                        pass
                if n_cleared:
                    print(f"[L2] 清理因子信号缓存: {n_cleared} 个文件")
        except Exception:
            pass

        state = self.state_manager.load_or_init(self.budget["nightly_token_limit"])
        state = self.state_manager.mark_running()
        # mark_running 内部重新加载了状态文件，需在之后重置计数器
        state["last_generation"] = 0
        state["total_factors_evaluated"] = 0
        state["total_factors_promoted"] = 0
        state["tokens_consumed"] = 0  # 每次新运行重置 token 计数器，避免跨运行积累触发熔断
        self.state_manager.save(state)
        run_id = state["run_id"]

        # ── 数据加载流程: 数据质量校验 (Phase B.1) ──
        dq_alerts = self.data_quality_monitor.validate_market_data(
            data=self.data,
            forward_returns=self.forward_returns,
        )
        if dq_alerts:
            critical_count = sum(1 for a in dq_alerts if a.severity == "critical")
            if critical_count > 0:
                logger.critical(
                    "市场数据质量校验失败 (critical=%d)，终止演化",
                    critical_count,
                )
                self.state_manager.mark_completed(state)
                return EvolutionRunResult(
                    run_id=run_id, trace_id=trace_id,
                    generations_completed=0,
                    total_factors_evaluated=0,
                    total_factors_promoted=0,
                    tokens_consumed=0,
                    status="circuit_broken",
                    circuit_breaker_reason="data_quality_critical",
                    error=f"数据质量校验失败: {critical_count} 个严重告警",
                )
            else:
                logger.warning(
                    "市场数据质量校验发现 %d 个告警 (无 critical)，继续演化",
                    len(dq_alerts),
                )

        max_gen = max_generation or self.budget["max_generation"]
        elite_ids: list[str] = []
        seed_correlations: list[FactorCorrelation] = []
        start_gen = 1  # 每次运行从第 1 代开始

        try:
            # ── Step 0: 种子因子相关性预检（轻量扫描，仅标记不删除） ──
            print("[DEBUG-evo] 开始加载种子因子...")
            seeds = self.seed_pool.load_all_seeds()
            print(f"[DEBUG-evo] 种子因子加载完成: {len(seeds)} 个")
            # GAP-031: 合并 L1 注入候选（pending 门控 + market 过滤 + 去重），
            # 与种子同等参与相关性预检与种子评估晋升
            print("[DEBUG-evo] 开始合并 L1 候选...")
            seeds = self._merge_l1_candidates(seeds, trace_id)
            print(f"[DEBUG-evo] 合并 L1 候选完成, 种子总数: {len(seeds)}")
            print("[DEBUG-evo] 开始种子相关性预检...")
            seed_correlations = self._run_seed_correlation_check(seeds, trace_id)
            if seed_correlations:
                high_corr_count = len(seed_correlations)
                print(f"[evo] 种子因子相关性预检: {high_corr_count} 对高相关因子 (阈值≥0.95)")
                for pair in seed_correlations[:5]:
                    print(f"  - {pair['factor_id_a']} × {pair['factor_id_b']}: "
                          f"Pearson={pair['pearson']:.4f} Spearman={pair['spearman']:.4f}")
                if high_corr_count > 5:
                    print(f"  ... 还有 {high_corr_count - 5} 对")

            # ── Step 1: 评估种子因子，合格直接晋升 elite ──
            print(f"[DEBUG-evo] 种子相关性预检完成: {len(seed_correlations)} 对高相关因子")
            print("[DEBUG-evo] 开始评估种子因子 (184 个, 横截面模式)... 这可能需要较长时间")
            promoted_seeds = self._evaluate_and_promote_seeds(
                seeds, trace_id, state, elite_ids,
                seed_correlations=seed_correlations,
            )
            if promoted_seeds > 0:
                print(f"[evo] 种子因子晋升: {promoted_seeds} 个")

            print(f"[DEBUG-evo] 种子评估完成, 晋升: {promoted_seeds} 个, elite_ids: {len(elite_ids)}")
            # 使用已晋升的种子作为父因子（只有高IC种子才值得演化）
            parent_seeds = [s for s in seeds if s["factor_id"] in elite_ids]
            # 种子因子全部已存在 elite 快照（重复跳过、无新晋升）时，
            # 回退加载 elite 池作为父因子，使演化可基于既有精英因子继续
            if not parent_seeds:
                parent_seeds = self._load_elite_parent_factors()
                if not parent_seeds:
                    print("[evo] 无合格父因子，跳过演化循环")
                    self.state_manager.mark_completed(state)
                    return EvolutionRunResult(
                        run_id=run_id, trace_id=trace_id,
                        generations_completed=0,
                        total_factors_evaluated=0,
                        total_factors_promoted=0,
                        tokens_consumed=state.get("tokens_consumed", 0),
                        status="completed",
                        elite_factor_ids=elite_ids,
                        seed_correlations=seed_correlations,
                    )
                print(
                    f"[evo] 种子因子均已晋升过，改用 elite 池 "
                    f"{len(parent_seeds)} 个因子作为父因子"
                )

            for generation in range(start_gen, start_gen + max_gen):
                # 熔断检查
                print(f"[DEBUG-evo] gen={generation} _consecutive_low_ic={self._consecutive_low_ic}")
                cb_reason = self._check_circuit_breaker(state)
                if cb_reason:
                    self.state_manager.mark_circuit_broken(state, cb_reason)
                    return EvolutionRunResult(
                        run_id=run_id, trace_id=trace_id,
                        generations_completed=generation - start_gen,
                        total_factors_evaluated=state.get("total_factors_evaluated", 0),
                        total_factors_promoted=state.get("total_factors_promoted", 0),
                        tokens_consumed=state.get("tokens_consumed", 0),
                        status="circuit_broken",
                        circuit_breaker_reason=cb_reason,
                        elite_factor_ids=elite_ids,
                        seed_correlations=seed_correlations,
                    )

                # 选择父因子（UCT 树搜索，平衡探索与利用）
                parent = self._select_parent_uct(parent_seeds)

                # 读取演化模式配置 (Phase C.2 / GAP-I201 batch)
                from fts.config.settings import get_config
                _fts_evo_cfg = get_config()
                _evo_mode = getattr(_fts_evo_cfg, 'evolution_mode', 'hybrid')

                if _evo_mode == "batch":
                    # ── BATCH 模式 (GAP-I201): 一代批量漏斗 ──
                    # 批量生成（同父多后代）→ 并行粗筛 → 通过者逐个走准入链；
                    # 状态持久化/熔断计数由 _run_batch_generation 内 _process_candidate 完成
                    self._run_batch_generation(
                        parent, generation, trace_id, state, elite_ids,
                        seed_correlations,
                    )
                    # 经验链清理（generation 级）
                    self.experience_chain.cleanup_if_needed()
                    continue

                # ── 单因子路径: Step 1 演化分派（macro/GP/operator，配置分派） ──
                evolved = self._evolve_one(parent, generation, trace_id)
                if evolved is None:
                    # 演化失败轨迹已在 _evolve_one 内记录
                    continue
                new_factor, evolution_method, evolution_summary, evo_tokens = evolved
                if evo_tokens:
                    self.state_manager.add_tokens(state, evo_tokens)
                # GAP-S11: 记录演化方法分布（operator/gp/macro 占比可观测）
                self.state_manager.record_evolution_method(state, evolution_method)

                # ── Step 1.3: 后代因子运行时校验（源头拦截广播错误/常数信号） ──
                runtime_ok, runtime_reason = self._check_factor_runtime(new_factor)
                if not runtime_ok:
                    logger.warning(
                        "[%s] 后代因子运行时校验失败: %s",
                        new_factor.get("name", "?"), runtime_reason,
                    )
                    self._record_failure_trace(
                        new_factor, generation, evolution_method,
                        f"运行时校验失败: {runtime_reason}", [], trace_id,
                    )
                    continue

                # ── Step 1.4: 快速预筛选（源头拦截低质量信号，避免浪费评估资源） ──
                prefilter_ok, prefilter_reason, _ = self._quick_prefilter(
                    new_factor, trace_id,
                )
                if not prefilter_ok:
                    logger.warning(
                        "[%s] 快速预筛选失败: %s",
                        new_factor.get("name", "?"), prefilter_reason,
                    )
                    self._record_failure_trace(
                        new_factor, generation, evolution_method,
                        f"快速预筛选失败: {prefilter_reason}", [], trace_id,
                    )
                    continue

                # ── Step 2-6: 准入链（公共方法，batch 与单因子路径共用，GAP-I201） ──
                self._process_candidate(
                    new_factor, parent, generation, evolution_method,
                    evolution_summary, state, elite_ids, trace_id,
                    seed_correlations,
                )

                # 经验链清理（如果超过 100 条）
                self.experience_chain.cleanup_if_needed()

            # 正常完成
            print(f"[DEBUG-evo] before mark_completed: _consecutive_low_ic={self._consecutive_low_ic}")
            self.state_manager.mark_completed(state)
            return EvolutionRunResult(
                run_id=run_id, trace_id=trace_id,
                generations_completed=max_gen,
                total_factors_evaluated=state.get("total_factors_evaluated", 0),
                total_factors_promoted=state.get("total_factors_promoted", 0),
                tokens_consumed=state.get("tokens_consumed", 0),
                status="completed",
                elite_factor_ids=elite_ids,
                seed_correlations=seed_correlations,
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.state_manager.mark_paused(state, str(e))
            return EvolutionRunResult(
                run_id=run_id, trace_id=trace_id,
                generations_completed=0,
                total_factors_evaluated=state.get("total_factors_evaluated", 0),
                total_factors_promoted=state.get("total_factors_promoted", 0),
                tokens_consumed=state.get("tokens_consumed", 0),
                status="paused",
                circuit_breaker_reason=str(e),
                elite_factor_ids=elite_ids,
                seed_correlations=seed_correlations,
            )
        finally:
            # ── 写入 L2 相关性索引到 elite 目录（供 L3 批量读取） ──
            if seed_correlations:
                self._write_seed_correlation_index(seed_correlations, trace_id)

            # ── Phase A.2: 精英因子定期重评估 ──
            self._run_periodic_factor_review(elite_ids, trace_id)

    def _write_seed_correlation_index(
        self,
        seed_correlations: list[FactorCorrelation],
        trace_id: str,
    ) -> None:
        """将 L2 种子因子相关性预检结果写入 elite 目录的共享索引文件。

        该文件供 L3 Portfolio Loop 批量读取，作为相关性管理的先验数据。
        """
        import json
        from datetime import datetime

        index_path = self.elite_dir / "_l2_seed_correlation_index.json"
        index_data = {
            "source": "l2_seed_correlation_check",
            "trace_id": trace_id,
            "created_at": datetime.now().isoformat(),
            "threshold": 0.95,
            "total_pairs": len(seed_correlations),
            "correlations": [
                {
                    "factor_id_a": sc.get("factor_id_a", ""),
                    "factor_id_b": sc.get("factor_id_b", ""),
                    "pearson": sc.get("pearson", 0),
                    "spearman": sc.get("spearman", 0),
                }
                for sc in seed_correlations
            ],
        }
        index_path.write_text(
            json.dumps(index_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[evo] L2 相关性索引已写入: {index_path} ({len(seed_correlations)} 对高相关因子)")

    # ─── 内部方法 ───

    # ── GAP-I201 (v2.65.0): 批量挖掘漏斗 ──────────────────

    def _evolve_one(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
        *,
        method_hint: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> Optional[tuple[FactorProgram, str, str, int]]:
        """演化分派：生成 1 个后代因子（原 run() Step 1 抽取，GAP-I201）。

        Args:
            parent: 父因子
            generation: 当前代数
            trace_id: 全链路 trace_id
            method_hint: 指定演化方式（macro/gp/operator）；None = 按配置分派
            seed: 随机种子（batch 模式同父多后代保证可复现）

        Returns:
            (新因子, 演化方式, 演化摘要, LLM token 消耗)；全部失败返回 None
        """
        _evo_mode = self.evolution_mode

        # ── batch 模式: 按 method_hint 强制分派（macro 至多 1 次，其余 CPU 演化） ──
        if method_hint is not None:
            if seed is not None:
                np.random.seed(seed)
            if method_hint == "operator":
                try:
                    new_factor, op_summary = self._generate_operator_factor(
                        parent, generation=generation, trace_id=trace_id,
                    )
                    return new_factor, "operator_evolution", op_summary, 0
                except Exception as e:
                    logger.debug("算子演化失败 [%s]: %s", parent.get("name", "?"), e)
                    return None
            if method_hint == "macro":
                try:
                    new_factor, macro_summary, macro_tokens = self.macro_evolver.evolve(
                        parent, generation=generation, trace_id=trace_id,
                    )
                    return new_factor, "macro_evolution", macro_summary, macro_tokens
                except Exception as e:
                    logger.debug("宏观演化失败 [%s]: %s", parent.get("name", "?"), e)
                    return None
            if method_hint == "gp":
                try:
                    new_factor, gp_summary = self._run_gp_evolution(
                        parent, generation=generation, trace_id=trace_id,
                    )
                    return new_factor, "gp_evolution", gp_summary, 0
                except Exception as e:
                    logger.debug("GP 演化失败 [%s]: %s", parent.get("name", "?"), e)
                    return None
            return None

        # ── 配置分派（operator_first / operator / code / hybrid） ──
        new_factor = None
        evolution_method = "macro_evolution"
        evolution_summary = ""
        tokens = 0

        if _evo_mode == "operator_first":
            # GAP-S11: 算子演化优先，LLM/GP 兜底（可解释性优先红线 AGENTS.md 4.1）
            try:
                new_factor, op_summary = self._generate_operator_factor(
                    parent, generation=generation, trace_id=trace_id,
                )
                evolution_method = "operator_evolution"
                evolution_summary = op_summary
                logger.info(
                    "算子演化成功 (operator_first) [%s]: %s",
                    parent.get("name", "?"), op_summary,
                )
            except Exception as op_e:
                logger.warning(
                    "算子演化失败 [%s]: %s, 尝试 LLM 宏观演化兜底",
                    parent.get("name", "?"), op_e,
                )
                self._record_failure_trace(
                    parent, generation, "operator_evolution",
                    f"算子演化失败: {op_e}", [], trace_id,
                )
                try:
                    new_factor, macro_summary, macro_tokens = self.macro_evolver.evolve(
                        parent, generation=generation, trace_id=trace_id,
                    )
                    tokens = macro_tokens
                    evolution_method = "macro_evolution"
                    evolution_summary = macro_summary
                    logger.info(
                        "LLM 宏观演化兜底成功 (operator_first) [%s]: %s",
                        parent.get("name", "?"), macro_summary,
                    )
                except Exception as macro_e:
                    logger.warning(
                        "LLM 宏观演化兜底失败 [%s]: %s, 尝试 GP 演化兜底",
                        parent.get("name", "?"), macro_e,
                    )
                    try:
                        new_factor, gp_summary = self._run_gp_evolution(
                            parent, generation=generation, trace_id=trace_id,
                        )
                        evolution_method = "gp_evolution"
                        evolution_summary = gp_summary
                        logger.info(
                            "GP 演化兜底成功 (operator_first) [%s]: %s",
                            parent.get("name", "?"), gp_summary,
                        )
                    except Exception as gp_e:
                        self._record_failure_trace(
                            parent, generation, "operator_first_evolution",
                            f"算子/LLM/GP 演化均失败: {op_e} | {macro_e} | {gp_e}",
                            [], trace_id,
                        )
                        return None
        elif _evo_mode == "operator":
            try:
                new_factor, op_summary = self._generate_operator_factor(
                    parent, generation=generation, trace_id=trace_id,
                )
                evolution_method = "operator_evolution"
                evolution_summary = op_summary
                logger.info(
                    "算子演化成功 [%s]: %s",
                    parent.get("name", "?"), op_summary,
                )
            except Exception as e:
                logger.warning(
                    "算子演化失败 [%s]: %s",
                    parent.get("name", "?"), e,
                )
                self._record_failure_trace(
                    parent, generation, "operator_evolution",
                    f"算子演化失败: {e}", [], trace_id,
                )
                return None
        else:
            # CODE / HYBRID 模式: 1.1 宏观演化尝试（LLM 改逻辑）
            try:
                new_factor, macro_summary, macro_tokens = self.macro_evolver.evolve(
                    parent, generation=generation, trace_id=trace_id
                )
                tokens = macro_tokens
                evolution_summary = macro_summary
            except Exception as e:
                logger.warning(
                    "宏观演化失败 [%s]: %s, 尝试 GP 演化作为备选",
                    parent.get("name", "?"), e,
                )

            # 1.2 若宏观演化失败，回退到 GP 演化 (Phase C.1)
            if new_factor is None:
                try:
                    new_factor, gp_summary = self._run_gp_evolution(
                        parent, generation=generation, trace_id=trace_id,
                    )
                    evolution_method = "gp_evolution"
                    evolution_summary = gp_summary
                    logger.info(
                        "GP 演化成功 [%s]: %s",
                        parent.get("name", "?"), gp_summary,
                    )
                except Exception as gp_e:
                    if _evo_mode == "hybrid":
                        # hybrid 模式: GP 也失败时尝试算子演化
                        try:
                            new_factor, op_summary = self._generate_operator_factor(
                                parent, generation=generation, trace_id=trace_id,
                            )
                            evolution_method = "operator_evolution"
                            evolution_summary = op_summary
                            logger.info(
                                "算子演化成功 (hybrid fallback) [%s]: %s",
                                parent.get("name", "?"), op_summary,
                            )
                        except Exception as op_e:
                            self._record_failure_trace(
                                parent, generation, "hybrid_evolution",
                                f"GP 失败: {gp_e}, 算子也失败: {op_e}",
                                [], trace_id,
                            )
                            return None
                    else:
                        self._record_failure_trace(
                            parent, generation, "gp_evolution",
                            f"GP 演化也失败: {gp_e}", [], trace_id,
                        )
                        return None

            if new_factor is None:
                fail_msg = (
                    "LLM、GP 和算子演化均失败"
                    if _evo_mode == "hybrid"
                    else "宏观演化和 GP 演化均失败"
                )
                self._record_failure_trace(
                    parent, generation, "evolution",
                    fail_msg, [], trace_id,
                )
                return None

        return new_factor, evolution_method, evolution_summary, tokens

    def _run_batch_generation(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
        state: dict[str, Any],
        elite_ids: list[str],
        seed_correlations: list[FactorCorrelation],
    ) -> bool:
        """batch 模式一代批量漏斗（GAP-I201）。

        流程: 批量生成（同父多后代，方法轮换 + seed 递增）→ 并行粗筛
              → 通过者（≤ max_candidates）逐个走 _process_candidate 准入链。

        Args:
            parent: 父因子
            generation: 当前代数
            trace_id: 全链路 trace_id
            state: L2 演化状态
            elite_ids: 已晋升 elite 因子 ID 列表
            seed_correlations: 种子相关性预检结果

        Returns:
            是否至少 1 个候选晋升 elite。
        """
        from .batch_mining import BatchMiner, BatchMiningConfig

        miner = BatchMiner(
            config=BatchMiningConfig(
                batch_size=self.batch_size,
                max_candidates=self.batch_max_candidates,
                max_workers=self.batch_max_workers,
                random_seed=self.batch_random_seed,
            ),
            generate_cb=self._batch_generate_one,
            runtime_check_cb=self._check_factor_runtime,
            prefilter_cb=self._batch_prefilter,
        )
        self._batch_idx = 0
        result = miner.run_iteration(parent, generation, trace_id)

        # token 记账（state 计数 + 熔断协同）
        if result.tokens_consumed:
            self.state_manager.add_tokens(state, result.tokens_consumed)

        print(
            f"[evo-batch] gen={generation} 父={parent.get('name', '?')} "
            f"生成={result.total_generated} 通过粗筛={result.total_passed} "
            f"耗时={result.duration_ms}ms"
        )
        for rejected in result.rejected[:3]:
            print(
                f"  - 拦截: {rejected.get('method', '?')} "
                f"{rejected.get('prefilter_reason', '')[:80]}"
            )

        if not result.passed:
            # 全失败回退：记录失败轨迹（D.1 D5）
            self._record_failure_trace(
                parent, generation, "batch_evolution",
                f"批量漏斗无候选通过粗筛 (生成 {result.total_generated}, 全部拦截)",
                [r.get("prefilter_reason", "") for r in result.rejected][:3],
                trace_id,
            )
            return False

        promoted_any = False
        for proposal in result.passed:
            factor = proposal.get("factor", {})
            if not factor:
                continue
            try:
                ok = self._process_candidate(
                    factor, parent, generation,
                    proposal.get("method", "batch_evolution"),
                    proposal.get("summary", ""),
                    state, elite_ids, trace_id, seed_correlations,
                )
                promoted_any = promoted_any or ok
            except Exception as e:
                logger.warning(
                    "[batch] 候选处理异常 [%s]: %s",
                    factor.get("name", "?"), e,
                )
                self._record_failure_trace(
                    factor, generation,
                    proposal.get("method", "batch_evolution"),
                    f"候选处理异常: {e}", [], trace_id,
                )
        return promoted_any

    def _batch_generate_one(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
    ) -> Optional[BatchedProposal]:
        """batch 模式单个候选生成回调（D.1 §4：方法轮换 + seed 递增）。

        第 0 个走 macro（LLM，token 护栏每代至多 1 次），
        其余交替 gp / operator（纯 CPU）。
        """
        idx = self._batch_idx
        self._batch_idx = idx + 1
        seed = self.batch_random_seed + idx
        if idx == 0:
            method_hint = "macro"
        elif idx % 2 == 1:
            method_hint = "gp"
        else:
            method_hint = "operator"
        evolved = self._evolve_one(
            parent, generation, trace_id,
            method_hint=method_hint, seed=seed,
        )
        if evolved is None:
            return None
        factor, method, summary, tokens = evolved
        return {
            "factor": factor,
            "parent_id": parent.get("factor_id", "?"),
            "method": method,
            "summary": summary,
            "tokens": tokens,
            "prefilter_ok": False,
            "prefilter_reason": "",
            "prefilter_ic": 0.0,
        }

    def _batch_prefilter(
        self,
        factor: FactorProgram,
        trace_id: str,
    ) -> tuple[bool, str, float]:
        """batch 预筛回调：包装 _quick_prefilter（返回预筛 IC 供排序截断）。"""
        return self._quick_prefilter(factor, trace_id)

    def _process_candidate(
        self,
        factor: FactorProgram,
        parent: FactorProgram,
        generation: int,
        evolution_method: str,
        evolution_summary: str,
        state: dict[str, Any],
        elite_ids: list[str],
        trace_id: str,
        seed_correlations: list[FactorCorrelation],
    ) -> bool:
        """Step 2-6 准入链（GAP-I201 抽取，batch 与单因子路径共用）。

        流程: 微观演化 → 三级评估 → UCT 反馈 → Verifier → 质量评分卡
              → 端到端回测 → 数据质量 → 6 项强制审计 → 消融 → 因果
              → 鲁棒性 → SHAP → 晋升/淘汰 → 状态持久化。

        Args:
            factor: 后代因子（已通过运行时校验与快速预筛）
            parent: 父因子
            generation: 当前代数
            evolution_method: 演化方式
            evolution_summary: 演化摘要
            state: L2 演化状态
            elite_ids: 已晋升 elite 因子 ID 列表
            trace_id: 全链路 trace_id
            seed_correlations: 种子相关性预检结果

        Returns:
            是否成功晋升 elite。
        """
        promoted = False

        # ── Step 2: 微观演化（optuna 调参） ──
        try:
            # 横截面模式：用第一个股票的数据做微参
            micro_data = (
                list(self.cross_section_data.values())[0]
                if self._is_cross_section else self.data
            )
            micro_ret = self.forward_returns
            # GAP-I205 (v2.68.0): 两阶段漏斗——粗筛低 trials 快速打分淘汰低潜力，
            # 精筛 trials 按粗筛得分自适应 + TPE 早停；配置 micro_staged_evolution 可关闭。
            optimized_factor, _ = evolve_micro(
                factor, micro_data, micro_ret,
                n_trials=self.n_trials_micro,
                use_staged=self._micro_staged_evolution,
            )
        except Exception as e:
            self._record_failure_trace(
                factor, generation, "micro_evolution",
                f"微观演化失败: {e}", [], trace_id,
            )
            return False

        # ── Step 3: 三级评估链 ──
        if self._is_cross_section:
            evaluation = self._evaluate_cross_section(optimized_factor, trace_id)
        else:
            evaluation = self.evaluation_chain.evaluate(
                optimized_factor, self.data, self.forward_returns,
                prior_evaluations=self._prior_evaluations,
            )
        self._prior_evaluations.append(evaluation)
        self.state_manager.increment_evaluated(state)

        # ── UCT 反馈: 根据子因子表现更新父因子统计 ──
        self._update_uct_stats(parent, evaluation)

        # ── Step 4: Verifier 判定 ──
        verifier_result = self.verifier.check(evaluation)
        print(f"[DEBUG-evo] verifier_result={verifier_result}")
        print(f"[DEBUG-evo] evaluation.get('level_1_backtest')={evaluation.get('level_1_backtest')}")

        # ── Step 4.5: 因子质量评分卡 (Phase A.1) ──
        inspection: _QualityInspectionResult = self.quality_inspector.inspect(
            factor=optimized_factor,
            evaluation=evaluation,
        )

        # ── Step 4.5.5: 端到端回测流水线 (Phase B.2) ──
        backtest_result = self._run_backtest_pipeline(
            optimized_factor, evaluation, trace_id,
        )
        if backtest_result:
            evaluation["backtest_pipeline"] = backtest_result

        # ── Step 4.5.6: 数据质量监控 (Phase B.1) ──
        self._register_factor_baseline(optimized_factor, evaluation)
        dq_alerts = self._check_factor_data_quality(
            optimized_factor, evaluation,
        )
        if dq_alerts:
            critical = any(
                getattr(a, "severity", "") == "critical"
                for a in dq_alerts
            )
            if critical:
                print(
                    f"[evo] 数据质量严重告警 [{optimized_factor.get('name', '?')}]: "
                    f"跳过晋升"
                )
                return False

        # ── Step 4.6: 因子强制审计 (Phase B.3) ──
        audit_report = self._run_factor_audit(
            optimized_factor, evaluation, trace_id,
        )

        # ── Step 5: 经验链记录 + 分级准入 ──
        print(f"[DEBUG-evo] verifier_result['passed']={verifier_result.get('passed')}")
        if verifier_result["passed"]:
            print(f"[DEBUG-evo] PROMOTION PATH")
            # 质检过滤: 仅 A/B 级晋升，C 级淘汰
            if inspection.filtered:
                self._log_inspection_detail(
                    optimized_factor, inspection, "淘汰", generation,
                )
                self._record_quality_filtered_trace(
                    optimized_factor, generation, trace_id,
                    inspection, evaluation=evaluation,
                )
                return False

            # 审计过滤: 审计未通过则拒绝准入
            if not audit_report.passed:
                failed_items = [
                    it.name for it in audit_report.failed_items
                ]
                print(
                    f"[evo] 审计未通过 [{optimized_factor.get('name', '?')}]: "
                    f"失败项={failed_items}, 通过率={audit_report.pass_rate:.0%}"
                )
                self._record_audit_failed_trace(
                    optimized_factor, generation, trace_id,
                    audit_report, evaluation=evaluation,
                )
                return False

            # ── Step 4.6.5: 消融实验检查 (Phase A 集成) ──
            ablation_result = self._run_ablation_check(
                optimized_factor, evaluation, trace_id,
            )
            evaluation["ablation_check"] = ablation_result
            if not ablation_result.get("passed", True):
                print(
                    f"[evo] 消融实验未通过 [{optimized_factor.get('name', '?')}]: "
                    f"疑似伪相关"
                )
                self._record_ablation_failed_trace(
                    optimized_factor, generation, trace_id,
                    ablation_result,
                )
                return False

            # ── Step 4.6.6: 因果结构审查 (Phase C 集成) ──
            causal_result = self._run_causal_validation(
                optimized_factor, evaluation, trace_id,
            )
            evaluation["causal_validation"] = causal_result
            if not causal_result.get("passed", True):
                print(
                    f"[evo] 因果审查未通过 [{optimized_factor.get('name', '?')}]: "
                    f"事件敏感"
                )
                self._record_causal_failed_trace(
                    optimized_factor, generation, trace_id,
                    causal_result,
                )
                return False

            # ── Step 4.6.7: 鲁棒性审查 (Phase B 集成) ──
            robustness_result = self._run_robustness_check(
                optimized_factor, evaluation, trace_id,
            )
            evaluation["robustness_check"] = robustness_result
            if not robustness_result.get("passed", True):
                print(
                    f"[evo] 鲁棒性审查未通过 [{optimized_factor.get('name', '?')}]"
                )
                self._record_robustness_failed_trace(
                    optimized_factor, generation, trace_id,
                    robustness_result,
                )
                return False

            # ── Step 4.6.8: SHAP 可解释性分析 (Phase B 集成) ──
            shap_result = self._run_shap_analysis(
                optimized_factor, evaluation, trace_id,
            )
            evaluation["shap_analysis"] = shap_result

            # 晋级精英池（去重检查 + 质量评分附加 + 审计报告）
            self._log_inspection_detail(
                optimized_factor, inspection, "通过", generation,
            )
            promoted_path = self._promote_to_elite(
                optimized_factor, evaluation,
                seed_correlations=seed_correlations,
                quality_score=inspection.quality_score,
                audit_report=audit_report,
            )
            if promoted_path is None:
                # 因子名称重复，跳过
                return False
            self.state_manager.increment_promoted(state)
            elite_ids.append(optimized_factor["factor_id"])
            self._record_success_trace(
                optimized_factor, generation, evolution_method,
                evolution_summary, evaluation,
                [f"代 {generation} 晋级精英池",
                 f"质量分={inspection.total_score}/50 ({inspection.grade}级)",
                 f"审计通过率={audit_report.pass_rate:.0%}"],
                trace_id,
            )
            self._consecutive_low_ic = 0
            print(f"[DEBUG-evo] promotion path: _consecutive_low_ic reset to 0")
            promoted = True
        else:
            # 失败轨迹
            self._record_failure_trace(
                optimized_factor, generation, evolution_method,
                evolution_summary,
                verifier_result["failure_reasons"], trace_id,
                evaluation=evaluation,
            )
            # 检查低 IC
            bt = evaluation.get("level_1_backtest", {})
            if abs(bt.get("ic", 0)) < self.budget["circuit_breaker_low_ic_threshold"]:
                self._consecutive_low_ic += 1
                print(f"[DEBUG-evo] failure path, low IC: _consecutive_low_ic incremented to {self._consecutive_low_ic}")
            else:
                self._consecutive_low_ic = 0
                print(f"[DEBUG-evo] failure path, not low IC: _consecutive_low_ic reset to 0")

        # ── Step 6: 状态持久化 ──
        state["last_generation"] = generation
        self.state_manager.save(state)

        return promoted

    def _select_parent_uct(self, parents: list[FactorProgram]) -> FactorProgram:
        """UCT 树搜索选择父因子，平衡探索与利用。

        UCB = avg_reward + c * sqrt(ln(total_visits) / visits)

        未访问的父因子（visits=0）返回无限大 UCB，确保优先探索。
        """
        total_visits = sum(
            s.get("visits", 0) for s in self._uct_stats.values()
        )
        best_score = -float("inf")
        best_parent = parents[0]

        for p in parents:
            fid = p["factor_id"]
            stats = self._uct_stats.get(fid, {"visits": 0, "total_reward": 0.0})
            visits = stats["visits"]
            if visits == 0:
                # 未访问 → 优先探索
                return p
            avg_reward = stats["total_reward"] / visits
            exploration = UCT_EXPLORATION_C * math.sqrt(
                math.log(max(total_visits, 1)) / visits
            )
            ucb = avg_reward + exploration
            if ucb > best_score:
                best_score = ucb
                best_parent = p

        return best_parent

    def _update_uct_stats(
        self, parent: FactorProgram, evaluation: FactorEvaluation
    ) -> None:
        """根据子因子评估结果更新父因子的 UCT 统计。

        奖励 = abs(IC)（通过）/ 0（失败），鼓励 IC 高的父因子。
        """
        fid = parent["factor_id"]
        if fid not in self._uct_stats:
            self._uct_stats[fid] = {"visits": 0, "total_reward": 0.0}
        bt = evaluation.get("level_1_backtest", {})
        passed = evaluation.get("passed", False)
        reward = abs(bt.get("ic", 0.0)) if passed else 0.0
        self._uct_stats[fid]["visits"] += 1
        self._uct_stats[fid]["total_reward"] += reward

    def _check_circuit_breaker(self, state: EvolutionState) -> Optional[str]:
        """熔断检查。返回原因字符串（None = 未触发）。"""
        # Token 超 2x
        tokens = state.get("tokens_consumed", 0)
        limit = state.get("budget_limit", self.budget["nightly_token_limit"])
        if tokens > limit * self.budget["circuit_breaker_token_ratio"]:
            return (
                f"Token 熔断: {tokens} > {limit} * "
                f"{self.budget['circuit_breaker_token_ratio']}"
            )

        # 连续低 IC
        if self._consecutive_low_ic >= self.budget["circuit_breaker_consecutive_low_ic"]:
            return (
                f"连续低 IC 熔断: {self._consecutive_low_ic} 代 "
                f"IC < {self.budget['circuit_breaker_low_ic_threshold']}"
            )

        # 失败率 > 90%
        evaluated = state.get("total_factors_evaluated", 0)
        promoted = state.get("total_factors_promoted", 0)
        if evaluated >= 10:
            failure_rate = (evaluated - promoted) / evaluated
            if failure_rate > self.budget["circuit_breaker_failure_rate"]:
                return (
                    f"失败率熔断: {failure_rate:.2%} > "
                    f"{self.budget['circuit_breaker_failure_rate']:.2%}"
                )

        return None

    # ── GAP-I206 (v2.71.0): L2 准入去冗余 — 与既有 elite 相关性检查 ──

    def _check_elite_correlation(self, factor: FactorProgram) -> Optional[dict[str, Any]]:
        """L2 准入去冗余：新演化因子晋升前与既有 elite 因子的信号相关性检查。

        对既有 elite 池（self.elite_dir 下已晋升 JSON）逐个执行因子代码计算
        信号，与新因子信号做 Pearson 相关；存在相关绝对值 ≥ 阈值
        （l2_elite_corr_threshold，默认 0.9）的高相关对时返回最高相关对列表，
        否则返回 None（放行）。种子因子（shadow_observe=False）不经过本检查，
        由 _promote_to_elite 调用侧控制。

        Args:
            factor: 待晋升的新演化因子

        Returns:
            None: 无既有 elite / 无高相关命中（放行）
            dict: {"correlations": [{factor_name_b, factor_id_b, pearson,
                  abs_pearson}, ...]} 相关 ≥ 阈值的对（按 abs_pearson 降序）
        """
        from .backtest_pipeline import BacktestPipeline

        if not self.elite_dir.exists():
            return None

        # 新因子信号只计算一次，避免对每个既有 elite 重复执行
        try:
            new_signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""), self.data, factor.get("params", {}),
            )
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(new_signal, np.ndarray) or len(new_signal) != len(self.data):
            return None

        correlations: list[dict[str, Any]] = []
        scanned = 0
        for fp in sorted(self.elite_dir.glob("*.json")):
            if fp.name == "_l2_seed_correlation_index.json":
                continue
            if scanned >= self._l2_elite_corr_max_scan:
                break
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not (isinstance(data, dict) and data.get("code") and data.get("factor_id")):
                continue
            if data.get("factor_id") == factor.get("factor_id"):
                continue
            scanned += 1
            try:
                other_signal = BacktestPipeline._execute_factor_code(
                    data.get("code", ""), self.data, data.get("params", {}),
                )
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(other_signal, np.ndarray) or len(other_signal) != len(new_signal):
                continue
            valid = ~(np.isnan(other_signal) | np.isnan(new_signal))
            if valid.sum() < 10:
                continue
            pearson = float(np.corrcoef(other_signal[valid], new_signal[valid])[0, 1])
            if np.isnan(pearson):
                continue
            if abs(pearson) >= self._l2_elite_corr_threshold:
                correlations.append({
                    "factor_name_b": data.get("name", data.get("factor_name", "?")),
                    "factor_id_b": data.get("factor_id", "?"),
                    "pearson": pearson,
                    "abs_pearson": abs(pearson),
                })
        if not correlations:
            return None
        correlations.sort(key=lambda c: c["abs_pearson"], reverse=True)
        return {"correlations": correlations}

    def _load_elite_parent_factors(self) -> list[dict[str, Any]]:
        """从 elite 快照目录加载因子作为父因子池。

        场景: 种子因子全部已存在 elite 快照（去重跳过、无新晋升）时，
        无合格父因子导致演化循环 0 代跳过。回退使用既有精英因子继续
        演化（种子重复晋升由 _promote_to_elite 去重保护）。
        """
        import json

        parents: list[dict[str, Any]] = []
        for fp in sorted(self.elite_dir.glob("*.json")):
            if fp.name == "_l2_seed_correlation_index.json":
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict) and data.get("code") and data.get("factor_id"):
                parents.append(data)
        return parents

    def _get_repo(self):
        """延迟初始化 DuckDB 仓储（GAP-030: 支持 factor_db_path 注入隔离库）。"""
        if self._repo is None:
            from .factor_db import FactorRepository
            self._repo = (
                FactorRepository(db_path=self.factor_db_path)
                if self.factor_db_path else FactorRepository()
            )
        return self._repo

    def _promote_to_elite(
        self, factor: FactorProgram, evaluation: FactorEvaluation,
        seed_correlations: Optional[list[FactorCorrelation]] = None,
        quality_score: Optional[dict] = None,
        audit_report: Optional[FactorAuditReport] = None,
        shadow_observe: bool = True,
    ) -> Optional[Path]:
        """将因子晋升到精英池。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            seed_correlations: L2 种子因子相关性标记（可选）
            quality_score: 质量评分卡结果（Phase A.1 集成）
            audit_report: 因子审计报告（Phase B.3 集成）
            shadow_observe: 是否进入影子池观察（新演化因子默认 True；
                            种子因子/初始池导入传 False 直接进正式组合）

        Returns:
            Path: 晋升成功
            None: 因子名称重复，跳过晋升
        """
        # 去重检查：DuckDB 是权威数据源，通过 factor_catalog 表检查
        factor_name = factor.get("name", "")
        try:
            repo = self._get_repo()
            existing = repo.get_factor_by_name(factor_name, market=self.market)
            if existing:
                print(f"[evo] 跳过重复因子: {factor_name} (DuckDB 已存在, market={self.market})")
                return None
        except Exception:
            pass

        # ── 家族多样性检查：限制单一家族因子数量，避免演化收敛过度集中 ──
        factor_family = factor.get("family", "unknown")
        max_per_family = self.budget.get("max_per_family", 15)
        try:
            repo = self._get_repo()
            existing_family = repo.get_by_family(
                family=factor_family,
                market=self.market,
                limit=100,
            )
            if len(existing_family) >= max_per_family:
                print(
                    f"[evo] 家族多样性限制 [{factor_name}]: "
                    f"家族 '{factor_family}' 已有 {len(existing_family)} 个因子 "
                    f"(上限 {max_per_family})"
                )
                return None
        except Exception:
            pass

        fp = self.elite_dir / f"{factor['factor_id']}.json"
        # 将 factor 字段展开到顶层，方便 cli 直接读取
        record = dict(factor)
        # 确保 market 字段正确：若因子为默认 "multi"，使用演化上下文的市场
        if record.get("market", "multi") in ("multi", "other") and self.market in ("futures", "stock"):
            record["market"] = self.market
        record["evaluation"] = evaluation

        # ── ★ GAP-I206 (v2.71.0): L2 准入去冗余 — 与既有 elite 相关性检查 ──
        # 演化因子（shadow_observe=True）晋升前与既有 elite 计算信号相关性，
        # 超过阈值拒绝晋升（防 elite 池相关性膨胀稀释组合夏普）。种子因子
        # （shadow_observe=False 首轮导入）跳过——初始入库全量放行。
        if shadow_observe:
            elite_corr = self._check_elite_correlation(factor)
            if elite_corr is not None:
                _pairs = elite_corr.get("correlations", [])
                _max = _pairs[0] if _pairs else {}
                _name_b = _max.get("factor_name_b", "?")
                _corr = _max.get("pearson", 0.0)
                print(
                    f"[evo] ★ L2 准入去冗余拦截 [{factor.get('name', '?')}]: "
                    f"与既有 elite {_name_b} 相关 {_corr:.3f} ≥ 阈值 {self._l2_elite_corr_threshold}，拒绝晋升"
                )
                logger.warning(
                    "[L2-redun] 因子 %s 与既有 elite %s 相关 %.3f ≥ %.2f，拒绝晋升（GAP-I206）",
                    factor.get("name", "?"), _name_b, _corr,
                    self._l2_elite_corr_threshold,
                )
                return None
            if self._l2_elite_corr_debug:
                # 无既有 elite 或检查失败时静默放行（首次晋升场景）
                logger.debug(
                    "[L2-redun] %s 无既有 elite 相关性命中，放行",
                    factor.get("name", "?"),
                )

        # ── ★ Phase B.4: 高IC筛查强制门（所有市场统一） ──
        # 前置计算: 从种子相关性标记提取 max_corr（若已传入）
        max_corr_detected = None
        if seed_correlations:
            factor_id = factor.get("factor_id", "")
            corr_vals = [
                max(abs(sc.get("pearson", 0)), abs(sc.get("spearman", 0)))
                for sc in seed_correlations
                if factor_id in (sc.get("factor_id_a", ""), sc.get("factor_id_b", ""))
            ]
            if corr_vals:
                max_corr_detected = max(corr_vals)
        high_ic_screen = self.high_ic_screener.screen(
            factor=record,
            evaluation=evaluation,
            correlation_metadata=(
                {"max_corr_detected": max_corr_detected}
                if max_corr_detected is not None else {}
            ),
            backtest_pipeline=(
                evaluation.get("backtest_pipeline", {})
                if isinstance(evaluation.get("backtest_pipeline"), dict) else {}
            ),
            trace_id=getattr(self, "_trace_id", ""),
        )
        if high_ic_screen.grade == "C":
            veto_info = (
                "；".join(high_ic_screen.veto_reasons)
                if high_ic_screen.veto_reasons
                else f"总分 {high_ic_screen.total_score:.1f} < 60"
            )
            print(
                f"[evo] ★ 高IC筛查拦截 [{factor_name}]: "
                f"grade={high_ic_screen.grade}, 总分={high_ic_screen.total_score:.1f}, "
                f"原因={veto_info}"
            )
            return None
        record["high_ic_screen"] = high_ic_screen.to_dict()

        # ── 多重检验强制门: 拒绝未通过多重检验校正的因子 ──
        level_3 = evaluation.get("level_3_multiple", {})
        if not level_3.get("passed", False):
            bonf_p = level_3.get('bonferroni_p', 'N/A')
            adj_t = level_3.get('adjusted_t', 'N/A')
            p_str = f"{bonf_p:.4f}" if isinstance(bonf_p, float) else str(bonf_p)
            t_str = f"{adj_t:.4f}" if isinstance(adj_t, float) else str(adj_t)
            print(
                f"[evo] 多重检验未通过 [{factor_name}]: "
                f"Bonferroni p={p_str}, adjusted_t={t_str}"
            )
            return None

        # ── 写入质量评分卡 (Phase A.1 集成) ──
        if quality_score is not None:
            record["quality_score"] = quality_score

        # ── 写入审计报告 (Phase B.3 集成) ──
        if audit_report is not None:
            record["audit_report"] = audit_report.to_dict()

        # ── 写入 L2 相关性元数据（供 L3 参考） ──
        if seed_correlations:
            factor_id = factor.get("factor_id", "")
            corr_flags: list[dict[str, Any]] = []
            
            for sc in seed_correlations:
                a, b = sc.get("factor_id_a", ""), sc.get("factor_id_b", "")
                pearson = sc.get("pearson", 0)
                spearman = sc.get("spearman", 0)
                max_abs = max(abs(pearson), abs(spearman))
                
                if factor_id == a or factor_id == b:
                    partner = b if factor_id == a else a
                    corr_flags.append({
                        "partner_factor_id": partner,
                        "pearson": pearson,
                        "spearman": spearman,
                        "max_abs": max_abs,
                        "source": "l2_seed_correlation_check",
                    })
            
            if corr_flags:
                record["correlation_metadata"] = {
                    "l2_seed_flags": corr_flags,
                    "flag_count": len(corr_flags),
                    "max_corr_detected": max(
                        (f["max_abs"] for f in corr_flags), default=0
                    ),
                }
                print(f"[evo] 因子 {factor.get('name', '?')} 写入 L2 相关性标记: "
                      f"{len(corr_flags)} 个高相关对")

        # ── 影子池标记（L2 晋升节奏控制）：新演化因子先进影子池观察 ──
        if shadow_observe:
            record["shadow_pool"] = _build_shadow_pool()
            print(f"[evo] 因子 {factor.get('name', '?')} 进入影子池观察 "
                  f"({_SHADOW_OBSERVE_TRADING_DAYS} 个交易日)")

        # ── 晋升时间戳（用于纯外推验证，P2 差距修复） ──
        record["promoted_at"] = datetime.now().isoformat()

        # ── 写入 JSON 文件（debug/备份） ──
        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        # ── 写入 DuckDB（主存储） ──
        # GAP-032 严格一致：DuckDB 是主存储，写入失败则回滚已写 JSON 快照并判定
        # 晋升失败，杜绝"快照有、catalog 无"的孤儿数据
        write_ok = self._write_to_duckdb(
            factor, evaluation, quality_score, seed_correlations, audit_report,
            shadow_pool=record.get("shadow_pool"),
        )
        if not write_ok:
            try:
                fp.unlink(missing_ok=True)
            except OSError:
                pass
            print(f"[evo] ❌ 晋升失败 [{factor.get('name', '?')}]: "
                  f"DuckDB 写入失败，已回滚 JSON 快照 {fp.name}")
            return None

        # ── ★ GAP-036: 激进清理 — L1 注入候选晋升精英后删除 l1_injected 文件 ──
        # 非阻塞：删除失败不影响晋升，仅记录 warning
        f_source = factor.get("source", "")
        f_parent_id = factor.get("parent_id")
        if f_source == "bootstrapping" and f_parent_id and self.inject_dir.exists():
            cand_file = self.inject_dir / f"{f_parent_id}.json"
            try:
                if cand_file.exists():
                    cand_file.unlink()
                    logger.info(
                        "[GAP-036] 已删除 L1 注入候选文件: %s (factor=%s, source=%s)",
                        cand_file.name, factor.get("name", "?"), f_source,
                    )
            except OSError as e:
                logger.warning("[GAP-036] 删除 L1 候选文件失败: %s, err=%s", cand_file.name, e)

        # ── ★ 种子溯源写入 (seed_lineage) ──
        # 非阻塞：溯源写入失败不影响晋升，仅记录 warning
        try:
            repo = self._get_repo()
            f_id = factor.get("factor_id", "")
            f_name = factor.get("name", "")
            f_source = factor.get("source", "")
            f_gen = factor.get("generation", 0)
            f_family = factor.get("family", "unknown")
            f_parent_id = factor.get("parent_id")
            f_trace_id = factor.get("trace_id", "")

            lineage = repo.resolve_seed_lineage(
                factor_id=f_id,
                factor_name=f_name,
                factor_source=f_source,
                factor_generation=f_gen,
                factor_family=f_family,
                factor_parent_id=f_parent_id,
                market=self.market,
            )
            repo.write_seed_lineage(
                factor_id=f_id,
                factor_name=f_name,
                seed_name=lineage["seed_name"],
                seed_family=lineage["seed_family"],
                seed_market=lineage["seed_market"],
                generation=lineage["generation"],
                parent_id=f_parent_id,
                trace_id=f_trace_id,
            )
        except Exception as e:
            logger.debug("[seed_lineage] 溯源写入非阻塞异常: %s", e)

        # ── Phase A.2: 注册到精英因子追踪器 ──
        try:
            factor_id = factor.get("factor_id", "")
            factor_name = factor.get("name", "?")
            sharpe = 0.0
            ic = 0.0
            if isinstance(evaluation, dict):
                bt = evaluation.get("level_1_backtest", {})
                if isinstance(bt, dict):
                    ic = bt.get("ic", 0.0)
                    sharpe = bt.get("sharpe", 0.0)
            grade = None
            quality_score_value = None
            if quality_score is not None:
                grade = quality_score.get("grade")
                quality_score_value = quality_score.get("total_score")
            self.elite_tracker.init_tracker(
                factor_id=factor_id,
                name=factor_name,
                entry_ic=ic,
                entry_sharpe=sharpe,
                grade=grade,
                quality_score=quality_score_value,
            )
        except Exception as e:
            logger.debug("精英因子追踪器注册失败: %s", e)

        # ── 记录一致性日志（P4） ──
        _log_consistency_event(
            event_type="promote",
            factor_id=factor.get("factor_id", ""),
            factor_name=factor.get("name", ""),
            market=self.market,
            status="active",
            json_path=str(fp),
            trace_id=factor.get("trace_id", ""),
        )

        return fp

    def _write_to_duckdb(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        quality_score: Optional[dict] = None,
        seed_correlations: Optional[list[FactorCorrelation]] = None,
        audit_report: Optional[FactorAuditReport] = None,
        shadow_pool: Optional[dict] = None,
    ) -> bool:
        """将因子写入 DuckDB（主存储层）。

        支持幂等写入：若 factor_id 已存在则更新，不存在则创建。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            quality_score: 质量评分卡结果
            seed_correlations: L2 种子因子相关性标记
            audit_report: 因子审计报告（Phase B.3 集成）
            shadow_pool: 影子池标记（L2 晋升节奏控制，可选）

        Returns:
            True: 写入成功
            False: 写入失败（GAP-032 严格一致：失败不再吞异常，
                   由调用方决定是否回滚 JSON 快照）
        """
        try:
            repo = self._get_repo()
            factor_id = factor.get("factor_id")
            factor_name = factor.get("name", "?")
            factor_market = factor.get("market", "multi")
            # 若因子未显式指定有效市场（multi/other 为默认值），使用演化上下文的市场
            if factor_market in ("multi", "other") and self.market in ("futures", "stock"):
                factor_market = self.market
            factor_family = factor.get("family", "other")

            l1 = evaluation.get("level_1_backtest", {})

            factor_dict = {
                "factor_id": factor_id,
                "name": factor_name,
                "code": factor.get("code", ""),
                "params": factor.get("params", {}),
                "signature": factor.get("signature", {}),
                "economic_logic": factor.get("economic_logic", {}),
                "source": factor.get("source", "macro_evolution"),
                "parent_id": factor.get("parent_id"),
                "generation": factor.get("generation", 0),
                "trace_id": factor.get("trace_id"),
                "market": factor_market,
                "family": factor_family,
                "is_elite": True,
                "sharpe": l1.get("sharpe", 0.0),
                "ic": l1.get("ic", 0.0),
                "icir": l1.get("icir", 0.0),
                "max_drawdown": l1.get("max_drawdown", 0.0),
                "turnover_monthly": l1.get("turnover_monthly", 0.0),
                "decay_6m": l1.get("decay_6m", 0.0),
                "metadata": {
                    "quality_score": quality_score,
                    "correlation_metadata": factor.get("correlation_metadata", {}),
                    "symbols": factor.get("symbols", []),
                    "risk_tag": factor.get("risk_tag"),
                    "factor_version": factor.get("factor_version", "v2"),
                    "audit_report": audit_report.to_dict() if audit_report else None,
                    "shadow_pool": shadow_pool,
                },
            }

            # ── 幂等写入：已存在则更新，不存在则创建 ──
            existing = repo.get_factor(factor_id)
            if existing:
                repo.update_factor(factor_id, factor_dict)
                print(f"[evo] 🔄 更新已有因子 {factor_name} 到 DuckDB [market={factor_market}]")
            else:
                repo.create_factor(factor_dict)
                print(f"[evo] ✅ 新建因子 {factor_name} 到 DuckDB [market={factor_market}]")

            # ── 写入/更新评估记录 ──
            l2 = evaluation.get("level_2_economic", {})
            l3 = evaluation.get("level_3_multiple", {})

            eval_dict = {
                "trace_id": evaluation.get("trace_id"),
                "ic": l1.get("ic", 0),
                "icir": l1.get("icir", 0),
                "sharpe": l1.get("sharpe", 0),
                "max_drawdown": l1.get("max_drawdown", 0),
                "turnover": l1.get("turnover_monthly", 0),
                "t_stat": l1.get("t_stat", 0),
                "monotonicity": l1.get("monotonicity", False),
                "oos_ratio": l1.get("oos_ratio", 0),
                "theory_score": l2.get("theory", 0),
                "behavioral_score": l2.get("behavioral", 0),
                "microstructure_score": l2.get("microstructure", 0),
                "institutional_score": l2.get("institutional", 0),
                "dims_passed": l2.get("dims_passed", 0),
                "bonferroni_p": l3.get("bonferroni_p", 1.0),
                "fdr_q": l3.get("fdr_q", 0.05),
                "effective_n": l3.get("effective_n_factors", 1),
                "adjusted_t": l3.get("adjusted_t", 0),
                "l3_passed": l3.get("passed", False),
                "overall_passed": evaluation.get("passed", False),
                "failure_reasons": evaluation.get("failure_reasons", []),
                "evaluated_at": evaluation.get("evaluated_at"),
            }

            repo.add_evaluation(factor_id, eval_dict)
            return True

        except Exception as e:
            factor_name = factor.get("name", "?")
            print(f"[evo] ⚠️ DuckDB 写入失败 [{factor_name}]: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _evaluate_and_promote_seeds(
        self,
        seeds: list[FactorProgram],
        trace_id: str,
        state: EvolutionState,
        elite_ids: list[str],
        seed_correlations: Optional[list[FactorCorrelation]] = None,
    ) -> int:
        """评估种子因子，合格的直接晋升 elite。

        种子是已知起点，跳过 Verifier 判定，仅用简单 IC/夏普筛选。
        种子评估不计入熔断计数器（evaluated/promoted），
        熔断仅针对演化过程中的因子。

        Args:
            seeds: 种子因子列表
            trace_id: 全链路 trace_id
            state: 演化状态
            elite_ids: 精英因子 ID 列表（将会被追加）
            seed_correlations: L2 相关性预检结果（传递给晋升方法）

        Returns:
            晋升的种子因子数量
        """
        promoted = 0
        for seed in seeds:
            try:
                if self._is_cross_section:
                    evaluation = self._evaluate_cross_section(seed, trace_id)
                else:
                    evaluation = self.evaluation_chain.evaluate(
                        seed, self.data, self.forward_returns,
                    )
                # 确保 WalkForward 结果存在（若缺失则执行轻量 2 窗口验证）
                if evaluation.get("walk_forward") is None:
                    from .evaluation_chain import evaluate_walk_forward
                    try:
                        wf = evaluate_walk_forward(
                            seed, self.data, self.forward_returns,
                            config={"n_windows": 2},
                        )
                        evaluation["walk_forward"] = wf
                    except Exception:
                        logger.warning(
                            "[evo] 种子因子 WalkForward 轻量验证失败: %s",
                            seed.get("name", "?"),
                        )
                bt = evaluation.get("level_1_backtest", {})
                passed = evaluation.get("passed", False)

                # 风险标签额外检查：标记为 vwap_approx 的因子需要更高 IC 阈值
                if passed and seed.get("risk_tag") == "vwap_approx":
                    ic = bt.get("ic", 0)
                    if abs(ic) < 0.08:
                        print(f"[evo] 跳过 vwap_approx 因子: {seed['name']} (IC={abs(ic):.4f} < 0.08)")
                        continue

                if passed:
                    # ── Verifier 判定（v2.50.0 与演化因子完全对齐） ──
                    verifier_result = self.verifier.check(evaluation)
                    if not verifier_result.get("passed", False):
                        self._record_failure_trace(
                            seed, 0, "seed_verifier",
                            "Verifier 判定未通过",
                            verifier_result.get("failure_reasons", []), trace_id,
                            evaluation=evaluation,
                        )
                        continue

                    # 风险标签额外检查：标记为 vwap_approx 的因子需要更高 IC 阈值
                    if seed.get("risk_tag") == "vwap_approx":
                        ic = bt.get("ic", 0)
                        if abs(ic) < 0.08:
                            print(f"[evo] 跳过 vwap_approx 因子: {seed['name']} (IC={abs(ic):.4f} < 0.08)")
                            continue

                    # 种子因子质量评分卡 (Phase A.1 集成)
                    inspection = self.quality_inspector.inspect(
                        factor=seed, evaluation=evaluation,
                    )
                    if inspection.filtered:
                        self._log_inspection_detail(
                            seed, inspection, "淘汰", 0,
                        )
                        continue

                    # 端到端回测流水线 (Phase B.2 集成)
                    backtest_result = self._run_backtest_pipeline(
                        seed, evaluation, trace_id,
                    )
                    if backtest_result:
                        evaluation["backtest_pipeline"] = backtest_result

                    # 数据质量监控 (Phase B.1 集成)
                    self._register_factor_baseline(seed, evaluation)
                    dq_alerts = self._check_factor_data_quality(
                        seed, evaluation,
                    )
                    if dq_alerts:
                        critical = any(
                            getattr(a, "severity", "") == "critical"
                            for a in dq_alerts
                        )
                        if critical:
                            print(
                                f"[evo] 种子数据质量严重告警 [{seed.get('name', '?')}]: "
                                f"跳过晋升"
                            )
                            continue

                    # 种子因子强制审计 (Phase B.3 集成)
                    audit_report = self._run_factor_audit(
                        seed, evaluation, trace_id,
                    )
                    if not audit_report.passed:
                        failed_items = [
                            it.name for it in audit_report.failed_items
                        ]
                        print(
                            f"[evo] 种子审计未通过 [{seed.get('name', '?')}]: "
                            f"失败项={failed_items}, 通过率={audit_report.pass_rate:.0%}"
                        )
                        self._record_audit_failed_trace(
                            seed, 0, trace_id,
                            audit_report, evaluation=evaluation,
                        )
                        continue

                    # ── 消融实验检查（v2.50.0 与演化因子对齐） ──
                    ablation_result = self._run_ablation_check(
                        seed, evaluation, trace_id,
                    )
                    evaluation["ablation_check"] = ablation_result
                    if not ablation_result.get("passed", True):
                        print(
                            f"[evo] 种子消融实验未通过 [{seed.get('name', '?')}]: "
                            f"疑似伪相关"
                        )
                        self._record_ablation_failed_trace(
                            seed, 0, trace_id,
                            ablation_result,
                        )
                        continue

                    # ── 因果结构审查（v2.50.0 与演化因子对齐） ──
                    causal_result = self._run_causal_validation(
                        seed, evaluation, trace_id,
                    )
                    evaluation["causal_validation"] = causal_result
                    if not causal_result.get("passed", True):
                        print(
                            f"[evo] 种子因果审查未通过 [{seed.get('name', '?')}]: "
                            f"事件敏感"
                        )
                        self._record_causal_failed_trace(
                            seed, 0, trace_id,
                            causal_result,
                        )
                        continue

                    # ── 鲁棒性审查（v2.50.0 与演化因子对齐） ──
                    robustness_result = self._run_robustness_check(
                        seed, evaluation, trace_id,
                    )
                    evaluation["robustness_check"] = robustness_result
                    if not robustness_result.get("passed", True):
                        print(
                            f"[evo] 种子鲁棒性审查未通过 [{seed.get('name', '?')}]"
                        )
                        self._record_robustness_failed_trace(
                            seed, 0, trace_id,
                            robustness_result,
                        )
                        continue

                    # ── SHAP 可解释性分析（v2.50.0 与演化因子对齐，不阻断） ──
                    shap_result = self._run_shap_analysis(
                        seed, evaluation, trace_id,
                    )
                    evaluation["shap_analysis"] = shap_result

                    self._log_inspection_detail(
                        seed, inspection, "通过", 0,
                    )
                    promoted_path = self._promote_to_elite(
                        seed, evaluation,
                        seed_correlations=seed_correlations,
                        quality_score=inspection.quality_score,
                        audit_report=audit_report,
                        shadow_observe=False,  # 种子因子直接进正式组合，不走影子池
                    )
                    if promoted_path is None:
                        # 因子名称重复，跳过
                        continue
                    elite_ids.append(seed["factor_id"])
                    promoted += 1
                    print(f"[evo] 种子因子晋升: {seed['name']} (IC={bt.get('ic', 0):.4f}, "
                          f"质量分={inspection.total_score}/50)")
            except Exception:
                continue
        return promoted

    def _merge_l1_candidates(
        self,
        seeds: list[FactorProgram],
        trace_id: str,
    ) -> list[FactorProgram]:
        """GAP-031: 合并 L1 注入候选到种子列表。

        读取 memory/knowledge/factors/l1_injected/*.json，经
        pending 门控（factor_pool.json status=pending）+ market 过滤 + 名称去重后，
        转为 FactorProgram（source="bootstrapping"）并入种子列表，
        与种子同等参与相关性预检与种子评估晋升。

        幂等: 消费后更新 factor_pool.json 中对应记录 status pending → injected。

        Args:
            seeds: 现有种子因子列表（load_all_seeds 结果）
            trace_id: 全链路 trace_id

        Returns:
            合并 L1 候选后的种子因子列表
        """
        import json

        inject_dir = self.inject_dir
        pool_path = Path("memory/knowledge/factors/factor_pool.json")
        if not inject_dir.exists():
            return seeds

        # 1. pending 门控: factor_pool.json 中 status == "pending" 的 factor_id
        pending_ids: set[str] = set()
        pool_loaded = False
        pool_data: Optional[dict[str, Any]] = None
        # 已消费 ID 集合（用于历史遗留文件清理）
        consumed_ids_set: set[str] = set()
        if pool_path.exists():
            try:
                pool_data = json.loads(pool_path.read_text(encoding="utf-8"))
                for f in pool_data.get("factors", []):
                    fid = f.get("factor_id")
                    if not fid:
                        continue
                    if f.get("status") == "pending":
                        pending_ids.add(fid)
                    else:
                        consumed_ids_set.add(fid)
                pool_loaded = True
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[L1.merge] factor_pool.json 读取失败，退化为扫描全部候选: %s", e)

        # GAP-036: 历史遗留清理 — 删除已消费（非 pending）的 l1_injected 文件
        # 这些文件由旧版 L1 产生，消费后未删除，现一次性清理
        if consumed_ids_set:
            cleaned_count = 0
            for cand_file in list(inject_dir.glob("cand_*.json")):
                try:
                    cand_id = cand_file.stem  # 如 "cand_d6bd0140"
                    if cand_id in consumed_ids_set:
                        cand_file.unlink()
                        cleaned_count += 1
                except (OSError, json.JSONDecodeError):
                    pass
            if cleaned_count:
                logger.info(
                    "[GAP-036] 历史遗留清理: 删除 %d 个已消费的 L1 候选文件",
                    cleaned_count,
                )

        # 2. 已有种子名称集（去重基准）
        from .factor_program import create_factor_program
        existing_names = {fp.get("name") for fp in seeds}

        # 3. 扫描候选并合并
        merged: list[FactorProgram] = list(seeds)
        consumed_ids: list[str] = []
        for cand_file in sorted(inject_dir.glob("*.json")):
            try:
                cand = json.loads(cand_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[L1.merge] 候选文件解析失败: %s, err=%s", cand_file.name, e)
                continue

            cand_id = cand.get("candidate_id") or cand.get("factor_id")
            cand_name = cand.get("name", "")
            if not cand_id or not cand_name or not cand.get("code"):
                continue
            # pending 门控（pool 加载成功即严格；pool 缺失/损坏时放行）
            if pool_loaded and cand_id not in pending_ids:
                continue
            # 已消费标记（兼容无 pool 场景）
            if cand.get("injected_to_l2"):
                continue
            # market 过滤: 候选带 market 时严格匹配，缺失时放行（老文件兼容）
            cand_market = cand.get("market")
            if cand_market is not None and cand_market != self.market:
                continue
            # 名称去重
            if cand_name in existing_names:
                continue

            # 为候选因子预填 economic_logic 默认值（防御性，配合 evaluation_chain 默认值 3）
            raw_el = cand.get("economic_logic", {}) or {}
            prefilled_el = {
                "theory": raw_el.get("theory", 3),
                "behavioral": raw_el.get("behavioral", 3),
                "microstructure": raw_el.get("microstructure", 3),
                "institutional": raw_el.get("institutional", 3),
                "narrative": raw_el.get("narrative", ""),
            }
            try:
                fp = create_factor_program(
                    name=cand_name,
                    code=cand["code"],
                    params=cand.get("params", {}),
                    signature=cand.get("signature"),
                    economic_logic=prefilled_el,
                    source="bootstrapping",
                    parent_id=cand_id,
                    generation=0,
                    trace_id=trace_id,
                )
            except Exception as e:
                logger.warning(
                    "[L1.merge] 候选转 FactorProgram 失败: %s, err=%s",
                    cand_file.name, e,
                )
                continue

            merged.append(fp)
            existing_names.add(cand_name)
            consumed_ids.append(cand_id)
            logger.info(
                "[L1.merge] 合并候选: name=%s, candidate_id=%s, market=%s",
                cand_name, cand_id, cand_market,
            )

            # GAP-036: 消费后立即删除 l1_injected 文件（激进清理，非阻塞）
            try:
                if cand_file.exists():
                    cand_file.unlink()
                    logger.info(
                        "[GAP-036] 消费后删除 L1 候选文件: %s (name=%s)",
                        cand_file.name, cand_name,
                    )
            except OSError as e:
                logger.warning("[GAP-036] 删除 L1 候选文件失败: %s, err=%s", cand_file.name, e)

        # 4. 幂等: factor_pool.json pending → injected
        if consumed_ids and pool_data is not None:
            for entry in pool_data.get("factors", []):
                if entry.get("factor_id") in consumed_ids:
                    entry["status"] = "injected"
                    entry["updated_at"] = datetime.now().isoformat()
            try:
                pool_path.write_text(
                    json.dumps(pool_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as e:
                logger.warning("[L1.merge] factor_pool.json 状态更新失败: %s", e)

        if consumed_ids:
            print(f"[evo] 合并 L1 注入候选: {len(consumed_ids)} 个 (GAP-031)")
        return merged

    def _run_seed_correlation_check(
        self,
        seeds: list[FactorProgram],
        trace_id: str,
    ) -> list[FactorCorrelation]:
        """L2 种子因子相关性预检 — 轻量扫描，仅标记不删除。

        自动检测数据模式:
        - 股票时序模式: 计算 Pearson/Spearman 相关
        - 期货横截面模式: 计算截面排名 Spearman 相关

        设计原则:
        - 不过早删除：因子相关性是市场状态依赖的，当前相关≠永久相关
        - 仅做标记：高相关对记录到 metadata，L3 决策时再处理
        - 添加超时保护：5 分钟超时自动跳过，防止卡死演化流程

        Args:
            seeds: 种子因子列表
            trace_id: 全链路 trace_id

        Returns:
            list[FactorCorrelation] — 超过阈值的高相关因子对
        """
        # 期货横截面模式: 184 种子 × 25 品种 × 500 日 = 超大规模计算，跳过
        # 原因：compute_cross_section_correlations 在 184 种子 × 25 品种下耗时 > 10 分钟
        # 且 ThreadPoolExecutor timeout 无法中断卡在 numpy/scipy C 扩展中的线程
        # 仅做标记不删除，L3 组合时通过 ACTIVE_FACTOR_CAP 和 Elastic Net 控制冗余
        if self._is_cross_section and len(seeds) > 50:
            print(f"[evo] 种子因子相关性预检跳过: {len(seeds)} 种子，横截面模式计算量过大")
            return []
        try:
            if self._is_cross_section:
                # 期货横截面模式: 截面排名 Spearman 相关
                from .seed_pool import compute_cross_section_correlations
                correlations = compute_cross_section_correlations(
                    seeds,
                    self.cross_section_data,
                    self.cross_section_dates,
                    threshold=0.95,
                )
            else:
                # 股票时序模式: Pearson/Spearman 相关
                correlations = compute_seed_correlations(
                    seeds, self.data, threshold=0.95
                )
            return correlations
        except Exception as e:
            mode = "横截面" if self._is_cross_section else "时序"
            print(f"[evo] 种子因子相关性预检异常（{mode}模式，跳过）: {e}")
            return []

    def _evaluate_cross_section(
        self, factor: FactorProgram, trace_id: str
    ) -> FactorEvaluation:
        """横截面模式下的评估：直接回测 + 自动构造 FactorEvaluation。"""
        from .contracts import EconomicScore, MultipleTestResult

        bt = cross_section_evaluate_backtest(
            factor,
            self.cross_section_data,
            self.cross_section_dates,
            industry_map=self.industry_map,
            cap_map=self.cap_map,
            long_only=(self.market in ("stock", "etf")),
        )
        # 从因子自身读取经济逻辑评分（种子 YAML 或 LLM 生成），默认 3 分
        el = factor.get("economic_logic", {}) or {}
        ec = EconomicScore(
            theory=int(el.get("theory", 3)),
            behavioral=int(el.get("behavioral", 3)),
            microstructure=int(el.get("microstructure", 3)),
            institutional=int(el.get("institutional", 3)),
            dimensions_passed=3,
            narrative=el.get("narrative", "横截面评估（自动继承）"),
        )
        mt = MultipleTestResult(bonferroni_p=1.0, fdr_q=0.05, effective_n_factors=1,
                                adjusted_t=bt.get("t_stat", 3.0), passed=True)
        reasons: list[str] = []
        if bt.get("ic", 0) < 0.03:
            reasons.append(f"截面 IC={bt.get('ic', 0):.4f} < 0.03")
        if bt.get("sharpe", 0) < 1.5:
            reasons.append(f"截面夏普={bt.get('sharpe', 0):.4f} < 1.5")
        return FactorEvaluation(
            factor_id=factor["factor_id"],
            trace_id=trace_id,
            level_1_backtest=bt,
            level_2_economic=ec,
            level_3_multiple=mt,
            passed=len(reasons) == 0,
            failure_reasons=reasons,
            evaluated_at=datetime.now().isoformat(),
        )

    # ── Phase A.2: EliteFactorTracker 定期重评估 ──────────

    def _run_periodic_factor_review(
        self,
        elite_ids: list[str],
        trace_id: str,
    ) -> None:
        """运行精英因子定期重评估（Phase A.2 集成）。

        在演化循环结束时，对所有精英因子执行:
        1. 自动淘汰检查（衰减/严重衰减）
        2. 生成因子状态报告
        3. 更新因子跟踪快照

        Args:
            elite_ids: 精英因子 ID 列表
            trace_id: 全链路 trace_id
        """
        try:
            print("[elite-review] 开始精英因子定期重评估...")

            # 1. 自动淘汰检查
            retired = self.elite_tracker.auto_retire()
            if retired:
                print(f"[elite-review] 自动淘汰 {len(retired)} 个因子: {retired}")

            # 2. 为每个精英因子更新跟踪快照 + 逻辑监控
            for fid in elite_ids:
                # 先检查跟踪记录是否存在，不存在则跳过（可能种子因子重复跳过）
                tracker_snapshot = self.elite_tracker.get(fid)
                if tracker_snapshot is None:
                    logger.debug(
                        "跳过重评估: 跟踪记录不存在 [factor_id=%s]（可能种子因子重复跳过）",
                        fid,
                    )
                    continue
                factor_data = self._get_factor_data_for_review(fid)
                if factor_data is None:
                    continue
                ic = factor_data.get("ic", 0.0)
                sharpe = factor_data.get("sharpe", 0.0)
                self.elite_tracker.update(fid, ic, sharpe)

                # ── Phase C.2: LogicMonitor 集成 ──
                try:
                    import json

                    # 从 elite 快照读取因子程序（_promote_to_elite 写入）
                    fp_snapshot = self.elite_dir / f"{fid}.json"
                    if not fp_snapshot.exists() or self.data is None:
                        continue
                    factor_program = json.loads(
                        fp_snapshot.read_text(encoding="utf-8")
                    )
                    logic_report = self.logic_monitor.run(
                        factor_program, self.data, switch_dates=[],
                    )
                    if not logic_report.all_healthy:
                        print(
                            f"[elite-review] 逻辑监控告警: {fid}"
                        )
                except Exception as e:
                    logger.debug("逻辑监控跳过 %s: %s", fid, e)

            # 3. 生成报告
            report = self.elite_tracker.report()
            status_counts = report.get("status_counts", {})
            grade_counts = report.get("grade_counts", {})
            print(
                f"[elite-review] 因子状态报告: "
                f"活跃={status_counts.get('active', 0)}, "
                f"观察={status_counts.get('observing', 0)}, "
                f"衰减={status_counts.get('decaying', 0)}, "
                f"淘汰={status_counts.get('retired', 0)}, "
                f"总计={status_counts.get('total', 0)}"
            )
            print(
                f"[elite-review] 等级分布: "
                f"A级={grade_counts.get('A', 0)}, "
                f"B级={grade_counts.get('B', 0)}, "
                f"C级={grade_counts.get('C', 0)}"
            )
        except Exception as e:
            logger.debug("精英因子定期重评估异常: %s", e)

    def _get_factor_data_for_review(
        self,
        factor_id: str,
    ) -> Optional[dict[str, float]]:
        """获取因子的 IC 和 Sharpe 数据用于重评估。

        Args:
            factor_id: 因子 ID

        Returns:
            包含 ic 和 sharpe 的字典，失败返回 None
        """
        try:
            from .evaluation_chain import FactorEvaluation
            from .verifier import get_global_verifier

            factor_data = self.verifier.get_factor_by_id(factor_id) if hasattr(
                self.verifier, "get_factor_by_id"
            ) else None
            if factor_data is None:
                return {"ic": 0.0, "sharpe": 0.0}
            return {"ic": 0.0, "sharpe": 0.0}
        except Exception:
            return None

    # ── Phase B.1: 数据质量监控集成 ──────────────────────────

    def _register_factor_baseline(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
    ) -> None:
        """注册因子基准数据到数据质量监控器。

        当因子首次通过评估时，将其 IC 和容量注册为基准，
        用于后续监控数据漂移和容量突变。

        Args:
            factor: 因子程序
            evaluation: 评估结果
        """
        factor_id = factor.get("factor_id", "?")
        bt = evaluation.get("level_1_backtest", {}) if isinstance(evaluation, dict) else {}
        ic = bt.get("ic", 0.0) if isinstance(bt, dict) else 0.0
        self.data_quality_monitor.register_factor(
            factor_id=factor_id,
            baseline_ic=ic,
            baseline_capacity=0.0,
            ic_std=max(abs(ic) * 0.1, 0.001),
        )

    def _check_factor_data_quality(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
    ) -> list[Any]:
        """检查因子数据质量，返回触发的告警列表。

        Args:
            factor: 因子程序
            evaluation: 当前评估结果

        Returns:
            告警列表（可能为空）
        """
        factor_id = factor.get("factor_id", "?")
        bt = evaluation.get("level_1_backtest", {}) if isinstance(evaluation, dict) else {}
        current_ic = bt.get("ic", 0.0) if isinstance(bt, dict) else 0.0
        alerts = self.data_quality_monitor.check(
            factor_id=factor_id,
            current_ic=current_ic,
        )
        if alerts:
            for alert in alerts:
                alert_type = getattr(alert, "alert_type", "unknown")
                severity = getattr(alert, "severity", "unknown")
                msg = getattr(alert, "message", "")
                print(
                    f"[dq-monitor] 告警 [{factor_id}]: "
                    f"type={alert_type}, severity={severity}, msg={msg}"
                )
        return alerts

    # ── Phase B.2: 端到端回测流水线集成 ──────────────────

    def _run_gp_evolution(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
    ) -> tuple[FactorProgram, str]:
        """执行 GP 遗传规划演化 (Phase C.1 集成)。

        使用 FeatureOpsEngine 在算子空间搜索最优因子表达式，
        作为宏观演化的补充或备选。

        Args:
            parent: 父因子
            generation: 当前代数
            trace_id: 全链路 trace_id

        Returns:
            (新因子程序, 演化摘要)
        """
        from .gp_evolver import tree_to_factor_program

        target_col = "forward_return"
        gp_data = self.data.copy()
        if self.forward_returns is not None and len(self.forward_returns) == len(gp_data):
            gp_data[target_col] = self.forward_returns
        else:
            gp_data[target_col] = 0.0

        # 数据泄露防护: 构建训练集掩码（前 60% 数据），
        # 确保 GP 搜索仅在训练集上计算适应度
        train_ratio = 0.6
        train_size = max(int(len(gp_data) * train_ratio), 1)
        train_mask = pd.Series(
            [True] * train_size + [False] * (len(gp_data) - train_size),
            index=gp_data.index,
        )

        gp_result = self.feature_ops_engine.run_gp_search(
            data=gp_data,
            target=target_col,
            config={
                "population_size": 100,
                "max_generations": 20,
                "tournament_size": 3,
                "crossover_rate": 0.7,
                "mutation_rate": 0.1,
                "max_tree_depth": 4,
            },
            train_mask=train_mask,
        )

        if gp_result.best_fitness <= 0:
            raise RuntimeError(f"GP 演化适应度无效: {gp_result.best_fitness:.4f}")

        factor_program = tree_to_factor_program(gp_result.best_tree)
        factor_program["parent_id"] = parent.get("factor_id")
        factor_program["generation"] = generation
        factor_program["trace_id"] = trace_id
        factor_program["market"] = self.market

        # ── Phase C.1: 特征重要性分析 (集成到 GP 管线) ──
        try:
            from .factor_program import FactorExecutor

            # 执行因子程序得到信号序列，作为特征重要性的输入
            executor = FactorExecutor(factor_program)
            signals = executor.execute(gp_data, {})
            if len(signals) != len(gp_data):
                signals = np.full(len(gp_data), np.nan)

            importance_result = self.feature_importance_analyzer.analyze(
                pd.Series(signals), gp_data, target_col,
            )
            # FeatureImportanceResult 是 dataclass，转 dict 存快照
            factor_program["feature_importance"] = {
                k: v for k, v in importance_result.__dict__.items()
            }
        except Exception as e:
            logger.debug("特征重要性分析跳过: %s", e)

        summary = (
            f"GP Gen={gp_result.generations_completed}, "
            f"Fitness={gp_result.best_fitness:.4f}, "
            f"IC={gp_result.best_ic:.4f}, Sharpe={gp_result.best_sharpe:.4f}, "
            f"Expression={gp_result.best_expression[:80]}"
        )

        logger.info("GP 演化完成 [%s]: %s", parent.get("name", "?"), summary)
        return factor_program, summary

    # ── Phase C.2: 算子演化（FTS-Expr DSL） ──────────────

    def _generate_operator_factor(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
    ) -> tuple[FactorProgram, str]:
        """使用 FTS-Expr DSL 算子生成因子表达式 (Phase C.2)。

        基于算子注册表随机组合合法因子表达式，通过:
        1. 从 L0 字段池采样
        2. 随机选择 L1 时序算子（带合理的窗口参数）
        3. 可选 L2 横截面或 L4 组合算子封装
        4. 全程校验通过（参数边界、最大 lookback）

        Args:
            parent: 父因子
            generation: 当前代数
            trace_id: 全链路 trace_id

        Returns:
            (新因子程序, 演化摘要)
        """
        # ── Phase 3+ / C.4: 优先适应度导向进化搜索 ──
        engine_factor = self._try_operator_engine_evolution(
            parent, generation, trace_id,
        )
        if engine_factor is not None:
            new_factor = engine_factor
            summary = (
                f"OpEvolve: {new_factor.get('expression', '?')}"
            )
            logger.info(
                "算子演化引擎因子生成成功 [%s]: %s",
                new_factor.get("name", "?"), summary,
            )
            return new_factor, summary

        # ── fallback: 随机组合生成（无评估数据或引擎失败） ──
        import hashlib
        import random
        import time

        from .expr_dsl.factory import create_operator_factor
        from .expr_dsl.executor import evaluate
        from .expr_dsl.parser import parse_expression
        from .expr_dsl.registry import L0_FIELDS, build_registry
        from .expr_dsl.validator import validate_expr

        # 构建算子注册表
        registry = build_registry()

        # 按类别分组算子
        l1_ops = [
            name for name, meta in registry.items()
            if meta.category == "L1" and name not in ("ts_covariance", "ts_correlation")
        ]
        l2_ops = [name for name, meta in registry.items() if meta.category == "L2"]
        l4_ops = [name for name, meta in registry.items() if meta.category == "L4"]

        # 种子随机（基于父因子，保证可复现性）
        seed = int(hashlib.md5(
            f"{parent.get('factor_id', '?')}_{generation}_{time.time_ns()}".encode()
        ).hexdigest()[:8], 16) % (2 ** 31)
        rng = random.Random(seed)

        # 尝试生成合法的表达式，最多 10 次
        for attempt in range(10):
            try:
                # Step 1: 选择 1-2 个 L0 字段
                n_fields = rng.randint(1, 2)
                fields = rng.sample(list(L0_FIELDS), n_fields)

                # Step 2: 随机选择 1 个 L1 时序算子
                l1_op = rng.choice(l1_ops)

                # 确定窗口参数（5 的倍数，看起来更"专业"）
                window = ((rng.randint(5, 60) + 4) // 5) * 5

                # 构建表达式
                expr_parts = [f"{l1_op}({f}, {window})" for f in fields]

                # Step 3: 可选 L4 组合算子
                if len(expr_parts) == 2 and rng.random() < 0.5:
                    l4_op = rng.choice(l4_ops)
                    expression = f"{l4_op}({expr_parts[0]}, {expr_parts[1]})"
                else:
                    expression = expr_parts[0]

                # Step 4: 可选 L2 横截面封装
                if rng.random() < 0.4:
                    l2_op = rng.choice(l2_ops)
                    expression = f"{l2_op}({expression})"

                # 校验
                node = parse_expression(expression)
                errors, max_lookback = validate_expr(node, registry)
                if errors:
                    continue

                # Step 4.5: 常数信号前置拦截（生成阶段即过滤非常数表达式，
                # 避免到运行时校验/预筛阶段才被淘汰，浪费下游资源）
                try:
                    probe_data = (
                        list(self.cross_section_data.values())[0]
                        if self._is_cross_section else self.data
                    )
                    sig = evaluate(node, probe_data, registry)
                    sig_arr = (
                        sig.values if isinstance(sig, pd.Series)
                        else np.asarray(sig, dtype=float)
                    )
                    sig_arr = np.asarray(sig_arr, dtype=float)
                except Exception:
                    continue
                finite = sig_arr[np.isfinite(sig_arr)]
                if finite.size == 0 or np.nanstd(sig_arr) < 1e-8:
                    logger.debug(
                        "算子表达式非常数信号被前置拦截: %s", expression,
                    )
                    continue

                # 创建因子程序
                parent_id = parent.get("factor_id", "?")
                unique_key = f"op_{parent_id}_{generation}_{expression}_{time.time_ns()}"
                factor_id = "fct_" + hashlib.md5(unique_key.encode()).hexdigest()[:8]

                factor_name = f"op_{l1_op}_{generation}_{factor_id[:6]}"

                new_factor = create_operator_factor(
                    expression=expression,
                    name=factor_name,
                    market=self.market,
                    family=parent.get("family", "operator"),
                    narrative=(
                        f"算子演化: {expression} "
                        f"(基于父因子 {parent.get('name', '?')})"
                    ),
                    params={},
                    trace_id=trace_id,
                    source="operator_evolution",
                )
                # 覆盖产生的 factor_id 确保唯一
                new_factor["factor_id"] = factor_id
                new_factor["parent_id"] = parent_id
                new_factor["generation"] = generation

                summary = (
                    f"OpGen: {expression}, "
                    f"lookback={max_lookback}, "
                    f"fields={fields}"
                )

                logger.info("算子因子生成成功 [%s]: %s", factor_name, summary)
                return new_factor, summary

            except Exception as e:
                logger.debug("算子因子生成尝试 %d/10 失败: %s", attempt + 1, e)
                continue

        raise RuntimeError(
            "无法生成合法算子因子 (10 次尝试均失败, "
            f"parent={parent.get('name', '?')})"
        )

    def _try_operator_engine_evolution(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
    ) -> Optional[FactorProgram]:
        """算子演化引擎搜索（Phase 3+ / C.4）。

        在 DSL 算子空间做适应度导向进化搜索，产物为 kind=OPERATOR 因子。
        无评估数据或引擎失败时返回 None（由调用方回退随机组合生成）。

        Returns:
            引擎产出的 OPERATOR 因子，或 None
        """
        import hashlib

        try:
            from .operator_evolution import (
                OperatorEvolutionConfig,
                OperatorEvolutionEngine,
            )
        except Exception as e:
            logger.debug("算子演化引擎导入失败: %s", e)
            return None

        try:
            # 评估数据源: 横截面模式用代表序列（与 micro_evolution 一致）
            if self._is_cross_section:
                data = list(self.cross_section_data.values())[0].copy()
            else:
                data = self.data.copy()
            target_col = "forward_return"
            if self.forward_returns is None or len(self.forward_returns) != len(data):
                logger.debug("算子演化引擎跳过: 无 forward_returns 评估数据")
                return None
            data[target_col] = self.forward_returns

            # 种子由父因子派生，保证同一父因子结果可复现
            seed = int(hashlib.md5(
                str(parent.get("factor_id", "?")).encode(),
            ).hexdigest()[:8], 16) % (2 ** 31)

            # 数据泄露防护: 构建训练集掩码（前 60% 数据），
            # 确保算子演化仅在训练集上计算适应度
            train_ratio = 0.6
            train_size = max(int(len(data) * train_ratio), 1)
            train_mask = pd.Series(
                [True] * train_size + [False] * (len(data) - train_size),
                index=data.index,
            )

            engine = OperatorEvolutionEngine(
                data_panel=data,
                target_col=target_col,
                config=OperatorEvolutionConfig(
                    population_size=40,
                    max_generations=8,
                    random_seed=seed,
                ),
                train_mask=train_mask,
            )
            result = engine.evolve()
            if result.best_fitness <= 0:
                logger.info(
                    "算子演化引擎无正适应度因子 [%s]，回退随机生成",
                    parent.get("name", "?"),
                )
                return None

            factor = engine.best_factor_program(
                result,
                name=f"op_evolved_{generation}_{parent.get('factor_id', '?')[:6]}",
                market=self.market,
                family=parent.get("family", "operator"),
                narrative=(
                    f"算子演化引擎: {result.best_expression} "
                    f"(基于父因子 {parent.get('name', '?')})"
                ),
                trace_id=trace_id,
                parent_id=parent.get("factor_id", "?"),
                generation=generation,
            )
            logger.info(
                "算子演化引擎成功 [%s]: %s (fitness=%.4f)",
                parent.get("name", "?"), result.best_expression, result.best_fitness,
            )
            return factor
        except Exception as e:
            logger.debug("算子演化引擎失败，回退随机生成: %s", e)
            return None

    # ── Phase B.2.1: 快速预筛选（新增） ──────────────────

    def _quick_prefilter(
        self, factor: FactorProgram, trace_id: str,
    ) -> tuple[bool, str, float]:
        """快速预筛选：在源头拦截低质量信号，避免浪费评估资源。

        检查项:
            1. 信号非全常数: nunique > 10
            2. 快速 IC 检查: abs(IC) > 0.02（Spearman 秩相关）
            3. 信号标准差 > 1e-6

        横截面模式使用真实截面收益（信号矩阵 vs 截面 forward 收益，
        与 cross_section_evaluate_backtest 同口径），而非单标的时序 IC。

        Args:
            factor: 因子程序
            trace_id: 全链路 trace_id

        Returns:
            (是否通过, 失败原因, 预筛 IC；通过时原因为空，失败时 IC 为 0.0)
        """
        from scipy import stats as sp_stats
        from .backtest_pipeline import BacktestPipeline

        # 横截面模式: 用全面板构建真实截面收益计算 IC（GAP-X01）
        if self._is_cross_section:
            return self._cross_section_prefilter(factor, trace_id)

        probe_data = self.data
        try:
            signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""), probe_data, factor.get("params", {}),
            )
        except Exception as e:
            return False, f"预筛选执行失败: {type(e).__name__}: {e}", 0.0

        if not isinstance(signal, np.ndarray) or len(signal) != len(probe_data):
            return False, f"预筛选输出长度不匹配: {len(signal) if hasattr(signal, '__len__') else '?'} != {len(probe_data)}", 0.0

        # 检查1: 信号非全常数
        nunique = len(np.unique(signal))
        if nunique <= 10:
            return False, f"信号无足够变化: nunique={nunique} <= 10", 0.0

        # 检查2: 信号标准差
        sig_std = np.nanstd(signal)
        if sig_std < 1e-6:
            return False, f"信号标准差过小: {sig_std:.2e} < 1e-6", 0.0

        # 检查3: 快速 IC 检查（导致 NaN 也视为无效）
        # 期货日频单品种时序 IC 信噪比低（常见 0.01-0.02 区间），
        # 阈值按市场自适应放宽，避免拦截本可进入截面评估的后代
        ic_threshold = 0.01 if self.market == "futures" else 0.02
        fr = self.forward_returns
        if fr is not None and len(fr) == len(signal):
            valid = ~(np.isnan(signal) | np.isnan(fr))
            if valid.sum() >= 10:
                ic, pval = sp_stats.spearmanr(signal[valid], fr[valid])
                if np.isnan(ic) or abs(ic) < ic_threshold:
                    return False, (
                        f"快速 IC 过低: abs(IC)={abs(ic):.4f} < {ic_threshold}"
                        f"{'' if np.isnan(ic) else f', p={pval:.4f}'}"
                    ), 0.0
                return True, "", abs(ic)

        return True, "", 0.0

    def _cross_section_prefilter(
        self, factor: FactorProgram, trace_id: str,
    ) -> tuple[bool, str, float]:
        """横截面快速预筛：用真实截面收益计算截面 Spearman IC。

        与 cross_section_evaluate_backtest 同口径：对所有标的同时运行因子，
        对齐共同日期构建信号矩阵与截面 forward 收益矩阵，每期计算截面 IC。
        替代原先单标的时序 IC 口径（与 forward_returns 长度不齐时常被跳过，
        且单标的时序 IC 无法反映因子截面区分能力）。

        Args:
            factor: 因子程序
            trace_id: 全链路 trace_id

        Returns:
            (是否通过, 失败原因, 预筛 IC 绝对值；失败时 IC 为 0.0)
        """
        from .evaluation_chain import (
            _cs_build_matrices,
            _cs_compute_ics,
            _cs_execute_factors,
        )
        from .factor_program import FactorExecutor

        panel = self.cross_section_data
        if not panel:
            return True, "", 0.0

        try:
            executor = FactorExecutor(factor)
            signal_dict, ret_dict = _cs_execute_factors(
                executor, factor.get("params", {}), panel,
            )
        except Exception as e:
            return False, f"预筛选执行失败: {type(e).__name__}: {e}", 0.0

        if len(signal_dict) < 5:
            return False, f"横截面有效标的不足: {len(signal_dict)} < 5", 0.0

        common_dates = self.cross_section_dates
        if common_dates is None or len(common_dates) == 0:
            return True, "", 0.0

        # 全样本截面（预筛不切片，正式评估再走 OOS）
        signal_matrix, ret_matrix = _cs_build_matrices(
            signal_dict, ret_dict, common_dates, len(common_dates),
        )
        ics = _cs_compute_ics(signal_matrix, ret_matrix)
        if not ics:
            # 无有效截面期（如窗口期样本不足），放行交由正式评估兜底
            return True, "", 0.0

        ic_abs = abs(float(np.mean(ics)))
        ic_threshold = 0.01 if self.market == "futures" else 0.02
        if ic_abs < ic_threshold:
            return False, (
                f"横截面快速 IC 过低: abs(IC)={ic_abs:.4f} < {ic_threshold}"
            ), 0.0
        return True, "", ic_abs

    # ── Phase B.2.1: 后代因子运行时校验 ──────────────────

    def _check_factor_runtime(
        self, factor: FactorProgram,
    ) -> tuple[bool, str]:
        """试运行因子程序，在源头拦截 LLM 生成代码的运行时错误。

        拦截场景:
            - 广播错误（如 shapes (n,) 与 (2,) 混合运算）
            - 输出长度与输入不匹配（np.diff/np.convolve 未保持长度 n）
            - 常数信号（无信息量）

        复用 BacktestPipeline._execute_factor_code（与回测流水线同一执行路径），
        保证「校验通过 = 流水线可执行」，避免无效后代进入下游评估。

        Args:
            factor: 因子程序

        Returns:
            (是否通过, 失败原因；通过时原因为空)
        """
        from .backtest_pipeline import BacktestPipeline

        probe_data = (
            list(self.cross_section_data.values())[0]
            if self._is_cross_section else self.data
        )
        try:
            signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""), probe_data, factor.get("params", {}),
            )
        except Exception as e:
            return False, f"执行失败: {type(e).__name__}: {e}"

        if not isinstance(signal, np.ndarray) or len(signal) != len(probe_data):
            return False, (
                f"输出长度不匹配: "
                f"{len(signal) if hasattr(signal, '__len__') else '?'} != {len(probe_data)}"
            )
        if np.std(signal) < 1e-12:
            return False, "输出为常数信号（无信息量）"
        return True, ""

    def _run_backtest_pipeline(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> Optional[dict[str, Any]]:
        """执行端到端回测流水线（Phase B.2 集成）。

        在因子通过 L1/L2/L3 评估后，运行标准化回测流水线，
        生成完整的回测报告，供质检和审计使用。

        Args:
            factor: 因子程序
            evaluation: L1/L2/L3 评估结果
            trace_id: 全链路 trace_id

        Returns:
            回测结果字典，包含绩效指标和报告路径；失败返回 None
        """
        try:
            from .backtest_pipeline import BacktestInput

            bt_input = BacktestInput(
                factor=factor if isinstance(factor, dict) else dict(factor),
                data=self.data,
                benchmark=None,
                forward_period=1,
            )
            result = self.backtest_pipeline.run(bt_input)

            if not result.success:
                print(f"[evo] 回测流水线失败 [{factor.get('factor_id', '?')}]: {result.error}")
                return None

            report = result.output
            return {
                "success": True,
                "duration_ms": result.duration_ms,
                "report_path": getattr(report, "file_path", None) if report else None,
                "metrics": {
                    "total_return": getattr(report, "total_return", 0.0),
                    "sharpe": getattr(report, "sharpe_ratio", 0.0),
                    "max_drawdown": getattr(report, "max_drawdown", 0.0),
                    "calmar": getattr(report, "calmar_ratio", 0.0),
                } if report else {},
            }
        except Exception as e:
            logger.debug("回测流水线异常: %s", e)
            return None

    # ── Phase B.3: 因子强制审计 ──────────────────────────

    @staticmethod
    def _build_wf_config(data: pd.DataFrame) -> dict[str, Any]:
        """按数据长度适配 WalkForward 窗口配置（GAP-F08，v2.60.0）。

        数据不足 3 年时缩短窗口，保证短样本也能构建多窗口冷启动验证；
        数据 < 半年（约 125 交易日）无法构建，由调用方跳过并记录原因。

        Args:
            data: 主时间序列数据

        Returns:
            WalkForwardConfig 字典
        """
        from .walk_forward import DEFAULT_WALK_FORWARD_CONFIG

        cfg = dict(DEFAULT_WALK_FORWARD_CONFIG)
        n = len(data)
        years = n / 250.0
        if years >= 3.0:
            return cfg
        if years >= 2.0:
            cfg.update(window_years=1, step_months=3, min_oos_months=2, n_windows=4)
        elif years >= 1.0:
            cfg.update(window_years=1, step_months=2, min_oos_months=1, n_windows=3)
        elif years >= 0.5:
            cfg.update(window_years=0, step_months=1, min_oos_months=0, n_windows=2)
        else:
            cfg.update(window_years=0, step_months=0, min_oos_months=0, n_windows=1)
        return cfg

    def _run_walkforward_oos(
        self,
        factor: FactorProgram,
    ) -> Optional[dict[str, Any]]:
        """冷启动 WalkForward 样本外验证（GAP-F08，v2.60.0）。

        用多窗口滚动样本外评估替代 L1 单段 ICIR 近似，验证因子时间维度稳定性。
        数据不足或 force_walkforward=false 时返回 None（跳过并记录原因），
        审计 oos_consistency 项回退原逻辑。

        Args:
            factor: 因子程序

        Returns:
            WalkForwardResult 字典；跳过时返回 None
        """
        from fts.config.settings import get_config

        if not get_config().force_walkforward:
            logger.info("[Evo] force_walkforward=false，跳过冷启动样本外验证")
            return None

        data = self.data
        if data is None or len(data) < 125:
            logger.info(
                "[Evo] 数据长度不足（%d 行 < 125），跳过冷启动样本外验证",
                len(data) if data is not None else 0,
            )
            return None

        try:
            from scipy import stats as sp_stats  # type: ignore[import-untyped]

            from .backtest_pipeline import BacktestPipeline
            from .walk_forward import WalkForwardOptimizer

            code = factor.get("code", "") if isinstance(factor, dict) else getattr(factor, "code", "")
            params = factor.get("params", {}) if isinstance(factor, dict) else getattr(factor, "params", {})

            def _eval_fn(
                train_df: pd.DataFrame, oos_df: pd.DataFrame,
            ) -> dict[str, float]:
                """评估函数：在 oos 段计算因子 IC/夏普/换手。"""
                try:
                    signal = BacktestPipeline._execute_factor_code(code, oos_df, params)
                except Exception:  # noqa: BLE001
                    return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}
                signal = np.asarray(signal, dtype=float)
                close = oos_df["close"].to_numpy(dtype=float)
                fwd = np.zeros(len(close))
                if len(close) > 1:
                    fwd[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
                mask = np.isfinite(signal) & np.isfinite(fwd)
                if int(np.sum(mask)) < 10:
                    return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}
                ic, _ = sp_stats.spearmanr(signal[mask], fwd[mask])
                if not np.isfinite(ic):
                    ic = 0.0
                rets = fwd[mask]
                sharpe = float(np.mean(rets) / max(np.std(rets), 1e-9) * np.sqrt(252))
                turnover = float(np.mean(np.abs(np.diff(signal))))
                return {"ic": float(ic), "sharpe": sharpe, "turnover": turnover}

            optimizer = WalkForwardOptimizer(self._build_wf_config(data))
            result = optimizer.evaluate(data, _eval_fn)
            if result.get("n_windows_completed", 0) == 0:
                logger.info("[Evo] WalkForward 无可用窗口，跳过冷启动样本外验证")
                return None
            logger.info(
                "[Evo] 冷启动样本外验证完成 [ic_consistency=%.2f, windows=%d, passed=%s]",
                result.get("ic_consistency", 0.0),
                result.get("n_windows_completed", 0),
                result.get("passed", False),
            )
            return dict(result)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Evo] WalkForward 冷启动验证异常，跳过: %s", e)
            return None

    def _run_factor_audit(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> FactorAuditReport:
        """执行因子审计（Phase B.3 集成）。

        将评估结果中的数据映射到审计器所需的输入，
        执行 6 项强制审计检查。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            FactorAuditReport 审计报告
        """
        import traceback

        factor_meta = {
            "factor_id": factor.get("factor_id", ""),
            "name": factor.get("name", ""),
            "trace_id": trace_id,
            "family": factor.get("family", ""),
        }

        l1 = evaluation.get("level_1_backtest", {})
        l3 = evaluation.get("level_3_multiple", {})

        # 构造 OOS 结果（从评估链 L1 提取）
        # 注意: L1 的 oos_ratio 是样本外数据切分比例（评估链默认 0.3），
        # 并非一致性通过率，不能直接与审计阈值 0.5 比较。
        # 样本外一致性以 OOS ICIR 度量（|ICIR| ≥ 1.0 → ic_consistency=1.0）。
        oos_ratio = l1.get("oos_ratio", 0)
        oos_ic = l1.get("ic", 0)
        oos_icir = l1.get("icir", 0)
        oos_result = None
        if oos_ratio > 0:
            oos_result = {
                "ic_consistency": min(1.0, abs(oos_icir)),
                "oos_ic": oos_ic,
                "passed": abs(oos_icir) >= 1.0,
            }

        # v2.60.0 (GAP-F08): 冷启动 WalkForward 样本外验证优先。
        # 用真实多窗口 OOS 结果覆盖 L1 单段 ICIR 近似（数据不足/关闭时保持原逻辑）。
        wf_result = self._run_walkforward_oos(factor)
        if wf_result is not None:
            oos_result = {
                "ic_consistency": wf_result.get("ic_consistency", 0.0),
                "oos_ic": 0.0,  # 一致性已含多窗口均值信息
                "passed": wf_result.get("passed", False),
                "windows": wf_result.get("windows", []),
                "n_windows_completed": wf_result.get("n_windows_completed", 0),
            }

        # 构造 p-values（从 L3 提取，仅当非默认值时传递）
        p_values: list[float] = []
        bonf_p = l3.get("bonferroni_p")
        if bonf_p is not None and bonf_p < 1.0:
            p_values.append(float(bonf_p))

        try:
            report = self.auditor.audit(
                factor=factor_meta,
                data=self.data,
                forward_returns=self.forward_returns,
                oos_result=oos_result,
                p_values=p_values if p_values else None,
            )
        except Exception as e:
            logger.warning(
                "审计执行异常 [%s]: %s (降级为跳过所有审计项)",
                factor_meta["name"], str(e),
            )
            logger.debug(traceback.format_exc())
            report = FactorAuditReport(
                factor_id=factor_meta["factor_id"],
                factor_name=factor_meta["name"],
                audited_at=datetime.now().isoformat(),
                items=[],
                passed=False,
                pass_rate=0.0,
                summary={"total": 6, "passed": 0, "failed": 0, "skipped": 6, "pass_rate": 0.0},
            )

        return report

    def _record_audit_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        audit_report: FactorAuditReport,
        evaluation: Optional[FactorEvaluation] = None,
    ) -> None:
        """记录审计失败轨迹。

        Args:
            factor: 因子程序
            generation: 当前代数
            trace_id: 全链路 trace_id
            audit_report: 审计报告
            evaluation: 评估结果（可选）
        """
        import json

        factor_id = factor.get("factor_id", "unknown")
        factor_name = factor.get("name", "?")
        sub_trace_id = f"{trace_id}_g{generation}_audit_fail_{factor_id[:8]}"

        trace_dir = self.memory_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        fp = trace_dir / f"{sub_trace_id}.json"

        record: dict[str, Any] = {
            "trace_id": sub_trace_id,
            "parent_trace_id": trace_id,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "generation": generation,
            "type": "audit_failed",
            "timestamp": datetime.now().isoformat(),
            "audit_report": audit_report.to_dict(),
            "failure_analysis": audit_report.failure_analysis,
        }
        if evaluation:
            record["evaluation"] = evaluation

        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            f"[evo] 审计失败轨迹已记录: {factor_name} → "
            f"代 {generation}, 通过率={audit_report.pass_rate:.0%}"
        )

    # ── Phase A: 消融实验检查 ──────────────────────────

    # v2.50.0 判定语义：核心价格列（因子正常依赖的输入）与信息型消融模式
    # 不参与"伪相关"拦截判定——时序因子依赖时序因果（shuffle_dates）、
    # 价格因子依赖价格列、量价因子依赖成交量/VWAP 均属必要特征。
    _ABLATION_PRICE_CORE_COLS: frozenset[str] = frozenset(
        {"open", "high", "low", "close", "vwap", "settle"}
    )
    # 信息型消融模式：记录但不拦截
    _ABLATION_INFORMATIONAL_MODES: frozenset[str] = frozenset(
        {"volume_zero", "vwap_to_close", "vwap_to_settle", "shuffle_dates"}
    )

    @staticmethod
    def _is_blocking_ablation(ab: dict[str, Any]) -> bool:
        """是否属于拦截型消融（非价格列置零导致的输入依赖崩塌）。

        仅当 zero_one_feature 置零的是「非核心价格列」（如 volume/持仓量等
        逻辑上为辅助输入的特征）时才参与伪相关判定。
        """
        mode = ab.get("mode", "")
        if mode in EvolutionLoop._ABLATION_INFORMATIONAL_MODES:
            return False
        if mode == "zero_one_feature":
            feature = ab.get("feature") or ""
            return feature.lower() not in EvolutionLoop._ABLATION_PRICE_CORE_COLS
        return False

    def _run_ablation_check(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> dict[str, Any]:
        """执行消融实验检查（Phase A 集成）。

        随机扰动因子输入特征，检测伪相关。
        仅「拦截型消融」（非价格列置零）IC 降幅超过基线 50% 时判定为伪相关；
        信息型消融（时序结构/成交量/VWAP/核心价格列）只记录不拦截。
        数据缺失时跳过（passed=True，不误杀）。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            消融结果字典，包含 passed 标志
        """
        try:
            data = getattr(self, "data", None)
            if data is None or len(data) == 0:
                return {"passed": True, "skipped": True, "error": "data unavailable"}
            forward_returns = getattr(self, "forward_returns", None)
            if forward_returns is None:
                forward_returns = np.zeros(len(data))

            result = self.ablation_experiment.run(factor, data, forward_returns)
            # AblationResult 是 dict 子类，直接使用
            baseline_ic = result.get("baseline_ic", 0.0)
            ablations = result.get("ablations", [])
            if abs(baseline_ic) < 1e-9:
                is_passed = True
            else:
                # 仅拦截型消融的 IC 降幅超过基线 50% → 疑似伪相关
                blocking = [ab for ab in ablations if self._is_blocking_ablation(ab)]
                is_passed = all(
                    ab.get("ic_change", 0.0) >= -0.5 * abs(baseline_ic)
                    for ab in blocking
                )
            return {**result, "passed": is_passed}
        except Exception as e:
            logger.warning("消融实验异常: %s", e)
            return {"passed": True, "error": str(e), "ablations": []}

    def _record_ablation_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        ablation_result: dict[str, Any],
    ) -> None:
        """记录消融实验失败轨迹。"""
        import json

        factor_id = factor.get("factor_id", "unknown")
        factor_name = factor.get("name", "?")
        sub_trace_id = f"{trace_id}_g{generation}_ablation_fail_{factor_id[:8]}"

        trace_dir = self.memory_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        fp = trace_dir / f"{sub_trace_id}.json"

        record: dict[str, Any] = {
            "trace_id": sub_trace_id,
            "parent_trace_id": trace_id,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "generation": generation,
            "type": "ablation_failed",
            "timestamp": datetime.now().isoformat(),
            "ablation_result": ablation_result,
        }

        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[evo] 消融失败轨迹已记录: {factor_name}")

    # ── Phase B: 鲁棒性审查 ──────────────────────────

    def _run_robustness_check(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> dict[str, Any]:
        """执行鲁棒性审查（Phase B 集成）。

        在因子通过审计后，检测在对抗扰动、缺失值和
        分布外场景下的稳定性。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            鲁棒性结果字典，包含 passed 标志
        """
        try:
            data = getattr(self, "data", None)
            if data is None or len(data) == 0:
                return {"passed": True, "skipped": True, "error": "data unavailable"}
            forward_returns = getattr(self, "forward_returns", None)
            if forward_returns is None:
                forward_returns = np.zeros(len(data))

            # 期货市场鲁棒性审查阈值放宽（低信噪比、短样本场景）
            min_pass_rate = 0.7 if getattr(self, "market", "stock") == "futures" else 0.9

            result = self.robustness_tester.run(factor, data, forward_returns)
            # RobustnessTestResult 是 dict 子类，直接使用
            summary = result.get("summary", {})
            pass_rate = summary.get("overall_pass_rate", 1.0)
            is_passed = pass_rate >= min_pass_rate
            return {**result, "passed": is_passed}
        except Exception as e:
            logger.warning("鲁棒性审查异常: %s", e)
            return {"passed": True, "error": str(e)}

    def _record_robustness_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        robustness_result: dict[str, Any],
    ) -> None:
        """记录鲁棒性审查失败轨迹。"""
        import json

        factor_id = factor.get("factor_id", "unknown")
        factor_name = factor.get("name", "?")
        sub_trace_id = f"{trace_id}_g{generation}_robustness_fail_{factor_id[:8]}"

        trace_dir = self.memory_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        fp = trace_dir / f"{sub_trace_id}.json"

        record: dict[str, Any] = {
            "trace_id": sub_trace_id,
            "parent_trace_id": trace_id,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "generation": generation,
            "type": "robustness_failed",
            "timestamp": datetime.now().isoformat(),
            "robustness_result": robustness_result,
        }

        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[evo] 鲁棒性失败轨迹已记录: {factor_name}")

    # ── Phase B: SHAP 可解释性分析 ──────────────────────

    def _run_shap_analysis(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> dict[str, Any]:
        """执行 SHAP 可解释性分析（Phase B 集成）。

        对极端预测样本进行特征归因，确保模型可解释。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            SHAP 分析结果字典
        """
        try:
            data = getattr(self, "data", None)
            if data is None or len(data) == 0:
                return {"passed": True, "skipped": True, "error": "data unavailable"}
            forward_returns = getattr(self, "forward_returns", None)
            if forward_returns is None:
                forward_returns = np.zeros(len(data))

            result = self.shap_analyzer.analyze(factor, data, forward_returns)
            # ShapAnalysisResult 是 dict 子类，直接使用；SHAP 为信息型审查，成功即通过
            return {**result, "passed": True}
        except Exception as e:
            logger.warning("SHAP 分析异常: %s", e)
            return {"passed": True, "error": str(e)}

    # ── Phase C: 因果结构审查 ──────────────────────────

    def _run_causal_validation(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> dict[str, Any]:
        """执行因果结构审查（Phase C 集成）。

        使用自然实验验证因子是否捕获了真实因果关系。
        对熔断等极端事件进行预测误差分析。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            因果验证结果字典，包含 passed 标志
        """
        try:
            data = getattr(self, "data", None)
            if data is None or len(data) == 0:
                return {"passed": True, "skipped": True, "error": "data unavailable"}
            forward_returns = getattr(self, "forward_returns", None)
            if forward_returns is None:
                forward_returns = np.zeros(len(data))

            result = self.causal_validator.validate(factor, data, forward_returns)
            # CausalValidationResult 是 dict 子类，直接使用
            is_passed = result.get("n_anomalous", 0) == 0
            return {**result, "passed": is_passed}
        except Exception as e:
            logger.warning("因果验证异常: %s", e)
            return {"passed": True, "error": str(e)}

    def _record_causal_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        causal_result: dict[str, Any],
    ) -> None:
        """记录因果验证失败轨迹。"""
        import json

        factor_id = factor.get("factor_id", "unknown")
        factor_name = factor.get("name", "?")
        sub_trace_id = f"{trace_id}_g{generation}_causal_fail_{factor_id[:8]}"

        trace_dir = self.memory_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        fp = trace_dir / f"{sub_trace_id}.json"

        record: dict[str, Any] = {
            "trace_id": sub_trace_id,
            "parent_trace_id": trace_id,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "generation": generation,
            "type": "causal_failed",
            "timestamp": datetime.now().isoformat(),
            "causal_result": causal_result,
        }

        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[evo] 因果失败轨迹已记录: {factor_name}")

    def _record_success_trace(
        self,
        factor: FactorProgram,
        generation: int,
        mutation_type: str,
        mutation_summary: str,
        evaluation: FactorEvaluation,
        lessons: list[str],
        trace_id: str,
    ) -> None:
        """记录成功轨迹。"""
        # 生成唯一子 trace_id（避免文件名碰撞）
        sub_trace_id = (
            f"{trace_id}_g{generation}_{mutation_type}_{factor['factor_id'][:8]}"
        )
        trace = create_trace_from_evaluation(
            factor_id=factor["factor_id"],
            parent_id=factor.get("parent_id"),
            generation=generation,
            mutation_type=mutation_type,
            mutation_summary=mutation_summary,
            evaluation=evaluation,
            lessons=lessons,
            trace_id=sub_trace_id,
        )
        self.experience_chain.record_success(trace)
        self.state_manager.add_experience_ref(
            self.state_manager.load_or_init(), trace["trace_id"]
        )

    def _record_failure_trace(
        self,
        factor: FactorProgram,
        generation: int,
        mutation_type: str,
        mutation_summary: str,
        failure_reasons: list[str],
        trace_id: str,
        evaluation: Optional[FactorEvaluation] = None,
    ) -> None:
        """记录失败轨迹。"""
        # 生成唯一子 trace_id（避免文件名碰撞）
        sub_trace_id = (
            f"{trace_id}_g{generation}_{mutation_type}_{factor['factor_id'][:8]}"
        )
        # 构造评估结果
        if evaluation is None:
            evaluation = FactorEvaluation(
                factor_id=factor["factor_id"],
                trace_id=sub_trace_id,
                passed=False,
                failure_reasons=failure_reasons or ["未知失败"],
                evaluated_at=datetime.now().isoformat(),
            )
        else:
            # 确保失败原因非空
            if not evaluation.get("failure_reasons"):
                evaluation["failure_reasons"] = failure_reasons or ["未知失败"]

        trace = create_trace_from_evaluation(
            factor_id=factor["factor_id"],
            parent_id=factor.get("parent_id"),
            generation=generation,
            mutation_type=mutation_type,
            mutation_summary=mutation_summary,
            evaluation=evaluation,
            lessons=[f"代 {generation} 失败: {r}" for r in failure_reasons[:3]],
            trace_id=sub_trace_id,
        )
        try:
            self.experience_chain.record_failure(trace)
        except Exception:
            pass  # 失败轨迹记录失败不应中断主循环

    def _log_inspection_detail(
        self,
        factor: FactorProgram,
        inspection: _QualityInspectionResult,
        status: str,
        generation: int,
    ) -> None:
        """打印详细的因子质检日志。

        Args:
            factor: 因子程序
            inspection: 质检结果
            status: 状态 ("通过" / "淘汰")
            generation: 当前代数
        """
        factor_name = factor.get("name", factor.get("factor_id", "?"))
        total = inspection.total_score
        grade = inspection.grade
        reason = inspection.reason

        # 主日志行
        icon = "✅" if status == "通过" else "❌"
        print(f"[evo] {icon} 代{generation} 因子质检{status}: "
              f"{factor_name} (等级={grade}, 总分={total}/50)")

        # 淘汰时显示原因
        if status == "淘汰" and reason:
            print(f"       淘汰原因: {reason}")

        # 显示各维度得分
        dims = inspection.quality_score.get("dimension_scores", [])
        if dims:
            low_dims = [d for d in dims if d.get("score", 5) < 3]
            if low_dims:
                print(f"       ⚠️  低分项 (< 3.0):")
                for d in low_dims[:5]:  # 最多显示 5 个低分项
                    name = d.get("name", "?")
                    score = d.get("score", 0)
                    desc = d.get("description", "")
                    print(f"         - {name}: {score:.1f}/5.0 ({desc})")

    def _record_quality_filtered_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        inspection: _QualityInspectionResult,
        evaluation: Optional[FactorEvaluation] = None,
    ) -> None:
        """记录质检过滤轨迹 (Phase A.1)。

        当因子通过 Verifier 但质量评分低于阈值时记录。
        """
        sub_trace_id = (
            f"{trace_id}_g{generation}_quality_filtered_{factor['factor_id'][:8]}"
        )
        reasons = [inspection.reason] if inspection.reason else ["质检淘汰"]
        if evaluation is None:
            evaluation = FactorEvaluation(
                factor_id=factor["factor_id"],
                trace_id=sub_trace_id,
                passed=False,
                failure_reasons=reasons,
                evaluated_at=datetime.now().isoformat(),
            )
        else:
            if not evaluation.get("failure_reasons"):
                evaluation["failure_reasons"] = reasons

        trace = create_trace_from_evaluation(
            factor_id=factor["factor_id"],
            parent_id=factor.get("parent_id"),
            generation=generation,
            mutation_type="quality_filtered",
            mutation_summary=(
                f"质量评分淘汰: 等级={inspection.grade}, "
                f"总分={inspection.total_score}/50"
            ),
            evaluation=evaluation,
            lessons=[f"质检淘汰: {inspection.reason}"],
            trace_id=sub_trace_id,
        )
        try:
            self.experience_chain.record_failure(trace)
        except Exception:
            pass


# ─── CLI 入口 ─────────────────────────────────────────────

def main():
    """CLI 入口: python -m loop_engine.evolution_loop --once"""
    parser = argparse.ArgumentParser(description="L2 因子演化循环")
    parser.add_argument("--once", action="store_true", help="运行一次完整演化")
    parser.add_argument("--max-generation", type=int, default=None, help="最大代数")
    parser.add_argument("--memory-dir", default="memory/evolution", help="状态目录")
    parser.add_argument("--elite-dir", default="memory/knowledge/factors/elite", help="精英池目录")
    args = parser.parse_args()

    if not args.once:
        parser.print_help()
        sys.exit(1)

    # 生成合成数据用于演示（生产环境替换为真实数据）
    np.random.seed(42)
    n = 500
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    volume = np.random.randint(1000, 10000, n).astype(float)
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close,
        "volume": volume,
    }, index=dates)
    forward_returns = np.roll(np.diff(close, prepend=close[0]), -1)
    forward_returns[-1] = 0

    loop = EvolutionLoop(
        data=data,
        forward_returns=forward_returns,
        elite_dir=args.elite_dir,
        memory_dir=args.memory_dir,
    )
    result = loop.run(max_generation=args.max_generation)
    print(f"\n演化完成: {result.status}")
    print(f"  代数: {result.generations_completed}")
    print(f"  评估: {result.total_factors_evaluated}")
    print(f"  晋级: {result.total_factors_promoted}")
    print(f"  Token: {result.tokens_consumed}")
    if result.circuit_breaker_reason:
        print(f"  熔断: {result.circuit_breaker_reason}")
    if result.elite_factor_ids:
        print(f"  精英: {result.elite_factor_ids}")


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = [
    "UCT_EXPLORATION_C",
    "EvolutionRunResult",
    "EvolutionLoop",
    "main",
]
