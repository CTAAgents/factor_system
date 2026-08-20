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
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
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
        market: 市场类型（futures）
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


from .contracts import (  # noqa: E402 — 延迟导入规避循环依赖
    DEFAULT_BUDGET_CONFIG,
    BudgetConfig,
    EvolutionState,
    FactorCorrelation,
    FactorEvaluation,
    FactorProgram,
)
from .audit import FactorAuditReport  # noqa: E402
from .evaluation_chain import (  # noqa: E402
    EvaluationChain,
)
from .experience_chain import ExperienceChain, ParentFailureContext  # noqa: E402
from .macro_evolution import get_default_llm_client  # noqa: E402
from .seed_pool import SeedPool  # noqa: E402
from .state import EvolutionStateManager, generate_trace_id  # noqa: E402
from .success_pattern import SuccessPatternReport  # noqa: E402
from .verifier import FactorVerifier, get_global_verifier  # noqa: E402

# ─── UCT 协作类（34 计划 C 阶段 Phase 47a：EvolutionUctMixin 组合式重构为
#    UctSelector，主类组合持有 + 转发桩兼容；UCT_EXPLORATION_C 单一事实源
#    仍在本模块，此处 re-export 保持测试 `from ...evolution_loop import
#    UCT_EXPLORATION_C` 兼容） ──
from .evolution_uct import UctSelector, UCT_EXPLORATION_C  # noqa: E402

# ─── 领域 J trace Mixin（34 计划 Phase 46b：evolution_trace.py 迁移，
#    _QualityInspectionResult re-export 保持测试 import 兼容） ──
from .evolution_trace import TraceRecorder, _QualityInspectionResult  # noqa: E402

# ─── 领域 G 演化通道协作类（34 计划 C 阶段 Phase 47g：EvolutionChannelsMixin
#    组合式重构为 EvolutionChannels，主类组合持有 + 转发桩/property 兼容） ──
from .evolution_channels import EvolutionChannels  # noqa: E402

# ─── 领域 D 种子/横截面协作类（34 计划 C 阶段 Phase 47h：EvolutionSeedsMixin
#    组合式重构为 SeedManager，主类组合持有 + 转发桩/property 兼容） ──
from .evolution_seeds import SeedManager  # noqa: E402

# ─── 领域 E 审计/验证 Mixin（34 计划 Phase 46e：evolution_audit.py 迁移） ──
from .evolution_audit import AuditPipeline  # noqa: E402

# ─── 领域 F 定期评审/数据质量 Mixin（34 计划 Phase 46f：evolution_review.py 迁移） ──
from .evolution_review import FactorReviewer  # noqa: E402

# ─── 领域 H 候选预筛协作类（C 阶段 Phase 47b：evolution_prefilter.py 改造） ──
from .evolution_prefilter import CandidatePrefilter  # noqa: E402

# ─── 领域 C 精英晋升/持久化 Mixin（34 计划 Phase 46h：evolution_promote.py 迁移） ──
from .evolution_promote import EliteStore  # noqa: E402

# ─── 领域 B 候选准入链协作类（34 计划 C 阶段 Phase 47i：EvolutionCandidateMixin
#    组合式重构为 CandidateProcessor，主类组合持有 + 转发桩/property 兼容；
#    C 阶段 9 协作类收官，继承链清零） ──
from .evolution_candidate import CandidateProcessor  # noqa: E402

# GAP-070: 质检链信号缓存容量上限（LRU，超出淘汰最久未使用项）。
# 每条目为一份完整面板信号（~4MB/104品种×5163日），64 条上限覆盖单候选
# L1/极值扰动/消融 baseline/鲁棒性 baseline/SHAP 全部复用场景。
_QC_SIGNAL_CACHE_MAX_ENTRIES: int = 64

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
            elite_dir="memory/knowledge/factors/futures_elite",
        )
        result = loop.run()

    34 计划（plans/34-evolution-loop-refactor-inventory.md）C 阶段：领域 Mixin
    组合式重构为协作类（组合持有），Phase 47a-47i 全部交付——UctSelector（47a）/
    CandidatePrefilter（47b）/EliteStore（47c）/AuditPipeline（47d）/
    TraceRecorder（47e）/FactorReviewer（47f）/EvolutionChannels（47g）/
    SeedManager（47h）/CandidateProcessor（47i），**继承链清零**，主类组合持有
    9 个协作类实例 + 转发桩/property 兼容层，公开 API 与行为等价不变。
    """

    def __init__(
        self,
        data: pd.DataFrame,
        forward_returns: np.ndarray,
        elite_dir: str | Path | None = None,
        memory_dir: str | Path = "memory/evolution",
        inject_dir: str | Path = "memory/knowledge/factors/l1_injected",
        factor_pool_path: str | Path = "memory/knowledge/factors/factor_pool.json",
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
        holdout_panel: Optional[dict[str, pd.DataFrame]] = None,
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
        # 演化模式解析：读取 FTSConfig.evolution_mode（hybrid / operator_first / batch）
        from fts.config.settings import get_config

        _raw_mode = getattr(get_config(), "evolution_mode", "hybrid")
        self.evolution_mode = _raw_mode
        self.factor_db_path = factor_db_path
        self._is_cross_section = cross_section_data is not None

        # v2.59.0 (GAP-F03): 期货横截面模式自动注入板块映射（板块/产业链中性化）
        # 从 FUTURES_SECTOR_MAP 反向构建 {symbol: sector}；futures_neutralization=false
        # 或已显式传入 industry_map 时跳过。
        if self._is_cross_section and market == "futures" and self.industry_map is None:
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

        # ── GAP-160 (v3.0.0+7): 盲测池 panel（symbol_holdout 审计用，真外延泛化）──
        # 未显式传入且为 futures 横截面模式时自动构建（FUTURES_HOLDOUT 15 品种，
        # 与训练池零重叠）；失败容错 None → symbol_holdout 回退训练池内留出。
        self.holdout_panel = holdout_panel
        if self.holdout_panel is None and self._is_cross_section and market == "futures":
            try:
                from fts.config.settings import get_config as _get_panel_cfg
                from fts.data import FTSDataProvider
                from fts.data_futures import FUTURES_HOLDOUT

                _days = int(getattr(_get_panel_cfg(), "l2_panel_days", 750) or 750)
                self.holdout_panel, _ = FTSDataProvider().get_futures_panel(
                    symbols=FUTURES_HOLDOUT,
                    days=_days,
                )
                logger.info(
                    "[EvolutionLoop][GAP-160] 盲测池 panel 构建完成: %d 品种",
                    len(self.holdout_panel or {}),
                )
            except Exception as e:  # noqa: BLE001 — 盲测池构建失败回退训练池内留出
                logger.warning("[EvolutionLoop][GAP-160] 盲测池 panel 构建失败，回退训练池内留出: %s", e)
                self.holdout_panel = None

        # ── 市场隔离: 自动按 market 选择 elite 目录 ──
        if elite_dir is None:
            elite_dir = "memory/knowledge/factors/futures_elite"
        self.elite_dir = Path(elite_dir)
        self.elite_dir.mkdir(parents=True, exist_ok=True)
        self.inject_dir = Path(inject_dir)
        self.factor_pool_path = Path(factor_pool_path)
        self.memory_dir = Path(memory_dir)
        self._budget: BudgetConfig = budget or DEFAULT_BUDGET_CONFIG

        # ── P1-3 (Phase 3, 26 计划 §8): 提前达标停止（保守默认关闭） ──
        # budget 未显式配置时回退 FTSConfig（env FTS_EVOLUTION_STOP_* 可控）；
        # DEFAULT_BUDGET_CONFIG 不含这两个键，故默认走 FTSConfig（enabled=False）。
        # C 阶段 47a：解析结果传给 UctSelector 构造（状态装配已随迁协作类）。
        try:
            from fts.config.settings import get_config as _get_stop_cfg

            _stop_cfg = _get_stop_cfg()
            _stop_enabled_cfg = bool(getattr(_stop_cfg, "evolution_stop_enabled", False))
            _stop_k_cfg = int(getattr(_stop_cfg, "evolution_stop_consecutive_empty_generations", 5))
        except Exception:
            _stop_enabled_cfg = False
            _stop_k_cfg = 5
        _stop_enabled = bool(self.budget.get("evolution_stop_enabled", _stop_enabled_cfg))
        _stop_k = int(self.budget.get("evolution_stop_consecutive_empty_generations", _stop_k_cfg))
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
            self._micro_staged_evolution = bool(getattr(_micro_cfg, "micro_staged_evolution", True))
            self._micro_coarse_trials = int(getattr(_micro_cfg, "micro_coarse_trials", 20))
            self._micro_coarse_ic_floor = float(getattr(_micro_cfg, "micro_coarse_ic_floor", 0.02))
            # GAP-I305（v2.72.0）：衰减自动退役配置
            self._decay_observe_slope = float(getattr(_micro_cfg, "decay_observe_slope", 0.10))
            self._decay_retire_slope = float(getattr(_micro_cfg, "decay_retire_slope", 0.20))
            self._decay_slope_min_points = int(getattr(_micro_cfg, "decay_slope_min_points", 6))
            self._decay_auto_retire_enabled = bool(getattr(_micro_cfg, "decay_auto_retire_enabled", True))

        except Exception:
            # 配置读取失败时采用模块默认值，不阻断演化
            self._micro_staged_evolution = True
            self._micro_coarse_trials = 20
            self._micro_coarse_ic_floor = 0.02
            self._decay_observe_slope = 0.10
            self._decay_retire_slope = 0.20
            self._decay_slope_min_points = 6
            self._decay_auto_retire_enabled = True

        # 子模块
        self.state_manager = EvolutionStateManager(self.memory_dir)
        self.experience_chain = ExperienceChain(self.memory_dir)
        self.evaluation_chain = EvaluationChain()

        # 子模块: 因子质检过滤器 (Phase A.1 集成)
        # 使用 _QualityInspectionCompat 替代已删除的 pipeline.FactorQualityInspection
        self.quality_inspector = _QualityInspectionCompat(
            card_config=quality_card_config,
            min_grade=quality_min_grade,
        )

        # 子模块: 数据质量监控器 (Phase B.1 集成)
        from ..monitor.data_quality_monitor import DataQualityMonitor

        self.data_quality_monitor = DataQualityMonitor()

        # Phase B.1: 注册到 HTTP 指标端点
        from ..monitor import set_data_quality_monitor

        set_data_quality_monitor(self.data_quality_monitor)

        # 子模块: 逻辑监控 (Phase C.2 集成)
        from ..monitor.logic_monitor import LogicMonitor

        self.logic_monitor = LogicMonitor()

        # GAP-I305 (v2.72.0): 反馈闭环（FACTOR_DECAY 事件 → 衰减自动退役联动）
        from .feedback_loop import FeedbackLoop

        self.feedback_loop = FeedbackLoop()

        # 状态
        # ── C 阶段 47a: UCT 协作类（UctSelector）——领域状态随迁协作类，
        # 主循环仅持 _consecutive_low_ic 可变引用（box），经 property 读写。
        self._low_ic_box: list[int] = [0]
        # 生成端去重前置缓存（GAP-135 前置）：{normalized_code}，懒加载于首次
        # 演化后代检查时扫描 elite 池构建，运行中动态更新（本 run 已生成/已评估
        # 的表达式并入），避免重复表达式重复走评估链浪费算力。
        self._seen_expression_norms: Optional[set[str]] = None
        self._uct_selector = UctSelector(
            budget=self.budget,
            low_ic_box=self._low_ic_box,
            evolution_stop_enabled=_stop_enabled,
            evolution_stop_k=_stop_k,
        )
        # ── C 阶段 47b: 候选预筛协作类（CandidatePrefilter）——领域 H 纯读
        # 全局上下文；上下文可被主类/测试动态重赋值，故注入 owner 动态读取。
        self._candidate_prefilter = CandidatePrefilter(owner=self)
        # ── C 阶段 47d: 审计/验证管线协作类（AuditPipeline）——领域 E 组件与
        # _signal_cache（34 §8.3 归本类，CandidateProcessor 经 property 转发共享）
        # 随迁构造；audit_config 为 __init__ 注入参数。
        self._audit_pipeline = AuditPipeline(owner=self, audit_config=audit_config)
        # ── C 阶段 47e: trace 记录/经验链协作类（TraceRecorder）——领域 J 状态
        # 随迁构造，experience_chain/memory_dir/state_manager/market 经 owner 读取。
        self._trace_recorder = TraceRecorder(owner=self, experiment_log_dir=experiment_log_dir)
        # ── C 阶段 47f: 定期评审/数据质量协作类（FactorReviewer）——领域 F 无
        # 独享状态，组件经 owner 动态读取。
        self._factor_reviewer = FactorReviewer(owner=self)
        # ── C 阶段 47g: 演化通道协作类（EvolutionChannels）——领域 G 组件
        # （macro_evolver/feature_ops_engine/feature_importance_analyzer）随迁
        # 构造（依赖 owner.llm_client/experience_chain/budget，须在 llm_client
        # 与 experience_chain 装配之后），上下文经 owner 动态读取。
        self._evolution_channels = EvolutionChannels(owner=self)
        # ── C 阶段 47h: 种子管理/横截面协作类（SeedManager）——领域 D 状态
        # （_barra_exposures_cache/_barra_exposures_attempted）随迁构造，
        # 上下文（含可变 industry_map/cap_map）经 owner 动态读取。
        self._seed_manager = SeedManager(owner=self)
        # ── C 阶段 47i: 候选准入链协作类（CandidateProcessor）——领域 B 状态
        # （_prior_evaluations）随迁构造，_consecutive_low_ic（主循环持有，
        # 经 _low_ic_box property 共享）/_signal_cache（归 AuditPipeline）经
        # owner 动态读写。C 阶段 9 协作类收官，继承链清零。
        self._candidate_processor = CandidateProcessor(owner=self)
        # ── C 阶段 47c: 精英晋升/持久化协作类（EliteStore）——领域 C 重状态
        # 随迁协作类构造（_repo/_cluster_*/_l2_*/orthogonal_basis/high_ic_screener/
        # elite_tracker），上下文经 owner 动态读取；测试对 loop 的属性读写与
        # _get_repo 等 mock 经 property 转发 + owner 转发生效。
        self._elite_store = EliteStore(owner=self)

        # GAP-I201 (v2.65.0): 批量挖掘配置（evolution_mode="batch" 时生效）
        from fts.config.settings import get_config as _batch_cfg

        _cfg_batch = _batch_cfg()
        self.batch_size: int = int(getattr(_cfg_batch, "batch_size", 20))
        self.batch_max_candidates: int = int(getattr(_cfg_batch, "batch_max_candidates", 5))
        self.batch_max_workers: int = int(getattr(_cfg_batch, "batch_max_workers", 4))
        self.batch_random_seed: int = int(getattr(_cfg_batch, "batch_random_seed", 42))
        # batch 模式批量生成游标（_batch_generate_one 内自增，保证方法轮换 + seed 递增）
        self._batch_idx: int = 0

    @property
    def budget(self) -> BudgetConfig:
        """演化预算（含熔断阈值）。重绑时经 setter 同步传播到 UctSelector（GAP-115，v2.104.0+14）。

        UctSelector 是唯一经构造注入 budget 的协作类（其余协作类经 ``owner.budget``
        动态读主类引用，不受影响）；重绑不传播会导致失败率熔断仍按 DEFAULT(0.95)
        判定，使 cli.py 夜间任务「强制跑满世代数」（失败率阈值 1.0 / env 覆盖）失效。
        """
        return self._budget

    @budget.setter
    def budget(self, value: BudgetConfig) -> None:
        self._budget = value
        uct = getattr(self, "_uct_selector", None)
        if uct is not None:
            uct.budget = value  # GAP-115: 重绑同步传播，熔断判定使用最新阈值

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
            # ── 45 计划候选①：种子评估已独立至 l2_seed_promotion_job（每日 02:00）。
            #    本任务直接读 elite 池父因子（含当日刚晋升种子）+ 读取持久化相关性索引。
            seed_correlations = self._load_seed_correlation_index()
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

                # 45 计划候选②：batch 批量挖掘已独立至 l2_batch_mining_job（周日 06:00），
                # run() 不再按 evolution_mode=batch 走批量漏斗，统一走单因子演化路径。

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

                # ── Step 1.35: 生成端去重前置（GAP-135 前置，评估链前拦截重复表达式） ──
                # 与 elite 池既有因子或本 run 已生成/已评估的表达式规范化一致时直接丢弃，
                # 避免重复因子跑完整评估链（回测/审计/走航）浪费算力；晋升端去重（
                # _promote_to_elite GAP-135）保留为兜底。
                if self._is_generated_duplicate(new_factor):
                    logger.info(
                        "[L2-dedup] 生成端拦截重复表达式 [%s] gen=%d method=%s",
                        new_factor.get("name", "?"),
                        generation,
                        evolution_method,
                    )
                    self._record_failure_trace(
                        new_factor,
                        generation,
                        evolution_method,
                        "生成端同表达式去重（前置拦截）",
                        [],
                        trace_id,
                    )
                    self._record_experiment_variant(
                        new_factor, parent, generation, evolution_method, evolution_summary, None, "expr_duplicate"
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

            # ── Phase 2 (P1-2): 导出结构化实验日志（非阻塞） ──
            self._export_experiment_log(
                run_id,
                trace_id,
                state.get("last_generation", 0),
            )

    def run_seed_stage(
        self,
        trace_id: str,
        state: EvolutionState,
        elite_ids: list[str],
    ) -> tuple[int, list[FactorCorrelation], list[FactorProgram]]:
        """种子评估晋升独立入口（45 计划候选①，先种子后演化）。

        Step 0 种子相关性预检（轻量扫描，仅标记不删除）+ Step 1 评估晋升
        （合格直接晋升 elite）+ 父因子选择（直接读 elite 池，含当日刚晋升种子）。
        独立 job（l2_seed_promotion_job）与演化 run() 共用本入口；run() 演进时
        仅消费返回值，不再内联种子逻辑。

        Args:
            trace_id: 全链路 trace_id
            state: 演化状态（读取晋升计数，不重置）
            elite_ids: 精英因子 ID 列表（方法内追加晋升结果）

        Returns:
            (promoted_seeds, seed_correlations, parent_seeds)
        """
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

        # 45 计划候选①：父因子优先取本次晋升种子（只有高IC种子才值得演化），
        # 无新晋升时回退读 elite 池（含既有精英因子），保持组件化等价。
        parent_seeds = [s for s in seeds if s["factor_id"] in elite_ids]
        if not parent_seeds:
            parent_seeds = cast(list[FactorProgram], self._load_elite_parent_factors())
            print("[evo] 无合格父因子，跳过演化循环")
        else:
            print(f"[evo] 基于 elite 池 {len(parent_seeds)} 个因子作为父因子")

        # 45 计划候选①：种子评估独立任务（l2_seed_promotion_job）负责将
        # 相关性预检结果持久化，供 run() 演化任务与 L3 批量读取（先验数据）。
        if seed_correlations:
            self._write_seed_correlation_index(seed_correlations, trace_id)

        return promoted_seeds, seed_correlations, parent_seeds
    def _load_seed_correlation_index(self) -> list[FactorCorrelation]:
        """读取持久化的 L2 种子相关性索引（45 计划候选①）。

        种子评估已独立至 l2_seed_promotion_job，run() 演化任务读取其写入的
        elite 目录索引文件（_l2_seed_correlation_index.json）作为先验数据，
        缺失时返回空列表（不阻断演化）。
        """
        try:
            import json as _json

            index_path = self.elite_dir / "_l2_seed_correlation_index.json"
            if not index_path.exists():
                return []
            data = _json.loads(index_path.read_text(encoding="utf-8"))
            return list(data.get("correlations", []))
        except Exception as e:  # noqa: BLE001
            logger.debug("读取种子相关性索引失败: %s", e)
            return []


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

    def run_batch_stage(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
        state: dict[str, Any],
        elite_ids: list[str],
        seed_correlations: list[FactorCorrelation],
    ) -> bool:
        """batch 批量挖掘独立入口（45 计划候选②）。

        供独立 job（l2_batch_mining_job，周日 06:00）与 run() batch 分支共用。
        熔断隔离：_process_candidate 会写主循环 _consecutive_low_ic（连续低 IC 熔断），
        独立 batch 任务失败不应污染主循环熔断状态 → 执行前保存、结束后恢复。
        """
        saved_low_ic = self._consecutive_low_ic
        try:
            return self._run_batch_generation(
                parent,
                generation,
                trace_id,
                state,
                elite_ids,
                seed_correlations,
            )
        finally:
            self._consecutive_low_ic = saved_low_ic

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

    # ── C 阶段 47b: 领域 H 候选预筛方法转发桩（行为等价，委托
    #    self._candidate_prefilter，兼容测试直接调用/patch，见 34 §8.5） ──

    def _quick_prefilter(
        self,
        factor: FactorProgram,
        trace_id: str,
    ) -> tuple[bool, str, float]:
        return self._candidate_prefilter._quick_prefilter(factor, trace_id)

    def _cross_section_prefilter(
        self,
        factor: FactorProgram,
        trace_id: str,
    ) -> tuple[bool, str, float]:
        return self._candidate_prefilter._cross_section_prefilter(factor, trace_id)

    def _check_factor_runtime(
        self,
        factor: FactorProgram,
    ) -> tuple[bool, str]:
        return self._candidate_prefilter._check_factor_runtime(factor)

    # ── C 阶段 47f: 领域 F 定期评审/数据质量方法转发桩（行为等价，委托
    #    self._factor_reviewer，兼容测试直接调用/patch，见 34 §8.5） ──

    def _run_periodic_factor_review(
        self,
        elite_ids: list[str],
        trace_id: str,
    ) -> None:
        self._factor_reviewer._run_periodic_factor_review(elite_ids, trace_id)

    def _get_factor_data_for_review(self, factor_id: str) -> Optional[dict[str, float]]:
        return self._factor_reviewer._get_factor_data_for_review(factor_id)

    def _register_factor_baseline(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
    ) -> None:
        self._factor_reviewer._register_factor_baseline(factor, evaluation)

    def _check_factor_data_quality(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
    ) -> list[Any]:
        return self._factor_reviewer._check_factor_data_quality(factor, evaluation)

    # ── C 阶段 47g: 领域 G 演化通道方法转发桩（行为等价，委托
    #    self._evolution_channels，兼容测试直接调用/patch，见 34 §8.5） ──

    def _run_gp_evolution(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
    ) -> tuple[FactorProgram, str]:
        return self._evolution_channels._run_gp_evolution(parent, generation, trace_id)

    def _run_deep_evolution(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
        model_kind: str = "gru",
    ) -> tuple[FactorProgram, str]:
        return self._evolution_channels._run_deep_evolution(parent, generation, trace_id, model_kind)

    def _generate_operator_factor(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
    ) -> tuple[FactorProgram, str]:
        return self._evolution_channels._generate_operator_factor(parent, generation, trace_id)

    def _try_operator_engine_evolution(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
    ) -> Optional[FactorProgram]:
        return self._evolution_channels._try_operator_engine_evolution(parent, generation, trace_id)

    # ── C 阶段 47g: 领域 G 组件属性 property 转发（兼容测试对 loop.macro_evolver
    #    / feature_ops_engine / feature_importance_analyzer 的直接读写，见 34 §8.5） ──

    @property
    def macro_evolver(self) -> Any:
        """领域 G 宏观演化器（经 _evolution_channels 组合持有，property 转发）。"""
        return self._evolution_channels.macro_evolver

    @property
    def feature_ops_engine(self) -> Any:
        """领域 G GP 特征演化引擎（经 _evolution_channels 组合持有，property 转发）。"""
        return self._evolution_channels.feature_ops_engine

    @property
    def feature_importance_analyzer(self) -> Any:
        """领域 G 特征重要性分析器（经 _evolution_channels 组合持有，property 转发）。"""
        return self._evolution_channels.feature_importance_analyzer

    # ── C 阶段 47h: 领域 D 种子管理/横截面方法转发桩（行为等价，委托
    #    self._seed_manager，兼容测试直接调用/patch，见 34 §8.5） ──

    def _evaluate_and_promote_seeds(
        self,
        seeds: list[FactorProgram],
        trace_id: str,
        state: EvolutionState,
        elite_ids: list[str],
        seed_correlations: Optional[list[FactorCorrelation]] = None,
    ) -> int:
        return self._seed_manager._evaluate_and_promote_seeds(
            seeds,
            trace_id,
            state,
            elite_ids,
            seed_correlations,
        )

    def _merge_l1_candidates(
        self,
        seeds: list[FactorProgram],
        trace_id: str,
    ) -> list[FactorProgram]:
        return self._seed_manager._merge_l1_candidates(seeds, trace_id)

    def _run_seed_correlation_check(
        self,
        seeds: list[FactorProgram],
        trace_id: str,
    ) -> list[FactorCorrelation]:
        return self._seed_manager._run_seed_correlation_check(seeds, trace_id)

    def _build_barra_exposures(self) -> Optional[dict[str, Any]]:
        return self._seed_manager._build_barra_exposures()

    def _build_vol_map(self) -> Optional[dict[str, float]]:
        return self._seed_manager._build_vol_map()

    def _evaluate_cross_section(self, factor: FactorProgram, trace_id: str) -> FactorEvaluation:
        return self._seed_manager._evaluate_cross_section(factor, trace_id)

    def run_microstructure_promotion(
        self,
        symbols: Optional[list[str]] = None,
        limit: int = 0,
        trace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._seed_manager.run_microstructure_promotion(symbols, limit, trace_id)

    # ── C 阶段 47h: 领域 D 状态属性 property 转发（兼容测试对 loop.
    #    _barra_exposures_cache/_barra_exposures_attempted 的直接读写，见 34 §8.5） ──

    @property
    def _barra_exposures_cache(self) -> Optional[dict[str, Any]]:
        """领域 D Barra 风格暴露缓存（经 _seed_manager 组合持有，property 转发）。"""
        return self._seed_manager._barra_exposures_cache

    @_barra_exposures_cache.setter
    def _barra_exposures_cache(self, value: Optional[dict[str, Any]]) -> None:
        self._seed_manager._barra_exposures_cache = value

    @property
    def _barra_exposures_attempted(self) -> bool:
        """领域 D Barra 暴露构建尝试标记（经 _seed_manager 组合持有，property 转发）。"""
        return self._seed_manager._barra_exposures_attempted

    @_barra_exposures_attempted.setter
    def _barra_exposures_attempted(self, value: bool) -> None:
        self._seed_manager._barra_exposures_attempted = value

    # ── C 阶段 47i: 领域 B 候选准入链方法转发桩（行为等价，委托
    #    self._candidate_processor，兼容测试直接调用/patch，见 34 §8.5） ──

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
        return self._candidate_processor._process_candidate(
            factor,
            parent,
            generation,
            evolution_method,
            evolution_summary,
            state,
            elite_ids,
            trace_id,
            seed_correlations,
        )

    # ── C 阶段 47i: 领域 B 状态属性 property 转发（兼容测试对 loop.
    #    _prior_evaluations 的直接读写，见 34 §8.5） ──

    @property
    def _prior_evaluations(self) -> list[FactorEvaluation]:
        """领域 B 历史评估列表（经 _candidate_processor 组合持有，property 转发）。"""
        return self._candidate_processor._prior_evaluations

    @_prior_evaluations.setter
    def _prior_evaluations(self, value: list[FactorEvaluation]) -> None:
        self._candidate_processor._prior_evaluations = value

    # ── C 阶段 47e: 领域 J trace/经验链/实验日志方法转发桩（行为等价，委托
    #    self._trace_recorder，兼容测试直接调用/patch，见 34 §8.5） ──

    def _build_parent_failure_ctx(self, parent: FactorProgram) -> Optional[ParentFailureContext]:
        return self._trace_recorder._build_parent_failure_ctx(parent)

    def _build_success_pattern_report(self) -> Optional[SuccessPatternReport]:
        return self._trace_recorder._build_success_pattern_report()

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
        self._trace_recorder._record_experiment_variant(
            factor, parent, generation, method, summary, evaluation, outcome, quality_grade
        )

    def _export_experiment_log(
        self,
        run_id: str,
        trace_id: str,
        generations_completed: int,
    ) -> Optional[Path]:
        return self._trace_recorder._export_experiment_log(run_id, trace_id, generations_completed)

    def _record_audit_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        audit_report: FactorAuditReport,
        evaluation: Optional[FactorEvaluation] = None,
    ) -> None:
        self._trace_recorder._record_audit_failed_trace(factor, generation, trace_id, audit_report, evaluation)

    def _record_ablation_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        ablation_result: dict[str, Any],
    ) -> None:
        self._trace_recorder._record_ablation_failed_trace(factor, generation, trace_id, ablation_result)

    def _record_robustness_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        robustness_result: dict[str, Any],
    ) -> None:
        self._trace_recorder._record_robustness_failed_trace(factor, generation, trace_id, robustness_result)

    def _record_causal_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        causal_result: dict[str, Any],
    ) -> None:
        self._trace_recorder._record_causal_failed_trace(factor, generation, trace_id, causal_result)

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
        self._trace_recorder._record_success_trace(
            factor, generation, mutation_type, mutation_summary, evaluation, lessons, trace_id
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
        self._trace_recorder._record_failure_trace(
            factor, generation, mutation_type, mutation_summary, failure_reasons, trace_id, evaluation
        )

    def _log_inspection_detail(
        self,
        factor: FactorProgram,
        inspection: _QualityInspectionResult,
        status: str,
        generation: int,
    ) -> None:
        self._trace_recorder._log_inspection_detail(factor, inspection, status, generation)

    def _record_quality_filtered_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        inspection: _QualityInspectionResult,
        evaluation: Optional[FactorEvaluation] = None,
    ) -> None:
        self._trace_recorder._record_quality_filtered_trace(factor, generation, trace_id, inspection, evaluation)

    # ── C 阶段 47e: 领域 J 实例属性 property 转发（兼容测试直接读写；
    #    状态实际由 self._trace_recorder 持有，见 34 §8.3） ──

    @property
    def _success_pattern_cache(self) -> Optional[Any]:
        return self._trace_recorder._success_pattern_cache

    @_success_pattern_cache.setter
    def _success_pattern_cache(self, v: Optional[Any]) -> None:
        self._trace_recorder._success_pattern_cache = v

    @property
    def _experiment_log_dir(self) -> str:
        return self._trace_recorder._experiment_log_dir

    @_experiment_log_dir.setter
    def _experiment_log_dir(self, v: str) -> None:
        self._trace_recorder._experiment_log_dir = v

    @property
    def _experiment_variants(self) -> list[dict]:
        return self._trace_recorder._experiment_variants

    @_experiment_variants.setter
    def _experiment_variants(self, v: list[dict]) -> None:
        self._trace_recorder._experiment_variants = v

    # ── C 阶段 47d: 领域 E 审计/验证管线方法转发桩（行为等价，委托
    #    self._audit_pipeline，兼容测试直接调用/patch，见 34 §8.5） ──

    def _run_backtest_pipeline(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> Optional[dict[str, Any]]:
        return self._audit_pipeline._run_backtest_pipeline(factor, evaluation, trace_id)

    @staticmethod
    def _build_wf_config(data: Any) -> dict[str, Any]:
        return AuditPipeline._build_wf_config(data)

    def _run_walkforward_oos(self, factor: FactorProgram) -> Optional[dict[str, Any]]:
        return self._audit_pipeline._run_walkforward_oos(factor)

    def _run_factor_audit(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> FactorAuditReport:
        pipeline = getattr(self, "_audit_pipeline", None)
        if pipeline is not None:
            return pipeline._run_factor_audit(factor, evaluation, trace_id)
        # 兼容类级未绑定调用（测试 object.__new__ 绕过 __init__ 装配场景）
        return AuditPipeline._run_factor_audit(self, factor, evaluation, trace_id)

    @staticmethod
    def _is_blocking_ablation(ab: dict[str, Any]) -> bool:
        return AuditPipeline._is_blocking_ablation(ab)

    def _run_ablation_check(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> dict[str, Any]:
        return self._audit_pipeline._run_ablation_check(factor, evaluation, trace_id)

    def _run_robustness_check(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> dict[str, Any]:
        return self._audit_pipeline._run_robustness_check(factor, evaluation, trace_id)

    def _run_shap_analysis(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> dict[str, Any]:
        return self._audit_pipeline._run_shap_analysis(factor, evaluation, trace_id)

    def _run_causal_validation(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> dict[str, Any]:
        return self._audit_pipeline._run_causal_validation(factor, evaluation, trace_id)

    # ── C 阶段 47d: 领域 E 实例属性 property 转发（兼容测试直接读写；
    #    状态实际由 self._audit_pipeline 持有，见 34 §8.3） ──

    @property
    def _signal_cache(self) -> Any:
        return self._audit_pipeline._signal_cache

    @_signal_cache.setter
    def _signal_cache(self, v: Any) -> None:
        self._audit_pipeline._signal_cache = v

    @property
    def auditor(self) -> Any:
        pipeline = getattr(self, "_audit_pipeline", None)
        if pipeline is not None:
            return pipeline.auditor
        # 兼容测试 object.__new__ 绕过 __init__（无 _audit_pipeline 装配）场景
        return getattr(self, "_auditor_standalone", None)

    @auditor.setter
    def auditor(self, v: Any) -> None:
        pipeline = getattr(self, "_audit_pipeline", None)
        if pipeline is not None:
            pipeline.auditor = v
        else:
            object.__setattr__(self, "_auditor_standalone", v)

    @property
    def backtest_pipeline(self) -> Any:
        return self._audit_pipeline.backtest_pipeline

    @backtest_pipeline.setter
    def backtest_pipeline(self, v: Any) -> None:
        self._audit_pipeline.backtest_pipeline = v

    @property
    def ablation_experiment(self) -> Any:
        return self._audit_pipeline.ablation_experiment

    @ablation_experiment.setter
    def ablation_experiment(self, v: Any) -> None:
        self._audit_pipeline.ablation_experiment = v

    @property
    def robustness_tester(self) -> Any:
        return self._audit_pipeline.robustness_tester

    @robustness_tester.setter
    def robustness_tester(self, v: Any) -> None:
        self._audit_pipeline.robustness_tester = v

    @property
    def shap_analyzer(self) -> Any:
        return self._audit_pipeline.shap_analyzer

    @shap_analyzer.setter
    def shap_analyzer(self, v: Any) -> None:
        self._audit_pipeline.shap_analyzer = v

    @property
    def causal_validator(self) -> Any:
        return self._audit_pipeline.causal_validator

    @causal_validator.setter
    def causal_validator(self, v: Any) -> None:
        self._audit_pipeline.causal_validator = v

    # ── C 阶段 47c: 领域 C 精英晋升/持久化方法转发桩（行为等价，委托
    #    self._elite_store，兼容测试直接调用/patch，见 34 §8.5） ──

    def _promote_to_elite(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        seed_correlations: Optional[list[FactorCorrelation]] = None,
        quality_score: Optional[dict] = None,
        audit_report: Optional[FactorAuditReport] = None,
        shadow_observe: Optional[bool] = None,
    ) -> Optional[Path]:
        return self._elite_store._promote_to_elite(
            factor,
            evaluation,
            seed_correlations,
            quality_score,
            audit_report,
            shadow_observe,
        )

    def _write_to_duckdb(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        quality_score: Optional[dict] = None,
        seed_correlations: Optional[list[FactorCorrelation]] = None,
        audit_report: Optional[FactorAuditReport] = None,
        shadow_pool: Optional[dict] = None,
        qa_review: Optional[dict] = None,
    ) -> bool:
        return self._elite_store._write_to_duckdb(
            factor,
            evaluation,
            quality_score,
            seed_correlations,
            audit_report,
            shadow_pool,
            qa_review,
        )

    def _scan_elite_correlations(
        self,
        factor: FactorProgram,
        threshold: float,
        max_scan: int,
    ) -> list[dict[str, Any]]:
        return self._elite_store._scan_elite_correlations(factor, threshold, max_scan)

    def _check_elite_correlation(self, factor: FactorProgram) -> Optional[dict[str, Any]]:
        return self._elite_store._check_elite_correlation(factor)

    def _count_cluster_members(self, factor: FactorProgram) -> int:
        return self._elite_store._count_cluster_members(factor)

    def _orthogonalize_via_basis(self, factor: FactorProgram) -> Optional[dict[str, Any]]:
        store = getattr(self, "_elite_store", None)
        if store is not None:
            return store._orthogonalize_via_basis(factor)
        # 兼容类级未绑定调用（测试 EvolutionLoop._orthogonalize_via_basis(mock_loop, ...)）
        return EliteStore._orthogonalize_via_basis(self, factor)

    def _orthogonalize_candidate(
        self,
        factor: FactorProgram,
        pair: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return self._elite_store._orthogonalize_candidate(factor, pair)

    def _load_elite_parent_factors(self) -> list[dict[str, Any]]:
        return self._elite_store._load_elite_parent_factors()

    # ── 生成端去重前置（GAP-135 前置，plans 补充）：评估链前拦截重复表达式 ──

    def _build_seen_expression_norms(self) -> set[str]:
        """懒加载已见表达式规范化集合（本 run 去重基准）。

        扫描 elite_dir 全部 JSON 快照，收集 ``normalize_expression`` 规范化后的
        code（排除 ``_`` 前缀的辅助文件）。调用方按需构建一次并缓存于
        ``_seen_expression_norms``；异常时静默降级为空集（不阻断演化）。

        Returns:
            {normalized_code} 集合
        """
        from .evolution_promote import normalize_expression

        import json as _json

        norms: set[str] = set()
        try:
            if not self.elite_dir.exists():
                return norms
            for fp in sorted(self.elite_dir.glob("*.json")):
                if fp.name.startswith("_"):
                    continue
                try:
                    data = _json.loads(fp.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                code = data.get("code")
                if not code:
                    continue
                norm = normalize_expression(str(code))
                if norm:
                    norms.add(norm)
        except Exception as e:  # noqa: BLE001 — 去重降级不阻断演化
            logger.warning("[L2-dedup] 已见表达式集合构建失败（降级放行）: %s", e)
        return norms

    def _is_generated_duplicate(self, factor: FactorProgram) -> bool:
        """生成端同表达式去重判定（Step 1.35 前置拦截）。

        将后代因子 code 规范化后与已见集合比对（elite 池既有 + 本 run 已生成/
        已评估），命中返回 True（调用方直接丢弃，不进入评估链）。未命中则把该
        表达式并入已见集合（本 run 后续代相同表达式也拦截，防同批重复）。

        Args:
            factor: 演化生成的后代因子（含 code）

        Returns:
            True=与既有/已生成表达式重复，应拦截
        """
        from .evolution_promote import normalize_expression

        code = factor.get("code")
        if not code:
            return False
        norm = normalize_expression(str(code))
        if not norm:
            return False
        if self._seen_expression_norms is None:
            self._seen_expression_norms = self._build_seen_expression_norms()
        seen = self._seen_expression_norms
        if norm in seen:
            return True
        seen.add(norm)
        return False

    def _write_seed_correlation_index(
        self,
        seed_correlations: list[FactorCorrelation],
        trace_id: str,
    ) -> None:
        return self._elite_store._write_seed_correlation_index(seed_correlations, trace_id)

    def _get_repo(self) -> Any:
        return self._elite_store._get_repo()

    # ── C 阶段 47c: 领域 C 实例属性 property 转发（兼容测试直接读写；
    #    状态实际由 self._elite_store 持有，见 34 §8.3） ──

    @property
    def _repo(self) -> Optional[Any]:
        return self._elite_store._repo

    @_repo.setter
    def _repo(self, v: Optional[Any]) -> None:
        self._elite_store._repo = v

    @property
    def _cluster_quota_enabled(self) -> bool:
        return self._elite_store._cluster_quota_enabled

    @_cluster_quota_enabled.setter
    def _cluster_quota_enabled(self, v: bool) -> None:
        self._elite_store._cluster_quota_enabled = v

    @property
    def _cluster_max(self) -> int:
        return self._elite_store._cluster_max

    @_cluster_max.setter
    def _cluster_max(self, v: int) -> None:
        self._elite_store._cluster_max = v

    @property
    def _cluster_corr_threshold(self) -> float:
        return self._elite_store._cluster_corr_threshold

    @_cluster_corr_threshold.setter
    def _cluster_corr_threshold(self, v: float) -> None:
        self._elite_store._cluster_corr_threshold = v

    @property
    def _cluster_max_scan(self) -> int:
        return self._elite_store._cluster_max_scan

    @_cluster_max_scan.setter
    def _cluster_max_scan(self, v: int) -> None:
        self._elite_store._cluster_max_scan = v

    @property
    def _l2_elite_corr_threshold(self) -> float:
        return self._elite_store._l2_elite_corr_threshold

    @_l2_elite_corr_threshold.setter
    def _l2_elite_corr_threshold(self, v: float) -> None:
        self._elite_store._l2_elite_corr_threshold = v

    @property
    def _l2_elite_corr_max_scan(self) -> int:
        return self._elite_store._l2_elite_corr_max_scan

    @_l2_elite_corr_max_scan.setter
    def _l2_elite_corr_max_scan(self, v: int) -> None:
        self._elite_store._l2_elite_corr_max_scan = v

    @property
    def _l2_elite_corr_debug(self) -> bool:
        return self._elite_store._l2_elite_corr_debug

    @_l2_elite_corr_debug.setter
    def _l2_elite_corr_debug(self, v: bool) -> None:
        self._elite_store._l2_elite_corr_debug = v

    @property
    def _l2_elite_orthogonalize(self) -> bool:
        return self._elite_store._l2_elite_orthogonalize

    @_l2_elite_orthogonalize.setter
    def _l2_elite_orthogonalize(self, v: bool) -> None:
        self._elite_store._l2_elite_orthogonalize = v

    @property
    def _l2_orthogonal_residual_corr_max(self) -> float:
        return self._elite_store._l2_orthogonal_residual_corr_max

    @_l2_orthogonal_residual_corr_max.setter
    def _l2_orthogonal_residual_corr_max(self, v: float) -> None:
        self._elite_store._l2_orthogonal_residual_corr_max = v

    @property
    def _l2_orthogonal_min_retained_ratio(self) -> float:
        return self._elite_store._l2_orthogonal_min_retained_ratio

    @_l2_orthogonal_min_retained_ratio.setter
    def _l2_orthogonal_min_retained_ratio(self, v: float) -> None:
        self._elite_store._l2_orthogonal_min_retained_ratio = v

    @property
    def _l2_orthogonal_basis_enabled(self) -> bool:
        return self._elite_store._l2_orthogonal_basis_enabled

    @_l2_orthogonal_basis_enabled.setter
    def _l2_orthogonal_basis_enabled(self, v: bool) -> None:
        self._elite_store._l2_orthogonal_basis_enabled = v

    @property
    def _l2_orthogonal_basis_max_size(self) -> int:
        return self._elite_store._l2_orthogonal_basis_max_size

    @_l2_orthogonal_basis_max_size.setter
    def _l2_orthogonal_basis_max_size(self, v: int) -> None:
        self._elite_store._l2_orthogonal_basis_max_size = v

    @property
    def _l2_orthogonal_basis_min_sharpe(self) -> float:
        return self._elite_store._l2_orthogonal_basis_min_sharpe

    @_l2_orthogonal_basis_min_sharpe.setter
    def _l2_orthogonal_basis_min_sharpe(self, v: float) -> None:
        self._elite_store._l2_orthogonal_basis_min_sharpe = v

    @property
    def orthogonal_basis(self) -> Any:
        return self._elite_store.orthogonal_basis

    @orthogonal_basis.setter
    def orthogonal_basis(self, v: Any) -> None:
        self._elite_store.orthogonal_basis = v

    @property
    def high_ic_screener(self) -> Any:
        return self._elite_store.high_ic_screener

    @high_ic_screener.setter
    def high_ic_screener(self, v: Any) -> None:
        self._elite_store.high_ic_screener = v

    @property
    def elite_tracker(self) -> Any:
        return self._elite_store.elite_tracker

    @elite_tracker.setter
    def elite_tracker(self, v: Any) -> None:
        self._elite_store.elite_tracker = v

    # ── C 阶段 47a: UCT 域实例属性 property 转发（兼容测试直接读写；
    #    状态实际由 self._uct_selector 持有，见 34 §8.3） ──

    @property
    def _uct_stats(self) -> dict[str, dict[str, float]]:
        return self._uct_selector._uct_stats

    @_uct_stats.setter
    def _uct_stats(self, v: dict[str, dict[str, float]]) -> None:
        self._uct_selector._uct_stats = v

    @property
    def _consecutive_low_ic(self) -> int:
        return self._low_ic_box[0]

    @_consecutive_low_ic.setter
    def _consecutive_low_ic(self, v: int) -> None:
        self._low_ic_box[0] = v

    @property
    def _evolution_stop_enabled(self) -> bool:
        return self._uct_selector._evolution_stop_enabled

    @_evolution_stop_enabled.setter
    def _evolution_stop_enabled(self, v: bool) -> None:
        self._uct_selector._evolution_stop_enabled = v

    @property
    def _evolution_stop_k(self) -> int:
        return self._uct_selector._evolution_stop_k

    @_evolution_stop_k.setter
    def _evolution_stop_k(self, v: int) -> None:
        self._uct_selector._evolution_stop_k = v

    @property
    def _consecutive_empty_generations(self) -> int:
        return self._uct_selector._consecutive_empty_generations

    @_consecutive_empty_generations.setter
    def _consecutive_empty_generations(self, v: int) -> None:
        self._uct_selector._consecutive_empty_generations = v

    @property
    def _early_stop_last_count(self) -> int:
        return self._uct_selector._early_stop_last_count

    @_early_stop_last_count.setter
    def _early_stop_last_count(self, v: int) -> None:
        self._uct_selector._early_stop_last_count = v

    @property
    def _early_stop_reason(self) -> Optional[str]:
        return self._uct_selector._early_stop_reason

    @_early_stop_reason.setter
    def _early_stop_reason(self, v: Optional[str]) -> None:
        self._uct_selector._early_stop_reason = v

    # ── C 阶段 47a: UCT 域方法转发桩（兼容测试直接调用与 patch.object，
    #    run/_process_candidate 等 MRO 调用点零改动；实现委托 UctSelector） ──

    def _select_parent_uct(self, parents: list[FactorProgram]) -> FactorProgram:
        return self._uct_selector._select_parent_uct(parents)

    def _update_uct_stats(self, parent: FactorProgram, evaluation: FactorEvaluation) -> None:
        self._uct_selector._update_uct_stats(parent, evaluation)

    def _update_uct_failure(self, parent: FactorProgram) -> None:
        self._uct_selector._update_uct_failure(parent)

    def _check_circuit_breaker(self, state: EvolutionState) -> Optional[str]:
        return self._uct_selector._check_circuit_breaker(state)

    def _maybe_early_stop(self, state: EvolutionState) -> bool:
        return self._uct_selector._maybe_early_stop(state)


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
