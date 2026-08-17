"""
loop_engine/evolution_seeds.py — 领域 D 协作类：种子管理与横截面评估

34 计划（plans/34-evolution-loop-refactor-inventory.md）C 阶段 Phase 47h：
B 阶段产物 EvolutionSeedsMixin 组合式重构为 SeedManager 协作类，行为等价、
公开 API 不变。领域独享状态（_barra_exposures_cache / _barra_exposures_attempted）
随迁本类构造（原主类 __init__ 对应段迁移）；跨领域共享数据（data /
forward_returns / market / cross_section_data / cross_section_dates /
_is_cross_section / inject_dir / evaluation_chain / verifier /
quality_inspector / industry_map / cap_map——industry_map 在主类 __init__ 内经
期货板块映射自动注入、cap_map/industry_map 可被测试运行时重赋值，属可变上下文）
经 owner（主类实例）动态读取。跨域方法调用（_promote_to_elite / _run_*_check /
_record_*_trace / _log_inspection_detail / _register_factor_baseline /
_check_factor_data_quality / _evaluate_cross_section 等）经 owner 转发使测试
`loop._X = MagicMock` 类实例打桩生效。主类 EvolutionLoop 组合持有本类实例，
保留 7 方法转发桩 + 2 属性 property 转发（兼容测试零改动，见 34 §8.5）。

契约（见 01-architecture.md §5 EvolutionLoop Mixin 拆分契约）：
- 协作类不 import evolution_loop（防循环导入），owner 仅经 Any 标注，
  运行时经主类组装注入。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
import pandas as pd

from .contracts import FactorEvaluation  # noqa: E402 — 延迟导入规避循环依赖
from .evaluation_chain import cross_section_evaluate_backtest  # noqa: E402 — 延迟导入规避循环依赖
from .l1_l2_funnel import funnel_record  # noqa: E402 — plans/44 D1: L1→L2 闭环漏斗
from .seed_pool import compute_seed_correlations  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from .contracts import EvolutionState, FactorCorrelation, FactorProgram

logger = logging.getLogger(__name__)


def _subchain_waiver_pass(owner: Any, bt: dict[str, Any]) -> bool:
    """子链放行判定（GAP-144）：energy + 开关 + effective 子链存在 → True。

    Args:
        owner: EvolutionLoop（market/配置读取）
        bt: level_1_backtest 指标（含 cross_section_evaluate_backtest 产出的
            subchain_ic_report 画像段）

    Returns:
        True=豁免全链 IC 稀释维度；False=不豁免（IC 门槛照常判定）。
    """
    if not (getattr(owner, "market", "") == "energy"):
        return False
    try:
        from fts.config import get_config

        if not bool(getattr(get_config(), "l2_subchain_waiver_enabled", False)):
            return False
    except Exception:  # noqa: BLE001 — 配置读取失败保守回退
        return False
    try:
        report = (bt.get("subchain_ic_report") or {}).get("subchain_ic_profile") or {}
        return any(bool(st.get("effective")) for st in report.values())
    except AttributeError:
        return False


def _subchain_waiver_view(evaluation: "FactorEvaluation") -> "FactorEvaluation":
    """评分卡放行视图：effective 子链 max |mean_ic| 替换全链 IC（GAP-144）。"""
    eff_ic = _subchain_waiver_effective_ic(evaluation)
    if eff_ic is None:
        return evaluation
    view = dict(evaluation)
    l1 = dict(view.get("level_1_backtest") or {})
    l1["ic"] = eff_ic
    view["level_1_backtest"] = l1
    return view


def _subchain_waiver_effective_ic(evaluation: "FactorEvaluation") -> Optional[float]:
    """effective 子链最大 |mean_ic|（GAP-144）；无 → None。"""
    try:
        l1 = evaluation.get("level_1_backtest") or {}
        report = l1.get("subchain_ic_report") or {}
        profile = report.get("subchain_ic_profile") or {}
    except AttributeError:
        return None
    eff_ics = [
        abs(float(st["mean_ic"]))
        for c, st in profile.items()
        if bool(st.get("effective")) and st.get("mean_ic") is not None
    ]
    return max(eff_ics) if eff_ics else None


def _apply_seed_subchain_waiver(
    owner: Any,
    evaluation: "FactorEvaluation",
    verifier_result: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Verifier 子链 IC 放行（GAP-144，种子路径与演化路径同构）。

    仅 energy + 开关 + effective 子链时生效；只豁免 IC/ICIR 稀释维度，
    其余维度（Sharpe/回撤/OOS 等）失败仍拦截。

    Returns:
        豁免后 VerifierResult（passed=True）或 None（不豁免）。
    """
    if not (getattr(owner, "market", "") == "energy"):
        return None
    try:
        from fts.config import get_config

        if not bool(getattr(get_config(), "l2_subchain_waiver_enabled", False)):
            return None
    except Exception:  # noqa: BLE001
        return None
    if _subchain_waiver_effective_ic(evaluation) is None:
        return None
    reasons = verifier_result.get("failure_reasons", [])
    kept = [
        r for r in reasons
        if not (r.startswith("Level 1 失败: IC=") or r.startswith("Level 1 失败: ICIR="))
    ]
    if len(kept) == len(reasons):
        return None  # 无 IC/ICIR 失败
    if kept:
        return None  # 其它维度失败仍拦截
    return {**verifier_result, "passed": True, "failure_reasons": [], "subchain_waiver": True}


class SeedManager:
    """领域 D：种子管理与横截面评估（34 计划 C 阶段协作类）。

    状态所有权（34 §8.3）：领域独享状态（_barra_exposures_cache /
    _barra_exposures_attempted）随迁本类构造；跨领域共享数据经 owner（主类
    实例）动态读取，兼容运行时重赋值（34 §8.3 可变上下文修订，47b
    CandidatePrefilter 先例）；跨域方法调用经 owner 转发使测试实例打桩生效。
    主类 EvolutionLoop 组合持有本类实例，保留 7 方法转发桩 + 2 属性 property
    转发（兼容测试零改动，见 34 §8.5）。
    """

    # v2.104.0+76: 退化因子冷却期（天）。质检不合格因子（status=degraded，
    # 含种子池与从 elite 退回两类）在降级后冷却期内不参与种子评估，
    # 期满后放行重新评估（回归通道，配合 _promote_to_elite 重新激活）。
    _degraded_cooldown_days: int = 30

    def __init__(self, owner: Any) -> None:
        self._owner: Any = owner
        # ── 领域独享状态随迁（原主类 __init__ 对应段迁移） ──
        # GAP-I304 (v2.79.0): Barra 风格暴露缓存（成功=dict / 失败=None，避免每因子重复构建）
        self._barra_exposures_cache: Optional[dict[str, Any]] = None
        self._barra_exposures_attempted: bool = False

    def _within_degraded_cooldown(self, existing: dict[str, Any]) -> bool:
        """判断退化因子是否仍在冷却期内（v2.104.0+76）。

        以 factor_catalog.updated_at 作为降级时间戳（factor_inspector 降级经
        update_factor 自动回写 updated_at=CURRENT_TIMESTAMP）。时间戳缺失或
        解析失败时返回 False（放行评估，宁多评估不锁死回归通道）。

        Args:
            existing: get_factor_by_name 返回的因子记录

        Returns:
            True: 冷却期内（降级未满 _degraded_cooldown_days 天）
            False: 冷却期满或时间戳不可用
        """
        raw = existing.get("updated_at")
        if not raw:
            return False
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            return (now - dt).days < self._degraded_cooldown_days
        except (ValueError, TypeError):
            return False

    def _evaluate_and_promote_seeds(
        self,
        seeds: list["FactorProgram"],
        trace_id: str,
        state: "EvolutionState",
        elite_ids: list[str],
        seed_correlations: Optional[list["FactorCorrelation"]] = None,
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
        skipped_existing = 0
        for seed in seeds:
            try:
                # ── 预跳过优化（v2.104.0+75）：已入库且仍有效的种子不再重复评估 ──
                # 此前去重仅在 _promote_to_elite（完整评估之后）执行，已入库的
                # YAML 种子每次运行都被白跑一轮横截面评估（IC/质量卡/审计/回测），
                # 能源链实测浪费约 34% 种子评估算力。此处前置查重，评估前拦截。
                # v2.104.0+76 三态：active → 跳过（有效种子）；degraded 且在
                # 冷却期内（< _degraded_cooldown_days 天）→ 跳过（质检不合格
                # 因子 1 个月内不再评估）；degraded 且冷却期满 → 放行重新评估
                # （回归通道，与 _promote_to_elite 退化因子重新激活对称）。
                seed_name = seed.get("name", "")
                try:
                    existing = self._owner._get_repo().get_factor_by_name(
                        seed_name, market=self._owner.market
                    )
                    if existing and existing.get("status") == "active":
                        skipped_existing += 1
                        logger.info(
                            "[evo] 预跳过已入库种子: %s (DuckDB 已存在且 active, market=%s, trace_id=%s)",
                            seed_name,
                            self._owner.market,
                            getattr(self._owner, "_trace_id", ""),
                        )
                        continue
                    if (
                        existing
                        and existing.get("status") == "degraded"
                        and self._within_degraded_cooldown(existing)
                    ):
                        skipped_existing += 1
                        logger.info(
                            "[evo] 冷却期内跳过退化种子: %s (degraded < %d 天, market=%s, trace_id=%s)",
                            seed_name,
                            self._degraded_cooldown_days,
                            self._owner.market,
                            getattr(self._owner, "_trace_id", ""),
                        )
                        continue
                except Exception:
                    # 查重失败不阻断评估（保持原行为，由 _promote_to_elite 兜底）
                    pass

                if self._owner._is_cross_section:
                    evaluation = self._owner._evaluate_cross_section(seed, trace_id)
                else:
                    evaluation = self._owner.evaluation_chain.evaluate(
                        seed,
                        self._owner.data,
                        self._owner.forward_returns,
                    )
                # 确保 WalkForward 结果存在（若缺失则执行轻量 2 窗口验证）
                if evaluation.get("walk_forward") is None:
                    from .evaluation_chain import evaluate_walk_forward

                    try:
                        wf = evaluate_walk_forward(
                            seed,
                            self._owner.data,
                            self._owner.forward_returns,
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
                    verifier_result = self._owner.verifier.check(evaluation)
                    # GAP-144：子链放行（energy + 开关 + effective 子链）时豁免
                    # IC/ICIR 稀释维度，Sharpe 等其它维度仍硬判（与演化路径一致）。
                    if not verifier_result.get("passed", False):
                        _waiver = _apply_seed_subchain_waiver(
                            self._owner, evaluation, verifier_result
                        )
                        if _waiver is not None:
                            logger.info(
                                "[evo][%s] 种子子链放行通过（GAP-144）",
                                seed.get("name", "?"),
                            )
                            verifier_result = _waiver
                            evaluation["subchain_waiver"] = True
                    if not verifier_result.get("passed", False):
                        self._owner._record_failure_trace(
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
                    # GAP-144：子链放行时评分卡用放行视图（effective 子链 IC）
                    _seed_inspect_eval = (
                        _subchain_waiver_view(evaluation)
                        if evaluation.get("subchain_waiver")
                        else evaluation
                    )
                    inspection = self._owner.quality_inspector.inspect(
                        factor=seed,
                        evaluation=_seed_inspect_eval,
                    )
                    if inspection.filtered:
                        self._owner._log_inspection_detail(
                            seed,
                            inspection,
                            "淘汰",
                            0,
                        )
                        continue

                    # 端到端回测流水线 (Phase B.2 集成)
                    backtest_result = self._owner._run_backtest_pipeline(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    if backtest_result:
                        evaluation["backtest_pipeline"] = backtest_result

                    # 数据质量监控 (Phase B.1 集成)
                    self._owner._register_factor_baseline(seed, evaluation)
                    dq_alerts = self._owner._check_factor_data_quality(
                        seed,
                        evaluation,
                    )
                    if dq_alerts:
                        critical = any(getattr(a, "severity", "") == "critical" for a in dq_alerts)
                        if critical:
                            print(f"[evo] 种子数据质量严重告警 [{seed.get('name', '?')}]: 跳过晋升")
                            continue

                    # 种子因子强制审计 (Phase B.3 集成)
                    audit_report = self._owner._run_factor_audit(
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
                        self._owner._record_audit_failed_trace(
                            seed,
                            0,
                            trace_id,
                            audit_report,
                            evaluation=evaluation,
                        )
                        continue

                    # ── 消融实验检查（v2.50.0 与演化因子对齐） ──
                    ablation_result = self._owner._run_ablation_check(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    evaluation["ablation_check"] = ablation_result
                    if not ablation_result.get("passed", True):
                        print(f"[evo] 种子消融实验未通过 [{seed.get('name', '?')}]: 疑似伪相关")
                        self._owner._record_ablation_failed_trace(
                            seed,
                            0,
                            trace_id,
                            ablation_result,
                        )
                        continue

                    # ── 因果结构审查（v2.50.0 与演化因子对齐） ──
                    causal_result = self._owner._run_causal_validation(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    evaluation["causal_validation"] = causal_result
                    if not causal_result.get("passed", True):
                        print(f"[evo] 种子因果审查未通过 [{seed.get('name', '?')}]: 事件敏感")
                        self._owner._record_causal_failed_trace(
                            seed,
                            0,
                            trace_id,
                            causal_result,
                        )
                        continue

                    # ── 鲁棒性审查（v2.50.0 与演化因子对齐） ──
                    robustness_result = self._owner._run_robustness_check(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    evaluation["robustness_check"] = robustness_result
                    if not robustness_result.get("passed", True):
                        print(f"[evo] 种子鲁棒性审查未通过 [{seed.get('name', '?')}]")
                        self._owner._record_robustness_failed_trace(
                            seed,
                            0,
                            trace_id,
                            robustness_result,
                        )
                        continue

                    # ── SHAP 可解释性分析（v2.50.0 与演化因子对齐，不阻断） ──
                    shap_result = self._owner._run_shap_analysis(
                        seed,
                        evaluation,
                        trace_id,
                    )
                    evaluation["shap_analysis"] = shap_result

                    self._owner._log_inspection_detail(
                        seed,
                        inspection,
                        "通过",
                        0,
                    )
                    promoted_path = self._owner._promote_to_elite(
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
                    # plans/44 D1: L1→L2 闭环 — L1 注入候选晋升精英回写 promoted
                    self._record_l1_promoted(seed, trace_id)
                    print(
                        f"[evo] 种子因子晋升: {seed['name']} (IC={bt.get('ic', 0):.4f}, "
                        f"质量分={inspection.total_score}/50)"
                    )
            except Exception:
                continue
        if skipped_existing:
            logger.info(
                "[evo] 种子预跳过完成: 已入库 %d 个, 实际评估 %d 个, 晋升 %d 个",
                skipped_existing,
                len(seeds) - skipped_existing,
                promoted,
            )
        return promoted

    def _merge_l1_candidates(
        self,
        seeds: list["FactorProgram"],
        trace_id: str,
    ) -> list["FactorProgram"]:
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

        inject_dir = self._owner.inject_dir
        pool_path = Path(getattr(self._owner, "factor_pool_path", "memory/knowledge/factors/factor_pool.json"))
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
        merged: list["FactorProgram"] = list(seeds)
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
            if cand_market is not None and cand_market != self._owner.market:
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

        # plans/44 D1: L1→L2 闭环 — 消费数回写漏斗（L2 读取 l1_injected_* 即计入 consumed）
        self._record_l1_consumed(consumed_ids, trace_id)

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

    def _record_l1_consumed(self, consumed_ids: list[str], trace_id: str) -> None:
        """plans/44 D1: L2 消费 L1 注入候选回写漏斗 consumed（空消费无操作）。"""
        if not consumed_ids:
            return
        try:
            funnel_record(market=self._owner.market, consumed=len(consumed_ids), run_id=trace_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[L1.merge] 漏斗回写失败（不阻断）: %s", e)

    def _record_l1_promoted(self, seed: "FactorProgram", trace_id: str) -> None:
        """plans/44 D1: L1 注入候选晋升精英回写漏斗 promoted（非 L1 种子无操作）。

        L1 候选 parent_id 为 cand_ 前缀（meta_loop 生成），base 种子 parent_id 为 None。
        """
        if not str(seed.get("parent_id", "") or "").startswith("cand_"):
            return
        try:
            funnel_record(market=self._owner.market, promoted=1, run_id=trace_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[evo] 漏斗 promoted 回写失败（不阻断）: %s", e)

    def _run_seed_correlation_check(
        self,
        seeds: list["FactorProgram"],
        trace_id: str,
    ) -> list["FactorCorrelation"]:
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
        # GAP-038 补丁扩展: 时序模式同样存在规模爆炸——185 种子 × 500 日逐个因子
        # 代码执行（spearmanr/percentile 重算子）+ 全对相关矩阵，计算量随种子数平方
        # 增长且无超时保护，种子集 > 50 时同样跳过预检。
        if len(seeds) > 50:
            mode = "横截面" if self._owner._is_cross_section else "时序"
            print(f"[evo] 种子因子相关性预检跳过: {len(seeds)} 种子，{mode}模式计算量过大")
            return []
        try:
            if self._owner._is_cross_section:
                # 期货横截面模式: 截面排名 Spearman 相关
                from .seed_pool import compute_cross_section_correlations

                correlations = compute_cross_section_correlations(
                    seeds,
                    self._owner.cross_section_data,
                    self._owner.cross_section_dates,
                    threshold=0.95,
                )
            else:
                # 股票时序模式: Pearson/Spearman 相关
                correlations = compute_seed_correlations(seeds, self._owner.data, threshold=0.95)
            return correlations
        except Exception as e:
            mode = "横截面" if self._owner._is_cross_section else "时序"
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
        if not self._owner._is_cross_section:
            return None
        if self._barra_exposures_cache is not None or self._barra_exposures_attempted:
            return self._barra_exposures_cache
        self._barra_exposures_attempted = True
        try:
            from fts.config.settings import get_config

            if not get_config().l2_barra_style_neutral:
                return None
            from .barra.barra_style import BarraStyleEngine

            engine = BarraStyleEngine()
            exposures = engine.compute_exposures(
                self._owner.cross_section_data,
                self._owner.cross_section_dates,
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
        if not self._owner._is_cross_section or not self._owner.cross_section_data:
            return None
        try:
            from fts.config.settings import get_config

            if not get_config().l2_barra_style_neutral:
                return None
            vol_map: dict[str, float] = {}
            for sym, df in self._owner.cross_section_data.items():
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
                len(self._owner.cross_section_data),
            )
            return vol_map or None
        except Exception as e:  # noqa: BLE001 — 构建失败不阻断评估
            logger.warning("[EvolutionLoop] 波动率中性化映射构建失败，跳过: %s", e)
            return None

    def _evaluate_cross_section(self, factor: "FactorProgram", trace_id: str) -> "FactorEvaluation":
        """横截面模式下的评估：直接回测 + 自动构造 FactorEvaluation。"""
        from .contracts import EconomicScore
        from .evaluation_chain import cross_section_walk_forward, evaluate_multiple_tests

        bt = cross_section_evaluate_backtest(
            factor,
            self._owner.cross_section_data,
            self._owner.cross_section_dates,
            industry_map=self._owner.industry_map,
            cap_map=self._owner.cap_map,
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
        # GAP-121 评估链修复: Level3 不再硬编码（bonferroni_p=1.0, effective_n=1,
        # passed=True 恒过）——按本批次实际被检验因子数计算，与候选规模对齐。
        prior_evals: list = list(getattr(self._owner, "_prior_evaluations", []) or [])
        temp_eval = {"factor_id": factor["factor_id"], "trace_id": trace_id, "level_1_backtest": bt}
        mt = evaluate_multiple_tests([*prior_evals, temp_eval])
        # GAP-121 评估链修复: 横截面多窗口走航验证（短样本自适应窗口/步长）。
        wf: Optional[dict] = None
        try:
            build_cfg = getattr(self._owner, "_build_wf_config", None)
            wf = cross_section_walk_forward(
                factor,
                self._owner.cross_section_data,
                self._owner.cross_section_dates,
                config=build_cfg(pd.DataFrame(index=pd.DatetimeIndex(self._owner.cross_section_dates)))
                if callable(build_cfg)
                else None,
                industry_map=self._owner.industry_map,
                cap_map=self._owner.cap_map,
                style_exposures=self._build_barra_exposures(),
                vol_map=self._build_vol_map(),
            )
        except Exception as e:  # noqa: BLE001 — 走航失败记录但不阻断评估（晋升侧硬门槛兜底）
            logger.warning("[Evo] 横截面走航验证失败 [%s]: %s", factor.get("factor_id", "?"), e)

        # GAP-121 补全（横截面路径）：走航跨窗口 IC 波动率/一致性衰减
        # （供 HighICScreener param_sensitivity / signal_halflife 消费；此前横截面
        # level_1 不产出 ic_volatility/decay_6m 导致两项恒 skipped）。
        if wf and wf.get("n_windows_completed", 0) > 0:
            bt.setdefault("ic_volatility", float(wf.get("ic_volatility", 0.0) or 0.0))
            bt["decay_6m"] = max(0.0, 1.0 - float(wf.get("ic_consistency", 0.0) or 0.0))
        # GAP-121 补全（横截面路径）：端到端成本口径（net_excess_return 由
        # cross_section_evaluate_backtest 派生，此处包装为 backtest_pipeline 供
        # HighICScreener V4 / net_excess 消费）。
        if bt.get("net_excess_return") is not None:
            bt["backtest_pipeline"] = {"net_excess_return": bt["net_excess_return"]}

        reasons: list[str] = []
        if bt.get("ic", 0) < 0.03:
            # GAP-144：子链放行（energy + 开关 + effective 子链）时豁免全链 IC
            # 稀释维度——单链特异因子全链 IC 被无效子链稀释，但存在 t 检验显著的
            # effective 子链；Sharpe 等其它维度仍硬判。
            if not _subchain_waiver_pass(self._owner, bt):
                reasons.append(f"截面 IC={bt.get('ic', 0):.4f} < 0.03")
        if bt.get("sharpe", 0) < 1.5:
            reasons.append(f"截面夏普={bt.get('sharpe', 0):.4f} < 1.5")
        return FactorEvaluation(
            factor_id=factor["factor_id"],
            trace_id=trace_id,
            level_1_backtest=bt,
            level_2_economic=ec,
            level_3_multiple=mt,
            walk_forward=wf,
            extreme_perturbation=bt.get("extreme_perturbation"),
            cross_symbol_positive_ratio=bt.get("cross_symbol_positive_ratio"),
            backtest_pipeline=bt.get("backtest_pipeline"),
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
        与 ``_promote_to_elite``（重复/去冗余护栏），与 L2 演化晋升完全同构；
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
                ev = self._owner._evaluate_cross_section(factor, tid)
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
            path = self._owner._promote_to_elite(factor, ev, audit_report=audit)
            if path is not None:
                result["promoted"] += 1
                result["promoted_ids"].append(fid)
        return result
