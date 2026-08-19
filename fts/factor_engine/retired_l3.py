"""fts/factor_engine/retired_l3.py — 阶段 2 L3 组合侧退役登记（plans/57 §4.1 / §5.3）。

双系统切分完成：FTS 因子生产 → 信号矩阵输出；策略合成（信号合成/组合校验/
权重学习/资金分配/拥挤度）迁移至 Regime-Driven。以下 FTS L3 组合侧函数正式
**退役（retired）**——不再是 FTS 职责，禁止新增调用点；存量调用点经本登记
发出 DeprecationWarning，待调用图清零后物理删除（删除属里程碑操作，需全量回归）。

§4.1 退役清单：
  - futures_signal_pipeline.py（组合侧）: _compute_composite_scores /
    _compute_per_variety_weights / _apply_regime_weight_adjustment /
    _apply_regime_direction_bias / _generate_trading_advice* /
    _compute_holdout_validation / _load_l3_combo_*
  - portfolio_loop.py（策略侧）: synthesize_signals / _compute_elastic_net_weights /
    _compute_ml_ensemble_weights / _synthesize_bl_weights /
    regime_adaptive_weight_adjustment / build_combo / _cap_safety_valve /
    _validate_combo_sharpe / _run_sharpe_randomization_test / decay_test /
    apply_turnover_penalty / _apply_sticky_constraints / _compute_subchain_exposure /
    _merge_gate_scale_into_modulation / _greedy_select_by_correlation /
    _dedup_factors_by_chain* / _filter_*
  - 整体迁移模块: weight_learning.py / capital_allocator.py / regime_crowding.py
    （平移 RD 后 FTS 侧标记弃用）

退役门槛（§5.2/§5.3）已满足：阶段 1 双轨对账真实数据全门槛通过
（方向一致率 100% / 敞口差 0.00% / 换手差 0.00% / 绩效差 0.000000，
报告 memory/logs/ab_stage1/report_2026-08-20.json）。
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetiredEntry:
    """退役对象登记。"""

    name: str            # 函数/模块名
    module: str          # 所在模块
    migrated_to: str     # RD 承接位置（§2.3）
    status: str = "retired"


# ─── 退役清单（§4.1）─────────────────────────────────────

_RETIRED_FUNCTIONS: list[RetiredEntry] = [
    # futures_signal_pipeline.py 组合侧
    RetiredEntry("_compute_composite_scores", "scripts/futures_signal_pipeline.py", "RD strategy_synthesis"),
    RetiredEntry("_compute_per_variety_weights", "scripts/futures_signal_pipeline.py", "RD strategy_synthesis"),
    RetiredEntry("_apply_regime_weight_adjustment", "scripts/futures_signal_pipeline.py", "RD router"),
    RetiredEntry("_apply_regime_direction_bias", "scripts/futures_signal_pipeline.py", "RD router"),
    RetiredEntry("_generate_trading_advice", "scripts/futures_signal_pipeline.py", "RD router"),
    RetiredEntry("_generate_trading_advice_report", "scripts/futures_signal_pipeline.py", "RD router"),
    RetiredEntry("_compute_holdout_validation", "scripts/futures_signal_pipeline.py", "RD combo_verifier"),
    RetiredEntry("_load_l3_combo_weights", "scripts/futures_signal_pipeline.py", "RD signal_client"),
    RetiredEntry("_load_l3_subchain_meta", "scripts/futures_signal_pipeline.py", "RD sub_chain"),
    RetiredEntry("_load_l3_combo_meta", "scripts/futures_signal_pipeline.py", "RD signal_client"),
    RetiredEntry("_load_l3_combo_factors", "scripts/futures_signal_pipeline.py", "RD signal_client"),
    # portfolio_loop.py 策略侧
    RetiredEntry("synthesize_signals", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis.synthesize"),
    RetiredEntry("_compute_elastic_net_weights", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis.elastic_net_weights"),
    RetiredEntry("_compute_ml_ensemble_weights", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis.ml_ensemble_weights"),
    RetiredEntry("_synthesize_bl_weights", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis.black_litterman_weights"),
    RetiredEntry("regime_adaptive_weight_adjustment", "fts/factor_engine/portfolio_loop.py", "RD router"),
    RetiredEntry("build_combo", "fts/factor_engine/portfolio_loop.py", "RD combo_verifier"),
    RetiredEntry("_cap_safety_valve", "fts/factor_engine/portfolio_loop.py", "RD combo_verifier.cap_safety_valve"),
    RetiredEntry("_validate_combo_sharpe", "fts/factor_engine/portfolio_loop.py", "RD combo_verifier.validate_combo_sharpe"),
    RetiredEntry("_run_sharpe_randomization_test", "fts/factor_engine/portfolio_loop.py", "RD combo_verifier.sharpe_randomization_test"),
    RetiredEntry("decay_test", "fts/factor_engine/portfolio_loop.py", "RD combo_verifier"),
    RetiredEntry("apply_turnover_penalty", "fts/factor_engine/portfolio_loop.py", "RD router"),
    RetiredEntry("_apply_sticky_constraints", "fts/factor_engine/portfolio_loop.py", "RD router"),
    RetiredEntry("_compute_subchain_exposure", "fts/factor_engine/portfolio_loop.py", "RD sub_chain"),
    RetiredEntry("_merge_gate_scale_into_modulation", "fts/factor_engine/portfolio_loop.py", "RD router"),
    RetiredEntry("_greedy_select_by_correlation", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis.greedy_select_by_correlation"),
    RetiredEntry("_dedup_factors_by_chain", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis.dedup_by_chain"),
    RetiredEntry("_dedup_factors_by_chain_cluster", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis.dedup_by_chain"),
    RetiredEntry("_dedup_within_chain", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis.dedup_by_chain"),
    RetiredEntry("_filter_by_quality_gate", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis"),
    RetiredEntry("_filter_shadow_pending", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis"),
    RetiredEntry("_filter_review_approved", "fts/factor_engine/portfolio_loop.py", "RD strategy_synthesis"),
    # 整体迁移模块
    RetiredEntry("weight_learning", "fts/factor_engine/weight_learning.py", "RD strategy_synthesis/money_management"),
    RetiredEntry("capital_allocator", "fts/factor_engine/capital_allocator.py", "RD money_management.capital_allocate"),
    RetiredEntry("regime_crowding", "fts/factor_engine/regime_crowding.py", "RD crowding_gate（权威口径平移）"),
]

# 快捷索引
_RETIRED_BY_NAME: dict[str, RetiredEntry] = {e.name: e for e in _RETIRED_FUNCTIONS}


def is_retired(name: str) -> bool:
    """对象是否已退役。"""
    return name in _RETIRED_BY_NAME


def warn_if_retired(name: str, category: type[Warning] = DeprecationWarning) -> None:
    """退役对象调用告警（存量调用点迁移期透明，待调用图清零后物理删除）。"""
    entry = _RETIRED_BY_NAME.get(name)
    if entry is None:
        return
    msg = (
        f"[L3-RETIRED] {name}（{entry.module}）已退役——策略合成职责迁移至 "
        f"{entry.migrated_to}（plans/57 §4.1/§5.3）。FTS 仅保留因子管理 + 信号矩阵输出。"
    )
    warnings.warn(msg, category, stacklevel=2)
    logger.warning(msg)


def retired_registry() -> list[dict]:
    """退役登记全量（审计/报告用）。"""
    return [{"name": e.name, "module": e.module, "migrated_to": e.migrated_to, "status": e.status}
            for e in _RETIRED_FUNCTIONS]


__all__ = [
    "RetiredEntry",
    "retired_registry",
    "is_retired",
    "warn_if_retired",
]
