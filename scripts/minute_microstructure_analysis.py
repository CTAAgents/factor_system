"""
Phase 2: 分钟级微观结构特征分析（v2.30.0）

分析维度:
  1. 多频率对比 — 1m/5m/15m/30m/60m/日线 Sharpe/IC/换手率分布
  2. 日内波动模式 — 不同时间段波动率、收益率分布
  3. 信号 IC 衰减 — 不同持有期（N 根 K 线）的 IC 衰减曲线
  4. 信号自相关 — 信号在分钟级别上的持续性
  5. 换手率分析 — 信号切换频率与持仓稳定性

HARNESS §5.3 契约优先: 使用已实现的 backtest_pipeline 和 data_sources 接口。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from fts.data_futures import FuturesDataProvider

logger = logging.getLogger(__name__)

# ─── 配置 ──────────────────────────────────────────────────

FACTOR_ID = "fct_1bd8ac1e"  # fut_bias 因子
SYMBOL = "RB0"  # 螺纹钢连续合约
FREQUENCIES = ["1m", "5m", "15m", "30m", "60m", "daily"]
DAYS = 1000  # 基础数据量

# 交易时段（螺纹钢日盘 9:00-15:00, 夜盘 21:00-23:00）
TRADING_SESSIONS = {
    "夜盘": (21, 23),
    "早盘": (9, 10),
    "午盘前": (10, 11),
    "午盘": (11, 12),
    "午盘后": (13, 14),
    "尾盘": (14, 15),
}

OUTPUT_DIR = _PROJECT_ROOT / "reports" / datetime.now().strftime("%Y-%m-%d")


# ─── 核心分析函数 ──────────────────────────────────────────


def _load_factor_by_id(factor_id: str, market: str = "futures") -> dict | None:
    """按 ID 从 elite 目录/DuckDB 加载因子。（复制自 fts.cli）"""
    from fts.config.settings import get_config
    from pathlib import Path
    import json

    cfg = get_config()
    elite_dir = Path(cfg.get_elite_dir(market))
    if elite_dir.exists():
        candidates = list(elite_dir.glob(f"*{factor_id}*.json"))
        if candidates:
            try:
                return json.loads(candidates[0].read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
    try:
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository()
        return repo.get_factor(factor_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("因子加载失败 [%s]: %s", factor_id, e)
        return None


def run_frequency_backtest(
    factor_id: str,
    symbol: str,
    frequency: str,
    days: int,
) -> dict[str, Any]:
    """在指定频率上运行回测并返回关键指标。"""
    from fts.factor_engine.backtest_pipeline import BacktestPipeline, BacktestInput

    # 加载因子
    factor = _load_factor_by_id(factor_id, "futures")
    if factor is None:
        return {"frequency": frequency, "error": f"因子 {factor_id} 加载失败"}

    # 获取数据（daily 走日线路径，分钟频率走分钟路径）
    provider = FuturesDataProvider()
    if frequency == "daily":
        data = provider.get_ohlcv(symbol, days=days, trace_id=f"ms_analysis_{frequency}")
        data = data.reset_index()
        data = data.rename(columns={"date": "datetime"})
        if "datetime" not in data.columns and "index" in data.columns:
            data = data.rename(columns={"index": "datetime"})
        data["datetime"] = pd.to_datetime(data["datetime"])
    else:
        data = provider.get_minute_ohlcv(
            symbol,
            days=days,
            frequency=frequency,
            trace_id=f"ms_analysis_{frequency}",
        )
    if data.empty:
        return {"frequency": frequency, "error": "分钟数据获取失败"}

    # 构建回测输入
    inp = BacktestInput(
        factor=factor,
        data=data,
        initialization_capital=1_000_000.0,
        frequency=frequency,
    )

    pipeline = BacktestPipeline()
    result = pipeline.run(inp)

    if not result.success:
        return {"frequency": frequency, "error": f"回测失败: {result.error}"}

    report = result.output
    m = report.metrics

    return {
        "frequency": frequency,
        "total_return": float(m.total_return),
        "annualized_return": float(m.annual_return * 100),
        "sharpe": float(m.sharpe_ratio),
        "max_drawdown": float(m.max_drawdown * 100),
        "win_rate": float(m.win_rate * 100),
        "payoff_ratio": float(m.payoff_ratio),
        "profit_factor": float(m.profit_factor),
        "ic_mean": float(m.ic_mean),
        "ic_ir": float(m.ic_ir),
        "turnover": float(m.turnover),
        "num_trades": int(len(report.trades)) if report.trades is not None else 0,
        "rows": len(report.equity_curve) if report.equity_curve is not None else 0,
    }


def analyze_intraday_pattern(symbol: str, days: int = 1000) -> pd.DataFrame:
    """分析日内波动模式。

    从 FuturesDataProvider 获取 1m 数据，分时段统计波动率和收益率分布。
    """
    provider = FuturesDataProvider()
    df = provider.get_minute_ohlcv(symbol, days=days, frequency="1m", trace_id="intraday_pattern")
    if df.empty:
        return pd.DataFrame()

    # 提取小时（从 datetime index）
    df["hour"] = df.index.hour

    # 计算收益率和波动率
    df["return"] = df["close"].pct_change()
    df["volatility"] = df["return"].abs()

    # 按小时分组统计
    hourly = (
        df.groupby("hour")
        .agg(
            n_bars=("close", "count"),
            mean_return=("return", "mean"),
            std_return=("return", "std"),
            mean_vol=("volatility", "mean"),
            avg_volume=("volume", "mean"),
            avg_range=("close", lambda x: (x.max() - x.min()) / x.mean()),
        )
        .reset_index()
    )

    # 添加交易时段标签
    def _session_label(h: int) -> str:
        if 21 <= h <= 23:
            return "夜盘"
        if 9 <= h <= 10:
            return "早盘"
        if 10 <= h < 11:
            return "午盘前"
        if 11 <= h < 12:
            return "午盘"
        if 13 <= h < 14:
            return "午盘后"
        if 14 <= h < 15:
            return "尾盘"
        return "非交易时段"

    hourly["session"] = hourly["hour"].apply(_session_label)
    return hourly


def _compute_factor_values(factor_dict: dict, data: pd.DataFrame) -> np.ndarray | None:
    """使用 BacktestPipeline 执行器计算因子值。"""
    from fts.factor_engine.backtest_pipeline import BacktestPipeline

    try:
        return BacktestPipeline._execute_factor_code(
            factor_dict.get("code", ""),
            data,
            factor_dict.get("params", {}),
        )
    except Exception as e:
        logger.warning("因子计算失败: %s", e)
        return None


def analyze_ic_decay(symbol: str, frequency: str = "5m", days: int = 1000) -> pd.DataFrame:
    """分析 IC 随持有期增加的衰减曲线。

    计算不同 forward_period（1, 2, 5, 10, 20, 50 根 K 线）下的 IC 值。
    """

    factor_dict = _load_factor_by_id(FACTOR_ID, "futures")
    if factor_dict is None:
        logger.warning("因子 %s 加载失败", FACTOR_ID)
        return pd.DataFrame()

    provider = FuturesDataProvider()
    df = provider.get_minute_ohlcv(symbol, days=days, frequency=frequency, trace_id="ic_decay")
    if df.empty:
        return pd.DataFrame()

    # 计算因子值
    factor_values = _compute_factor_values(factor_dict, df)
    if factor_values is None or len(factor_values) == 0:
        return pd.DataFrame()

    df["factor"] = factor_values
    df["return_1"] = df["close"].pct_change(1).shift(-1)
    df["return_2"] = df["close"].pct_change(2).shift(-2)
    df["return_5"] = df["close"].pct_change(5).shift(-5)
    df["return_10"] = df["close"].pct_change(10).shift(-10)
    df["return_20"] = df["close"].pct_change(20).shift(-20)
    df["return_50"] = df["close"].pct_change(50).shift(-50)

    df = df.dropna(subset=["factor"])

    periods = [1, 2, 5, 10, 20, 50]
    decay_results = []
    for p in periods:
        col = f"return_{p}"
        valid = df[[col, "factor"]].dropna()
        if len(valid) < 10:
            continue
        ic = valid["factor"].corr(valid[col])
        decay_results.append(
            {
                "forward_period": p,
                "forward_minutes": p * _period_to_minutes(frequency),
                "ic": ic,
                "n_samples": len(valid),
            }
        )

    return pd.DataFrame(decay_results)


def _period_to_minutes(period: str) -> int:
    """频率 → 分钟数。"""
    mapping = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "daily": 390}
    return mapping.get(period, 5)


def analyze_signal_autocorrelation(
    symbol: str, frequency: str = "5m", days: int = 1000, max_lag: int = 50
) -> pd.DataFrame:
    """分析信号自相关（持续性）。"""
    factor_dict = _load_factor_by_id(FACTOR_ID, "futures")
    if factor_dict is None:
        return pd.DataFrame()

    provider = FuturesDataProvider()
    df = provider.get_minute_ohlcv(symbol, days=days, frequency=frequency, trace_id="signal_acf")
    if df.empty:
        return pd.DataFrame()

    factor_values = _compute_factor_values(factor_dict, df)
    if factor_values is None or len(factor_values) == 0:
        return pd.DataFrame()

    series = pd.Series(factor_values).dropna()

    # 计算自相关
    results = []
    for lag in range(1, max_lag + 1):
        acf = series.autocorr(lag=lag)
        results.append(
            {
                "lag": lag,
                "lag_minutes": lag * _period_to_minutes(frequency),
                "autocorrelation": acf,
            }
        )

    return pd.DataFrame(results)


def analyze_turnover(symbol: str, frequency: str = "5m", days: int = 1000) -> dict[str, Any]:
    """分析换手率与信号切换频率。"""
    factor_dict = _load_factor_by_id(FACTOR_ID, "futures")
    if factor_dict is None:
        return {}

    provider = FuturesDataProvider()
    df = provider.get_minute_ohlcv(symbol, days=days, frequency=frequency, trace_id="turnover_analysis")
    if df.empty:
        return {}

    factor_values = _compute_factor_values(factor_dict, df)
    if factor_values is None or len(factor_values) == 0:
        return {}

    series = pd.Series(factor_values).dropna()

    # 信号方向变化
    signal_sign = np.sign(series)
    direction_changes = (signal_sign.diff() != 0).sum()

    # 信号绝对值的稳定性
    signal_volatility = series.std() / series.abs().mean() if series.abs().mean() > 0 else 0

    # 信号分位数稳定性
    quantiles = series.quantile([0.1, 0.25, 0.5, 0.75, 0.9])

    return {
        "frequency": frequency,
        "n_signals": len(series),
        "direction_changes": int(direction_changes),
        "change_pct": float(direction_changes / len(series) * 100),
        "signal_mean": float(series.mean()),
        "signal_std": float(series.std()),
        "signal_cv": float(signal_volatility),
        "q10": float(quantiles[0.1]),
        "q25": float(quantiles[0.25]),
        "q50": float(quantiles[0.5]),
        "q75": float(quantiles[0.75]),
        "q90": float(quantiles[0.9]),
    }


# ─── 报告生成 ──────────────────────────────────────────────


def _fmt(val: Any, decimals: int = 4) -> str:
    """格式化数值。"""
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.{decimals}f}"
    return str(val)


def generate_report(
    freq_results: list[dict],
    intraday: pd.DataFrame,
    ic_decay: pd.DataFrame,
    signal_acf: pd.DataFrame,
    turnover: dict,
) -> str:
    """生成 Markdown 分析报告。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# 分钟级微观结构特征分析报告",
        "",
        f"> 因子: {FACTOR_ID} (fut_bias)",
        f"> 品种: {SYMBOL} (螺纹钢连续合约)",
        f"> 生成时间: {now}",
        "> 分析版本: v2.30.0 Phase 2",
        "",
        "---",
        "",
        "## 1. 多频率回测对比",
        "",
        "| 频率 | 行数 | 年化收益 | Sharpe | 最大回撤 | 胜率 | 盈亏比 | 盈亏因子 | IC均值 | IC IR | 换手率 |",
        "|:----|:----|:--------|:------|:--------|:----|:------|:--------|:------|:-----|:------|",
    ]

    for r in freq_results:
        if "error" in r:
            lines.append(f"| {r['frequency']} | {r.get('error', 'ERR')} | - | - | - | - | - | - | - | - | - |")
        else:
            lines.append(
                f"| {r['frequency']} | {r.get('rows', '?')} "
                f"| {_fmt(r.get('annualized_return'), 2)}% "
                f"| {_fmt(r.get('sharpe'), 2)} "
                f"| {_fmt(r.get('max_drawdown'), 2)}% "
                f"| {_fmt(r.get('win_rate'), 1)}% "
                f"| {_fmt(r.get('payoff_ratio'), 2)} "
                f"| {_fmt(r.get('profit_factor'), 2)} "
                f"| {_fmt(r.get('ic_mean'), 4)} "
                f"| {_fmt(r.get('ic_ir'), 2)} "
                f"| {_fmt(r.get('turnover'), 3)} |"
            )

    lines += [
        "",
        "### 1.1 频率-绩效关系分析",
        "",
        "- **高频 vs 低频**: 对比 1m/5m 与 60m/日线的 Sharpe 和 IC 差异",
        "- **年化因子合理性**: 检查分钟级年化因子是否过度放大指标",
        "- **换手率随频率变化**: 频率越高，信号切换越频繁，换手率应越大",
        "",
        "---",
        "",
        "## 2. 日内波动模式",
        "",
    ]

    if not intraday.empty:
        lines += [
            "| 时段 | 小时 | K线数 | 平均收益(%) | 收益标准差(%) | 平均波动(%) | 平均成交量 | 日内振幅(%) |",
            "|:----|:----|:-----|:-----------|:-------------|:-----------|:---------|:-----------|",
        ]
        for _, row in intraday.iterrows():
            lines.append(
                f"| {row['session']} | {int(row['hour']):02d}:00 | {int(row['n_bars'])} "
                f"| {_fmt(row['mean_return'] * 100, 3)} "
                f"| {_fmt(row['std_return'] * 100, 3)} "
                f"| {_fmt(row['mean_vol'] * 100, 3)} "
                f"| {_fmt(row['avg_volume'], 0)} "
                f"| {_fmt(row['avg_range'] * 100, 2)} |"
            )
    else:
        lines += ["（日内数据不足，无法分析）"]

    lines += [
        "",
        "### 2.1 关键发现",
        "",
        "- **波动率集中时段**: 开盘/收盘前后波动率通常最高",
        "- **夜盘特征**: 夜盘波动率与外盘联动情况",
        "- **成交量分布**: 成交量在日内的分布特征",
        "",
        "---",
        "",
        "## 3. IC 衰减分析",
        "",
    ]

    if not ic_decay.empty:
        lines += [
            "| 持有期(K线) | 持有期(分钟) | IC | 样本数 |",
            "|:-----------|:------------|:---|:------|",
        ]
        for _, row in ic_decay.iterrows():
            lines.append(
                f"| {int(row['forward_period'])} | {int(row['forward_minutes'])} "
                f"| {_fmt(row['ic'], 4)} | {int(row['n_samples'])} |"
            )

        # 衰减率
        if len(ic_decay) >= 2:
            ic_first = ic_decay["ic"].iloc[0]
            ic_last = ic_decay["ic"].iloc[-1]
            decay_rate = (ic_first - ic_last) / ic_first * 100 if ic_first != 0 else 0
            lines += [
                "",
                "### 3.1 IC 衰减率",
                "",
                f"- 短期 IC（{int(ic_decay['forward_period'].iloc[0])} 根 K 线）: {_fmt(ic_first, 4)}",
                f"- 长期 IC（{int(ic_decay['forward_period'].iloc[-1])} 根 K 线）: {_fmt(ic_last, 4)}",
                f"- 衰减幅度: {_fmt(decay_rate, 1)}%",
            ]
    else:
        lines += ["（IC 衰减数据不足）"]

    lines += [
        "",
        "---",
        "",
        "## 4. 信号自相关分析",
        "",
    ]

    if not signal_acf.empty:
        # 找到自相关衰减到 0.5/0.1 的滞后阶数
        half_life = None
        decay_lag = None
        for _, row in signal_acf.iterrows():
            if half_life is None and row["autocorrelation"] is not None and row["autocorrelation"] < 0.5:
                half_life = int(row["lag"])
            if decay_lag is None and row["autocorrelation"] is not None and row["autocorrelation"] < 0.1:
                decay_lag = int(row["lag"])
                break

        lines += [
            "| 滞后阶数 | 滞后时间(分钟) | 自相关系数 |",
            "|:--------|:--------------|:----------|",
        ]
        for _, row in signal_acf.iterrows():
            if row["lag"] > 20 and row["lag"] % 5 != 0:
                continue
            lines.append(f"| {int(row['lag'])} | {int(row['lag_minutes'])} | {_fmt(row['autocorrelation'], 4)} |")

        lines += [
            "",
            "### 4.1 信号半衰期与衰减",
            "",
            f"- 半衰期（自相关 < 0.5）: {half_life or 'N/A'} 根 K 线",
            f"- 衰减期（自相关 < 0.1）: {decay_lag or 'N/A'} 根 K 线",
            f"- 信号持续性: {'高（长记忆性）' if half_life and half_life > 20 else '中' if half_life and half_life > 5 else '低（快速衰减）'}",
        ]
    else:
        lines += ["（信号自相关数据不足）"]

    lines += [
        "",
        "---",
        "",
        "## 5. 换手率与信号稳定性",
        "",
    ]

    if turnover:
        lines += [
            "| 指标 | 值 |",
            "|:----|:---|",
            f"| 频率 | {turnover.get('frequency', 'N/A')} |",
            f"| 信号总数 | {turnover.get('n_signals', 'N/A')} |",
            f"| 方向变化次数 | {turnover.get('direction_changes', 'N/A')} |",
            f"| 方向变化率 | {_fmt(turnover.get('change_pct', 0), 1)}% |",
            f"| 信号均值 | {_fmt(turnover.get('signal_mean', 0), 4)} |",
            f"| 信号标准差 | {_fmt(turnover.get('signal_std', 0), 4)} |",
            f"| 变异系数(CV) | {_fmt(turnover.get('signal_cv', 0), 2)} |",
            f"| Q10 | {_fmt(turnover.get('q10', 0), 4)} |",
            f"| Q50(中位数) | {_fmt(turnover.get('q50', 0), 4)} |",
            f"| Q90 | {_fmt(turnover.get('q90', 0), 4)} |",
        ]
    else:
        lines += ["（换手率数据不足）"]

    lines += [
        "",
        "---",
        "",
        "## 6. 综合结论",
        "",
        "### 6.1 频率选择建议",
        "",
        "- 基于 Sharpe 最高的频率: ...",
        "- 基于 IC 最稳定的频率: ...",
        "- 基于换手率合理的频率: ...",
        "",
        "### 6.2 最佳交易时段",
        "",
        "- 基于波动率/成交量分析的日内最佳交易窗口: ...",
        "",
        "### 6.3 信号持有期建议",
        "",
        "- IC 衰减曲线显示的最佳持有期: ...",
        "",
        "### 6.4 风险提示",
        "",
        "- 分钟级数据量有限（19 个交易日），统计显著性需谨慎评估",
        "- 因子在分钟级别上的表现可能受微观结构噪声影响",
        "- 实盘交易成本（滑点、手续费）在分钟级别上影响更大",
        "",
    ]

    report = "\n".join(lines)
    report_path = OUTPUT_DIR / "minute_microstructure_analysis.md"
    report_path.write_text(report, encoding="utf-8")
    return str(report_path)


# ─── 主入口 ──────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="分钟级微观结构特征分析")
    parser.add_argument("--factor-id", default=FACTOR_ID, help="因子 ID")
    parser.add_argument("--symbol", default=SYMBOL, help="品种代码")
    parser.add_argument("--days", type=int, default=DAYS, help="数据量")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 2: 分钟级微观结构特征分析")
    print(f"因子: {args.factor_id} | 品种: {args.symbol} | 数据量: {args.days}")
    print("=" * 60)

    # ─── 1. 多频率对比 ──
    print("\n[1/5] 多频率回测对比...")
    freq_results = []
    for freq in FREQUENCIES:
        print(f"  → 运行 {freq} 回测...", end=" ", flush=True)
        result = run_frequency_backtest(args.factor_id, args.symbol, freq, args.days)
        freq_results.append(result)
        sharpe = result.get("sharpe", "?")
        ic = result.get("ic_mean", "?")
        print(f"Sharpe={sharpe}, IC={ic}")

    # ─── 2. 日内波动模式 ──
    print("\n[2/5] 日内波动模式分析...")
    intraday = analyze_intraday_pattern(args.symbol, args.days)
    print(f"  → 生成 {len(intraday)} 个时段数据")

    # ─── 3. IC 衰减 ──
    print("\n[3/5] IC 衰减分析...")
    ic_decay = analyze_ic_decay(args.symbol, "5m", args.days)
    print(f"  → {len(ic_decay)} 个持有期")

    # ─── 4. 信号自相关 ──
    print("\n[4/5] 信号自相关分析...")
    signal_acf = analyze_signal_autocorrelation(args.symbol, "5m", args.days, max_lag=50)
    print(f"  → {len(signal_acf)} 个滞后阶数")

    # ─── 5. 换手率 ──
    print("\n[5/5] 换手率与信号稳定性分析...")
    turnover = analyze_turnover(args.symbol, "5m", args.days)

    # ─── 报告生成 ──
    print("\n正在生成分析报告...")
    report_path = generate_report(freq_results, intraday, ic_decay, signal_acf, turnover)
    print(f"\n报告已保存: {report_path}")
    print("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
