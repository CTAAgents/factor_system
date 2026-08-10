"""
fts/cross_market/engine.py — 跨市场泛化验证引擎

核心职责:
    1. 加载源市场 Elite 因子
    2. 在目标市场面板上计算截面 IC
    3. 分类因子（通用/市场特异/失效）
    4. 生成验证报告

HARNESS §trace_id 全链路: 所有操作支持 trace_id 参数。
"""

from __future__ import annotations

import json
import logging
import time
import warnings
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .data_adapter import CrossMarketDataAdapter, TARGET_MARKET_STOCK, TARGET_MARKET_ETF, TARGET_MARKET_FUTURES

logger = logging.getLogger(__name__)

# 抑制 numpy/scipy 运行时警告
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")

# ─── 路径常量 ─────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FUTURES_ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/futures_elite"
STOCK_ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/elite"
REPORTS_ROOT = PROJECT_ROOT / "reports"

# 金融期货（排除）
FINANCIAL_FUTURES = {"IF0", "TF0", "IH0", "IC0", "TS0", "IM0"}


# ─── 分类标准 ─────────────────────────────────────────────

GENERALIZATION_THRESHOLD = 0.02  # 跨市场 IC 最低阈值（通用因子）
FUTURES_SPECIFIC_THRESHOLD = 0.03  # 期货市场 IC 最低阈值（期货特异）
FAILURE_THRESHOLD = 0.01  # 跨市场 IC 低于此视为失效
RETENTION_RATIO = 0.50  # IC 保持率 >= 50% 视为通用


@dataclass
class CrossMarketResult:
    """单因子跨市场验证结果。"""

    name: str
    factor_id: str
    source_market: str
    target_market: str
    source_ic: float  # 源市场 IC（晋级时的历史 IC）
    target_ic: float  # 目标市场 IC（计算的跨市场 IC）
    target_ic_abs: float  # 目标市场 IC 绝对值
    ic_retention: float  # IC 保持率
    generalization: str  # 分类: universal/futures_specific/stock_specific/failed
    n_target_symbols: int  # 目标市场有效品种数
    n_dates: int  # 交易日数
    eval_time_sec: float  # 评估耗时
    is_deprecated: bool  # 是否已降级


@dataclass
class CrossMarketReport:
    """跨市场验证报告。"""

    generated_at: str
    source_market: str
    target_market: str
    total_factors: int
    n_universal: int
    n_market_specific: int
    n_failed: int
    n_deprecated: int
    n_dates: int
    n_target_symbols: int
    elapsed_sec: float
    results: list[CrossMarketResult] = field(default_factory=list)
    target_ic_distribution: list[float] = field(default_factory=list)


class CrossMarketEngine:
    """跨市场泛化验证引擎。

    用法:
        engine = CrossMarketEngine()
        report = engine.run_futures_to_stock(days=120, max_stocks=50)
        print(report.n_universal, report.n_failed)
    """

    def __init__(self, adapter: CrossMarketDataAdapter | None = None):
        self._adapter = adapter or CrossMarketDataAdapter()

    # ── 公开入口 ──────────────────────────────────────────

    def run_futures_to_stock(
        self,
        days: int = 120,
        max_stocks: int = 0,
        max_factors: int = 0,
        trace_id: str = "",
    ) -> CrossMarketReport:
        """期货→股票跨市场泛化验证。

        Args:
            days: 回溯天数
            max_stocks: 最大成分股数（0=全量）
            max_factors: 最大因子数（0=全量）
            trace_id: HARNESS trace_id

        Returns:
            CrossMarketReport 验证报告
        """
        return self._run(
            source_market="futures",
            target_market=TARGET_MARKET_STOCK,
            factor_loader=self._load_futures_factors,
            panel_getter=lambda: self._adapter.get_panel(
                TARGET_MARKET_STOCK,
                days=days,
                max_stocks=max_stocks,
                trace_id=trace_id,
            ),
            max_factors=max_factors,
        )

    def run_futures_to_etf(
        self,
        days: int = 120,
        max_factors: int = 0,
        trace_id: str = "",
    ) -> CrossMarketReport:
        """期货→ETF 跨市场泛化验证。"""
        return self._run(
            source_market="futures",
            target_market=TARGET_MARKET_ETF,
            factor_loader=self._load_futures_factors,
            panel_getter=lambda: self._adapter.get_panel(
                TARGET_MARKET_ETF,
                days=days,
                trace_id=trace_id,
            ),
            max_factors=max_factors,
        )

    def run_stock_to_futures(
        self,
        days: int = 120,
        max_factors: int = 0,
        trace_id: str = "",
    ) -> CrossMarketReport:
        """股票→期货跨市场泛化验证。"""
        return self._run(
            source_market="stock",
            target_market=TARGET_MARKET_FUTURES,
            factor_loader=self._load_stock_factors,
            panel_getter=lambda: self._adapter.get_panel(
                TARGET_MARKET_FUTURES,
                days=days,
                trace_id=trace_id,
            ),
            max_factors=max_factors,
        )

    # ── 核心验证逻辑 ──────────────────────────────────────

    def _run(
        self,
        source_market: str,
        target_market: str,
        factor_loader: Callable[..., Any],
        panel_getter: Callable[..., Any],
        max_factors: int = 0,
    ) -> CrossMarketReport:
        """执行跨市场泛化验证。

        Args:
            source_market: 源市场名称
            target_market: 目标市场名称
            factor_loader: 因子加载函数
            panel_getter: 面板数据获取函数
            max_factors: 最大因子数

        Returns:
            CrossMarketReport
        """
        t0 = time.time()
        today = date.today().isoformat()

        # Step 1: 加载因子
        factors = factor_loader()
        if not factors:
            logger.warning("无源市场因子")
            return CrossMarketReport(
                generated_at=today,
                source_market=source_market,
                target_market=target_market,
                total_factors=0,
                n_universal=0,
                n_market_specific=0,
                n_failed=0,
                n_deprecated=0,
                n_dates=0,
                n_target_symbols=0,
                elapsed_sec=0,
                results=[],
            )

        if max_factors > 0:
            factors = factors[:max_factors]

        n_active = sum(1 for f in factors if not f.get("_deprecated", False))
        n_deprecated = sum(1 for f in factors if f.get("_deprecated", False))
        logger.info(f"[1] 加载因子: {len(factors)} 个 ({n_active} 活跃, {n_deprecated} 已降级)")

        # Step 2: 获取目标市场面板数据
        panel, common_dates = panel_getter()
        if not panel or len(common_dates) < 10:
            logger.warning("目标市场数据不足")
            return CrossMarketReport(
                generated_at=today,
                source_market=source_market,
                target_market=target_market,
                total_factors=len(factors),
                n_universal=0,
                n_market_specific=0,
                n_failed=0,
                n_deprecated=n_deprecated,
                n_dates=0,
                n_target_symbols=0,
                elapsed_sec=time.time() - t0,
                results=[],
            )

        n_dates = len(common_dates)
        n_symbols = len(panel)
        logger.info(f"[2] 获取数据: {n_symbols} 个品种, {n_dates} 个交易日")

        # Step 3: 逐因子验证
        logger.info(f"[3] 逐因子验证 ({len(factors)} 个)...")
        results: list[CrossMarketResult] = []
        target_ics: list[float] = []

        for i, factor_data in enumerate(factors, 1):
            ft0 = time.time()
            name = factor_data.get("name", "?")
            fid = factor_data.get("factor_id", "")
            is_deprecated = factor_data.get("_deprecated", False)

            # 获取源市场 IC（晋级时的历史 IC）
            ev = factor_data.get("evaluation", {})
            bt = ev.get("level_1_backtest", {})
            source_ic = bt.get("ic", 0) or 0

            # 计算目标市场 IC
            target_ic, n_valid = self._compute_cross_market_ic(
                factor_data,
                panel,
                common_dates,
            )

            target_ic_abs = abs(target_ic) if np.isfinite(target_ic) else 0.0
            ic_retention = target_ic_abs / max(abs(source_ic), 1e-10) if abs(source_ic) > 1e-10 else 0.0

            # 分类
            generalization = self._classify(
                source_ic,
                target_ic_abs,
                ic_retention,
            )

            result = CrossMarketResult(
                name=name,
                factor_id=fid,
                source_market=source_market,
                target_market=target_market,
                source_ic=abs(source_ic),
                target_ic=target_ic,
                target_ic_abs=target_ic_abs,
                ic_retention=ic_retention,
                generalization=generalization,
                n_target_symbols=n_valid,
                n_dates=n_dates,
                eval_time_sec=time.time() - ft0,
                is_deprecated=is_deprecated,
            )
            results.append(result)
            target_ics.append(target_ic_abs)

            # 进度输出
            if i % 5 == 0 or i == len(factors) or generalization != "unknown":
                gen_icon = {
                    "universal": "🌍",
                    "futures_specific": "🔄",
                    "stock_specific": "📈",
                    "failed": "❌",
                    "unknown": "❓",
                }.get(generalization, "❓")
                logger.info(
                    f"  [{i}/{len(factors)}] {gen_icon} {name}: "
                    f"目标 IC={target_ic_abs:.4f}, 源 IC={abs(source_ic):.4f}, "
                    f"保持率={ic_retention:.1%}"
                )

        # Step 4: 统计汇总
        elapsed = time.time() - t0
        n_universal = sum(1 for r in results if r.generalization == "universal")
        n_market_specific = sum(1 for r in results if r.generalization in ("futures_specific", "stock_specific"))
        n_failed = sum(1 for r in results if r.generalization == "failed")

        report = CrossMarketReport(
            generated_at=today,
            source_market=source_market,
            target_market=target_market,
            total_factors=len(factors),
            n_universal=n_universal,
            n_market_specific=n_market_specific,
            n_failed=n_failed,
            n_deprecated=n_deprecated,
            n_dates=n_dates,
            n_target_symbols=n_symbols,
            elapsed_sec=elapsed,
            results=results,
            target_ic_distribution=target_ics,
        )

        logger.info(f"[4] 验证完成: {len(factors)} 个因子, 耗时 {elapsed:.1f}s")
        logger.info(
            f"     🌍 通用: {n_universal} | 🔄 市场特异: {n_market_specific} | "
            f"❌ 失效: {n_failed} | ⬇️ 已降级: {n_deprecated}"
        )

        return report

    # ── 跨市场 IC 计算 ─────────────────────────────────────

    def _compute_cross_market_ic(
        self,
        factor_data: dict[str, Any],
        panel: dict[str, pd.DataFrame],
        common_dates: pd.DatetimeIndex,
    ) -> tuple[float, int]:
        """计算因子在目标市场面板上的截面平均 IC。

        Args:
            factor_data: 因子定义
            panel: 目标市场面板数据
            common_dates: 共有日期

        Returns:
            (mean_ic, n_valid_symbols)
        """
        from scipy.stats import spearmanr

        n_dates = len(common_dates)
        if n_dates < 10:
            return float("nan"), 0

        # 执行因子
        sym_signals = self._adapter.execute_factor_on_market(
            factor_data,
            panel,
            common_dates,
        )
        if len(sym_signals) < 5:
            return float("nan"), len(sym_signals)

        # 每日截面 IC
        daily_ics: list[float] = []
        for t in range(n_dates - 5):
            signals_t: dict[str, float] = {}
            rets_t: dict[str, float] = {}
            for sym, arr in sym_signals.items():
                if t >= len(arr) or not np.isfinite(arr[t]):
                    continue
                df = panel.get(sym)
                if df is None:
                    continue
                closes = df.reindex(common_dates)["close"].values
                if t + 5 >= len(closes):
                    continue
                p_t = closes[t]
                if not np.isfinite(p_t) or p_t <= 1e-10:
                    continue
                ret = (closes[t + 5] - p_t) / p_t
                if not np.isfinite(ret):
                    continue
                signals_t[sym] = float(arr[t])
                rets_t[sym] = ret

            common = set(signals_t.keys()) & set(rets_t.keys())
            if len(common) >= 5:
                s_vals = [signals_t[s] for s in common]
                r_vals = [rets_t[s] for s in common]
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=RuntimeWarning)
                    r, _ = spearmanr(s_vals, r_vals)
                if not np.isnan(r):
                    daily_ics.append(r)

        if not daily_ics:
            return float("nan"), len(sym_signals)
        return float(np.mean(daily_ics)), len(sym_signals)

    # ── 因子分类 ──────────────────────────────────────────

    def _classify(
        self,
        source_ic: float,
        target_ic_abs: float,
        ic_retention: float,
    ) -> str:
        """根据跨市场 IC 表现分类因子。

        Returns:
            universal / futures_specific / stock_specific / failed / unknown
        """
        source_ic_abs = abs(source_ic)

        if target_ic_abs >= GENERALIZATION_THRESHOLD and ic_retention >= RETENTION_RATIO:
            return "universal"

        if target_ic_abs < FAILURE_THRESHOLD and source_ic_abs >= FUTURES_SPECIFIC_THRESHOLD:
            return "failed"

        if target_ic_abs < GENERALIZATION_THRESHOLD and source_ic_abs >= FUTURES_SPECIFIC_THRESHOLD:
            return "futures_specific"

        if source_ic_abs < FUTURES_SPECIFIC_THRESHOLD:
            return "unknown"

        return "futures_specific"

    # ── 因子加载 ──────────────────────────────────────────

    def _load_futures_factors(self) -> list[dict[str, Any]]:
        """加载所有期货精英因子（含已降级）。"""
        return self._load_factors_from_dir(FUTURES_ELITE_DIR)

    def _load_stock_factors(self) -> list[dict[str, Any]]:
        """加载所有股票精英因子（含已降级）。"""
        return self._load_factors_from_dir(STOCK_ELITE_DIR)

    @staticmethod
    def _load_factors_from_dir(directory: Path) -> list[dict[str, Any]]:
        """从目录加载所有 JSON 因子文件。"""
        factors: list[dict[str, Any]] = []

        # 主目录
        if directory.exists():
            for fp in sorted(directory.glob("*.json")):
                try:
                    data = json.loads(fp.read_text(encoding="utf-8"))
                    data["_filepath"] = str(fp)
                    data["_deprecated"] = False
                    factors.append(data)
                except (json.JSONDecodeError, OSError):
                    continue

        # 降级目录
        deprecated_dir = directory / "_deprecated"
        if deprecated_dir.exists():
            for fp in sorted(deprecated_dir.glob("*.json")):
                try:
                    data = json.loads(fp.read_text(encoding="utf-8"))
                    data["_filepath"] = str(fp)
                    data["_deprecated"] = True
                    factors.append(data)
                except (json.JSONDecodeError, OSError):
                    continue

        return factors

    # ── 报告生成 ──────────────────────────────────────────

    def generate_report(
        self,
        report: CrossMarketReport,
        output_path: str | Path | None = None,
    ) -> str:
        """生成跨市场泛化验证 Markdown 报告。

        Args:
            report: 验证结果
            output_path: 输出路径，默认自动生成到 reports/{date}/

        Returns:
            str: 报告文件路径
        """
        if output_path is None:
            report_dir = REPORTS_ROOT / report.generated_at
            report_dir.mkdir(parents=True, exist_ok=True)
            output_path = report_dir / f"cross_market_revalidation_{report.generated_at}.md"

        output_path = Path(output_path)

        lines: list[str] = []

        def w(s=""):
            lines.append(s)

        market_label = {
            "futures": "期货",
            "stock": "A股",
            "etf": "ETF",
        }
        src_label = market_label.get(report.source_market, report.source_market)
        tgt_label = market_label.get(report.target_market, report.target_market)

        w(f"# 跨市场泛化验证报告 — {src_label}→{tgt_label}")
        w()
        w(f"生成时间: {report.generated_at} | 耗时: {report.elapsed_sec:.1f}s")
        w(f"验证因子: {report.total_factors} 个")
        w(f"  - 活跃: {report.total_factors - report.n_deprecated}")
        w(f"  - 已降级: {report.n_deprecated}")
        w(f"数据窗口: {report.n_dates} 个交易日 × {report.n_target_symbols} 个{tgt_label}品种")
        w()
        w("## 验证结果汇总")
        w()
        w(f"- 🌍 通用因子 (跨市场有效): **{report.n_universal}**")
        w(f"- 🔄 市场特异 (仅{src_label}有效): **{report.n_market_specific}**")
        w(f"- ❌ 跨市场失效: **{report.n_failed}**")
        w()

        # IC 分布
        ics = [r.target_ic_abs for r in report.results if not r.is_deprecated]
        if ics:
            w("## 跨市场 IC 分布")
            w()
            w(f"- 平均跨市场 IC: {np.mean(ics):.4f}")
            w(f"- 中位数跨市场 IC: {np.median(ics):.4f}")
            w(f"- 标准差: {np.std(ics):.4f}")
            w(f"- 跨市场 IC ≥ 0.02: {sum(1 for ic in ics if ic >= 0.02)}/{len(ics)}")
            w(f"- 跨市场 IC < 0.01: {sum(1 for ic in ics if ic < 0.01)}/{len(ics)}")
            w()

        # 通用因子详情
        universal = [r for r in report.results if r.generalization == "universal"]
        if universal:
            w("## 🌍 通用因子 (跨市场有效)")
            w()
            w("| 因子名称 | 源市场 IC | 跨市场 IC | IC 保持率 | 有效品种数 |")
            w("|----------|-----------|-----------|-----------|------------|")
            for r in sorted(universal, key=lambda x: -x.target_ic_abs):
                w(
                    f"| {r.name} | {r.source_ic:.4f} | {r.target_ic_abs:.4f} | "
                    f"{r.ic_retention:.1%} | {r.n_target_symbols} |"
                )
            w()

        # 市场特异因子
        specific = [r for r in report.results if r.generalization in ("futures_specific", "stock_specific")]
        if specific:
            w(f"## 🔄 市场特异因子 (仅{src_label}有效)")
            w()
            w("| 因子名称 | 源市场 IC | 跨市场 IC | 降低幅度 | 有效品种数 |")
            w("|----------|-----------|-----------|----------|------------|")
            for r in sorted(specific, key=lambda x: -x.source_ic):
                drop = 1 - r.ic_retention if r.ic_retention <= 1 else 0
                w(f"| {r.name} | {r.source_ic:.4f} | {r.target_ic_abs:.4f} | {drop:.1%} | {r.n_target_symbols} |")
            w()

        # 失效因子
        failed = [r for r in report.results if r.generalization == "failed"]
        if failed:
            w("## ❌ 跨市场失效因子")
            w()
            w("| 因子名称 | 源市场 IC | 跨市场 IC | 降低幅度 | 有效品种数 |")
            w("|----------|-----------|-----------|----------|------------|")
            for r in sorted(failed, key=lambda x: -x.source_ic):
                drop = 1 - r.ic_retention if r.ic_retention <= 1 else 0
                w(f"| {r.name} | {r.source_ic:.4f} | {r.target_ic_abs:.4f} | {drop:.1%} | {r.n_target_symbols} |")
            w()

        # 全量因子 IC 对比
        w("## 全量因子跨市场 IC 对比")
        w()
        w("| 因子名称 | 分类 | 源市场 IC | 跨市场 IC | IC 保持率 |")
        w("|----------|------|-----------|-----------|-----------|")
        for r in sorted(report.results, key=lambda x: -x.target_ic_abs):
            gen_label = {
                "universal": "🌍 通用",
                "futures_specific": "🔄 期货特异",
                "stock_specific": "📈 股票特异",
                "failed": "❌ 失效",
                "unknown": "❓ 未知",
            }.get(r.generalization, "❓")
            status = "⬇️ 已降级" if r.is_deprecated else ""
            w(f"| {r.name} {status} | {gen_label} | {r.source_ic:.4f} | {r.target_ic_abs:.4f} | {r.ic_retention:.1%} |")
        w()

        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"报告已保存: {output_path}")
        return str(output_path)
