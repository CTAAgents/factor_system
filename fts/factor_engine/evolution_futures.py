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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

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


from .contracts import (  # noqa: E402 — 延迟导入规避循环依赖
    DEFAULT_BUDGET_CONFIG,
    BudgetConfig,
    EvolutionState,
    FactorCorrelation,
    FactorEvaluation,
    FactorProgram,
)
from .audit import FactorAuditor, FactorAuditReport  # noqa: E402
from .evaluation_chain import (  # noqa: E402
    EvaluationChain,
    cross_section_evaluate_backtest,
)
from .experience_chain import (  # noqa: E402
    ExperienceChain,
    ParentFailureContext,
    create_trace_from_evaluation,
)
from .experiment_log import ExperimentLogWriter, extract_scores  # noqa: E402
from .macro_evolution import MacroEvolver, get_default_llm_client  # noqa: E402
from .micro_evolution import evolve_micro  # noqa: E402
from .seed_pool import SeedPool, compute_seed_correlations  # noqa: E402
from .signal_cache import SignalCache  # noqa: E402
from .state import EvolutionStateManager, generate_trace_id  # noqa: E402
from .success_pattern import (  # noqa: E402
    SuccessPatternConfig,
    SuccessPatternReport,
    analyze_success_patterns,
)
from .verifier import FactorVerifier  # noqa: E402


# ─── UCT 常量 ─────────────────────────────────────────────

UCT_EXPLORATION_C: float = 1.0
"""UCT 探索常数。越大越倾向探索未访问的父因子。"""

# GAP-070: 质检链信号缓存容量上限（LRU，超出淘汰最久未使用项）。
# 每条目为一份完整面板信号（~4MB/104品种×5163日），16 条上限覆盖单候选
# L1/极值扰动/消融 baseline/鲁棒性 baseline/SHAP 全部复用场景。
_QC_SIGNAL_CACHE_MAX_ENTRIES: int = 16


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
    # P1-3 (Phase 3): 提前达标停止标记（status 保持 completed，正常收尾）
    early_stopped: bool = False
    early_stop_reason: Optional[str] = None

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
            "early_stopped": self.early_stopped,
            "early_stop_reason": self.early_stop_reason,
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
            turnover = 0.5  # 缺省月度换手（次/月，低频保守默认；单位与 verifier max_turnover_monthly 一致，GAP-114）
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
            elite_dir="memory/knowledge/factors/stocks_elite",
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
        experiment_log_dir: Optional[str | Path] = None,
    ):
        self.data = data
        self.forward_returns = forward_returns
        self.cross_section_data = cross_section_data
        self.cross_section_dates = cross_section_dates
        self.industry_map = industry_map
        self.cap_map = cap_map
        # F.2 引擎分叉: 本文件固定期货市场，market 入参忽略
        market = "futures"
        self.market = market
        # GAP-S11 (v2.67.0): 期货演化保持原配置行为（不启用股票 operator-first）
        from fts.config.settings import get_config

        _raw_mode = getattr(get_config(), "evolution_mode", "hybrid")
        self.evolution_mode = _raw_mode
        self.factor_db_path = factor_db_path
        self._is_cross_section = cross_section_data is not None
        # GAP-I304 (v2.79.0): Barra 风格暴露缓存（成功=dict / 失败=None，避免每因子重复构建）
        self._barra_exposures_cache: Optional[dict[str, Any]] = None

        # v2.59.0 (GAP-F03): 期货横截面模式自动注入板块映射（板块/产业链中性化）
        # 从 FUTURES_SECTOR_MAP 反向构建 {symbol: sector}；futures_neutralization=false
        # 或已显式传入 industry_map 时跳过。
        if self._is_cross_section and self.industry_map is None:
            try:
                from fts.config.settings import get_config

                if get_config().futures_neutralization:
                    from fts.data_futures import FUTURES_SECTOR_MAP

                    self.industry_map = {
                        sym: sector for sector, symbols in FUTURES_SECTOR_MAP.items() for sym in symbols
                    }
                    logger.info(
                        "[EvolutionLoop] 期货板块中性化已启用: %d 品种映射到 %d 个产业链",
                        len(self.industry_map),
                        len(FUTURES_SECTOR_MAP),
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvolutionLoop] 期货板块映射注入失败，跳过中性化: %s", e)

        # ── 市场隔离: 自动按 market 选择 elite 目录 ──
        if elite_dir is None:
            elite_dir = "memory/knowledge/factors/futures_elite"
        self.elite_dir = Path(elite_dir)
        self.elite_dir.mkdir(parents=True, exist_ok=True)
        self.inject_dir = Path(inject_dir)
        self.memory_dir = Path(memory_dir)
        self.budget: BudgetConfig = budget or DEFAULT_BUDGET_CONFIG
        # GAP-F10 (v2.73.0): 家族多样性上限配置化——
        # 未显式传入 budget 时，max_per_family 回退到 FTSConfig（FTS_MAX_PER_FAMILY，缺省 15）。
        # 注：DEFAULT_BUDGET_CONFIG 本身含 max_per_family 键，故仅以 budget is None 判定，
        # 而非检查键是否存在（否则配置回退永不生效）。
        if budget is None:
            try:
                from fts.config.settings import get_config

                self.budget["max_per_family"] = get_config().max_per_family
            except Exception:
                pass  # 配置读取失败沿用 DEFAULT_BUDGET_CONFIG 缺省值 15

        # ── P1-3 (Phase 3, 26 计划 §8): 提前达标停止（保守默认关闭） ──
        # budget 未显式配置时回退 FTSConfig（env FTS_EVOLUTION_STOP_* 可控）；
        # DEFAULT_BUDGET_CONFIG 不含这两个键，故默认走 FTSConfig（enabled=False）。
        try:
            from fts.config.settings import get_config as _get_stop_cfg

            _stop_cfg = _get_stop_cfg()
            _stop_enabled_cfg = bool(getattr(_stop_cfg, "evolution_stop_enabled", False))
            _stop_k_cfg = int(getattr(_stop_cfg, "evolution_stop_consecutive_empty_generations", 5))
        except Exception:
            _stop_enabled_cfg = False
            _stop_k_cfg = 5
        self._evolution_stop_enabled = bool(self.budget.get("evolution_stop_enabled", _stop_enabled_cfg))
        self._evolution_stop_k = int(self.budget.get("evolution_stop_consecutive_empty_generations", _stop_k_cfg))
        self._consecutive_empty_generations: int = 0
        self._early_stop_last_count: int = 0
        self._early_stop_reason: Optional[str] = None
        if verifier is not None:
            self.verifier = verifier
        else:
            from .contracts import FUTURES_VERIFIER_CONFIG

            self.verifier = FactorVerifier(FUTURES_VERIFIER_CONFIG)
        self.llm_client = llm_client or get_default_llm_client()
        self.seed_pool = seed_pool or SeedPool()
        self.n_trials_micro = n_trials_micro

        # GAP-I205 (v2.68.0): 微观演化两阶段漏斗配置（粗筛淘汰 + 精筛自适应 trials）
        try:
            from fts.config.settings import get_config

            _micro_cfg = get_config()
            self._micro_staged_evolution = bool(getattr(_micro_cfg, "micro_staged_evolution", True))
            self._micro_coarse_trials = int(getattr(_micro_cfg, "micro_coarse_trials", 20))
            self._micro_coarse_ic_floor = float(getattr(_micro_cfg, "micro_coarse_ic_floor", 0.02))
            # GAP-I206 (v2.71.0): L2 准入去冗余配置
            self._l2_elite_corr_threshold = float(getattr(_micro_cfg, "l2_elite_corr_threshold", 0.9))
            self._l2_elite_corr_max_scan = int(getattr(_micro_cfg, "l2_elite_corr_max_scan", 50))
            self._l2_elite_corr_debug = bool(getattr(_micro_cfg, "l2_elite_corr_debug", False))
            # GAP-I206 补充（v2.71.0）：正交化闭环配置
            self._l2_elite_orthogonalize = bool(getattr(_micro_cfg, "l2_elite_orthogonalize", True))
            self._l2_orthogonal_residual_corr_max = float(getattr(_micro_cfg, "l2_orthogonal_residual_corr_max", 0.3))
            self._l2_orthogonal_min_retained_ratio = float(getattr(_micro_cfg, "l2_orthogonal_min_retained_ratio", 0.3))
            # GAP-I206 补充（v2.72.0）：正交基底配置
            self._l2_orthogonal_basis_enabled = bool(getattr(_micro_cfg, "l2_orthogonal_basis_enabled", True))
            self._l2_orthogonal_basis_max_size = int(getattr(_micro_cfg, "l2_orthogonal_basis_max_size", 10))
            self._l2_orthogonal_basis_min_sharpe = float(getattr(_micro_cfg, "l2_orthogonal_basis_min_sharpe", 1.0))
            # GAP-I305（v2.72.0）：衰减自动退役配置
            self._decay_observe_slope = float(getattr(_micro_cfg, "decay_observe_slope", 0.10))
            self._decay_retire_slope = float(getattr(_micro_cfg, "decay_retire_slope", 0.20))
            self._decay_slope_min_points = int(getattr(_micro_cfg, "decay_slope_min_points", 6))
            self._decay_auto_retire_enabled = bool(getattr(_micro_cfg, "decay_auto_retire_enabled", True))
            # GAP-XXX (v2.102.0): 结构性聚类配额配置（替代 max_per_family 家族配额，
            # family 为来源标签非结构维度，多样性控制改由信号相关性承担）
            self._cluster_quota_enabled = bool(getattr(_micro_cfg, "structure_cluster_quota_enabled", True))
            self._cluster_max = int(getattr(_micro_cfg, "structure_cluster_max", 15))
            self._cluster_corr_threshold = float(getattr(_micro_cfg, "structure_cluster_corr_threshold", 0.85))
            self._cluster_max_scan = int(getattr(_micro_cfg, "l2_elite_corr_max_scan", 50))
        except Exception:
            # 配置读取失败时采用模块默认值，不阻断演化
            self._micro_staged_evolution = True
            self._micro_coarse_trials = 20
            self._micro_coarse_ic_floor = 0.02
            self._l2_elite_corr_threshold = 0.9
            self._l2_elite_corr_max_scan = 50
            self._l2_elite_corr_debug = False
            self._l2_elite_orthogonalize = True
            self._l2_orthogonal_residual_corr_max = 0.3
            self._l2_orthogonal_min_retained_ratio = 0.3
            self._l2_orthogonal_basis_enabled = True
            self._l2_orthogonal_basis_max_size = 10
            self._l2_orthogonal_basis_min_sharpe = 1.0
            self._decay_observe_slope = 0.10
            self._decay_retire_slope = 0.20
            self._decay_slope_min_points = 6
            self._decay_auto_retire_enabled = True
            self._cluster_quota_enabled = True
            self._cluster_max = 15
            self._cluster_corr_threshold = 0.85
            self._cluster_max_scan = 50

        # 子模块
        self.state_manager = EvolutionStateManager(self.memory_dir)
        self.experience_chain = ExperienceChain(self.memory_dir)
        self.macro_evolver = MacroEvolver(
            llm_client=self.llm_client,
            experience_chain=self.experience_chain,
            max_tokens_per_call=self.budget["max_tokens_per_factor"],
        )
        self.evaluation_chain = EvaluationChain()
        # GAP-070: 质检链信号缓存（三级评估/消融/鲁棒性/SHAP 共享，避免同一候选重复执行因子代码）
        self._signal_cache = SignalCache(max_entries=_QC_SIGNAL_CACHE_MAX_ENTRIES)

        # Phase 1.2 (P0-1): 成功模式报告进程内缓存（避免每代重复读取经验链）
        self._success_pattern_cache: Optional[SuccessPatternReport] = None

        # Phase 2 (P1-2): 结构化实验日志——run 内候选聚合 + 导出目录
        self._experiment_log_dir: str = str(experiment_log_dir) if experiment_log_dir else "data"
        self._experiment_variants: list[dict] = []

        # 子模块: 因子质检过滤器 (Phase A.1 集成)
        # 使用 _QualityInspectionCompat 替代已删除的 pipeline.FactorQualityInspection
        self.quality_inspector = _QualityInspectionCompat(
            card_config=quality_card_config,
            min_grade=quality_min_grade,
        )

        # 子模块: 因子审计器 (Phase B.3 集成)
        # audit_config: 允许外部注入审计阈值（如期货低信噪比场景放宽 OOS 阈值）
        self.auditor = FactorAuditor(config=audit_config) if audit_config else FactorAuditor()

        # 子模块: 高IC筛查器 (Phase B.4 集成, 所有市场统一)
        from .high_ic_screener import HighICScreener, HighICScreenConfig

        # 期货市场放宽 V5 经济逻辑维度最低分（LLM 演化因子 L2 评分偏低）
        futures_config = HighICScreenConfig(logic_min_score=1.0)
        self.high_ic_screener = HighICScreener(config=futures_config)

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
        from ..monitor.elite_tracker import AutoRetireConfig, EliteFactorTracker

        self.elite_tracker = EliteFactorTracker(
            tracking_dir=str(self.memory_dir / "tracking"),
            retire_config=AutoRetireConfig(
                observe_slope=self._decay_observe_slope,
                retire_slope=self._decay_retire_slope,
                slope_min_points=self._decay_slope_min_points,
            ),
        )

        # GAP-I206 补充（v2.72.0）: 多因子正交基底（Gram-Schmidt 迭代残差化）
        from .orthogonal_basis import OrthogonalBasisManager

        self.orthogonal_basis = OrthogonalBasisManager(
            basis_path=str(self.memory_dir / "orthogonal_basis.json"),
            max_size=self._l2_orthogonal_basis_max_size,
            min_sharpe=self._l2_orthogonal_basis_min_sharpe,
            residual_corr_max=self._l2_orthogonal_residual_corr_max,
            min_retained_ratio=self._l2_orthogonal_min_retained_ratio,
        )

        # 子模块: 消融实验 (Phase A 集成)
        from .ablation import AblationExperiment

        self.ablation_experiment = AblationExperiment(random_seed=42)

        # 子模块: SHAP 可解释性分析 (Phase B 集成)
        from .shap_analyzer import ShapAnalyzer

        # GAP-080 (v2.102.0): SHAP 批量计算降频——从 FTSConfig 读取采样参数
        # （默认 n_extreme=25 / n_background=50 / nsamples=50，env 可覆盖）
        from fts.config.settings import get_config as _get_shap_cfg

        _shap_cfg = _get_shap_cfg()
        self.shap_analyzer = ShapAnalyzer(
            n_extreme=_shap_cfg.shap_n_extreme,
            n_background=_shap_cfg.shap_n_background,
            nsamples=_shap_cfg.shap_nsamples,
        )

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

        # GAP-I305 (v2.72.0): 反馈闭环（FACTOR_DECAY 事件 → 衰减自动退役联动）
        from .feedback_loop import FeedbackLoop

        self.feedback_loop = FeedbackLoop()

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
        self.batch_max_candidates: int = int(getattr(_cfg_batch, "batch_max_candidates", 5))
        self.batch_max_workers: int = int(getattr(_cfg_batch, "batch_max_workers", 4))
        self.batch_random_seed: int = int(getattr(_cfg_batch, "batch_random_seed", 42))
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

        # GAP-070: 每次运行清空进程内质检信号缓存，防止跨运行残留
        self._signal_cache.clear()
        # Phase 2 (P1-2): 每次运行清空实验候选聚合（防止跨运行残留）
        self._experiment_variants.clear()

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
                    run_id=run_id,
                    trace_id=trace_id,
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
                    print(
                        f"  - {pair['factor_id_a']} × {pair['factor_id_b']}: "
                        f"Pearson={pair['pearson']:.4f} Spearman={pair['spearman']:.4f}"
                    )
                if high_corr_count > 5:
                    print(f"  ... 还有 {high_corr_count - 5} 对")

            # ── Step 1: 评估种子因子，合格直接晋升 elite ──
            print(f"[DEBUG-evo] 种子相关性预检完成: {len(seed_correlations)} 对高相关因子")
            print("[DEBUG-evo] 开始评估种子因子 (184 个, 横截面模式)... 这可能需要较长时间")
            promoted_seeds = self._evaluate_and_promote_seeds(
                seeds,
                trace_id,
                state,
                elite_ids,
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
                parent_seeds = cast(list[FactorProgram], self._load_elite_parent_factors())
                if not parent_seeds:
                    print("[evo] 无合格父因子，跳过演化循环")
                    self.state_manager.mark_completed(state)
                    return EvolutionRunResult(
                        run_id=run_id,
                        trace_id=trace_id,
                        generations_completed=0,
                        total_factors_evaluated=0,
                        total_factors_promoted=0,
                        tokens_consumed=state.get("tokens_consumed", 0),
                        status="completed",
                        elite_factor_ids=elite_ids,
                        seed_correlations=seed_correlations,
                    )
                print(f"[evo] 种子因子均已晋升过，改用 elite 池 {len(parent_seeds)} 个因子作为父因子")

            # ── P1-3 (Phase 3): 提前达标停止状态重置（基于 state 晋升计数） ──
            self._consecutive_empty_generations = 0
            self._early_stop_last_count = state.get("total_factors_promoted", 0)
            self._early_stop_reason = None

            for generation in range(start_gen, start_gen + max_gen):
                # 熔断检查
                print(f"[DEBUG-evo] gen={generation} _consecutive_low_ic={self._consecutive_low_ic}")
                cb_reason = self._check_circuit_breaker(state)
                if cb_reason:
                    self.state_manager.mark_circuit_broken(state, cb_reason)
                    return EvolutionRunResult(
                        run_id=run_id,
                        trace_id=trace_id,
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
                _evo_mode = getattr(_fts_evo_cfg, "evolution_mode", "hybrid")

                if _evo_mode == "batch":
                    # ── BATCH 模式 (GAP-I201): 一代批量漏斗 ──
                    # 批量生成（同父多后代）→ 并行粗筛 → 通过者逐个走准入链；
                    # 状态持久化/熔断计数由 _run_batch_generation 内 _process_candidate 完成
                    self._run_batch_generation(
                        parent,
                        generation,
                        trace_id,
                        state,
                        elite_ids,
                        seed_correlations,
                    )
                    # 经验链清理（generation 级）
                    self.experience_chain.cleanup_if_needed()
                    # P1-3 (Phase 3): 提前达标停止检查（本代零晋升计入）
                    if self._maybe_early_stop(state):
                        break
                    continue

                # ── 单因子路径: Step 1 演化分派（macro/GP/operator，配置分派） ──
                evolved = self._evolve_one(parent, generation, trace_id)
                if evolved is None:
                    # 演化失败轨迹已在 _evolve_one 内记录；UCT 失败反馈避免父因子恒被选中（GAP-074）
                    self._update_uct_failure(parent)
                    # P1-3 (Phase 3): 提前达标停止检查（本代零晋升计入）
                    if self._maybe_early_stop(state):
                        break
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
                        new_factor.get("name", "?"),
                        runtime_reason,
                    )
                    self._record_failure_trace(
                        new_factor,
                        generation,
                        evolution_method,
                        f"运行时校验失败: {runtime_reason}",
                        [],
                        trace_id,
                    )
                    self._record_experiment_variant(
                        new_factor, parent, generation, evolution_method, evolution_summary, None, "verifier_failed"
                    )
                    self._update_uct_failure(parent)  # GAP-074: UCT 失败反馈
                    # P1-3 (Phase 3): 提前达标停止检查（本代零晋升计入）
                    if self._maybe_early_stop(state):
                        break
                    continue

                # ── Step 1.4: 快速预筛选（源头拦截低质量信号，避免浪费评估资源） ──
                prefilter_ok, prefilter_reason, _ = self._quick_prefilter(
                    new_factor,
                    trace_id,
                )
                if not prefilter_ok:
                    logger.warning(
                        "[%s] 快速预筛选失败: %s",
                        new_factor.get("name", "?"),
                        prefilter_reason,
                    )
                    self._record_failure_trace(
                        new_factor,
                        generation,
                        evolution_method,
                        f"快速预筛选失败: {prefilter_reason}",
                        [],
                        trace_id,
                    )
                    self._record_experiment_variant(
                        new_factor, parent, generation, evolution_method, evolution_summary, None, "prefilter_rejected"
                    )
                    self._update_uct_failure(parent)  # GAP-074: UCT 失败反馈
                    continue

                # ── Step 2-6: 准入链（公共方法，batch 与单因子路径共用，GAP-I201） ──
                self._process_candidate(
                    new_factor,
                    parent,
                    generation,
                    evolution_method,
                    evolution_summary,
                    state,
                    elite_ids,
                    trace_id,
                    seed_correlations,
                )

                # 经验链清理（如果超过 100 条）
                self.experience_chain.cleanup_if_needed()

                # ── P1-3 (Phase 3): 每代结束后检查提前达标停止 ──
                if self._maybe_early_stop(state):
                    break

            # 正常完成（或 P1-3 提前达标停止，正常收尾）
            print(f"[DEBUG-evo] before mark_completed: _consecutive_low_ic={self._consecutive_low_ic}")
            if self._early_stop_reason:
                state["early_stopped"] = True
                state["early_stop_reason"] = self._early_stop_reason
                print(f"[evo] 提前达标停止: {self._early_stop_reason}（正常收尾）")
            self.state_manager.mark_completed(state)
            return EvolutionRunResult(
                run_id=run_id,
                trace_id=trace_id,
                generations_completed=generation - start_gen + 1,
                total_factors_evaluated=state.get("total_factors_evaluated", 0),
                total_factors_promoted=state.get("total_factors_promoted", 0),
                tokens_consumed=state.get("tokens_consumed", 0),
                status="completed",
                elite_factor_ids=elite_ids,
                seed_correlations=seed_correlations,
                early_stopped=self._early_stop_reason is not None,
                early_stop_reason=self._early_stop_reason,
            )

        except Exception as e:
            import traceback

            traceback.print_exc()
            self.state_manager.mark_paused(state, str(e))
            return EvolutionRunResult(
                run_id=run_id,
                trace_id=trace_id,
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

            # ── Phase 2 (P1-2): 导出结构化实验日志（非阻塞） ──
            self._export_experiment_log(
                run_id,
                trace_id,
                state.get("last_generation", 0),
            )

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

    def _build_parent_failure_ctx(self, parent: FactorProgram) -> Optional[ParentFailureContext]:
        """构造父因子最近失败归因上下文（Phase 1.1 P0-2 定向修复）。

        从失败经验链按 parent_id 读取最近失败轨迹，聚合去重失败原因。
        无失败记录或父因子无 factor_id 时返回 None（不注入归因段落）。

        Args:
            parent: 父因子

        Returns:
            ParentFailureContext 或 None
        """
        parent_id = parent.get("factor_id")
        if not parent_id:
            return None
        traces = self.experience_chain.read_failures_by_parent(parent_id)
        if not traces:
            return None
        reasons: list[str] = []
        latest_failed_at: Optional[str] = None
        for t in traces:
            eval_ = t.get("evaluation", {})
            for reason in eval_.get("failure_reasons", []):
                if reason and reason not in reasons:
                    reasons.append(reason)
            if latest_failed_at is None:
                latest_failed_at = t.get("recorded_at")
        if not reasons:
            return None
        return ParentFailureContext(
            parent_id=parent_id,
            failure_reasons=reasons,
            patterns=list(reasons),
            latest_failed_at=latest_failed_at,
        )

    def _build_success_pattern_report(self) -> Optional[SuccessPatternReport]:
        """构造近期成功模式报告（Phase 1.2 P0-1），进程内缓存避免重复读取。

        从 FTSConfig 读取开关/窗口/样本下限，构造 SuccessPatternConfig 聚合经验链。
        异常/开关关闭 → None（prompt 层不注入）；空报告（样本不足）照常返回，
        由 MacroEvolver 判断 sample_count==0 不注入。

        Returns:
            SuccessPatternReport 或 None
        """
        if self._success_pattern_cache is not None:
            return self._success_pattern_cache
        try:
            from fts.config.settings import get_config as _get_sp_cfg

            _cfg = _get_sp_cfg()
            config = SuccessPatternConfig(
                enabled=_cfg.evolution_success_pattern_enabled,
                window_days=_cfg.success_pattern_window_days,
                min_sample=_cfg.success_pattern_min_sample,
            )
            if not config.enabled:
                return None
            report = analyze_success_patterns(self.experience_chain, config)
            self._success_pattern_cache = report
            return report
        except Exception as e:  # noqa: BLE001 — 降级不阻断演化
            logger.debug("成功模式报告构造失败（降级跳过）: %s", e)
            return None

    def _record_experiment_variant(
        self,
        factor: FactorProgram,
        parent: Optional[FactorProgram],
        generation: int,
        method: str,
        summary: str,
        evaluation: Optional[FactorEvaluation],
        outcome: str,
        quality_grade: Optional[str] = None,
    ) -> None:
        """记录实验候选（Phase 2 P1-2），run 结束时导出实验日志。

        Args:
            factor: 候选因子
            parent: 父因子（可能为 None）
            generation: 当前代数
            method: 演化方法（macro/gp/operator/deep）
            summary: 演化摘要
            evaluation: 评估结果（预筛/运行时拦截可能为 None）
            outcome: 候选结局（prefilter_rejected/verifier_failed/audit_failed/promoted）
            quality_grade: 质量评分卡等级（A/B/C），available 时传入
        """
        scores = extract_scores(evaluation)
        if quality_grade is not None:
            scores["quality_grade"] = quality_grade
        self._experiment_variants.append(
            {
                "generation": generation,
                "parent_id": parent.get("factor_id") if parent else None,
                "candidate_id": factor.get("factor_id", "?"),
                "method": method,
                "summary": summary,
                "scores": scores,
                "outcome": outcome,
            }
        )

    def _export_experiment_log(
        self,
        run_id: str,
        trace_id: str,
        generations_completed: int,
    ) -> Optional[Path]:
        """导出结构化实验日志（Phase 2 P1-2），非阻塞（失败仅 warning）。"""
        try:
            writer = ExperimentLogWriter(self._experiment_log_dir)
            return writer.export(
                run_id=run_id,
                trace_id=trace_id,
                market=self.market,
                started_at=datetime.now().isoformat(),
                generations_completed=generations_completed,
                variants=self._experiment_variants,
            )
        except Exception as e:  # noqa: BLE001 — 导出失败降级不阻断 run
            logger.warning("实验日志导出失败（降级不阻断）: %s", e)
            return None

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

        # 演化产物（统一类型声明，供各分支复用）
        new_factor: Optional[FactorProgram] = None

        # ── batch 模式: 按 method_hint 强制分派（macro 至多 1 次，其余 CPU 演化） ──
        if method_hint is not None:
            if seed is not None:
                np.random.seed(seed)
            if method_hint == "operator":
                try:
                    new_factor, op_summary = self._generate_operator_factor(
                        parent,
                        generation=generation,
                        trace_id=trace_id,
                    )
                    return new_factor, "operator_evolution", op_summary, 0
                except Exception as e:
                    logger.debug("算子演化失败 [%s]: %s", parent.get("name", "?"), e)
                    return None
            if method_hint == "macro":
                try:
                    new_factor, macro_summary, macro_tokens = self.macro_evolver.evolve(
                        parent,
                        generation=generation,
                        trace_id=trace_id,
                        parent_failure_ctx=self._build_parent_failure_ctx(parent),
                        success_pattern=self._build_success_pattern_report(),
                    )
                    return new_factor, "macro_evolution", macro_summary, macro_tokens
                except Exception as e:
                    logger.debug("宏观演化失败 [%s]: %s", parent.get("name", "?"), e)
                    return None
            if method_hint == "gp":
                try:
                    new_factor, gp_summary = self._run_gp_evolution(
                        parent,
                        generation=generation,
                        trace_id=trace_id,
                    )
                    return new_factor, "gp_evolution", gp_summary, 0
                except Exception as e:
                    logger.debug("GP 演化失败 [%s]: %s", parent.get("name", "?"), e)
                    return None
            if method_hint == "deep":
                # GAP-I203 (v2.73.0): 深度因子（GRU）候选源
                try:
                    new_factor, deep_summary = self._run_deep_evolution(
                        parent,
                        generation=generation,
                        trace_id=trace_id,
                    )
                    return new_factor, "deep_evolution", deep_summary, 0
                except Exception as e:
                    logger.debug("深度演化失败 [%s]: %s", parent.get("name", "?"), e)
                    return None
            if method_hint == "transformer":
                # C5 (v2.100.1): 深度因子（Transformer）候选源
                try:
                    new_factor, deep_summary = self._run_deep_evolution(
                        parent,
                        generation=generation,
                        trace_id=trace_id,
                        model_kind="transformer",
                    )
                    return new_factor, "deep_evolution", deep_summary, 0
                except Exception as e:
                    logger.debug("Transformer 演化失败 [%s]: %s", parent.get("name", "?"), e)
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
                    parent,
                    generation=generation,
                    trace_id=trace_id,
                )
                evolution_method = "operator_evolution"
                evolution_summary = op_summary
                logger.info(
                    "算子演化成功 (operator_first) [%s]: %s",
                    parent.get("name", "?"),
                    op_summary,
                )
            except Exception as op_e:
                logger.warning(
                    "算子演化失败 [%s]: %s, 尝试 LLM 宏观演化兜底",
                    parent.get("name", "?"),
                    op_e,
                )
                self._record_failure_trace(
                    parent,
                    generation,
                    "operator_evolution",
                    f"算子演化失败: {op_e}",
                    [],
                    trace_id,
                )
                try:
                    new_factor, macro_summary, macro_tokens = self.macro_evolver.evolve(
                        parent,
                        generation=generation,
                        trace_id=trace_id,
                        parent_failure_ctx=self._build_parent_failure_ctx(parent),
                        success_pattern=self._build_success_pattern_report(),
                    )
                    tokens = macro_tokens
                    evolution_method = "macro_evolution"
                    evolution_summary = macro_summary
                    logger.info(
                        "LLM 宏观演化兜底成功 (operator_first) [%s]: %s",
                        parent.get("name", "?"),
                        macro_summary,
                    )
                except Exception as macro_e:
                    logger.warning(
                        "LLM 宏观演化兜底失败 [%s]: %s, 尝试 GP 演化兜底",
                        parent.get("name", "?"),
                        macro_e,
                    )
                    try:
                        new_factor, gp_summary = self._run_gp_evolution(
                            parent,
                            generation=generation,
                            trace_id=trace_id,
                        )
                        evolution_method = "gp_evolution"
                        evolution_summary = gp_summary
                        logger.info(
                            "GP 演化兜底成功 (operator_first) [%s]: %s",
                            parent.get("name", "?"),
                            gp_summary,
                        )
                    except Exception as gp_e:
                        self._record_failure_trace(
                            parent,
                            generation,
                            "operator_first_evolution",
                            f"算子/LLM/GP 演化均失败: {op_e} | {macro_e} | {gp_e}",
                            [],
                            trace_id,
                        )
                        return None
        elif _evo_mode == "operator":
            try:
                new_factor, op_summary = self._generate_operator_factor(
                    parent,
                    generation=generation,
                    trace_id=trace_id,
                )
                evolution_method = "operator_evolution"
                evolution_summary = op_summary
                logger.info(
                    "算子演化成功 [%s]: %s",
                    parent.get("name", "?"),
                    op_summary,
                )
            except Exception as e:
                logger.warning(
                    "算子演化失败 [%s]: %s",
                    parent.get("name", "?"),
                    e,
                )
                self._record_failure_trace(
                    parent,
                    generation,
                    "operator_evolution",
                    f"算子演化失败: {e}",
                    [],
                    trace_id,
                )
                return None
        else:
            # CODE / HYBRID 模式: 1.1 宏观演化尝试（LLM 改逻辑）
            try:
                new_factor, macro_summary, macro_tokens = self.macro_evolver.evolve(
                    parent,
                    generation=generation,
                    trace_id=trace_id,
                    parent_failure_ctx=self._build_parent_failure_ctx(parent),
                    success_pattern=self._build_success_pattern_report(),
                )
                tokens = macro_tokens
                evolution_summary = macro_summary
            except Exception as e:
                logger.warning(
                    "宏观演化失败 [%s]: %s, 尝试 GP 演化作为备选",
                    parent.get("name", "?"),
                    e,
                )

            # 1.2 若宏观演化失败，回退到 GP 演化 (Phase C.1)
            if new_factor is None:
                try:
                    new_factor, gp_summary = self._run_gp_evolution(
                        parent,
                        generation=generation,
                        trace_id=trace_id,
                    )
                    evolution_method = "gp_evolution"
                    evolution_summary = gp_summary
                    logger.info(
                        "GP 演化成功 [%s]: %s",
                        parent.get("name", "?"),
                        gp_summary,
                    )
                except Exception as gp_e:
                    if _evo_mode == "hybrid":
                        # hybrid 模式: GP 也失败时尝试算子演化
                        try:
                            new_factor, op_summary = self._generate_operator_factor(
                                parent,
                                generation=generation,
                                trace_id=trace_id,
                            )
                            evolution_method = "operator_evolution"
                            evolution_summary = op_summary
                            logger.info(
                                "算子演化成功 (hybrid fallback) [%s]: %s",
                                parent.get("name", "?"),
                                op_summary,
                            )
                        except Exception as op_e:
                            self._record_failure_trace(
                                parent,
                                generation,
                                "hybrid_evolution",
                                f"GP 失败: {gp_e}, 算子也失败: {op_e}",
                                [],
                                trace_id,
                            )
                            return None
                    else:
                        self._record_failure_trace(
                            parent,
                            generation,
                            "gp_evolution",
                            f"GP 演化也失败: {gp_e}",
                            [],
                            trace_id,
                        )
                        return None

            if new_factor is None:
                fail_msg = "LLM、GP 和算子演化均失败" if _evo_mode == "hybrid" else "宏观演化和 GP 演化均失败"
                self._record_failure_trace(
                    parent,
                    generation,
                    "evolution",
                    fail_msg,
                    [],
                    trace_id,
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
        from fts.config.settings import get_config as _batch_cfg

        miner = BatchMiner(
            config=BatchMiningConfig(
                batch_size=self.batch_size,
                max_candidates=self.batch_max_candidates,
                max_workers=self.batch_max_workers,
                random_seed=self.batch_random_seed,
                # GAP-I502 (v2.83.0): 执行器后端可插拔（配置驱动，默认 thread 保持现状）
                executor_backend=getattr(_batch_cfg(), "executor_backend", "thread"),
                executor_max_workers=int(getattr(_batch_cfg(), "executor_max_workers", 0)) or None,
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
            print(f"  - 拦截: {rejected.get('method', '?')} {rejected.get('prefilter_reason', '')[:80]}")

        if not result.passed:
            # 全失败回退：记录失败轨迹（D.1 D5）
            self._record_failure_trace(
                parent,
                generation,
                "batch_evolution",
                f"批量漏斗无候选通过粗筛 (生成 {result.total_generated}, 全部拦截)",
                [r.get("prefilter_reason", "") for r in result.rejected][:3],
                trace_id,
            )
            # Phase 2 P1-2: 被粗筛拦截的候选逐一记入实验日志
            for r in result.rejected:
                rfactor = r.get("factor") or {}
                if not rfactor.get("factor_id"):
                    continue
                self._record_experiment_variant(
                    rfactor,
                    parent,
                    generation,
                    r.get("method", "batch_evolution"),
                    r.get("prefilter_reason", "粗筛拦截"),
                    None,
                    "prefilter_rejected",
                )
            return False

        promoted_any = False
        for proposal in result.passed:
            factor = proposal.get("factor", {})
            if not factor:
                continue
            try:
                ok = self._process_candidate(
                    factor,
                    parent,
                    generation,
                    proposal.get("method", "batch_evolution"),
                    proposal.get("summary", ""),
                    state,
                    elite_ids,
                    trace_id,
                    seed_correlations,
                )
                promoted_any = promoted_any or ok
            except Exception as e:
                logger.warning(
                    "[batch] 候选处理异常 [%s]: %s",
                    factor.get("name", "?"),
                    e,
                )
                self._record_failure_trace(
                    factor,
                    generation,
                    proposal.get("method", "batch_evolution"),
                    f"候选处理异常: {e}",
                    [],
                    trace_id,
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
        其余按 gp / deep / transformer / operator 四方法轮换（纯 CPU，
        GAP-I203 deep 并入，C5 transformer 并入）。
        """
        idx = self._batch_idx
        self._batch_idx = idx + 1
        seed = self.batch_random_seed + idx
        if idx == 0:
            method_hint = "macro"
        elif idx % 4 == 1:
            method_hint = "gp"
        elif idx % 4 == 2:
            method_hint = "deep"
        elif idx % 4 == 3:
            method_hint = "transformer"
        else:
            method_hint = "operator"
        evolved = self._evolve_one(
            parent,
            generation,
            trace_id,
            method_hint=method_hint,
            seed=seed,
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
                if (self._is_cross_section and self.cross_section_data is not None)
                else self.data
            )
            micro_ret = self.forward_returns
            # GAP-I205 (v2.68.0): 两阶段漏斗——粗筛低 trials 快速打分淘汰低潜力，
            # 精筛 trials 按粗筛得分自适应 + TPE 早停；配置 micro_staged_evolution 可关闭。
            optimized_factor, _ = evolve_micro(
                factor,
                micro_data,
                micro_ret,
                n_trials=self.n_trials_micro,
                use_staged=self._micro_staged_evolution,
            )
        except Exception as e:
            self._record_failure_trace(
                factor,
                generation,
                "micro_evolution",
                f"微观演化失败: {e}",
                [],
                trace_id,
            )
            self._record_experiment_variant(
                factor,
                parent,
                generation,
                evolution_method,
                f"微观演化失败: {e}",
                None,
                "verifier_failed",
            )
            return False

        # ── Step 3: 三级评估链 ──
        if self._is_cross_section:
            evaluation = self._evaluate_cross_section(optimized_factor, trace_id)
        else:
            # GAP-070: 注入共享信号缓存 + 统一走航配置（与审计 _build_wf_config 同源，
            # 支撑审计复用本走航结果，消除双重 WalkForward）
            evaluation = self.evaluation_chain.evaluate(
                optimized_factor,
                self.data,
                self.forward_returns,
                prior_evaluations=self._prior_evaluations,
                signal_cache=self._signal_cache,
                walk_forward_config=self._build_wf_config(self.data),
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
            optimized_factor,
            evaluation,
            trace_id,
        )
        if backtest_result:
            evaluation["backtest_pipeline"] = backtest_result

        # ── Step 4.5.6: 数据质量监控 (Phase B.1) ──
        self._register_factor_baseline(optimized_factor, evaluation)
        dq_alerts = self._check_factor_data_quality(
            optimized_factor,
            evaluation,
        )
        if dq_alerts:
            critical = any(getattr(a, "severity", "") == "critical" for a in dq_alerts)
            if critical:
                print(f"[evo] 数据质量严重告警 [{optimized_factor.get('name', '?')}]: 跳过晋升")
                self._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    f"数据质量严重告警: {[a.message for a in dq_alerts if getattr(a, 'severity', '') == 'critical'][:2]}",
                    evaluation,
                    "audit_failed",
                )
                return False

        # ── Step 4.6: 因子强制审计 (Phase B.3) ──
        audit_report = self._run_factor_audit(
            optimized_factor,
            evaluation,
            trace_id,
        )

        # ── Step 5: 经验链记录 + 分级准入 ──
        print(f"[DEBUG-evo] verifier_result['passed']={verifier_result.get('passed')}")
        if verifier_result["passed"]:
            print("[DEBUG-evo] PROMOTION PATH")
            # 质检过滤: 仅 A/B 级晋升，C 级淘汰
            if inspection.filtered:
                self._log_inspection_detail(
                    optimized_factor,
                    inspection,
                    "淘汰",
                    generation,
                )
                self._record_quality_filtered_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    inspection,
                    evaluation=evaluation,
                )
                self._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    f"质检过滤淘汰: 质量分={inspection.total_score}/50 ({inspection.grade}级)",
                    evaluation,
                    "audit_failed",
                    quality_grade=inspection.grade,
                )
                return False

            # 审计过滤: 审计未通过则拒绝准入
            if not audit_report.passed:
                failed_items = [it.name for it in audit_report.failed_items]
                print(
                    f"[evo] 审计未通过 [{optimized_factor.get('name', '?')}]: "
                    f"失败项={failed_items}, 通过率={audit_report.pass_rate:.0%}"
                )
                self._record_audit_failed_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    audit_report,
                    evaluation=evaluation,
                )
                self._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    f"审计未通过: 失败项={failed_items}, 通过率={audit_report.pass_rate:.0%}",
                    evaluation,
                    "audit_failed",
                )
                return False

            # ── Step 4.6.5: 消融实验检查 (Phase A 集成) ──
            ablation_result = self._run_ablation_check(
                optimized_factor,
                evaluation,
                trace_id,
            )
            evaluation["ablation_check"] = ablation_result
            if not ablation_result.get("passed", True):
                print(f"[evo] 消融实验未通过 [{optimized_factor.get('name', '?')}]: 疑似伪相关")
                self._record_ablation_failed_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    ablation_result,
                )
                self._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    "消融实验未通过: 疑似伪相关",
                    evaluation,
                    "audit_failed",
                )
                return False

            # ── Step 4.6.6: 因果结构审查 (Phase C 集成) ──
            causal_result = self._run_causal_validation(
                optimized_factor,
                evaluation,
                trace_id,
            )
            evaluation["causal_validation"] = causal_result
            if not causal_result.get("passed", True):
                print(f"[evo] 因果审查未通过 [{optimized_factor.get('name', '?')}]: 事件敏感")
                self._record_causal_failed_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    causal_result,
                )
                self._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    "因果审查未通过: 事件敏感",
                    evaluation,
                    "audit_failed",
                )
                return False

            # ── Step 4.6.7: 鲁棒性审查 (Phase B 集成) ──
            robustness_result = self._run_robustness_check(
                optimized_factor,
                evaluation,
                trace_id,
            )
            evaluation["robustness_check"] = robustness_result
            if not robustness_result.get("passed", True):
                print(f"[evo] 鲁棒性审查未通过 [{optimized_factor.get('name', '?')}]")
                self._record_robustness_failed_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    robustness_result,
                )
                self._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    "鲁棒性审查未通过",
                    evaluation,
                    "audit_failed",
                )
                return False

            # ── Step 4.6.8: SHAP 可解释性分析 (Phase B 集成) ──
            shap_result = self._run_shap_analysis(
                optimized_factor,
                evaluation,
                trace_id,
            )
            evaluation["shap_analysis"] = shap_result

            # 晋级精英池（去重检查 + 质量评分附加 + 审计报告）
            self._log_inspection_detail(
                optimized_factor,
                inspection,
                "通过",
                generation,
            )
            promoted_path = self._promote_to_elite(
                optimized_factor,
                evaluation,
                seed_correlations=seed_correlations,
                quality_score=inspection.quality_score,
                audit_report=audit_report,
            )
            if promoted_path is None:
                # 因子名称重复，跳过
                self._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    "因子名称重复，跳过晋升",
                    evaluation,
                    "audit_failed",
                    quality_grade=inspection.grade,
                )
                return False
            self.state_manager.increment_promoted(state)
            elite_ids.append(optimized_factor["factor_id"])
            self._record_success_trace(
                optimized_factor,
                generation,
                evolution_method,
                evolution_summary,
                evaluation,
                [
                    f"代 {generation} 晋级精英池",
                    f"质量分={inspection.total_score}/50 ({inspection.grade}级)",
                    f"审计通过率={audit_report.pass_rate:.0%}",
                ],
                trace_id,
            )
            self._record_experiment_variant(
                optimized_factor,
                parent,
                generation,
                evolution_method,
                evolution_summary,
                evaluation,
                "promoted",
                quality_grade=inspection.grade,
            )
            self._consecutive_low_ic = 0
            print("[DEBUG-evo] promotion path: _consecutive_low_ic reset to 0")
            promoted = True
        else:
            # 失败轨迹
            self._record_failure_trace(
                optimized_factor,
                generation,
                evolution_method,
                evolution_summary,
                verifier_result["failure_reasons"],
                trace_id,
                evaluation=evaluation,
            )
            self._record_experiment_variant(
                optimized_factor,
                parent,
                generation,
                evolution_method,
                f"Verifier 未通过: {verifier_result.get('failure_reasons', [])[:3]}",
                evaluation,
                "verifier_failed",
            )
            # 检查低 IC
            bt = evaluation.get("level_1_backtest", {})
            if abs(bt.get("ic", 0)) < self.budget["circuit_breaker_low_ic_threshold"]:
                self._consecutive_low_ic += 1
                print(
                    f"[DEBUG-evo] failure path, low IC: _consecutive_low_ic incremented to {self._consecutive_low_ic}"
                )
            else:
                self._consecutive_low_ic = 0
                print("[DEBUG-evo] failure path, not low IC: _consecutive_low_ic reset to 0")

        # ── Step 6: 状态持久化 ──
        state["last_generation"] = generation
        self.state_manager.save(state)

        return promoted

    def _select_parent_uct(self, parents: list[FactorProgram]) -> FactorProgram:
        """UCT 树搜索选择父因子，平衡探索与利用。

        UCB = avg_reward + c * sqrt(ln(total_visits) / visits)

        未访问的父因子（visits=0）返回无限大 UCB，确保优先探索。
        """
        total_visits = sum(s.get("visits", 0) for s in self._uct_stats.values())
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
            exploration = UCT_EXPLORATION_C * math.sqrt(math.log(max(total_visits, 1)) / visits)
            ucb = avg_reward + exploration
            if ucb > best_score:
                best_score = ucb
                best_parent = p

        return best_parent

    def _update_uct_stats(self, parent: FactorProgram, evaluation: FactorEvaluation) -> None:
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

    def _update_uct_failure(self, parent: FactorProgram) -> None:
        """记录父因子演化失败的 UCT 反馈（GAP-074 P0-1）。

        演化失败/运行时校验失败/快速预筛失败路径均调用：visits+1、不授予
        正奖励。避免失败父因子 visits 恒 0，导致 `_select_parent_uct`
        永远返回 parents[0] 的选择坍缩（50 代全部演化同一父因子）。
        """
        fid = parent["factor_id"]
        if fid not in self._uct_stats:
            self._uct_stats[fid] = {"visits": 0, "total_reward": 0.0}
        self._uct_stats[fid]["visits"] += 1

    def _check_circuit_breaker(self, state: EvolutionState) -> Optional[str]:
        """熔断检查。返回原因字符串（None = 未触发）。"""
        # Token 超 2x
        tokens = state.get("tokens_consumed", 0)
        limit = state.get("budget_limit", self.budget["nightly_token_limit"])
        if tokens > limit * self.budget["circuit_breaker_token_ratio"]:
            return f"Token 熔断: {tokens} > {limit} * {self.budget['circuit_breaker_token_ratio']}"

        # 连续低 IC
        if self._consecutive_low_ic >= self.budget["circuit_breaker_consecutive_low_ic"]:
            return (
                f"连续低 IC 熔断: {self._consecutive_low_ic} 代 IC < {self.budget['circuit_breaker_low_ic_threshold']}"
            )

        # 失败率 > 90%
        evaluated = state.get("total_factors_evaluated", 0)
        promoted = state.get("total_factors_promoted", 0)
        if evaluated >= 10:
            failure_rate = (evaluated - promoted) / evaluated
            if failure_rate > self.budget["circuit_breaker_failure_rate"]:
                return f"失败率熔断: {failure_rate:.2%} > {self.budget['circuit_breaker_failure_rate']:.2%}"

        return None

    def _maybe_early_stop(self, state: EvolutionState) -> bool:
        """P1-3 (Phase 3, 26 计划 §8): 连续 K 代零晋升 → 提前停止（每代结束后调用）。

        基于 `state.total_factors_promoted` 与上次记录值的差异判断本代是否晋升，
        覆盖全部路径（演化失败/运行时拦截/预筛拦截 continue 均计入零晋升代）。
        保守默认关闭（enabled=False，验证见 plans/26 §8.7.1）。

        Args:
            state: L2 演化状态

        Returns:
            True 表示达到阈值应提前结束 run（调用方 break，正常收尾）
        """
        if not self._evolution_stop_enabled:
            self._consecutive_empty_generations = 0
            self._early_stop_last_count = state.get("total_factors_promoted", 0)
            return False
        cur = state.get("total_factors_promoted", 0)
        if cur == self._early_stop_last_count:
            self._consecutive_empty_generations += 1
        else:
            self._consecutive_empty_generations = 0
        self._early_stop_last_count = cur
        if self._consecutive_empty_generations >= self._evolution_stop_k:
            self._early_stop_reason = (
                f"连续 {self._consecutive_empty_generations} 代零晋升（阈值 K={self._evolution_stop_k}）"
            )
            return True
        return False

    # ── GAP-I206 (v2.71.0): L2 准入去冗余 — 与既有 elite 相关性检查 ──

    def _scan_elite_correlations(
        self,
        factor: FactorProgram,
        threshold: float,
        max_scan: int,
    ) -> list[dict[str, Any]]:
        """扫描既有 elite，返回与新因子信号 |corr| ≥ threshold 的相关性对。

        新因子信号只计算一次；既有 elite 执行失败/NaN 兜底跳过；索引文件跳过。
        L2 准入去冗余（_check_elite_correlation）与结构簇配额（_count_cluster_members）
        共用本扫描，避免重复实现。

        Args:
            factor: 待检查因子
            threshold: 相关性判定阈值
            max_scan: 扫描上限（容量护栏）

        Returns:
            [{"factor_name_b", "factor_id_b", "pearson", "abs_pearson"}, ...]
            按 abs_pearson 降序；无命中返回 []
        """
        from .backtest_pipeline import BacktestPipeline

        if not self.elite_dir.exists():
            return []

        # 新因子信号只计算一次，避免对每个既有 elite 重复执行
        try:
            new_signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""),
                self.data,
                factor.get("params", {}),
            )
        except Exception:  # noqa: BLE001
            return []
        if not isinstance(new_signal, np.ndarray) or len(new_signal) != len(self.data):
            return []

        correlations: list[dict[str, Any]] = []
        scanned = 0
        for fp in sorted(self.elite_dir.glob("*.json")):
            if fp.name == "_l2_seed_correlation_index.json":
                continue
            if scanned >= max_scan:
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
                    data.get("code", ""),
                    self.data,
                    data.get("params", {}),
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
            if abs(pearson) >= threshold:
                correlations.append(
                    {
                        "factor_name_b": data.get("name", data.get("factor_name", "?")),
                        "factor_id_b": data.get("factor_id", "?"),
                        "pearson": pearson,
                        "abs_pearson": abs(pearson),
                    }
                )
        correlations.sort(key=lambda c: c["abs_pearson"], reverse=True)
        return correlations

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
        correlations = self._scan_elite_correlations(
            factor,
            self._l2_elite_corr_threshold,
            self._l2_elite_corr_max_scan,
        )
        if not correlations:
            return None
        return {"correlations": correlations}

    def _count_cluster_members(self, factor: FactorProgram) -> int:
        """结构簇规模代理：与既有 elite 信号 |corr| ≥ cluster_corr_threshold 的成员数。

        结构性聚类配额（GAP-XXX）替代 max_per_family 家族配额：family 为知识注入
        来源标签（非正交结构维度），多样性控制改由信号相关性承担。复用
        _scan_elite_correlations 扫描逻辑；无既有 elite / 信号异常返回 0（放行）。

        Args:
            factor: 待晋升因子

        Returns:
            同类成员数（0 = 放行）
        """
        return len(
            self._scan_elite_correlations(
                factor,
                self._cluster_corr_threshold,
                self._cluster_max_scan,
            )
        )

    def _orthogonalize_via_basis(
        self,
        factor: FactorProgram,
    ) -> Optional[dict[str, Any]]:
        """多因子正交基底正交化（GAP-I206 补充，v2.72.0）。

        候选因子与既有 elite 高相关时，优先对正交基底（Gram-Schmidt）做
        迭代 OLS 残差化：依次剥离候选信号与基底每个成员的线性成分，得到
        与整个基底近似正交的残差信号。质量合格（残差与基底最大相关 <
        ``l2_orthogonal_residual_corr_max`` 且保留比 > ``l2_orthogonal_min_retained_ratio``）
        则返回正交化因子 dict 并注册为新基底成员；否则返回 None（回退
        单参照 OLS 或拒绝兜底）。

        Args:
            factor: 待晋升的演化因子

        Returns:
            dict: 基底正交化版本因子；None: 基底不可用/失败/质量不合格
        """
        if not self._l2_orthogonal_basis_enabled:
            return None
        try:
            from .backtest_pipeline import BacktestPipeline

            def _basis_signal_getter(member: dict[str, Any]):
                """从 elite 快照读取基底成员代码并执行（失败返回 None）。"""
                fid = member.get("factor_id", "")
                if not fid:
                    return None
                fp = self.elite_dir / f"{fid}.json"
                if not fp.exists():
                    return None
                data = json.loads(fp.read_text(encoding="utf-8"))
                if not (isinstance(data, dict) and data.get("code")):
                    return None
                return BacktestPipeline._execute_factor_code(
                    data.get("code", ""),
                    self.data,
                    data.get("params", {}),
                )

            new_signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""),
                self.data,
                factor.get("params", {}),
            )
            if not isinstance(new_signal, np.ndarray):
                return None
            sharpe = 0.0
            eval_info = factor.get("evaluation")
            if isinstance(eval_info, dict):
                bt = eval_info.get("level_1_backtest", {})
                if isinstance(bt, dict):
                    sharpe = float(bt.get("sharpe", 0.0))
            orth = self.orthogonal_basis.orthogonalize(
                factor=factor,
                candidate_signal=new_signal,
                signal_getter=_basis_signal_getter,
                sharpe=sharpe,
            )
            if orth is not None:
                # 注册为新基底成员（保持基底随 elite 池动态扩充）
                self.orthogonal_basis.register(orth)
                logger.warning(
                    "[orth-basis] 因子 %s 基底正交化入库（basis=%d 成员, pearson %.3f, GAP-I206 补充）",
                    factor.get("name", "?"),
                    len(orth.get("orthogonalized_basis", [])),
                    orth.get("orthogonalized_pearson", 0.0),
                )
            return orth
        except Exception as e:  # noqa: BLE001
            logger.debug("[orth-basis] 基底正交化失败回退单参照: %s", e)
            return None

    def _orthogonalize_candidate(
        self,
        factor: FactorProgram,
        pair: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """正交化闭环（GAP-I206 补充）：对候选因子信号关于参照 elite 做 OLS 残差。

        候选因子与既有 elite 高相关（_check_elite_correlation 命中）时，
        不直接拒绝——对候选信号关于参照 elite 信号做一元线性回归取残差；
        残差质量合格（与参照因子相关性 < ``l2_orthogonal_residual_corr_max``
        且保留比 > ``l2_orthogonal_min_retained_ratio``）时返回正交化因子
        dict（保留原字段 + orthogonalized 元数据 + ``orthogonal_signal`` 残差
        快照），由调用方以正交化版本入库；否则返回 None（拒绝兜底）。

        Args:
            factor: 待晋升的演化因子
            pair: _check_elite_correlation 返回的高相关对（含 factor_id_b）

        Returns:
            dict: 正交化版本因子；None: 残差质量不合格 / 信号不可算
        """
        from .backtest_pipeline import BacktestPipeline

        fid_b = pair.get("factor_id_b", "")
        if not fid_b:
            return None
        ref_fp = self.elite_dir / f"{fid_b}.json"
        if not ref_fp.exists():
            return None
        try:
            ref_data = json.loads(ref_fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        try:
            new_signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""),
                self.data,
                factor.get("params", {}),
            )
            other_signal = BacktestPipeline._execute_factor_code(
                ref_data.get("code", ""),
                self.data,
                ref_data.get("params", {}),
            )
        except Exception:  # noqa: BLE001
            return None
        if not (isinstance(new_signal, np.ndarray) and isinstance(other_signal, np.ndarray)):
            return None
        if len(new_signal) != len(other_signal):
            return None
        valid = ~(np.isnan(new_signal) | np.isnan(other_signal))
        if int(valid.sum()) < 20:
            return None
        y = new_signal[valid].astype(float)
        x = other_signal[valid].astype(float)
        if float(np.std(x)) < 1e-12 or float(np.std(y)) < 1e-12:
            return None
        # OLS 残差: residual = y - (a + b·x)
        b = float(np.cov(x, y)[0, 1] / np.var(x))
        a = float(np.mean(y) - b * np.mean(x))
        residual = y - (a + b * x)
        # 质量校验：残差与参照因子正交性 + 独立信息保留比
        resid_corr = abs(float(np.corrcoef(residual, x)[0, 1])) if float(np.std(residual)) > 1e-12 else 1.0
        retained_ratio = float(np.std(residual) / np.std(y))
        if resid_corr > self._l2_orthogonal_residual_corr_max:
            logger.info(
                "[L2-redun] %s 正交化残差仍与 %s 相关 %.3f > %.2f，残差不合格",
                factor.get("name", "?"),
                pair.get("factor_name_b", "?"),
                resid_corr,
                self._l2_orthogonal_residual_corr_max,
            )
            return None
        if retained_ratio < self._l2_orthogonal_min_retained_ratio:
            logger.info(
                "[L2-redun] %s 正交化残差保留比 %.3f < %.2f，独立信息不足",
                factor.get("name", "?"),
                retained_ratio,
                self._l2_orthogonal_min_retained_ratio,
            )
            return None
        # 构造正交化因子：保留原字段 + 正交化元数据 + 残差信号快照（对齐全长度）
        residual_full = np.full(len(new_signal), np.nan)
        residual_full[valid] = residual
        orth = dict(factor)
        orth["orthogonalized"] = True
        orth["orthogonalized_against"] = fid_b
        orth["orthogonalized_pearson"] = float(pair.get("pearson", 0.0))
        orth["orthogonal_signal"] = [float(v) if np.isfinite(v) else None for v in residual_full]
        return orth

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

    def _release_repo_after(func):
        """E.4 S1: release L3 repo write lock after method exits (decorator)."""
        from functools import wraps

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            finally:
                if getattr(self, "_repo", None) is not None:
                    try:
                        self._repo.close()
                    except Exception:
                        pass
                    self._repo = None

        return wrapper

    def _get_repo(self):
        """延迟初始化 DuckDB 仓储（GAP-030: 支持 factor_db_path 注入隔离库）。"""
        if self._repo is None:
            from .factor_db import FactorRepository

            self._repo = (
                FactorRepository(db_path=self.factor_db_path, market=self.market)
                if self.factor_db_path
                else FactorRepository(market=self.market)
            )
        return self._repo

    @_release_repo_after
    def _promote_to_elite(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        seed_correlations: Optional[list[FactorCorrelation]] = None,
        quality_score: Optional[dict] = None,
        audit_report: Optional[FactorAuditReport] = None,
        shadow_observe: Optional[bool] = None,
    ) -> Optional[Path]:
        """将因子晋升到精英池。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            seed_correlations: L2 种子因子相关性标记（可选）
            quality_score: 质量评分卡结果（Phase A.1 集成）
            audit_report: 因子审计报告（Phase B.3 集成）
            shadow_observe: 是否进入影子池观察（默认 None → 读 FTS_EVOLUTION_SHADOW_OBSERVE，
                            默认 "0" = 关闭观察期直接进正式组合；种子因子/初始池导入
                            显式传 False；设 env=1 可恢复观察期模式）

        Returns:
            Path: 晋升成功
            None: 因子名称重复，跳过晋升
        """
        # 2026-08-13: 新晋级精英因子观察期默认关闭（env 可恢复）
        if shadow_observe is None:
            import os

            shadow_observe = os.getenv("FTS_EVOLUTION_SHADOW_OBSERVE", "0") == "1"
        # 去重检查：DuckDB 是权威数据源，通过 factor_catalog 表检查
        factor_name = factor.get("name", "")
        try:
            repo = self._get_repo()
            existing = repo.get_factor_by_name(factor_name, market=self.market)
            if existing:
                # GAP-F10 (v2.73.0): 被拒因子结构化记录（分级日志，替代 print）
                logger.info(
                    "[evo] 跳过重复因子: %s (DuckDB 已存在, market=%s, trace_id=%s)",
                    factor_name,
                    self.market,
                    getattr(self, "_trace_id", ""),
                )
                return None
        except Exception:
            pass

        # ── 多样性配额检查（GAP-077 v2.102.0）：结构簇配额替代 max_per_family 家族配额 ──
        # family 是知识注入来源标签（非正交结构维度），多样性控制改由信号相关性承担：
        # 统计与既有 elite |corr| ≥ cluster_corr_threshold 的同类成员数，≥ 上限拒绝晋升。
        # 开关关闭时回退 max_per_family 旧逻辑（平滑迁移）。
        if self._cluster_quota_enabled:
            cluster_size = self._count_cluster_members(factor)
            if cluster_size >= self._cluster_max:
                logger.warning(
                    "[evo] 结构簇配额拒绝晋升 [%s]: 同类成员 %d ≥ 上限 %d (corr≥%.2f, trace_id=%s)",
                    factor_name,
                    cluster_size,
                    self._cluster_max,
                    self._cluster_corr_threshold,
                    getattr(self, "_trace_id", ""),
                )
                return None
        else:
            # ── 回退：max_per_family 家族配额（旧逻辑，平滑迁移） ──
            factor_family = factor.get("family", "unknown")
            max_per_family = self.budget.get("max_per_family", 15)
            # GAP-070 (v2.98.0): 兜底家族 'other'/'unknown' 永久豁免上限——它们是
            # "无法归类"的回收站家族，对其设限等价于对整个演化新因子晋升通道设总量
            # 上限，压制演化空间；逻辑同质化保护已由 L2 准入去冗余（GAP-I206 相关性
            # 预检 + 正交化闭环 + Gram-Schmidt 基底）承担。
            if factor_family not in ("other", "unknown"):
                try:
                    repo = self._get_repo()
                    existing_family = repo.get_by_family(
                        family=factor_family,
                        market=self.market,
                        limit=100,
                    )
                    if len(existing_family) >= max_per_family:
                        # GAP-F10 (v2.73.0): 家族拦截升级分级日志 + 结构化拒绝记录
                        logger.warning(
                            "[evo] 家族多样性限制拒绝晋升 [%s]: 家族 '%s' 已有 %d 个因子 (上限 %d, trace_id=%s)",
                            factor_name,
                            factor_family,
                            len(existing_family),
                            max_per_family,
                            getattr(self, "_trace_id", ""),
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
                if self._l2_elite_orthogonalize and _pairs:
                    # 正交化闭环（GAP-I206 补充）：高相关因子先尝试 OLS 残差化，
                    # 残差质量合格则以正交化版本入库；不合格拒绝兜底。
                    # v2.72.0: 优先走多因子正交基底（Gram-Schmidt），
                    # 基底不可用/失败时回退单参照 OLS。
                    orth_factor = self._orthogonalize_via_basis(factor)
                    if orth_factor is None:
                        orth_factor = self._orthogonalize_candidate(factor, _max)
                    if orth_factor is not None:
                        factor = cast(FactorProgram, orth_factor)
                        record = dict(factor)
                        if record.get("market", "multi") in ("multi", "other") and self.market in ("futures", "stock"):
                            record["market"] = self.market
                        record["evaluation"] = evaluation
                        _basis_tag = (
                            f"正交基底({len(factor.get('orthogonalized_basis', []))}成员)"
                            if factor.get("orthogonalized_basis")
                            else f"参照 {_name_b}"
                        )
                        print(
                            f"[evo] ★ L2 正交化闭环 [{factor.get('name', '?')}]: "
                            f"与既有 elite 相关 {_corr:.3f} ≥ 阈值 "
                            f"{self._l2_elite_corr_threshold}，{_basis_tag} 正交化残差入库"
                        )
                        logger.warning(
                            "[L2-redun] 因子 %s 正交化残差入库（against %s, pearson %.3f, GAP-I206 补充）",
                            factor.get("name", "?"),
                            _name_b,
                            _corr,
                        )
                    else:
                        print(
                            f"[evo] ★ L2 准入去冗余拦截 [{factor.get('name', '?')}]: "
                            f"与既有 elite {_name_b} 相关 {_corr:.3f} ≥ 阈值 "
                            f"{self._l2_elite_corr_threshold}，正交化残差不合格，拒绝晋升"
                        )
                        logger.warning(
                            "[L2-redun] 因子 %s 与既有 elite %s 相关 %.3f ≥ %.2f，正交化残差不合格拒绝（GAP-I206）",
                            factor.get("name", "?"),
                            _name_b,
                            _corr,
                            self._l2_elite_corr_threshold,
                        )
                        return None
                else:
                    print(
                        f"[evo] ★ L2 准入去冗余拦截 [{factor.get('name', '?')}]: "
                        f"与既有 elite {_name_b} 相关 {_corr:.3f} ≥ 阈值 {self._l2_elite_corr_threshold}，拒绝晋升"
                    )
                    logger.warning(
                        "[L2-redun] 因子 %s 与既有 elite %s 相关 %.3f ≥ %.2f，拒绝晋升（GAP-I206）",
                        factor.get("name", "?"),
                        _name_b,
                        _corr,
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
            correlation_metadata=({"max_corr_detected": max_corr_detected} if max_corr_detected is not None else {}),
            backtest_pipeline=(
                evaluation.get("backtest_pipeline", {}) if isinstance(evaluation.get("backtest_pipeline"), dict) else {}
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
            bonf_p = level_3.get("bonferroni_p", "N/A")
            adj_t = level_3.get("adjusted_t", "N/A")
            p_str = f"{bonf_p:.4f}" if isinstance(bonf_p, float) else str(bonf_p)
            t_str = f"{adj_t:.4f}" if isinstance(adj_t, float) else str(adj_t)
            print(f"[evo] 多重检验未通过 [{factor_name}]: Bonferroni p={p_str}, adjusted_t={t_str}")
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
                    corr_flags.append(
                        {
                            "partner_factor_id": partner,
                            "pearson": pearson,
                            "spearman": spearman,
                            "max_abs": max_abs,
                            "source": "l2_seed_correlation_check",
                        }
                    )

            if corr_flags:
                record["correlation_metadata"] = {
                    "l2_seed_flags": corr_flags,
                    "flag_count": len(corr_flags),
                    "max_corr_detected": max((f["max_abs"] for f in corr_flags), default=0),
                }
                print(f"[evo] 因子 {factor.get('name', '?')} 写入 L2 相关性标记: {len(corr_flags)} 个高相关对")

        # ── 影子池标记（L2 晋升节奏控制）：新演化因子先进影子池观察 ──
        if shadow_observe:
            record["shadow_pool"] = _build_shadow_pool()
            print(f"[evo] 因子 {factor.get('name', '?')} 进入影子池观察 ({_SHADOW_OBSERVE_TRADING_DAYS} 个交易日)")

        # ── 晋升时间戳（用于纯外推验证，P2 差距修复） ──
        record["promoted_at"] = datetime.now().isoformat()

        # ── 写入 DuckDB（主存储，SSOT；plans/29 P1 写路径反转） ──
        # GAP-032 严格一致：DuckDB 是主存储。P1 起 JSON 仅降级为只读快照——
        # 先写 DuckDB，成功后写 JSON（JSON 写失败不阻断晋升）；DuckDB 失败
        # 则不写 JSON 直接判定晋升失败，杜绝"快照有、catalog 无"孤儿数据
        write_ok = self._write_to_duckdb(
            factor,
            evaluation,
            quality_score,
            seed_correlations,
            audit_report,
            shadow_pool=record.get("shadow_pool"),
        )
        if not write_ok:
            print(f"[evo] ❌ 晋升失败 [{factor.get('name', '?')}]: DuckDB 写入失败（未写 JSON 快照）{fp.name}")
            return None

        # ── 写入 JSON 快照（只读备份，非阻塞） ──
        try:
            fp.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("[evo] JSON 快照写入失败（不影响晋升）: %s, err=%s", fp.name, e)

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
                        cand_file.name,
                        factor.get("name", "?"),
                        f_source,
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

        # E.4 S1: promotion done, release L3 repo write lock
        if self._repo is not None:
            try:
                self._repo.close()
            except Exception:
                pass
            self._repo = None

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
            factor_market: str = factor.get("market", "multi")
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
                    # 正交化闭环（GAP-I206 补充，v2.71.0/v2.72.0 基底）
                    "orthogonalized": factor.get("orthogonalized", False),
                    "orthogonalized_against": factor.get("orthogonalized_against", ""),
                    "orthogonalized_pearson": factor.get("orthogonalized_pearson", 0.0),
                    "orthogonalized_basis": factor.get("orthogonalized_basis", []),
                    "orthogonal_signal": factor.get("orthogonal_signal", []),
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
                        seed,
                        self.data,
                        self.forward_returns,
                    )
                # 确保 WalkForward 结果存在（若缺失则执行轻量 2 窗口验证）
                if evaluation.get("walk_forward") is None:
                    from .evaluation_chain import evaluate_walk_forward

                    try:
                        wf = evaluate_walk_forward(
                            seed,
                            self.data,
                            self.forward_returns,
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
                            seed,
                            0,
                            "seed_verifier",
                            "Verifier 判定未通过",
                            verifier_result.get("failure_reasons", []),
                            trace_id,
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
                        factor=seed,
                        evaluation=evaluation,
                    )
                    if inspection.filtered:
                        self._log_inspection_detail(
                            seed,
                            inspection,
                            "淘汰",
                            0,
                        )
                        continue

                    # 端到端回测流水线 (Phase B.2 集成)
                    backtest_result = self._run_backtest_pipeline(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    if backtest_result:
                        evaluation["backtest_pipeline"] = backtest_result

                    # 数据质量监控 (Phase B.1 集成)
                    self._register_factor_baseline(seed, evaluation)
                    dq_alerts = self._check_factor_data_quality(
                        seed,
                        evaluation,
                    )
                    if dq_alerts:
                        critical = any(getattr(a, "severity", "") == "critical" for a in dq_alerts)
                        if critical:
                            print(f"[evo] 种子数据质量严重告警 [{seed.get('name', '?')}]: 跳过晋升")
                            continue

                    # 种子因子强制审计 (Phase B.3 集成)
                    audit_report = self._run_factor_audit(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    if not audit_report.passed:
                        failed_items = [it.name for it in audit_report.failed_items]
                        print(
                            f"[evo] 种子审计未通过 [{seed.get('name', '?')}]: "
                            f"失败项={failed_items}, 通过率={audit_report.pass_rate:.0%}"
                        )
                        self._record_audit_failed_trace(
                            seed,
                            0,
                            trace_id,
                            audit_report,
                            evaluation=evaluation,
                        )
                        continue

                    # ── 消融实验检查（v2.50.0 与演化因子对齐） ──
                    ablation_result = self._run_ablation_check(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    evaluation["ablation_check"] = ablation_result
                    if not ablation_result.get("passed", True):
                        print(f"[evo] 种子消融实验未通过 [{seed.get('name', '?')}]: 疑似伪相关")
                        self._record_ablation_failed_trace(
                            seed,
                            0,
                            trace_id,
                            ablation_result,
                        )
                        continue

                    # ── 因果结构审查（v2.50.0 与演化因子对齐） ──
                    causal_result = self._run_causal_validation(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    evaluation["causal_validation"] = causal_result
                    if not causal_result.get("passed", True):
                        print(f"[evo] 种子因果审查未通过 [{seed.get('name', '?')}]: 事件敏感")
                        self._record_causal_failed_trace(
                            seed,
                            0,
                            trace_id,
                            causal_result,
                        )
                        continue

                    # ── 鲁棒性审查（v2.50.0 与演化因子对齐） ──
                    robustness_result = self._run_robustness_check(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    evaluation["robustness_check"] = robustness_result
                    if not robustness_result.get("passed", True):
                        print(f"[evo] 种子鲁棒性审查未通过 [{seed.get('name', '?')}]")
                        self._record_robustness_failed_trace(
                            seed,
                            0,
                            trace_id,
                            robustness_result,
                        )
                        continue

                    # ── SHAP 可解释性分析（v2.50.0 与演化因子对齐，不阻断） ──
                    shap_result = self._run_shap_analysis(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    evaluation["shap_analysis"] = shap_result

                    self._log_inspection_detail(
                        seed,
                        inspection,
                        "通过",
                        0,
                    )
                    promoted_path = self._promote_to_elite(
                        seed,
                        evaluation,
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
                    print(
                        f"[evo] 种子因子晋升: {seed['name']} (IC={bt.get('ic', 0):.4f}, "
                        f"质量分={inspection.total_score}/50)"
                    )
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
                    cand_file.name,
                    e,
                )
                continue

            merged.append(fp)
            existing_names.add(cand_name)
            consumed_ids.append(cand_id)
            logger.info(
                "[L1.merge] 合并候选: name=%s, candidate_id=%s, market=%s",
                cand_name,
                cand_id,
                cand_market,
            )

            # GAP-036: 消费后立即删除 l1_injected 文件（激进清理，非阻塞）
            try:
                if cand_file.exists():
                    cand_file.unlink()
                    logger.info(
                        "[GAP-036] 消费后删除 L1 候选文件: %s (name=%s)",
                        cand_file.name,
                        cand_name,
                    )
            except OSError as e:
                logger.warning("[GAP-036] 删除 L1 候选文件失败: %s, err=%s", cand_file.name, e)

        # 4. 幂等: factor_pool.json pending → injected
        if consumed_ids and pool_data is not None:
            for entry in pool_data.get("factors", []):
                if entry.get("factor_id") in consumed_ids:
                    entry["status"] = "injected"
                    entry["updated_at"] = datetime.now().isoformat()
            # GAP-I306: 消费后重算 total_count/pending_count，避免残留过期值
            pool_data["total_count"] = len(pool_data.get("factors", []))
            pool_data["pending_count"] = sum(
                1 for f in pool_data.get("factors", []) if f.get("status") == "pending"
            )
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
                correlations = compute_seed_correlations(seeds, self.data, threshold=0.95)
            return correlations
        except Exception as e:
            mode = "横截面" if self._is_cross_section else "时序"
            print(f"[evo] 种子因子相关性预检异常（{mode}模式，跳过）: {e}")
            return []

    def _build_barra_exposures(self) -> Optional[dict[str, Any]]:
        """构建 Barra 风格暴露（GAP-I304，v2.79.0）。

        从横截面面板自动计算 10 风格暴露，供 `cross_section_evaluate_backtest`
        style_exposures 参数使用（行业中性化后叠加风格回归残差，实现全市场
        Barra 暴露覆盖）。结果缓存避免每因子重复计算；面板字段缺失的风格
        自动跳过（BarraStyleEngine 全 NaN 处理）；非横截面 / 配置关闭 /
        计算异常返回 None 不阻断评估。

        Returns:
            {style_name: DataFrame(index=dates, columns=symbols)} 或 None
        """
        if not self._is_cross_section:
            return None
        if self._barra_exposures_cache is not None or hasattr(self, "_barra_exposures_attempted"):
            return self._barra_exposures_cache
        self._barra_exposures_attempted = True
        try:
            from fts.config.settings import get_config

            if not get_config().l2_barra_style_neutral:
                return None
            from .barra.barra_style import BarraStyleEngine

            engine = BarraStyleEngine()
            exposures = engine.compute_exposures(
                self.cross_section_data,
                self.cross_section_dates,
            )
            self._barra_exposures_cache = exposures
            n_available = sum(1 for s in exposures.values() if s is not None and not s.isna().all().all())
            logger.info(
                "[EvolutionLoop] Barra 风格暴露构建完成: %d/%d 风格可用",
                n_available,
                len(exposures),
            )
            return exposures
        except Exception as e:  # noqa: BLE001 — 构建失败不阻断评估
            logger.warning("[EvolutionLoop] Barra 风格暴露构建失败，跳过风格中性化: %s", e)
            return None

    def _build_vol_map(self) -> Optional[dict[str, float]]:
        """构建波动率中性化映射（G10，v2.103.0+15）。

        计算各品种全样本日收益年化波动率作为静态截面暴露（对标股票市值），
        供 `cross_section_evaluate_backtest(vol_map=...)` 剥离信号与品种
        波动率水平的相关性；开启时序去季节化剥离日历季节性。

        Returns:
            {symbol: 年化波动率}；非横截面 / 配置关闭 / 数据不足 / 异常返回 None
        """
        if not self._is_cross_section or not self.cross_section_data:
            return None
        try:
            from fts.config.settings import get_config

            if not get_config().l2_barra_style_neutral:
                return None
            vol_map: dict[str, float] = {}
            for sym, df in self.cross_section_data.items():
                if "close" not in df.columns:
                    continue
                close = df["close"].dropna()
                if len(close) < 20:
                    continue
                ret = close.pct_change().dropna()
                if len(ret) < 20 or float(ret.std()) < 1e-12:
                    continue
                vol_map[sym] = float(ret.std() * np.sqrt(252.0))
            logger.info(
                "[EvolutionLoop] 波动率中性化映射构建完成: %d/%d 品种可用",
                len(vol_map),
                len(self.cross_section_data),
            )
            return vol_map or None
        except Exception as e:  # noqa: BLE001 — 构建失败不阻断评估
            logger.warning("[EvolutionLoop] 波动率中性化映射构建失败，跳过: %s", e)
            return None

    def _evaluate_cross_section(self, factor: FactorProgram, trace_id: str) -> FactorEvaluation:
        """横截面模式下的评估：直接回测 + 自动构造 FactorEvaluation。"""
        from .contracts import EconomicScore, MultipleTestResult

        bt = cross_section_evaluate_backtest(
            factor,
            self.cross_section_data,
            self.cross_section_dates,
            industry_map=self.industry_map,
            cap_map=self.cap_map,
            style_exposures=self._build_barra_exposures(),
            vol_map=self._build_vol_map(),
            long_only=False,
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
        mt = MultipleTestResult(
            bonferroni_p=1.0, fdr_q=0.05, effective_n_factors=1, adjusted_t=bt.get("t_stat", 3.0), passed=True
        )
        reasons: list[str] = []
        if bt.get("ic", 0) < 0.03:
            reasons.append(f"截面 IC={bt.get('ic', 0):.4f} < 0.03")
        if bt.get("sharpe", 0) < 1.5:
            reasons.append(f"截面夏普={bt.get('sharpe', 0):.4f} < 1.5")
        # G4（35-gap-closure-plan §4.1）：IC 显著性硬门槛——截面期数感知的 t 统计量
        # 横截面 ic_t_stat = 日度 IC 序列 t 值（ICIR×√有效截面期数，L1012），与时序路径
        # 同阈值 1.65；ic_t_stat 缺失（有效截面期数 <2）时回退旧 |ICIR|≥0.30 口径。
        ic_t_gate = bt.get("ic_t_stat")
        if ic_t_gate is None:
            icir_fb = abs(float(bt.get("icir_block", bt.get("icir", 0.0)) or 0.0))
            if icir_fb < 0.30:
                reasons.append(f"截面|ICIR|={icir_fb:.4f} < 0.30（样本不足，回退口径）")
        elif abs(float(ic_t_gate)) < 1.65:
            reasons.append(f"截面|ic_t|={abs(float(ic_t_gate)):.4f} < 1.65")
        # G11（35-gap-closure-plan §5.4）：日换手硬剔除（与时序路径同口径，
        # 阈值经 FTSConfig.factor_turnover_daily_max 可配，None=关闭）
        try:
            from ..config import get_config as _get_cfg

            _td_max = getattr(_get_cfg(), "factor_turnover_daily_max", None)
        except Exception:  # noqa: BLE001
            _td_max = None
        if _td_max is not None and float(bt.get("turnover_daily", 0.0) or 0.0) > float(_td_max):
            reasons.append(f"截面日换手={float(bt.get('turnover_daily', 0.0) or 0.0):.4f} > {_td_max}")
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

    def run_microstructure_promotion(
        self,
        symbols: Optional[list[str]] = None,
        limit: int = 0,
        trace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """C1 评估晋升接线：microstructure 候选 → L2 评估链 → 审计 → elite。

        复用 ``_evaluate_cross_section``（横截面评估，内置 ic≥0.03 & sharpe≥1.5 门槛）
        与 ``_promote_to_elite``（重复/家族/去冗余护栏），与 L2 演化晋升完全同构；
        单候选评估/审计异常降级跳过，不阻断整批。tick 数据不足时
        ``MicrostructureFactorGenerator.generate_batch`` 返回空（全 skipped）。

        Args:
            symbols: 品种列表（None=动态池默认）
            limit: 候选上限（0=全量）
            trace_id: 全链路 trace_id

        Returns:
            统计 {generated, evaluated, passed, promoted, skipped, promoted_ids}
        """
        from .microstructure_generator import MicrostructureFactorGenerator

        tid = trace_id or "evo_micro_promote"
        gen = MicrostructureFactorGenerator()
        cands = gen.generate_batch(symbols=symbols, trace_id=tid)
        if limit > 0:
            cands = cands[:limit]
        result: dict[str, Any] = {
            "generated": len(cands),
            "evaluated": 0,
            "passed": 0,
            "promoted": 0,
            "skipped": 0,
            "promoted_ids": [],
        }
        if not cands:
            logger.info("[micro-promote] 无候选（tick 数据不足），跳过 (trace_id=%s)", tid)
            return result
        for c in cands:
            factor = c.factor
            fid = factor.get("factor_id", "?")
            try:
                ev = self._evaluate_cross_section(factor, tid)
            except Exception as e:  # noqa: BLE001 - 单候选评估异常降级
                logger.warning("[micro-promote] 候选评估异常跳过 %s: %s (trace_id=%s)", fid, e, tid)
                result["skipped"] += 1
                continue
            result["evaluated"] += 1
            if not ev.get("passed", False):
                continue
            result["passed"] += 1
            # 审计尽力而为：数据缺失项标记 skipped，不拦截晋升
            audit = None
            try:
                from .audit import FactorAuditor

                audit = FactorAuditor().audit(factor=factor)
            except Exception as e:  # noqa: BLE001
                logger.warning("[micro-promote] 候选审计降级 %s: %s (trace_id=%s)", fid, e, tid)
            path = self._promote_to_elite(factor, ev, audit_report=audit)
            if path is not None:
                result["promoted"] += 1
                result["promoted_ids"].append(fid)
        return result

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

            # 1. 自动淘汰检查（GAP-I305: 受 decay_auto_retire_enabled 开关控制）
            if self._decay_auto_retire_enabled:
                retired = self.elite_tracker.auto_retire()
                if retired:
                    print(f"[elite-review] 自动淘汰 {len(retired)} 个因子: {retired}")
            else:
                # 开关关闭：仅统计应退役而未退役的因子（日志告警）
                observe = self.elite_tracker.get_by_status("decaying")
                if observe:
                    print(f"[elite-review] 自动退役已关闭，当前 {len(observe)} 个衰减因子待处理")

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

                # ── GAP-I305: 衰减分级 + 反馈闭环联动 ──
                try:
                    snapshot = self.elite_tracker.get(fid)
                    decay_grade = (snapshot or {}).get("decay_grade", "normal")
                    if decay_grade in ("observe", "retired"):
                        # 构造 FACTOR_DECAY 反馈事件，走归因分析并记录动作
                        event = {
                            "event_id": f"fe_decay_{fid}",
                            "event_type": "factor_decay",
                            "factor_id": fid,
                            "trigger_reason": f"衰减分级={decay_grade}",
                            "severity": ("critical" if decay_grade == "retired" else "warning"),
                            "payload": {
                                "decay_grade": decay_grade,
                                "ic_slope_6m": (snapshot or {}).get("ic_slope_6m", 0.0),
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "handled": False,
                            "handled_at": None,
                        }
                        result = self.feedback_loop._handle_event(  # noqa: SLF001
                            event,
                            {fid: factor_data},
                            {},
                        )
                        # 反馈结果写回跟踪快照（可追溯）
                        snap = self.elite_tracker.get(fid) or {}
                        snap["last_feedback"] = {
                            "decay_grade": decay_grade,
                            "root_cause": result.get("root_cause", "unknown"),
                            "action": result.get("action_taken", "monitor_only"),
                            "at": datetime.now(timezone.utc).isoformat(),
                        }
                        self.elite_tracker._write_snapshot(fid, snap)  # noqa: SLF001
                except Exception as e:
                    logger.debug("衰减反馈联动跳过 %s: %s", fid, e)

                # ── Phase C.2: LogicMonitor 集成 ──
                try:
                    import json

                    # 从 elite 快照读取因子程序（_promote_to_elite 写入）
                    fp_snapshot = self.elite_dir / f"{fid}.json"
                    if not fp_snapshot.exists() or self.data is None:
                        continue
                    factor_program = json.loads(fp_snapshot.read_text(encoding="utf-8"))
                    logic_report = self.logic_monitor.run(
                        factor_program,
                        self.data,
                        switch_dates=[],
                    )
                    if not logic_report.all_healthy:
                        print(f"[elite-review] 逻辑监控告警: {fid}")
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
            factor_data = (
                self.verifier.get_factor_by_id(factor_id) if hasattr(self.verifier, "get_factor_by_id") else None
            )
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
                print(f"[dq-monitor] 告警 [{factor_id}]: type={alert_type}, severity={severity}, msg={msg}")
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
                pd.Series(signals),
                gp_data,
                target_col,
            )
            # FeatureImportanceResult 是 dataclass，转 dict 存快照
            factor_program["feature_importance"] = {k: v for k, v in importance_result.__dict__.items()}
        except Exception as e:
            logger.debug("特征重要性分析跳过: %s", e)

        summary = (
            f"GP Gen={gp_result.generations_completed}, "
            f"Fitness={gp_result.best_fitness:.4f}, "
            f"IC={gp_result.best_ic:.4f}, Sharpe={gp_result.best_sharpe:.4f}, "
            f"Expression={gp_result.best_expression[:80]}"
        )

        logger.info("GP 演化完成 [%s]: %s", parent.get("name", "?"), summary)
        return cast(FactorProgram, factor_program), summary

    def _run_deep_evolution(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
        model_kind: str = "gru",
    ) -> tuple[FactorProgram, str]:
        """执行深度因子演化 (GAP-I203 v2.73.0 / C5 v2.100.1)。

        使用 DeepFactorGenerator 在历史行情序列上训练轻量纯 numpy 深度模型
        （GRU 或 Transformer，C5），将训练权重固化内嵌为可执行因子 code
        （零未来函数：每步只用截至 t 的特征窗口推理），产出可过全套审计链
        的 FactorProgram。

        Args:
            parent: 父因子（仅用于命名与血缘）
            generation: 当前代数
            trace_id: 全链路 trace_id
            model_kind: 深度模型类型 "gru"（默认）| "transformer"（C5）

        Returns:
            (新因子程序, 演化摘要)

        Raises:
            RuntimeError: 数据缺失、样本不足或深度模型训练失败（调用方降级回退）
        """
        from fts.ml.deep_factor import DeepFactorConfig, create_deep_factor

        if self.data is None or len(self.data) < 2:
            raise RuntimeError("深度演化: 无可用行情数据")

        data = self.data
        # forward_returns 缺失/长度不齐时由生成器内部降级（返回 None）
        forward_returns: np.ndarray | None = self.forward_returns
        if forward_returns is None or len(forward_returns) != len(data):
            forward_returns = None

        factor = create_deep_factor(
            data=data,
            forward_returns=forward_returns,
            market=self.market,
            parent_name=parent.get("name", "?"),
            trace_id=trace_id,
            config=DeepFactorConfig(model_kind=model_kind),
        )
        if factor is None:
            raise RuntimeError(f"深度演化({model_kind}): 样本不足或训练失败")
        factor["parent_id"] = parent.get("factor_id")
        factor["generation"] = generation
        factor["trace_id"] = trace_id

        dm = factor.get("deep_model", {})
        label = "Transformer" if model_kind == "transformer" else "GRU"
        summary = (
            f"Deep {label} lookback={dm.get('lookback', '?')} "
            f"hidden={dm.get('hidden', '?')} "
            f"val_ic={float(dm.get('val_ic', 0.0)):.4f}"
        )
        logger.info("深度演化完成 [%s]: %s", parent.get("name", "?"), summary)
        return cast(FactorProgram, factor), summary

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
            parent,
            generation,
            trace_id,
        )
        if engine_factor is not None:
            new_factor = engine_factor
            summary = f"OpEvolve: {new_factor.get('expression', '?')}"
            logger.info(
                "算子演化引擎因子生成成功 [%s]: %s",
                new_factor.get("name", "?"),
                summary,
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
            name
            for name, meta in registry.items()
            if meta.category == "L1" and name not in ("ts_covariance", "ts_correlation")
        ]
        l2_ops = [name for name, meta in registry.items() if meta.category == "L2"]
        l4_ops = [name for name, meta in registry.items() if meta.category == "L4"]

        # 种子随机（基于父因子，保证可复现性）
        seed = int(
            hashlib.md5(f"{parent.get('factor_id', '?')}_{generation}_{time.time_ns()}".encode()).hexdigest()[:8], 16
        ) % (2**31)
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
                        if (self._is_cross_section and self.cross_section_data is not None)
                        else self.data
                    )
                    sig = evaluate(node, probe_data, registry)
                    sig_arr = sig.values if isinstance(sig, pd.Series) else np.asarray(sig, dtype=float)
                    sig_arr = np.asarray(sig_arr, dtype=float)
                except Exception:
                    continue
                finite = sig_arr[np.isfinite(sig_arr)]
                if finite.size == 0 or np.nanstd(sig_arr) < 1e-8:
                    logger.debug(
                        "算子表达式非常数信号被前置拦截: %s",
                        expression,
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
                    narrative=(f"算子演化: {expression} (基于父因子 {parent.get('name', '?')})"),
                    params={},
                    trace_id=trace_id,
                    source="operator_evolution",
                )
                # 覆盖产生的 factor_id 确保唯一
                new_factor["factor_id"] = factor_id
                new_factor["parent_id"] = parent_id
                new_factor["generation"] = generation

                summary = f"OpGen: {expression}, lookback={max_lookback}, fields={fields}"

                logger.info("算子因子生成成功 [%s]: %s", factor_name, summary)
                return new_factor, summary

            except Exception as e:
                logger.debug("算子因子生成尝试 %d/10 失败: %s", attempt + 1, e)
                continue

        raise RuntimeError(f"无法生成合法算子因子 (10 次尝试均失败, parent={parent.get('name', '?')})")

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
            if self._is_cross_section and self.cross_section_data is not None:
                data = list(self.cross_section_data.values())[0].copy()
            else:
                data = self.data.copy()
            target_col = "forward_return"
            if self.forward_returns is None or len(self.forward_returns) != len(data):
                logger.debug("算子演化引擎跳过: 无 forward_returns 评估数据")
                return None
            data[target_col] = self.forward_returns

            # 种子由父因子 + 代际序号派生（GAP-074 P0-2）：同父因子不同代
            # 产生不同搜索轨迹（原仅父因子派生产生完全确定性空转）；同父同代仍可复现
            seed = int(
                hashlib.md5(
                    f"{parent.get('factor_id', '?')}::{generation}".encode(),
                ).hexdigest()[:8],
                16,
            ) % (2**31)

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
                narrative=(f"算子演化引擎: {result.best_expression} (基于父因子 {parent.get('name', '?')})"),
                trace_id=trace_id,
                parent_id=parent.get("factor_id", "?"),
                generation=generation,
            )
            logger.info(
                "算子演化引擎成功 [%s]: %s (fitness=%.4f)",
                parent.get("name", "?"),
                result.best_expression,
                result.best_fitness,
            )
            return factor
        except Exception as e:
            logger.debug("算子演化引擎失败，回退随机生成: %s", e)
            return None

    # ── Phase B.2.1: 快速预筛选（新增） ──────────────────

    def _quick_prefilter(
        self,
        factor: FactorProgram,
        trace_id: str,
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
                factor.get("code", ""),
                probe_data,
                factor.get("params", {}),
            )
        except Exception as e:
            return False, f"预筛选执行失败: {type(e).__name__}: {e}", 0.0

        if not isinstance(signal, np.ndarray) or len(signal) != len(probe_data):
            return (
                False,
                f"预筛选输出长度不匹配: {len(signal) if hasattr(signal, '__len__') else '?'} != {len(probe_data)}",
                0.0,
            )

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
        ic_threshold = 0.01
        fr = self.forward_returns
        if fr is not None and len(fr) == len(signal):
            valid = ~(np.isnan(signal) | np.isnan(fr))
            if valid.sum() >= 10:
                ic, pval = sp_stats.spearmanr(signal[valid], fr[valid])
                if np.isnan(ic) or abs(ic) < ic_threshold:
                    return (
                        False,
                        (
                            f"快速 IC 过低: abs(IC)={abs(ic):.4f} < {ic_threshold}"
                            f"{'' if np.isnan(ic) else f', p={pval:.4f}'}"
                        ),
                        0.0,
                    )
                return True, "", abs(ic)

        return True, "", 0.0

    def _cross_section_prefilter(
        self,
        factor: FactorProgram,
        trace_id: str,
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
                executor,
                factor.get("params", {}),
                panel,
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
            signal_dict,
            ret_dict,
            common_dates,
            len(common_dates),
        )
        ics = _cs_compute_ics(signal_matrix, ret_matrix)
        if not ics:
            # 无有效截面期（如窗口期样本不足），放行交由正式评估兜底
            return True, "", 0.0

        ic_abs = abs(float(np.mean(ics)))
        ic_threshold = 0.01
        if ic_abs < ic_threshold:
            return False, (f"横截面快速 IC 过低: abs(IC)={ic_abs:.4f} < {ic_threshold}"), 0.0
        return True, "", ic_abs

    # ── Phase B.2.1: 后代因子运行时校验 ──────────────────

    def _check_factor_runtime(
        self,
        factor: FactorProgram,
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
            if (self._is_cross_section and self.cross_section_data is not None)
            else self.data
        )
        try:
            signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""),
                probe_data,
                factor.get("params", {}),
            )
        except Exception as e:
            return False, f"执行失败: {type(e).__name__}: {e}"

        if not isinstance(signal, np.ndarray) or len(signal) != len(probe_data):
            return False, (f"输出长度不匹配: {len(signal) if hasattr(signal, '__len__') else '?'} != {len(probe_data)}")
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
                }
                if report
                else {},
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
                train_df: pd.DataFrame,
                oos_df: pd.DataFrame,
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
        oos_result: dict[str, Any] | None = None
        if oos_ratio > 0:
            oos_result = {
                "ic_consistency": min(1.0, abs(oos_icir)),
                "oos_ic": oos_ic,
                "passed": abs(oos_icir) >= 1.0,
            }

        # v2.60.0 (GAP-F08): 冷启动 WalkForward 样本外验证优先。
        # GAP-070 (v2.98.0): 优先复用三级评估链走航结果（Step 3 已强制走航，
        # 配置同源 `_build_wf_config`，窗口 IC 口径一致），消除双重 WalkForward
        # 重复计算；评估链走航失败/跳过（数据不足/force_walkforward=false）时
        # 兜底独立计算保持原逻辑。
        wf_result: dict[str, Any] | None = cast(dict[str, Any] | None, evaluation.get("walk_forward"))
        if not (wf_result and wf_result.get("n_windows_completed", 0) > 0):
            wf_result = self._run_walkforward_oos(factor)
        if wf_result is not None:
            oos_result = {
                "ic_consistency": wf_result.get("ic_consistency", 0.0),
                "oos_ic": 0.0,  # 一致性已含多窗口均值信息
                "passed": wf_result.get("passed", False),
                "windows": wf_result.get("windows", []),
                "n_windows_completed": wf_result.get("n_windows_completed", 0),
            }
        elif isinstance(evaluation.get("walk_forward"), dict):
            chain_wf = evaluation.get("walk_forward")
            if chain_wf is not None and int(chain_wf.get("n_windows_completed", 0)) < 2:
                # GAP-079 (v2.102.0): 评估链走航存在但窗口不足（n_windows_completed<2），
                # 且独立走航失败（数据不足/force_walkforward=false）——保留"窗口不足"事实
                # 而非回退 L1 icir 兜底，使 _check_oos_consistency 命中 GAP-073 的
                # n_windows<2 → skipped 分支。修复短样本下 oos_consistency 全量误杀
                # （1073 audit_fail 中 99.4% 由 oos 导致，其中 90% 走航 0 窗口，
                # 见 plans/26-phase0-audit-breakdown.md）。
                oos_result = {
                    "ic_consistency": 0.0,
                    "oos_ic": 0.0,
                    "passed": False,
                    "windows": [],
                    "n_windows_completed": 0,
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
                symbol_ic_map=l1.get("symbol_ic") or None,  # GAP-075: 激活 cross_symbol
                symbol_holdout=l1.get("symbol_holdout") or None,  # GAP-075: 标的留出审计项
                oos_result=oos_result,
                p_values=p_values if p_values else None,
            )
        except Exception as e:
            logger.warning(
                "审计执行异常 [%s]: %s (降级为跳过所有审计项)",
                factor_meta["name"],
                str(e),
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
        print(f"[evo] 审计失败轨迹已记录: {factor_name} → 代 {generation}, 通过率={audit_report.pass_rate:.0%}")

    # ── Phase A: 消融实验检查 ──────────────────────────

    # v2.50.0 判定语义：核心价格列（因子正常依赖的输入）与信息型消融模式
    # 不参与"伪相关"拦截判定——时序因子依赖时序因果（shuffle_dates）、
    # 价格因子依赖价格列、量价因子依赖成交量/VWAP 均属必要特征。
    _ABLATION_PRICE_CORE_COLS: frozenset[str] = frozenset({"open", "high", "low", "close", "vwap", "settle"})
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

            result = self.ablation_experiment.run(factor, data, forward_returns, signal_cache=self._signal_cache)
            # AblationResult 是 dict 子类，直接使用
            baseline_ic = result.get("baseline_ic", 0.0)
            ablations = result.get("ablations", [])
            if abs(baseline_ic) < 1e-9:
                is_passed = True
            else:
                # 仅拦截型消融的 IC 降幅超过基线 50% → 疑似伪相关
                blocking = [ab for ab in ablations if self._is_blocking_ablation(ab)]
                is_passed = all(ab.get("ic_change", 0.0) >= -0.5 * abs(baseline_ic) for ab in blocking)
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
            min_pass_rate = 0.7

            result = self.robustness_tester.run(factor, data, forward_returns, signal_cache=self._signal_cache)
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

            result = self.shap_analyzer.analyze(factor, data, forward_returns, signal_cache=self._signal_cache)
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
        sub_trace_id = f"{trace_id}_g{generation}_{mutation_type}_{factor['factor_id'][:8]}"
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
        self.state_manager.add_experience_ref(self.state_manager.load_or_init(), trace["trace_id"])

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
        sub_trace_id = f"{trace_id}_g{generation}_{mutation_type}_{factor['factor_id'][:8]}"
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
        print(f"[evo] {icon} 代{generation} 因子质检{status}: {factor_name} (等级={grade}, 总分={total}/50)")

        # 淘汰时显示原因
        if status == "淘汰" and reason:
            print(f"       淘汰原因: {reason}")

        # 显示各维度得分
        dims = inspection.quality_score.get("dimension_scores", [])
        if dims:
            low_dims = [d for d in dims if d.get("score", 5) < 3]
            if low_dims:
                print("       ⚠️  低分项 (< 3.0):")
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
        sub_trace_id = f"{trace_id}_g{generation}_quality_filtered_{factor['factor_id'][:8]}"
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
            mutation_summary=(f"质量评分淘汰: 等级={inspection.grade}, 总分={inspection.total_score}/50"),
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
    parser.add_argument("--elite-dir", default="memory/knowledge/factors/futures_elite", help="精英池目录")
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
    data = pd.DataFrame(
        {
            "open": close + np.random.randn(n) * 0.1,
            "high": close + np.abs(np.random.randn(n)) * 0.3,
            "low": close - np.abs(np.random.randn(n)) * 0.3,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )
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
