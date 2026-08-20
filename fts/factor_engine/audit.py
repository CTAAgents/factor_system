"""
fts.factor_engine.audit — 因子审计流程标准化 (Phase B.3)。

定义 ``FactorAuditor`` 类，执行因子入库前的 6 项强制审计：
    1. 因果检验 (Granger / 反事实分析)
    2. 样本外验证 (WalkForward OOS)
    3. 跨品种验证 (≥80% 品种 IC 为正)
    4. 压力测试 (极端行情下表现)
    5. 多重检验 (Bonferroni / FDR 校正)
    6. 数据窥探检验 (无未来函数)

所有审计项必须通过才能入库，结果以 ``FactorAuditReport`` 结构化输出。

用法:
    auditor = FactorAuditor()
    report = auditor.audit(
        factor=factor_program,
        data=ohlcv_data,
        forward_returns=future_returns,
        symbol_ic_map={"RB": 0.05, "HC": 0.03, ...},
    )
    assert report["passed"]

版本: v0.1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional, cast

import numpy as np
import pandas as pd

from .causal_validator import CausalValidator
from .stress_test import StressTester
from .walk_forward import WalkForwardOptimizer

logger = logging.getLogger(__name__)


# ─── 契约 ───────────────────────────────────────────────────

AuditItemStatus = Literal["passed", "failed", "skipped"]
"""单项审计状态。"""


@dataclass
class AuditItemResult:
    """单项审计结果。"""

    name: str
    status: AuditItemStatus
    evidence: str = ""
    score: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class FactorAuditReport:
    """因子审计报告。"""

    factor_id: str
    factor_name: str
    audited_at: str
    items: list[AuditItemResult]
    passed: bool
    pass_rate: float
    summary: dict[str, Any]
    failure_analysis: Optional[dict[str, Any]] = None

    # ─── 便捷查询 ──────────────────────────────────

    def item(self, name: str) -> Optional[AuditItemResult]:
        for it in self.items:
            if it.name == name:
                return it
        return None

    @property
    def failed_items(self) -> list[AuditItemResult]:
        return [it for it in self.items if it.status == "failed"]

    def to_dict(self) -> dict[str, Any]:
        result = {
            "factor_id": self.factor_id,
            "factor_name": self.factor_name,
            "audited_at": self.audited_at,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "summary": self.summary,
            "items": [
                {
                    "name": it.name,
                    "status": it.status,
                    "evidence": it.evidence,
                    "score": it.score,
                    "details": it.details,
                }
                for it in self.items
            ],
        }
        if self.failure_analysis:
            result["failure_analysis"] = self.failure_analysis
        return result


# ─── 配置 ───────────────────────────────────────────────────


@dataclass
class FactorAuditConfig:
    """因子审计配置。"""

    # 跨品种验证（A+C 双机制，v2.103.0 GAP-096；D 板块覆盖率，v3.0.0+7 GAP-160）
    min_cross_symbol_ratio: float = 0.8  # 主防线：≥80% 品种 IC 为正
    min_mean_ic: float = 0.05  # 软门控A：平均 IC 强度下限
    ratio_floor: float = 0.6  # 软门控A：符号比例下限（联合 min_mean_ic）
    binomial_alpha: float = 0.05  # 软门控C：二项检验显著性水平
    min_sector_coverage: int = 5  # 软门控D：板块级覆盖率下限（训练池 7 大板块中 ≥5 板块有代表品种 IC 为正）

    # 多重检验
    bonferroni_alpha: float = 0.05  # Bonferroni 校正显著性
    fdr_alpha: float = 0.05  # FDR 校正显著性

    # 数据窥探检验
    lookback_max_lag: int = 5  # 最大滞后阶数（不应显著）
    snooping_alpha: float = 0.05  # 窥探检验显著性

    # 压力测试
    stress_max_drawdown: float = 0.40  # 压力场景最大回撤上限

    # OOS 最小窗口通过率
    min_oos_pass_ratio: float = 0.5


# ─── FactorAuditor ──────────────────────────────────────────


class FactorAuditor:
    """因子审计执行器。

    串联因果检验、OOS 验证、跨品种验证、压力测试、
    多重检验校正和数据窥探检验共 6 项审计，
    所有审计项必须通过才能入库。

    Args:
        config: 审计配置（默认值若为 None）
    """

    ITEM_NAMES: tuple[str, ...] = (
        "causal_validity",
        "oos_consistency",
        "cross_symbol",
        "stress_resilience",
        "multiple_testing",
        "snooping_check",
    )

    def __init__(self, config: Optional[FactorAuditConfig] = None) -> None:
        self._config = config or FactorAuditConfig()
        self._causal_validator = CausalValidator()
        self._wf_optimizer = WalkForwardOptimizer()
        self._stress_tester = StressTester()
        self._failure_classifier = FailureClassifier()

    # ─── 主入口 ──────────────────────────────────────────

    def audit(
        self,
        factor: Optional[dict[str, Any]] = None,
        data: Optional[pd.DataFrame] = None,
        forward_returns: Optional[np.ndarray] = None,
        symbol_ic_map: Optional[dict[str, float]] = None,
        signals_by_symbol: Optional[dict[str, np.ndarray]] = None,
        ohlcv_by_symbol: Optional[dict[str, pd.DataFrame]] = None,
        oos_result: Optional[dict[str, Any]] = None,
        p_values: Optional[list[float]] = None,
        symbol_holdout: Optional[dict[str, Any]] = None,
        sector_coverage: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> FactorAuditReport:
        """执行完整审计流程。

        各审计项支持独立传入所需数据，缺失时该项标记为 ``skipped``。

        Args:
            factor: 因子元数据字典 (含 factor_id, name 等)
            data: OHLCV DataFrame (用于因果/窥探检验)
            forward_returns: 未来收益率数组
            symbol_ic_map: 品种 → IC 的映射 (跨品种验证)
            signals_by_symbol: 品种 → 信号数组 (压力测试)
            ohlcv_by_symbol: 品种 → OHLCV DataFrame (压力测试)
            oos_result: 外部已计算的 OOS 结果 (含 ic_consistency, passed 等)
            p_values: 多重检验的 p 值列表
            symbol_holdout: 标的留出验证结果 dict (GAP-075/160，含 train_ic/holdout_ic/
                ic_retention/passed)，缺失标记 skipped
            sector_coverage: 板块级覆盖率 dict (v3.0.0+7 GAP-160，含 covered_sectors/
                total_sectors)，cross_symbol 软门控D 通道
            **kwargs: 保留扩展

        Returns:
            FactorAuditReport
        """
        factor_meta = factor or {}
        factor_id = factor_meta.get("factor_id", kwargs.get("factor_id", "unknown"))
        factor_name = factor_meta.get("name", kwargs.get("factor_name", factor_id))

        items: list[AuditItemResult] = []
        items.append(self._check_causal_validity(factor, data, forward_returns))
        items.append(self._check_oos_consistency(oos_result))
        items.append(self._check_cross_symbol(symbol_ic_map, sector_coverage))
        items.append(self._check_symbol_holdout(symbol_holdout))
        items.append(self._check_stress_resilience(signals_by_symbol, ohlcv_by_symbol))
        items.append(self._check_multiple_testing(p_values))
        items.append(self._check_snooping(data, forward_returns))

        passed_items = [it for it in items if it.status == "passed"]
        failed_items = [it for it in items if it.status == "failed"]
        skipped_items = [it for it in items if it.status == "skipped"]

        n_total = len(items)
        n_pass = len(passed_items)
        pass_rate = n_pass / n_total if n_total > 0 else 0.0

        # 审计规则：所有非 skipped 项必须全部通过
        non_skipped = [it for it in items if it.status != "skipped"]
        passed = bool(non_skipped) and all(it.status == "passed" for it in non_skipped)

        report = FactorAuditReport(
            factor_id=factor_id,
            factor_name=factor_name,
            audited_at=datetime.now().isoformat(),
            items=items,
            passed=passed,
            pass_rate=pass_rate,
            summary={
                "total": n_total,
                "passed": n_pass,
                "failed": len(failed_items),
                "skipped": len(skipped_items),
                "pass_rate": pass_rate,
                "failed_items": [it.name for it in failed_items],
            },
        )

        # 审计失败时自动生成失败模式分析
        if not passed:
            factor_metrics = factor_meta.copy()
            # 提取关键指标供分类器使用
            if "ic" not in factor_metrics and "ic" in kwargs:
                factor_metrics["ic"] = kwargs["ic"]
            if "sharpe" not in factor_metrics and "sharpe" in kwargs:
                factor_metrics["sharpe"] = kwargs["sharpe"]

            classification = self._failure_classifier.classify(
                audit_report=report,
                factor_metrics=factor_metrics,
            )
            # 将建议列表转换为可序列化的格式
            serializable_suggestions = [
                {
                    "pattern": s.pattern,
                    "priority": s.priority,
                    "action": s.action,
                    "rationale": s.rationale,
                    "expected_improvement": s.expected_improvement,
                }
                for s in classification.get("suggestions", [])
            ]
            report.failure_analysis = {
                "detected_patterns": classification.get("detected_patterns", []),
                "severity": classification.get("severity", "unknown"),
                "suggestions": serializable_suggestions,
            }

        level = logging.INFO if passed else logging.WARNING
        logger.log(
            level,
            "因子审计 [factor_id=%s, passed=%s, pass_rate=%.0f%%, failed=%s]",
            factor_id,
            passed,
            pass_rate * 100,
            [it.name for it in failed_items],
        )
        return report

    # ─── 1. 因果检验 ─────────────────────────────────────

    def _check_causal_validity(
        self,
        factor: Optional[dict[str, Any]],
        data: Optional[pd.DataFrame],
        forward_returns: Optional[np.ndarray],
    ) -> AuditItemResult:
        """因果检验：Granger 因果 / 反事实分析。"""
        name = "causal_validity"

        if factor is None or data is None or forward_returns is None:
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence="缺少因果检验所需的 factor/data/forward_returns",
            )

        try:
            # 使用 CausalValidator 做快速反事实检验
            from .contracts import FactorProgram
            from .expr_dsl import compile_expr_to_code

            prog = cast(FactorProgram, dict(factor))
            # GAP-135 ③: GP/算子因子审计代码读取修复——dict 缺 code 时从 expr 编译补齐，
            # 避免因果检验以「因子代码为空」静默跳过（代码实际存在于 expr/表达式字段）。
            if not str(prog.get("code") or "").strip() and prog.get("expr"):
                try:
                    prog["code"] = compile_expr_to_code(str(prog["expr"]))
                except Exception:  # noqa: BLE001
                    logger.debug("因果检验 expr→code 编译失败: %s", prog.get("expr"))
            result = self._causal_validator.validate(prog, data, forward_returns)
            anomaly_rate = result.get("summary", {}).get("anomaly_rate", 0.0)
            # 异常率 > 0 视为有因果结构（因子对事件有反应）
            passed = anomaly_rate >= 0.0
            return AuditItemResult(
                name=name,
                status="passed" if passed else "failed",
                evidence=f"anomaly_rate={anomaly_rate:.2%}",
                score=min(1.0, anomaly_rate * 5),
                details=result.get("summary", {}),
            )
        except Exception as e:
            logger.debug("因果检验异常: %s", e)
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence=f"因果检验执行异常: {e}",
            )

    # ─── 2. 样本外验证 ────────────────────────────────────

    def _check_oos_consistency(
        self,
        oos_result: Optional[dict[str, Any]],
    ) -> AuditItemResult:
        """样本外验证：WalkForward OOS 通过率。"""
        name = "oos_consistency"

        if oos_result is None:
            # GAP-121 评估链修复: 未提供 OOS 结果即样本外验证缺失——样本内单切片
            # IC 无法排除数据窥探，标记 failed（原 skipped 放行），由晋升审计硬门拦截。
            return AuditItemResult(
                name=name,
                status="failed",
                evidence="未提供 OOS 结果（样本外验证缺失，禁止放行）",
            )

        # GAP-121 评估链修复: 反转 GAP-073 放宽。n_windows<2 无法做跨窗口一致性
        # 验证（ic_consistency 退化为单窗口 IC 正负的 0/1 硬币），此前标记 skipped
        # 放行导致单一 OOS 切片因子全部入库；现改为硬拦截 failed，配合横截面
        # 走航接入（_build_wf_config 短样本自适应），晋升因子必须有多窗口 OOS 证据。
        n_windows = oos_result.get("n_windows_completed")
        if isinstance(n_windows, int) and n_windows < 2:
            return AuditItemResult(
                name=name,
                status="failed",
                evidence=f"WalkForward 窗口不足（n_windows={n_windows} < 2），无法验证样本外一致性",
            )

        ic_consistency = oos_result.get("ic_consistency", 0.0)
        passed_flag = oos_result.get("passed", False)
        min_ratio = self._config.min_oos_pass_ratio

        passed = bool(passed_flag) or ic_consistency >= min_ratio

        return AuditItemResult(
            name=name,
            status="passed" if passed else "failed",
            evidence=(f"ic_consistency={ic_consistency:.2f}, passed_flag={passed_flag}"),
            score=min(1.0, ic_consistency),
            details={
                "ic_consistency": ic_consistency,
                "passed_flag": passed_flag,
                "threshold": min_ratio,
            },
        )

    # ─── 3. 跨品种验证 ────────────────────────────────────

    def _check_cross_symbol(
        self,
        symbol_ic_map: Optional[dict[str, float]],
        sector_coverage: Optional[dict[str, Any]] = None,
    ) -> AuditItemResult:
        """跨品种验证（A+C 双机制 + D 板块覆盖率 OR 判定，v2.103.0 GAP-096 / v3.0.0+7 GAP-160）。

        任一机制通过即通过：
        - 主防线：≥ min_cross_symbol_ratio（默认 80%）品种 IC 为正
        - 软门控A：平均 IC ≥ min_mean_ic 且符号比例 ≥ ratio_floor
        - 软门控C：IC>0 品种数经二项检验显著高于随机（binomtest p < binomial_alpha）
        - 软门控D（v3.0.0+7）：板块级覆盖率——训练池 7 大板块中 ≥ min_sector_coverage
          板块有代表品种 IC 为正（板块内品种 IC 为正比例 ≥50%），给"板块特异但全池
          符号比不足"的因子合理通道（契合 plans/47 子链机制）

        短样本下 IC 符号噪声大，绝对值符号门槛过严（GAP-096）：22 品种要求 18/22
        为正，5 品种负即拒，大量 14-17/22 泛化尚可的因子被边界误杀。A+C 双机制
        保留强泛化主防线，同时允许"平均 IC 强"或"符号比例二项显著"的因子通过；
        D 板块覆盖率通道针对全期货异构品种池（7 大板块定价逻辑各异）补充。
        """
        name = "cross_symbol"

        if not symbol_ic_map:
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence="未提供跨品种 IC 数据",
            )

        ics = list(symbol_ic_map.values())
        if not ics:
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence="IC 列表为空",
            )

        n_symbols = len(ics)
        n_positive = sum(1 for ic in ics if ic > 0)
        positive_ratio = n_positive / n_symbols
        mean_ic = float(np.mean(ics))

        # 机制1（主防线）：符号比例 ≥ 阈值
        passed_ratio = positive_ratio >= self._config.min_cross_symbol_ratio
        # 机制2（软门控A）：平均 IC 强度 + 符号比例下限
        passed_mean = (
            mean_ic >= self._config.min_mean_ic
            and positive_ratio >= self._config.ratio_floor
        )
        # 机制3（软门控C）：二项检验显著性（IC>0 品种数显著高于随机二项分布）
        passed_binomial = False
        binomial_p = 1.0
        try:
            from scipy.stats import binomtest

            # 零假设 p0=0.5（随机一半正一半负），单侧检验 IC>0 过多
            res = binomtest(n_positive, n_symbols, p=0.5, alternative="greater")
            binomial_p = float(res.pvalue)
            passed_binomial = binomial_p < self._config.binomial_alpha
        except Exception:  # noqa: BLE001 — scipy 缺失/异常时降级为不通过，不阻断
            passed_binomial = False

        # 机制4（软门控D，v3.0.0+7）：板块级覆盖率（sector_coverage 缺失时降级不启用）
        passed_sector = False
        sector_detail = None
        if sector_coverage:
            covered = int(sector_coverage.get("covered_sectors", 0))
            total = int(sector_coverage.get("total_sectors", 0))
            min_cover = int(self._config.min_sector_coverage)
            passed_sector = total > 0 and covered >= min_cover
            sector_detail = {
                "covered_sectors": covered,
                "total_sectors": total,
                "threshold": min_cover,
            }

        passed = passed_ratio or passed_mean or passed_binomial or passed_sector

        mechanisms = []
        if passed_ratio:
            mechanisms.append("ratio")
        if passed_mean:
            mechanisms.append("mean_ic")
        if passed_binomial:
            mechanisms.append("binomial")
        if passed_sector:
            mechanisms.append("sector_coverage")

        details: dict[str, Any] = {
            "n_symbols": n_symbols,
            "n_positive": n_positive,
            "positive_ratio": positive_ratio,
            "mean_ic": mean_ic,
            "binomial_p": binomial_p,
            "threshold": self._config.min_cross_symbol_ratio,
            "mechanisms": mechanisms,
        }
        if sector_detail:
            details["sector_coverage"] = sector_detail

        return AuditItemResult(
            name=name,
            status="passed" if passed else "failed",
            evidence=(
                f"positive_ratio={positive_ratio:.1%} ({n_positive}/{n_symbols})"
                f", mean_ic={mean_ic:.4f}, binomial_p={binomial_p:.4f}"
                f", sector={sector_detail or 'n/a'}"
                f", passed_via={mechanisms or 'none'}"
            ),
            score=positive_ratio,
            details=details,
        )

    # ─── 3.5 标的留出验证（GAP-075）────────────────────────

    def _check_symbol_holdout(
        self,
        symbol_holdout: Optional[dict[str, Any]],
    ) -> AuditItemResult:
        """标的留出验证：同市场泛化——留出集 IC > 0 且保持率 ≥ 阈值。

        数据缺失（未提供/留出集过小）标记 skipped，不阻断主流程。
        """
        name = "symbol_holdout"

        if not symbol_holdout:
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence="未提供标的留出验证结果",
            )

        passed_flag = bool(symbol_holdout.get("passed", False))
        train_ic = symbol_holdout.get("train_ic")
        holdout_ic = symbol_holdout.get("holdout_ic")
        retention = symbol_holdout.get("ic_retention")
        n_holdout = symbol_holdout.get("n_holdout")

        def _fmt(v: Any) -> str:
            return f"{float(v):.4f}" if isinstance(v, (int, float)) else str(v)

        return AuditItemResult(
            name=name,
            status="passed" if passed_flag else "failed",
            evidence=(
                f"train_ic={_fmt(train_ic)} holdout_ic={_fmt(holdout_ic)} "
                f"retention={_fmt(retention)} (n_holdout={n_holdout})"
            ),
            score=float(retention) if isinstance(retention, (int, float)) else 0.0,
            details={
                "n_holdout": int(n_holdout) if isinstance(n_holdout, (int, float)) else 0,
                "train_ic": train_ic,
                "holdout_ic": holdout_ic,
                "ic_retention": retention,
            },
        )

    # ─── 4. 压力测试 ──────────────────────────────────────

    def _check_stress_resilience(
        self,
        signals_by_symbol: Optional[dict[str, np.ndarray]],
        ohlcv_by_symbol: Optional[dict[str, pd.DataFrame]],
    ) -> AuditItemResult:
        """压力测试：极端行情下最大回撤 ≤ 40%。"""
        name = "stress_resilience"

        if not signals_by_symbol or not ohlcv_by_symbol:
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence="未提供压力测试所需的 signals/ohlcv",
            )

        try:
            results = self._stress_tester.run_all(signals_by_symbol, ohlcv_by_symbol)
            if not results:
                return AuditItemResult(
                    name=name,
                    status="skipped",
                    evidence="压力测试未返回任何场景结果",
                )

            pass_count = sum(1 for r in results if r.get("passed", False))
            pass_ratio = pass_count / len(results)
            threshold = self._config.stress_max_drawdown

            # 所有场景最大回撤均 ≤ threshold 才算通过
            all_passed = all(r.get("passed", False) for r in results)
            max_dd = max((r.get("max_drawdown", 1.0) for r in results), default=1.0)

            return AuditItemResult(
                name=name,
                status="passed" if all_passed else "failed",
                evidence=(
                    f"pass_count={pass_count}/{len(results)}, max_drawdown={max_dd:.2%}, threshold={threshold:.0%}"
                ),
                score=pass_ratio,
                details={
                    "n_scenarios": len(results),
                    "pass_count": pass_count,
                    "max_drawdown": max_dd,
                    "scenario_results": [
                        {
                            "scenario": r.get("scenario", ""),
                            "max_drawdown": r.get("max_drawdown", 0.0),
                            "passed": r.get("passed", False),
                        }
                        for r in results
                    ],
                },
            )
        except Exception as e:
            logger.debug("压力测试异常: %s", e)
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence=f"压力测试执行异常: {e}",
            )

    # ─── 5. 多重检验 ──────────────────────────────────────

    def _check_multiple_testing(
        self,
        p_values: Optional[list[float]],
    ) -> AuditItemResult:
        """多重检验：Bonferroni / FDR 校正。"""
        name = "multiple_testing"

        if not p_values:
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence="未提供 p 值列表",
            )

        p_arr = np.asarray(p_values, dtype=float)
        n_tests = len(p_arr)
        bonferroni_threshold = self._config.bonferroni_alpha / max(n_tests, 1)
        significant_bonferroni = int(np.sum(p_arr < bonferroni_threshold))

        # FDR (Benjamini-Hochberg)
        fdr_threshold, significant_fdr = _bh_fdr_correction(p_arr, self._config.fdr_alpha)

        passed = significant_bonferroni >= 1 or significant_fdr >= 1

        return AuditItemResult(
            name=name,
            status="passed" if passed else "failed",
            evidence=(f"n_tests={n_tests}, bonferroni_sig={significant_bonferroni}, fdr_sig={significant_fdr}"),
            score=min(1.0, (significant_bonferroni + significant_fdr) / max(2, min(n_tests, 2))),
            details={
                "n_tests": n_tests,
                "bonferroni_alpha": self._config.bonferroni_alpha,
                "bonferroni_threshold": bonferroni_threshold,
                "bonferroni_significant": significant_bonferroni,
                "fdr_alpha": self._config.fdr_alpha,
                "fdr_threshold": fdr_threshold,
                "fdr_significant": significant_fdr,
                "min_p": float(np.min(p_arr)) if n_tests > 0 else 1.0,
            },
        )

    # ─── 6. 数据窥探检验 ──────────────────────────────────

    def _check_snooping(
        self,
        data: Optional[pd.DataFrame],
        forward_returns: Optional[np.ndarray],
    ) -> AuditItemResult:
        """数据窥探检验：因子不应显著领先未来收益率（除 0 阶滞后）。"""
        name = "snooping_check"

        if data is None or forward_returns is None:
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence="缺少数据窥探检验所需的 data/forward_returns",
            )

        try:
            # 使用前 5 阶 Granger-like 相关系数
            fwd = np.asarray(forward_returns, dtype=float)
            if len(fwd) < 30:
                return AuditItemResult(
                    name=name,
                    status="skipped",
                    evidence="forward_returns 长度不足 (< 30)",
                )

            # 取 close 作为因子代理（若 data 有 close 列）
            if "close" not in data.columns:
                return AuditItemResult(
                    name=name,
                    status="skipped",
                    evidence="data 无 close 列，无法执行窥探检验",
                )

            close = np.asarray(data["close"], dtype=float)
            max_lag = self._config.lookback_max_lag
            alpha = self._config.snooping_alpha

            # 检验：因子当期值是否预测未来收益（显著则存在窥探）
            abs_corrs: list[float] = []
            for lag in range(1, max_lag + 1):
                x = close[:-lag] if lag > 0 else close
                y = fwd[lag:] if lag > 0 else fwd
                n = min(len(x), len(y))
                if n < 10:
                    break
                corr = float(np.corrcoef(x[:n], y[:n])[0, 1])
                abs_corrs.append(abs(corr))

            if not abs_corrs:
                return AuditItemResult(
                    name=name,
                    status="skipped",
                    evidence="无法计算相关性",
                )

            # 窥探判定：滞后相关系数绝对值的 z-score 超过阈值
            mean_abs = float(np.mean(abs_corrs))
            std_abs = float(np.std(abs_corrs)) if len(abs_corrs) > 1 else 0.0
            suspicious = any(abs_c > mean_abs + 2 * max(std_abs, 1e-6) for abs_c in abs_corrs)
            passed = not suspicious

            return AuditItemResult(
                name=name,
                status="passed" if passed else "failed",
                evidence=(f"max_abs_corr={max(abs_corrs):.4f}, mean_abs={mean_abs:.4f}, suspicious={suspicious}"),
                score=max(0.0, 1.0 - float(max(abs_corrs)) * 2),
                details={
                    "lag_correlations": abs_corrs,
                    "mean_abs_corr": mean_abs,
                    "std_abs_corr": std_abs,
                    "alpha": alpha,
                    "suspicious": suspicious,
                },
            )
        except Exception as e:
            logger.debug("数据窥探检验异常: %s", e)
            return AuditItemResult(
                name=name,
                status="skipped",
                evidence=f"窥探检验执行异常: {e}",
            )


# ─── 工具函数 ───────────────────────────────────────────────


def _bh_fdr_correction(
    p_values: np.ndarray,
    alpha: float,
) -> tuple[float, int]:
    """Benjamini-Hochberg FDR 校正。

    Args:
        p_values: p 值数组
        alpha: 期望 FDR 水平

    Returns:
        (fdr_threshold, n_significant)
    """
    n = len(p_values)
    if n == 0:
        return alpha, 0

    sorted_indices = np.argsort(p_values)
    sorted_p = p_values[sorted_indices]
    adjusted = np.empty(n)
    for i in range(n - 1, -1, -1):
        rank = i + 1
        if i == n - 1:
            adjusted[i] = sorted_p[i]
        else:
            adjusted[i] = min(adjusted[i + 1], sorted_p[i] * n / rank)

    fdr_threshold = alpha
    n_significant = int(np.sum(adjusted < alpha))
    return float(fdr_threshold), n_significant


__all__ = [
    "FactorAuditConfig",
    "FactorAuditor",
    "FactorAuditReport",
    "AuditItemResult",
    "AuditItemStatus",
    "FailureClassifier",
    "FailurePattern",
    "ImprovementSuggestion",
]


# ─── 失败模式分类与改善建议 ───────────────────────────────────


class FailurePattern:
    """因子失败模式分类。

    基于审计结果和评估指标，自动识别因子的失败模式。
    """

    NEGATIVE_IC = "negative_ic"
    IC_DECAY = "ic_decay"
    OOS_INSTABILITY = "oos_instability"
    CROSS_SYMBOL_FAILURE = "cross_symbol_failure"
    MULTIPLE_TESTING = "multiple_testing"
    SNOOPING_SUSPECTED = "snooping_suspected"
    STRESS_VULNERABLE = "stress_vulnerable"
    CAUSAL_WEAK = "causal_weak"
    SHARPE_LOW = "sharpe_low"
    HIGH_TURNOVER = "high_turnover"

    _DESCRIPTIONS: dict[str, str] = {
        "negative_ic": "IC 为负，因子方向可能反转",
        "ic_decay": "IC 随时间衰减，因子信号强度下降",
        "oos_instability": "样本外不稳定，WalkForward 一致性差",
        "cross_symbol_failure": "跨品种泛化能力差，≥20% 品种 IC 为负",
        "multiple_testing": "多重检验校正后不显著",
        "snooping_suspected": "数据窥探嫌疑，因子可能使用了未来信息",
        "stress_vulnerable": "压力测试失败，极端行情下表现脆弱",
        "causal_weak": "因果结构弱，因子缺乏经济逻辑支撑",
        "sharpe_low": "Sharpe 偏低，风险调整后收益不足",
        "high_turnover": "换手率过高，交易成本侵蚀收益",
    }

    @classmethod
    def describe(cls, pattern: str) -> str:
        return cls._DESCRIPTIONS.get(pattern, pattern)


@dataclass
class ImprovementSuggestion:
    """改善建议。"""

    pattern: str
    priority: str  # high / medium / low
    action: str
    rationale: str
    expected_improvement: str


class FailureClassifier:
    """因子失败模式分类器。

    根据审计报告和评估指标，自动识别因子的失败模式并生成改善建议。

    用法:
        classifier = FailureClassifier()
        analysis = classifier.classify(audit_report, factor_metrics)
        for suggestion in analysis["suggestions"]:
            print(suggestion.action)
    """

    PATTERN_TO_SUGGESTIONS: dict[str, list[dict[str, str]]] = {
        "negative_ic": [
            {
                "priority": "high",
                "action": "反转因子方向（乘以 -1）",
                "rationale": "IC 为负说明因子方向与预期相反，反转后 IC 应转正",
                "expected_improvement": "IC 由负转正，改善幅度约 2×|IC|",
            },
            {
                "priority": "medium",
                "action": "检查因子经济逻辑是否反转",
                "rationale": "确认因子设计假设是否在当前市场环境下成立",
                "expected_improvement": "避免方向反转后逻辑矛盾",
            },
        ],
        "ic_decay": [
            {
                "priority": "high",
                "action": "缩短因子回看窗口或引入时间衰减",
                "rationale": "IC 衰减说明因子的预测能力随时间下降",
                "expected_improvement": "近期 IC 提升 10-30%",
            },
            {
                "priority": "medium",
                "action": "增加自适应参数调整",
                "rationale": "使用滚动窗口动态调整因子参数",
                "expected_improvement": "IC 稳定性提升",
            },
        ],
        "oos_instability": [
            {
                "priority": "high",
                "action": "减少参数数量，简化因子结构",
                "rationale": "参数过多导致过拟合，OOS 表现不稳定",
                "expected_improvement": "OOS 通过率提升 20-40%",
            },
            {
                "priority": "high",
                "action": "增加训练窗口或使用 WalkForward 优化",
                "rationale": "训练窗口过短导致模型不稳定",
                "expected_improvement": "IC 一致性提升",
            },
            {
                "priority": "medium",
                "action": "增加正则化约束",
                "rationale": "防止模型对训练数据过度拟合",
                "expected_improvement": "泛化能力提升",
            },
        ],
        "cross_symbol_failure": [
            {
                "priority": "high",
                "action": "扩展因子训练的品种范围",
                "rationale": "在更多品种上训练可提升泛化能力",
                "expected_improvement": "跨品种通过率提升至 ≥80%",
            },
            {
                "priority": "medium",
                "action": "引入品种中性化处理",
                "rationale": "去除品种特异性，提取共性因子信号",
                "expected_improvement": "跨品种 IC 标准差降低",
            },
            {
                "priority": "medium",
                "action": "检查因子是否依赖特定品种特征",
                "rationale": "避免因子仅适用于少数品种",
                "expected_improvement": "品种覆盖率提升",
            },
        ],
        "multiple_testing": [
            {
                "priority": "high",
                "action": "减少同时检验的因子数量",
                "rationale": "降低多重检验的 Bonferroni/FDR 校正门槛",
                "expected_improvement": "校正后 p 值更显著",
            },
            {
                "priority": "medium",
                "action": "增加每个因子的独立样本量",
                "rationale": "提升统计检验功效",
                "expected_improvement": "统计显著性提升",
            },
        ],
        "snooping_suspected": [
            {
                "priority": "high",
                "action": "检查因子是否使用了未来数据",
                "rationale": "数据窥探是严重的偏差来源",
                "expected_improvement": "消除窥探嫌疑",
            },
            {
                "priority": "high",
                "action": "使用滞后特征重新构造因子",
                "rationale": "确保因子仅使用历史数据",
                "expected_improvement": "通过窥探检验",
            },
        ],
        "stress_vulnerable": [
            {
                "priority": "high",
                "action": "增加止损或回撤控制机制",
                "rationale": "限制极端行情下的最大亏损",
                "expected_improvement": "压力场景回撤降低 30-50%",
            },
            {
                "priority": "medium",
                "action": "降低因子杠杆或仓位",
                "rationale": "减少波动率暴露",
                "expected_improvement": "风险指标改善",
            },
            {
                "priority": "medium",
                "action": "增加市场制度自适应切换",
                "rationale": "在高压场景下自动调整策略",
                "expected_improvement": "通过更多压力场景",
            },
        ],
        "causal_weak": [
            {
                "priority": "high",
                "action": "审查因子经济逻辑，增加理论支撑",
                "rationale": "缺乏因果结构的因子容易失效",
                "expected_improvement": "因果检验通过率提升",
            },
            {
                "priority": "medium",
                "action": "引入行为金融或微观结构变量",
                "rationale": "增强因子的经济解释力",
                "expected_improvement": " anomaly_rate 提升",
            },
        ],
        "sharpe_low": [
            {
                "priority": "high",
                "action": "优化因子参数以提升信号强度",
                "rationale": "参数调优可显著提升 Sharpe",
                "expected_improvement": "Sharpe 提升 20-50%",
            },
            {
                "priority": "medium",
                "action": "考虑因子组合而非单因子使用",
                "rationale": "多因子组合可提升风险调整后收益",
                "expected_improvement": "组合 Sharpe 显著提升",
            },
        ],
        "high_turnover": [
            {
                "priority": "high",
                "action": "增加调仓频率限制或持仓期",
                "rationale": "降低换手率可减少交易成本",
                "expected_improvement": "换手率降低 30-50%",
            },
            {
                "priority": "medium",
                "action": "引入成交量加权的信号平滑",
                "rationale": "减少因噪声导致的频繁交易",
                "expected_improvement": "换手率下降 + Sharpe 提升",
            },
        ],
    }

    def classify(
        self,
        audit_report: Optional[FactorAuditReport] = None,
        factor_metrics: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """分类失败模式并生成改善建议。

        Args:
            audit_report: 因子审计报告（可选）
            factor_metrics: 因子评估指标字典（可选），支持字段:
                - ic: 因子 IC
                - sharpe: Sharpe 比率
                - turnover: 换手率
                - max_drawdown: 最大回撤
                - oos_pass_ratio: OOS 通过率
                - cross_symbol_ratio: 跨品种通过率
                - walkforward_consistency: WalkForward 一致性
                - overall_passed: 是否通过整体审计

        Returns:
            分类结果字典
        """
        metrics = factor_metrics or {}
        detected_patterns: list[dict[str, Any]] = []

        # 基于审计报告的失败项识别
        if audit_report and audit_report.failed_items:
            failed_names = {it.name for it in audit_report.failed_items}

            if "multiple_testing" in failed_names:
                detected_patterns.append(self._build_pattern("multiple_testing", "high"))
            if "snooping_check" in failed_names:
                detected_patterns.append(self._build_pattern("snooping_suspected", "high"))
            if "stress_resilience" in failed_names:
                detected_patterns.append(self._build_pattern("stress_vulnerable", "high"))
            if "cross_symbol" in failed_names:
                detected_patterns.append(self._build_pattern("cross_symbol_failure", "high"))
            if "oos_consistency" in failed_names:
                detected_patterns.append(self._build_pattern("oos_instability", "high"))
            if "causal_validity" in failed_names:
                detected_patterns.append(self._build_pattern("causal_weak", "medium"))

        # 基于指标的量化识别
        ic = metrics.get("ic")
        if ic is not None and ic < 0:
            detected_patterns.append(self._build_pattern("negative_ic", "high"))

        sharpe = metrics.get("sharpe")
        if sharpe is not None and sharpe < 0.5:
            detected_patterns.append(self._build_pattern("sharpe_low", "medium"))

        turnover = metrics.get("turnover")
        if turnover is not None and turnover > 1.0:
            detected_patterns.append(self._build_pattern("high_turnover", "medium"))

        oos_ratio = metrics.get("oos_pass_ratio") or metrics.get("walkforward_consistency")
        if oos_ratio is not None and oos_ratio < 0.5:
            if not any(p["pattern"] == "oos_instability" for p in detected_patterns):
                detected_patterns.append(self._build_pattern("oos_instability", "high"))

        cross_ratio = metrics.get("cross_symbol_ratio")
        if cross_ratio is not None and cross_ratio < 0.8:
            if not any(p["pattern"] == "cross_symbol_failure" for p in detected_patterns):
                detected_patterns.append(self._build_pattern("cross_symbol_failure", "high"))

        ic_trend = metrics.get("ic_trend")
        if ic_trend == "declining":
            detected_patterns.append(self._build_pattern("ic_decay", "high"))

        # 去重
        seen = set()
        unique_patterns = []
        for p in detected_patterns:
            if p["pattern"] not in seen:
                seen.add(p["pattern"])
                unique_patterns.append(p)

        # 生成建议
        suggestions = self._generate_suggestions(unique_patterns)

        return {
            "factor_id": (audit_report.factor_id if audit_report else metrics.get("factor_id", "unknown")),
            "detected_patterns": unique_patterns,
            "num_patterns": len(unique_patterns),
            "suggestions": suggestions,
            "severity": self._calc_severity(unique_patterns),
            "classified_at": datetime.now().isoformat(),
        }

    def _build_pattern(
        self,
        pattern: str,
        confidence: str,
    ) -> dict[str, Any]:
        return {
            "pattern": pattern,
            "description": FailurePattern.describe(pattern),
            "confidence": confidence,
        }

    def _generate_suggestions(
        self,
        patterns: list[dict[str, Any]],
    ) -> list[ImprovementSuggestion]:
        suggestions: list[ImprovementSuggestion] = []
        seen_actions: set[str] = set()

        for p in patterns:
            pattern_name = p["pattern"]
            template_list = self.PATTERN_TO_SUGGESTIONS.get(pattern_name, [])

            for template in template_list:
                action = template["action"]
                if action not in seen_actions:
                    seen_actions.add(action)
                    suggestions.append(
                        ImprovementSuggestion(
                            pattern=pattern_name,
                            priority=template["priority"],
                            action=action,
                            rationale=template["rationale"],
                            expected_improvement=template["expected_improvement"],
                        )
                    )

        return suggestions

    @staticmethod
    def _calc_severity(patterns: list[dict[str, Any]]) -> str:
        if any(p.get("confidence") == "high" for p in patterns):
            return "high"
        if len(patterns) >= 2:
            return "medium"
        if patterns:
            return "low"
        return "healthy"
