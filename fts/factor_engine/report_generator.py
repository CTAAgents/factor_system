"""
fts.factor_engine.report_generator — 报告生成器（B.2 Stage 6）。

生成包含摘要、净值、回撤、IC 时序、月度收益热力表的回测报告。
当前输出 Markdown 格式（设计文档支持 HTML/PDF/Markdown，以 Markdown 为主，无外部绘图依赖）。

用法:
    from fts.factor_engine.report_generator import ReportGenerator

    gen = ReportGenerator()
    path = gen.generate(report=backtest_report, output_dir="reports")

版本: v1.0.0
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class ReportGenerator:
    """报告生成器（B.2 Stage 6）。

    输入为 ``BacktestReport`` 或任意含 ``metrics``/``equity_curve``/``ic_series``
    的 dict-like 对象，输出 Markdown 报告文件。
    """

    def __init__(self, output_dir: str = "reports") -> None:
        """初始化报告生成器。

        Args:
            output_dir: 默认输出目录
        """
        self._output_dir = Path(output_dir)

    # ─── 主入口 ──────────────────────────────────────────

    def generate(
        self,
        report: Any,
        output_dir: Optional[str] = None,
        fmt: str = "markdown",
    ) -> str:
        """生成回测报告，返回文件路径。

        Args:
            report: BacktestReport 或 dict-like 报告对象
            output_dir: 输出目录（None 用默认）
            fmt: 输出格式（仅支持 "markdown"）

        Returns:
            报告文件绝对路径。
        """
        out_dir = Path(output_dir) if output_dir else self._output_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        factor_id = getattr(report, "factor_id", None) or (
            report.get("factor_id") if isinstance(report, dict) else "unknown"
        )
        safe_id = str(factor_id).replace("/", "_").replace("\\", "_")
        path = out_dir / f"backtest_{safe_id}.md"

        content = self._build_markdown(report)
        path.write_text(content, encoding="utf-8")
        logger.info("[ReportGenerator] 报告已生成: %s", path)
        return str(path)

    # ─── Markdown 构建 ───────────────────────────────────

    def _build_markdown(self, report: Any) -> str:
        """构建 Markdown 报告内容。"""
        lines: list[str] = []
        lines.append(self._generate_summary(report))
        lines.append(self._generate_equity_curve(report))
        lines.append(self._generate_drawdown_curve(report))
        lines.append(self._generate_ic_timeline(report))
        lines.append(self._generate_monthly_heatmap(report))
        return "\n".join(lines)

    # ─── 分节生成 ────────────────────────────────────────

    def _generate_summary(self, report: Any) -> str:
        """生成报告摘要。"""
        metrics = _get_metrics(report)
        factor_id = _get(report, "factor_id", "unknown")
        factor_name = _get(report, "factor_name", factor_id)
        start = _get(report, "start_date", "N/A")
        end = _get(report, "end_date", "N/A")

        rows = [
            ("因子 ID", factor_id),
            ("因子名称", factor_name),
            ("回测期间", f"{start} ~ {end}"),
            ("总收益", f"{_num(metrics.get('total_return')):.2%}"),
            ("年化收益", f"{_num(metrics.get('annual_return')):.2%}"),
            ("Sharpe", f"{_num(metrics.get('sharpe_ratio')):.3f}"),
            ("最大回撤", f"{_num(metrics.get('max_drawdown')):.2%}"),
            ("Calmar", f"{_num(metrics.get('calmar_ratio')):.3f}"),
            ("胜率", f"{_num(metrics.get('win_rate')):.2%}"),
            ("年化波动", f"{_num(metrics.get('volatility')):.2%}"),
            ("IC 均值", f"{_num(metrics.get('ic_mean')):.4f}"),
            ("IC 波动", f"{_num(metrics.get('ic_std')):.4f}"),
            ("IC IR", f"{_num(metrics.get('ic_ir')):.3f}"),
            ("换手率", f"{_num(metrics.get('turnover')):.3f}"),
        ]
        body = "\n".join(f"| {k} | {v} |" for k, v in rows)
        return f"## 回测摘要\n\n| 指标 | 值 |\n|------|-----|\n{body}\n"

    def _generate_equity_curve(self, report: Any) -> str:
        """生成净值曲线（近 20 个观测点采样）。"""
        equity = _get(report, "equity_curve", None)
        if equity is None or len(equity) == 0:
            return "## 净值曲线\n\n（无净值数据）\n"
        sample = equity.iloc[:: max(1, len(equity) // 20)][:20]
        rows = "\n".join(
            f"| {idx.date() if hasattr(idx, 'date') else idx} | {v:.4f} |"
            for idx, v in sample.items()
        )
        return (
            "## 净值曲线\n\n| 日期 | 净值 |\n|------|------|\n"
            f"{rows}\n"
            f"\n期末净值: {equity.iloc[-1]:.4f}\n"
        )

    def _generate_drawdown_curve(self, report: Any) -> str:
        """生成回撤曲线（最深回撤窗口信息）。"""
        dd = _get(report, "drawdown_curve", None)
        if dd is None or len(dd) == 0:
            return "## 回撤曲线\n\n（无回撤数据）\n"
        min_val = float(dd.min())
        min_idx = dd.idxmin()
        return (
            "## 回撤曲线\n\n"
            f"- 最大回撤: **{min_val:.2%}**\n"
            f"- 最深回撤日期: {min_idx.date() if hasattr(min_idx, 'date') else min_idx}\n"
            f"- 当前回撤: {float(dd.iloc[-1]):.2%}\n"
        )

    def _generate_ic_timeline(self, report: Any) -> str:
        """生成 IC 时序摘要。"""
        ic = _get(report, "ic_series", None)
        if ic is None or len(ic) == 0:
            return "## IC 时序\n\n（无 IC 数据）\n"
        return (
            "## IC 时序\n\n"
            f"- IC 均值: {float(ic.mean()):.4f}\n"
            f"- IC 标准差: {float(ic.std()):.4f}\n"
            f"- IC IR: {float(ic.mean()) / float(ic.std() + 1e-12):.3f}\n"
            f"- IC>0 占比: {float((ic > 0).mean()):.2%}\n"
        )

    def _generate_monthly_heatmap(self, report: Any) -> str:
        """生成月度收益热力表（ASCII 表格）。"""
        equity = _get(report, "equity_curve", None)
        if equity is None or len(equity) < 2:
            return "## 月度收益\n\n（数据不足）\n"
        monthly = equity.resample("ME").last().pct_change().dropna()
        if len(monthly) == 0:
            return "## 月度收益\n\n（无月度数据）\n"
        rows = "\n".join(
            f"| {idx.strftime('%Y-%m')} | {v:+.2%} |" for idx, v in monthly.items()
        )
        return f"## 月度收益\n\n| 月份 | 收益 |\n|------|------|\n{rows}\n"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """从 dataclass 或 dict 获取字段。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _get_metrics(report: Any) -> dict[str, Any]:
    """获取指标 dict（兼容 dataclass/dict）。"""
    metrics = _get(report, "metrics", None)
    if isinstance(metrics, dict):
        return metrics
    return metrics.__dict__ if metrics is not None else {}


def _num(v: Any) -> float:
    """数值兜底。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["ReportGenerator"]
