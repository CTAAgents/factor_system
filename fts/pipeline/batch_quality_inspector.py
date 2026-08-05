"""
fts.pipeline.batch_quality_inspector — 全量因子库批量质检与排名

将 WalkForward 稳定性评分逻辑应用到全量因子库的批量质检中，
生成因子质量排名报告。

用法:
    from fts.pipeline.batch_quality_inspector import BatchQualityInspector

    inspector = BatchQualityInspector(panel_data, common_dates)
    report = inspector.inspect_all(factors)
    report.save_json("reports/quality_ranking.json")
    report.save_csv("reports/quality_ranking.csv")

版本: v1.0.0
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from ..factor_engine.contracts import (
    BacktestMetrics,
    EconomicScore,
    FactorEvaluation,
    FactorProgram,
    MultipleTestResult,
    normalize_factor_program,
)
from ..factor_engine.factor_quality_card import (
    FactorQualityCard,
    FactorQualityCardConfig,
    FactorQualityScore,
)
from ..factor_engine.walk_forward import (
    WalkForwardConfig,
    WalkForwardOptimizer,
    WalkForwardResult,
)
from ..factor_engine.evaluation_chain import cross_section_evaluate_backtest
from ..factor_engine.factor_program import FactorExecutor
from .factor_quality_inspection import FactorQualityInspection, InspectionResult

logger = logging.getLogger(__name__)


# ─── 报告数据结构 ───────────────────────────────────────────


class FactorRankEntry:
    """单个因子的质量排名条目。"""

    def __init__(
        self,
        factor_id: str,
        factor_name: str,
        total_score: float,
        grade: str,
        market: str,
        family: str,
        ic: float,
        sharpe: float,
        stability_score: float,
        compatibility_score: float,
        quality_score: FactorQualityScore,
        passed: bool,
        rank: int = 0,
        symbols: Optional[list[str]] = None,
        wf_result: Optional[WalkForwardResult] = None,
        elimination_reason: str = "",
    ) -> None:
        self.factor_id = factor_id
        self.factor_name = factor_name
        self.total_score = total_score
        self.grade = grade
        self.market = market
        self.family = family
        self.ic = ic
        self.sharpe = sharpe
        self.stability_score = stability_score
        self.compatibility_score = compatibility_score
        self.quality_score = quality_score
        self.passed = passed
        self.rank = rank
        self.symbols = symbols or []
        self.wf_result = wf_result
        self.elimination_reason = elimination_reason

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典。"""
        return {
            "rank": self.rank,
            "factor_id": self.factor_id,
            "factor_name": self.factor_name,
            "total_score": self.total_score,
            "grade": self.grade,
            "market": self.market,
            "family": self.family,
            "ic": self.ic,
            "sharpe": self.sharpe,
            "stability_score": self.stability_score,
            "compatibility_score": self.compatibility_score,
            "passed": self.passed,
            "symbols": self.symbols,
            "elimination_reason": self.elimination_reason,
            "dimension_scores": [
                {"name": d["name"], "score": d["score"], "raw_value": d.get("raw_value", 0)}
                for d in self.quality_score.get("dimension_scores", [])
            ],
            "walk_forward": {
                "ic_consistency": self.wf_result.get("ic_consistency", 0) if self.wf_result else 0,
                "ic_volatility": self.wf_result.get("ic_volatility", 0) if self.wf_result else 0,
                "consistency_score": self.wf_result.get("consistency_score", 0) if self.wf_result else 0,
                "n_windows": self.wf_result.get("n_windows_completed", 0) if self.wf_result else 0,
                "passed": self.wf_result.get("passed", False) if self.wf_result else False,
            } if self.wf_result else None,
        }


class QualityRankReport:
    """因子质量排名报告。"""

    def __init__(
        self,
        entries: list[FactorRankEntry],
        total_factors: int,
        passed_factors: int,
        failed_factors: int,
        generated_at: str,
        walk_forward_enabled: bool = True,
    ) -> None:
        self.entries = entries
        self.total_factors = total_factors
        self.passed_factors = passed_factors
        self.failed_factors = failed_factors
        self.generated_at = generated_at
        self.walk_forward_enabled = walk_forward_enabled

    @property
    def sorted_entries(self) -> list[FactorRankEntry]:
        """按总分降序排列的条目。"""
        return sorted(self.entries, key=lambda e: e.total_score, reverse=True)

    @property
    def top_20(self) -> list[FactorRankEntry]:
        """Top 20 因子。"""
        return self.sorted_entries[:20]

    @property
    def grade_distribution(self) -> dict[str, int]:
        """等级分布统计。"""
        dist = {"A": 0, "B": 0, "C": 0}
        for e in self.entries:
            grade = e.grade
            dist[grade] = dist.get(grade, 0) + 1
        return dist

    @property
    def market_distribution(self) -> dict[str, int]:
        """市场分布统计。"""
        dist: dict[str, int] = {}
        for e in self.entries:
            m = e.market
            dist[m] = dist.get(m, 0) + 1
        return dist

    @property
    def family_distribution(self) -> dict[str, int]:
        """家族分布统计。"""
        dist: dict[str, int] = {}
        for e in self.entries:
            f = e.family
            dist[f] = dist.get(f, 0) + 1
        return dist

    @property
    def average_score(self) -> float:
        """平均总分。"""
        if not self.entries:
            return 0.0
        return round(sum(e.total_score for e in self.entries) / len(self.entries), 2)

    @property
    def pass_rate(self) -> float:
        """通过率。"""
        if self.total_factors == 0:
            return 0.0
        return round(self.passed_factors / self.total_factors * 100, 1)

    def summary(self) -> str:
        """生成报告摘要。"""
        lines = [
            f"════════════════════════════════════════════════════════",
            f"  FTS 因子质量排名报告",
            f"  生成时间: {self.generated_at}",
            f"  WalkForward 稳定性验证: {'启用' if self.walk_forward_enabled else '未启用'}",
            f"════════════════════════════════════════════════════════",
            f"",
            f"  总因子数: {self.total_factors}",
            f"  通过质检: {self.passed_factors}",
            f"  淘汰数量: {self.failed_factors}",
            f"  通过率:   {self.pass_rate}%",
            f"  平均得分: {self.average_score}/50",
            f"",
            f"  ── 等级分布 ──",
            f"  A 级: {self.grade_distribution.get('A', 0)} 个",
            f"  B 级: {self.grade_distribution.get('B', 0)} 个",
            f"  C 级: {self.grade_distribution.get('C', 0)} 个",
            f"",
            f"  ── 市场分布 ──",
        ]
        for market, count in sorted(self.market_distribution.items(), key=lambda x: -x[1]):
            lines.append(f"  {market}: {count} 个")

        lines.append(f"")
        lines.append(f"  ── 家族分布 TOP 10 ──")
        for family, count in sorted(self.family_distribution.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"  {family}: {count} 个")

        lines.append(f"")
        lines.append(f"  ── TOP 10 因子 ──")
        for i, entry in enumerate(self.top_20[:10], 1):
            wf_info = ""
            if entry.wf_result:
                wf_info = f" [WF:{entry.wf_result.get('consistency_score', 0):.0f}]"
            lines.append(
                f"  {i:2d}. [{entry.grade}] {entry.factor_name:<30s} "
                f"得分={entry.total_score:.1f} IC={entry.ic:.4f} Sharpe={entry.sharpe:.2f}{wf_info}"
            )

        lines.append(f"")
        lines.append(f"════════════════════════════════════════════════════════")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """转为完整报告字典。"""
        return {
            "report_meta": {
                "generated_at": self.generated_at,
                "walk_forward_enabled": self.walk_forward_enabled,
                "total_factors": self.total_factors,
                "passed_factors": self.passed_factors,
                "failed_factors": self.failed_factors,
                "pass_rate": self.pass_rate,
                "average_score": self.average_score,
            },
            "distributions": {
                "grade": self.grade_distribution,
                "market": self.market_distribution,
                "family": self.family_distribution,
            },
            "rankings": [e.to_dict() for e in self.sorted_entries],
        }

    def save_json(self, filepath: str | Path) -> None:
        """保存为 JSON 文件。"""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info("报告已保存至: %s", filepath)

    def save_csv(self, filepath: str | Path) -> None:
        """保存为 CSV 文件。"""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        entries = self.sorted_entries
        if not entries:
            logger.warning("无数据可保存")
            return

        fieldnames = [
            "rank", "factor_id", "factor_name", "total_score", "grade",
            "market", "family", "ic", "sharpe", "stability_score",
            "compatibility_score", "passed", "symbols", "elimination_reason",
        ]

        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for entry in entries:
                row = {
                    "rank": entry.rank,
                    "factor_id": entry.factor_id,
                    "factor_name": entry.factor_name,
                    "total_score": entry.total_score,
                    "grade": entry.grade,
                    "market": entry.market,
                    "family": entry.family,
                    "ic": entry.ic,
                    "sharpe": entry.sharpe,
                    "stability_score": entry.stability_score,
                    "compatibility_score": entry.compatibility_score,
                    "passed": entry.passed,
                    "symbols": ",".join(entry.symbols) if entry.symbols else "",
                    "elimination_reason": entry.elimination_reason,
                }
                writer.writerow(row)
        logger.info("CSV 报告已保存至: %s", filepath)


# ─── 批量质检器 ───────────────────────────────────────────


class BatchQualityInspector:
    """全量因子库批量质检器。

    将 WalkForward 稳定性评分逻辑集成到批量质检流水线中，
    生成因子质量排名报告。

    Usage:
        inspector = BatchQualityInspector(
            panel_data=panel,
            common_dates=dates,
            walk_forward_config=wf_config,
        )
        report = inspector.inspect_all(factors)
        print(report.summary())
    """

    def __init__(
        self,
        panel_data: dict[str, pd.DataFrame],
        common_dates: Optional[pd.DatetimeIndex] = None,
        walk_forward_config: Optional[WalkForwardConfig] = None,
        card_config: Optional[FactorQualityCardConfig] = None,
        min_grade: str = "B",
        enable_walk_forward: bool = True,
    ) -> None:
        """初始化批量质检器。

        Args:
            panel_data: 面板数据 {品种名: DataFrame}
            common_dates: 共同日期索引（None 时自动检测）
            walk_forward_config: WalkForward 配置
            card_config: 质量评分卡配置
            min_grade: 最低准入等级
            enable_walk_forward: 是否启用 WalkForward 验证
        """
        self._panel = panel_data
        if common_dates is not None:
            self._dates = common_dates
        else:
            self._dates = self._detect_common_dates(panel_data)
        self._wf_config = walk_forward_config or self._default_wf_config()
        self._card_config = card_config
        self._min_grade = min_grade
        self._enable_wf = enable_walk_forward

        # 组件初始化
        self._card = FactorQualityCard(card_config)
        self._wf_optimizer = WalkForwardOptimizer(self._wf_config)
        self._inspector = FactorQualityInspection(card_config, min_grade=min_grade)

    @property
    def panel(self) -> dict[str, pd.DataFrame]:
        return self._panel

    @property
    def n_symbols(self) -> int:
        return len(self._panel)

    @property
    def walk_forward_enabled(self) -> bool:
        return self._enable_wf

    # ─── 公有方法 ──────────────────────────────────

    def inspect_all(
        self,
        factors: list[FactorProgram],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> QualityRankReport:
        """批量质检所有因子。

        Args:
            factors: 因子程序列表
            progress_callback: 进度回调 (completed, total, factor_name)

        Returns:
            QualityRankReport 质量排名报告
        """
        logger.info("开始批量质检: %d 个因子, %d 个品种", len(factors), self.n_symbols)

        entries: list[FactorRankEntry] = []
        total = len(factors)

        for i, factor in enumerate(factors):
            factor_name = factor.get("name", f"factor_{i}")
            try:
                entry = self._inspect_single_factor(factor)
                entries.append(entry)
            except Exception as e:
                logger.warning("因子 %s 质检失败: %s", factor_name, e)
                entries.append(self._create_failed_entry(factor, str(e)))

            if progress_callback:
                progress_callback(i + 1, total, factor_name)

            if (i + 1) % 50 == 0:
                logger.info("质检进度: %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)

        # 排序并分配排名
        sorted_entries = sorted(entries, key=lambda e: e.total_score, reverse=True)
        for rank, entry in enumerate(sorted_entries, 1):
            entry.rank = rank

        passed = sum(1 for e in entries if e.passed)
        failed = len(entries) - passed

        report = QualityRankReport(
            entries=sorted_entries,
            total_factors=total,
            passed_factors=passed,
            failed_factors=failed,
            generated_at=datetime.now(timezone.utc).isoformat(),
            walk_forward_enabled=self._enable_wf,
        )

        logger.info(
            "批量质检完成: 通过=%d, 淘汰=%d, 通过率=%.1f%%",
            passed, failed, report.pass_rate,
        )

        return report

    def inspect_single(
        self,
        factor: FactorProgram,
    ) -> FactorRankEntry:
        """质检单个因子。

        Args:
            factor: 因子程序

        Returns:
            FactorRankEntry
        """
        return self._inspect_single_factor(factor)

    # ─── 私有方法 ──────────────────────────────────

    def _inspect_single_factor(self, factor: FactorProgram) -> FactorRankEntry:
        """质检单个因子的完整流程。"""
        # 1. 规范化因子定义
        norm_factor = normalize_factor_program(factor)

        factor_id = norm_factor.get("factor_id", "unknown")
        factor_name = norm_factor.get("name", "unknown")
        market = norm_factor.get("market", "multi")
        family = norm_factor.get("family", "other")
        symbols = norm_factor.get("symbols", [])

        # 2. 计算跨品种覆盖率
        n_symbols = self.n_symbols
        coverage = min(n_symbols / 3, 1.0) if n_symbols > 0 else 0.5

        # 3. 横截面回测
        bt = self._safe_backtest(norm_factor)
        ic = bt.get("ic", 0.0)
        sharpe = bt.get("sharpe", 0.0)
        max_dd = bt.get("max_drawdown", 1.0)
        turnover = bt.get("turnover_monthly", 0.3)
        calmar = sharpe / max_dd if max_dd > 0 else 0.0

        # 4. WalkForward 稳定性验证
        wf_result = None
        if self._enable_wf:
            wf_result = self._run_walk_forward(norm_factor)

        # 5. 计算质量评分
        quality_score = self._card.evaluate(
            factor_id=factor_id,
            ic=ic,
            sharpe=sharpe,
            walk_forward_result=wf_result,
            decay_rate=0.2,
            turnover=turnover,
            correlation_max=0.5,
            logic_score=self._extract_logic_score(norm_factor),
            data_frequency="daily",
            cross_symbol_coverage=coverage,
            capacity_estimate=10_000_000,
            icir=bt.get("icir", 0.0),
            calmar=calmar,
        )

        total_score = quality_score["total_score"]
        grade = quality_score["grade"]

        # 提取稳定性和兼容性分
        stability_score = 0.0
        compatibility_score = 0.0
        for dim in quality_score.get("dimension_scores", []):
            if dim["name"] == "stability_score":
                stability_score = dim["score"]
            elif dim["name"] == "compatibility_score":
                compatibility_score = dim["score"]

        # 6. 判定是否通过
        passed = grade in ("A", "B")
        elimination_reason = ""
        if not passed:
            elimination_reason = (
                f"等级 {grade} 低于准入阈值 {self._min_grade} "
                f"(总分 {total_score}/50)"
            )

        return FactorRankEntry(
            factor_id=factor_id,
            factor_name=factor_name,
            total_score=total_score,
            grade=grade,
            market=market,
            family=family,
            ic=ic,
            sharpe=sharpe,
            stability_score=stability_score,
            compatibility_score=compatibility_score,
            quality_score=quality_score,
            passed=passed,
            symbols=symbols,
            wf_result=wf_result,
            elimination_reason=elimination_reason,
        )

    def _safe_backtest(self, factor: FactorProgram) -> BacktestMetrics:
        """安全执行横截面回测。"""
        try:
            bt = cross_section_evaluate_backtest(factor, self._panel, self._dates)
            return bt
        except Exception as e:
            logger.debug("回测失败 %s: %s", factor.get("name"), e)
            return BacktestMetrics(
                ic=0.0,
                icir=0.0,
                sharpe=0.0,
                max_drawdown=1.0,
                turnover_monthly=0.5,
            )

    def _run_walk_forward(self, factor: FactorProgram) -> WalkForwardResult:
        """执行 WalkForward 验证。"""
        # 使用第一个品种的数据进行 WalkForward
        if not self._panel:
            return WalkForwardResult(
                ic_consistency=0.0,
                ic_volatility=1.0,
                consistency_score=0.0,
                passed=False,
                n_windows_completed=0,
            )

        first_symbol = next(iter(self._panel))
        df = self._panel[first_symbol]
        if len(df) < 60:
            return WalkForwardResult(
                ic_consistency=0.0,
                ic_volatility=1.0,
                consistency_score=0.0,
                passed=False,
                n_windows_completed=0,
            )

        executor = FactorExecutor(factor)
        params = factor.get("params", {})

        def evaluate_fn(train: pd.DataFrame, oos: pd.DataFrame) -> dict:
            """WalkForward 窗口评估函数。"""
            try:
                signal = executor.execute(train, params)
                if len(signal) < 5:
                    return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.5}

                # 简化 IC 计算
                close_oos = oos["close"]
                signal_len = min(len(signal), len(close_oos))
                if signal_len < 3:
                    return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.5}

                from scipy import stats as sp_stats
                sig = signal[:signal_len]
                returns = close_oos.pct_change(5).shift(-5).fillna(0).values[:signal_len]

                mask = (sig != 0) & np.isfinite(returns)
                if mask.sum() < 3:
                    return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.5}

                ic_val, _ = sp_stats.spearmanr(sig[mask], returns[mask])
                if np.isnan(ic_val):
                    ic_val = 0.0

                sharpe_val = float(np.mean(returns[mask]) / (np.std(returns[mask]) + 1e-10) * np.sqrt(252))
                return {
                    "ic": float(ic_val),
                    "sharpe": sharpe_val,
                    "turnover": 0.5,
                }
            except Exception:
                return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.5}

        try:
            result = self._wf_optimizer.evaluate(df, evaluate_fn)
            return result
        except Exception as e:
            logger.debug("WalkForward 执行失败 %s: %s", factor.get("name"), e)
            return WalkForwardResult(
                ic_consistency=0.5,
                ic_volatility=0.3,
                consistency_score=50.0,
                passed=False,
                n_windows_completed=0,
            )

    def _extract_logic_score(self, factor: FactorProgram) -> int:
        """提取经济逻辑评分。"""
        logic = factor.get("economic_logic", {})
        if not logic:
            return 3

        dims_passed = logic.get("dimensions_passed", 0)
        if dims_passed and isinstance(dims_passed, (int, float)):
            return int(min(dims_passed / 4.0 * 5.0, 5.0))

        total = 0
        count = 0
        for dim in ("theory", "behavioral", "microstructure", "institutional"):
            val = logic.get(dim)
            if isinstance(val, (int, float)):
                total += val
                count += 1

        if count > 0:
            return int(min(total / count, 5.0))
        return 3

    def _create_failed_entry(self, factor: FactorProgram, error: str) -> FactorRankEntry:
        """创建失败条目。"""
        return FactorRankEntry(
            factor_id=factor.get("factor_id", "unknown"),
            factor_name=factor.get("name", "unknown"),
            total_score=0.0,
            grade="C",
            market=factor.get("market", "multi"),
            family=factor.get("family", "other"),
            ic=0.0,
            sharpe=0.0,
            stability_score=0.0,
            compatibility_score=0.0,
            quality_score={
                "score_id": f"qsc_{factor.get('factor_id', 'unknown')}",
                "factor_id": factor.get("factor_id", "unknown"),
                "total_score": 0.0,
                "dimension_scores": [],
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "score_version": "v1",
                "grade": "C",
            },
            passed=False,
            elimination_reason=f"质检异常: {error}",
        )

    @staticmethod
    def _detect_common_dates(panel_data: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
        """检测面板数据的共同日期。"""
        if not panel_data:
            return pd.DatetimeIndex([])

        # 取第一个品种的索引作为基准
        first_key = next(iter(panel_data))
        return panel_data[first_key].index

    @staticmethod
    def _default_wf_config() -> WalkForwardConfig:
        """默认 WalkForward 配置（针对批量质检优化）。"""
        return WalkForwardConfig(
            window_years=1,         # 1 年训练窗口（缩短以适应批量处理）
            step_months=3,          # 3 个月步长
            min_oos_months=1,       # 1 个月样本外
            n_windows=3,            # 3 个窗口
            min_ic_consistency=0.5,
            max_ic_volatility=0.5,
        )


# ─── 便捷函数 ──────────────────────────────────────────


def run_batch_quality_inspection(
    factors: list[FactorProgram],
    panel_data: dict[str, pd.DataFrame],
    output_dir: str | Path = "reports",
    enable_walk_forward: bool = True,
    min_grade: str = "B",
) -> QualityRankReport:
    """便捷函数：一键运行批量质检并生成报告。

    Args:
        factors: 因子列表
        panel_data: 面板数据
        output_dir: 报告输出目录
        enable_walk_forward: 是否启用 WalkForward
        min_grade: 最低准入等级

    Returns:
        QualityRankReport
    """
    output_dir = Path(output_dir)
    date_str = datetime.now().strftime("%Y-%m-%d")
    report_dir = output_dir / date_str
    report_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=== FTS 批量因子质量质检 ===")
    logger.info("因子数量: %d, 品种数量: %d", len(factors), len(panel_data))
    logger.info("WalkForward: %s, 准入等级: %s", "启用" if enable_walk_forward else "禁用", min_grade)

    inspector = BatchQualityInspector(
        panel_data=panel_data,
        enable_walk_forward=enable_walk_forward,
        min_grade=min_grade,
    )

    def progress_cb(completed: int, total: int, name: str) -> None:
        if completed % 10 == 0 or completed == total:
            logger.info("进度: %d/%d — 最后: %s", completed, total, name)

    report = inspector.inspect_all(factors, progress_callback=progress_cb)

    # 保存报告
    json_path = report_dir / "quality_ranking.json"
    csv_path = report_dir / "quality_ranking.csv"
    report.save_json(json_path)
    report.save_csv(csv_path)

    # 输出摘要
    print(report.summary())

    return report


__all__ = [
    "FactorRankEntry",
    "QualityRankReport",
    "BatchQualityInspector",
    "run_batch_quality_inspection",
]
