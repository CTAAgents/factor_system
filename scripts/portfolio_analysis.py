"""
scripts/portfolio_analysis.py — L3 组合策略深度分析

在回测结果基础上，进行：
    1. 行业暴露分析 — 多空组合的行业分布
    2. 因子归因 — 因子类型贡献度分解
    3. 市场环境表现 — 不同 regime 下的策略表现
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data import FTSDataProvider  # noqa: E402
from fts.factor_engine.factor_program import FactorExecutor, FactorCompileError  # noqa: E402
from fts.factor_engine.regime import RegimeAwareSelector  # noqa: E402

# ─── 常量 ──────────────────────────────────────────────────

ELITE_DIR = PROJECT_ROOT / "memory" / "knowledge" / "factors" / "elite"
COMBO_FILE = PROJECT_ROOT / "memory" / "portfolio" / "current_combo.json"
TOP_PCT = 0.2
OOS_RATIO = 0.3
PERIODS_PER_YEAR = 252

# ─── 行业分类映射（基于沪深 300 常见行业分类）────────────────

STOCK_SECTOR_MAP: dict[str, str] = {
    # 银行
    "600000": "银行",
    "601166": "银行",
    "601288": "银行",
    "601328": "银行",
    "601398": "银行",
    "600036": "银行",
    # 非银金融
    "601318": "保险",
    "601628": "保险",
    "600030": "券商",
    # 白酒
    "600519": "白酒",
    "000858": "白酒",
    "002304": "白酒",
    "600809": "白酒",
    "603288": "白酒",
    # 医药
    "300015": "医药",
    "300760": "医药",
    "600276": "医药",
    "600085": "医药",
    # 科技/电子
    "002371": "半导体",
    "300433": "电子",
    "300450": "电子",
    "300502": "通信",
    "603501": "半导体",
    "002475": "消费电子",
    "688008": "半导体",
    "688036": "电子",
    "688111": "科技",
    "688122": "军工",
    "688256": "科技",
    "688396": "电子",
    # 新能源
    "300274": "新能源",
    "300750": "新能源",
    "300438": "新能源",
    "002594": "新能源车",
    "601012": "光伏",
    "603659": "新能源",
    "600438": "光伏",
    # 家电
    "000651": "家电",
    "000333": "家电",
    "600690": "家电",
    # 地产
    "000002": "地产",
    "600048": "地产",
    # 汽车
    "600104": "汽车",
    "601127": "汽车",
    # 有色/资源
    "601899": "有色",
    "600547": "黄金",
    "600585": "建材",
    # 食品饮料
    "600887": "乳业",
    "002714": "养殖",
    # 医药器械
    "300413": "医药",
    # 互联网/传媒
    "002027": "广告传媒",
    "300059": "金融科技",
    # 通信/运营商
    "600941": "通信",
    "601728": "通信",
    # 电力/公用
    "600900": "电力",
    "601985": "电力",
    "600028": "石化",
    # 煤炭
    "601088": "煤炭",
    # 机械
    "600031": "机械",
    "601766": "轨交",
    # 计算机
    "002230": "人工智能",
    "002415": "安防",
    "300124": "工控",
    "300308": "通信",
    "600406": "软件",
    "600436": "医药",
    "601857": "石化",
    "601888": "免税",
    "603259": "医药",
    "000568": "白酒",
    "000725": "面板",
    "300498": "养殖",
    "300628": "电子",
    "600309": "化工",
    "600570": "金融科技",
    # 默认
}

# ─── 因子类型分类 ──────────────────────────────────────────

FACTOR_TYPE_PREFIXES = {
    "momentum": "动量",
    "volatility_reversion": "波动率",
    "quality_factor": "质量",
    "pmi_proxy": "宏观",
    "qlib": "Qlib 因子",
    "alpha": "世坤因子",
}


def classify_factor(name: str) -> str:
    """根据因子名称分类。"""
    for prefix, label in FACTOR_TYPE_PREFIXES.items():
        if name.lower().startswith(prefix):
            return label
    return "其他"


# ─── 加载数据 ──────────────────────────────────────────────


def load_portfolio() -> dict:
    return json.loads(COMBO_FILE.read_text(encoding="utf-8"))


def load_elite_factors() -> dict[str, dict]:
    factors: dict[str, dict] = {}
    for fp in sorted(ELITE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            factors[data.get("factor_id", fp.stem)] = data
        except (json.JSONDecodeError, OSError):
            continue
    return factors


# ══════════════════════════════════════════════════════════
# 1. 行业暴露分析
# ══════════════════════════════════════════════════════════


def analyze_sector_exposure(
    panel: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    composite: np.ndarray,
    oos_slice: slice,
    fwd_ret_matrix: np.ndarray,
) -> dict:
    """分析纯多头组合的行业暴露。

    每期:
        - 识别做多标的
        - 统计各行业的做多暴露
        - 计算各行业收益贡献
    """
    symbols = sorted(panel.keys())
    oos_n = oos_slice.stop - oos_slice.start
    oos_composite = composite[oos_slice, :]
    oos_fwd_ret = fwd_ret_matrix[oos_slice, :]

    # 累计各行业暴露
    sector_long_days: dict[str, int] = defaultdict(int)  # 被选中的天数
    sector_total_days: dict[str, int] = defaultdict(int)  # 总出现天数
    sector_long_return: dict[str, float] = defaultdict(float)

    for t in range(oos_n):
        scores = oos_composite[t, :]
        rets = oos_fwd_ret[t, :]
        valid = ~(np.isnan(scores) | np.isnan(rets))
        if np.sum(valid) < 5:
            continue

        valid_idx = np.where(valid)[0]
        scores_v = scores[valid_idx]
        rets[valid_idx]
        sorted_idx = np.argsort(scores_v)
        top_n = max(1, int(len(sorted_idx) * TOP_PCT))

        long_indices = valid_idx[sorted_idx[-top_n:]]

        for j in long_indices:
            sym = symbols[j]
            sector = STOCK_SECTOR_MAP.get(sym, "其他")
            sector_long_days[sector] += 1
            sector_total_days[sector] += 1
            sector_long_return[sector] += rets[j]

        # 未入选的标的也计入总天数统计
        for j in valid_idx:
            sym = symbols[j]
            sector = STOCK_SECTOR_MAP.get(sym, "其他")
            if sector not in sector_total_days:
                sector_total_days[sector] = 0
            # 只统计一次，用 long_indices 来避免重复
        for j in valid_idx:
            if j not in long_indices:
                sym = symbols[j]
                sector = STOCK_SECTOR_MAP.get(sym, "其他")
                sector_total_days[sector] = max(sector_total_days.get(sector, 0), 0)

    # 修正：用总天数中该行业出现的次数
    # 重新统计行业总出现天数
    sector_total_days = defaultdict(int)
    for t in range(oos_n):
        scores = oos_composite[t, :]
        rets = oos_fwd_ret[t, :]
        valid = ~(np.isnan(scores) | np.isnan(rets))
        if np.sum(valid) < 5:
            continue
        valid_idx = np.where(valid)[0]
        for j in valid_idx:
            sym = symbols[j]
            sector = STOCK_SECTOR_MAP.get(sym, "其他")
            sector_total_days[sector] += 1

    # 构建报告
    sum(sector_long_days.values())
    sector_report = []
    for sector in sorted(sector_total_days.keys()):
        select_pct = sector_long_days.get(sector, 0) / max(sector_total_days[sector], 1) * 100
        avg_ret = sector_long_return.get(sector, 0) / max(sector_long_days.get(sector, 1), 1) * 10000  # bp
        sector_report.append(
            {
                "sector": sector,
                "selection_pct": round(select_pct, 2),
                "avg_daily_return_bp": round(avg_ret, 2),
                "selected_days": sector_long_days.get(sector, 0),
                "total_days": sector_total_days[sector],
            }
        )

    sector_report.sort(key=lambda x: abs(x["avg_daily_return_bp"]), reverse=True)
    return {"sectors": sector_report}


# ══════════════════════════════════════════════════════════
# 2. 因子归因分析
# ══════════════════════════════════════════════════════════


def analyze_factor_attribution(
    combo: dict,
    elite_factors: dict[str, dict],
    panel: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    symbols: list[str],
    composite: np.ndarray,
    oos_slice: slice,
    fwd_ret_matrix: np.ndarray,
) -> dict:
    """分析各因子类型对组合的贡献。

    通过逐个剔除因子类型，观察绩效变化，
    计算各因子类型对组合夏普的边际贡献。
    """
    executors: dict[str, tuple[str, FactorExecutor]] = {}
    for sig in combo.get("signals", []):
        fid = sig["factor_id"]
        fp_data = elite_factors.get(fid)
        if fp_data is None:
            continue
        try:
            executor = FactorExecutor(fp_data)
            executor.compile()
            executors[fid] = (fp_data.get("name", fid), executor)
        except FactorCompileError:
            continue

    # 按类型分组
    type_members: dict[str, list[str]] = defaultdict(list)
    type_weights: dict[str, float] = defaultdict(float)
    weight_map = {s["factor_id"]: s.get("weight", 0) for s in combo.get("signals", [])}
    total_w = sum(weight_map.values())

    for fid, (name, _) in executors.items():
        ftype = classify_factor(name)
        type_members[ftype].append(fid)
        type_weights[ftype] += weight_map.get(fid, 0) / max(total_w, 1)

    # 计算各类型信号矩阵
    n_dates = len(common_dates)
    n_stocks = len(symbols)
    type_matrices: dict[str, np.ndarray] = {}
    type_compile_errors = 0

    for ftype, fids in type_members.items():
        type_composite = np.zeros((n_dates, n_stocks))
        n_ok = 0
        for fid in fids:
            if fid not in executors:
                continue
            _, executor = executors[fid]
            w = weight_map.get(fid, 0) / max(total_w, 1)
            stock_signals = []
            ok = True
            for sym in symbols:
                df = panel[sym]
                try:
                    arr = executor.execute(df, {})
                    stock_signals.append(arr)
                except Exception:
                    stock_signals.append(np.full(len(df), np.nan))
                    ok = False
            if ok:
                matrix = np.zeros((n_dates, n_stocks))
                for j, sym in enumerate(symbols):
                    full_sig = stock_signals[j]
                    date_idx = panel[sym].index
                    date_map = {d: i for i, d in enumerate(date_idx)}
                    for t, d in enumerate(common_dates):
                        if d in date_map:
                            matrix[t, j] = full_sig[date_map[d]]
                        else:
                            matrix[t, j] = np.nan
                # 横截面标准化
                for t in range(n_dates):
                    row = matrix[t, :]
                    mu = np.nanmean(row)
                    sigma = np.nanstd(row)
                    if sigma > 1e-10:
                        matrix[t, :] = (row - mu) / sigma
                    else:
                        matrix[t, :] = 0.0
                type_composite += w * matrix
                n_ok += 1
            else:
                type_compile_errors += 1
        if n_ok > 0:
            type_matrices[ftype] = type_composite

    # 计算各类型单独纯多头组合的夏普
    oos_start = oos_slice.start
    oos_n = oos_slice.stop - oos_start
    oos_fwd_ret = fwd_ret_matrix[oos_slice, :]

    type_performance = {}
    composite[oos_slice, :]

    for ftype, mat in type_matrices.items():
        mat_oos = mat[oos_slice, :]
        daily_ret = np.zeros(oos_n)
        for t in range(oos_n):
            scores = mat_oos[t, :]
            rets = oos_fwd_ret[t, :]
            valid = ~(np.isnan(scores) | np.isnan(rets))
            if np.sum(valid) < 5:
                continue
            scores_v = scores[valid]
            rets_v = rets[valid]
            sorted_idx = np.argsort(scores_v)
            top_n = max(1, int(len(sorted_idx) * TOP_PCT))
            long_ret = np.mean(rets_v[sorted_idx[-top_n:]])
            daily_ret[t] = long_ret  # 纯多头收益

        sharpe = _compute_sharpe(daily_ret)
        total_ret = float(np.cumsum(daily_ret)[-1])
        cum = np.cumsum(daily_ret)
        mdd = _compute_max_drawdown(cum)
        win_rate = float(np.mean(daily_ret > 0))
        ic_mean = 0.0
        for t in range(oos_n):
            scores = mat_oos[t, :]
            rets = oos_fwd_ret[t, :]
            valid = ~(np.isnan(scores) | np.isnan(rets))
            if np.sum(valid) < 5:
                continue
            from scipy import stats as sp_stats

            ic_val, _ = sp_stats.spearmanr(scores[valid], rets[valid])
            if not np.isnan(ic_val):
                ic_mean += ic_val
        ic_mean /= max(oos_n, 1)

        type_performance[ftype] = {
            "sharpe": round(sharpe, 2),
            "total_return_pct": round(total_ret * 100, 2),
            "max_drawdown_pct": round(mdd * 100, 2),
            "win_rate": round(win_rate * 100, 2),
            "ic_mean": round(ic_mean, 4),
            "n_factors": len(type_members[ftype]),
            "weight": round(type_weights[ftype] * 100, 1),
        }

    # 剔除效应：去掉某类型后绩效变化
    for ftype in type_performance:
        remaining = [ft for ft in type_performance if ft != ftype]
        if not remaining:
            continue
        remain_ret = np.zeros(oos_n)
        for t in range(oos_n):
            rets = oos_fwd_ret[t, :]
            # 用剩余类型加权合成
            remain_scores = np.zeros(n_stocks)
            total_rw = 0
            for ft in remaining:
                if ft in type_matrices:
                    w = type_weights.get(ft, 0)
                    remain_scores += w * type_matrices[ft][oos_start + t, :]
                    total_rw += w
            if total_rw > 0:
                remain_scores /= total_rw
            valid = ~(np.isnan(remain_scores) | np.isnan(rets))
            if np.sum(valid) < 5:
                continue
            scores_v = remain_scores[valid]
            rets_v = rets[valid]
            sorted_idx = np.argsort(scores_v)
            top_n = max(1, int(len(sorted_idx) * TOP_PCT))
            long_ret = np.mean(rets_v[sorted_idx[-top_n:]])
            remain_ret[t] = long_ret  # 纯多头收益

        without_sharpe = _compute_sharpe(remain_ret)
        full_sharpe = type_performance[ftype].get("_full_sharpe", 0)
        if full_sharpe == 0:
            full_sharpe = _compute_sharpe(composite_backtest(composite, fwd_ret_matrix, oos_slice, symbols, panel))
            for ft in type_performance:
                type_performance[ft]["_full_sharpe"] = full_sharpe

        type_performance[ftype]["marginal_contribution"] = round(full_sharpe - without_sharpe, 2)

    # 清理内部字段
    for ft in type_performance:
        type_performance[ft].pop("_full_sharpe", None)

    return {
        "type_performance": type_performance,
        "type_membership": {k: len(v) for k, v in type_members.items()},
    }


def composite_backtest(
    composite: np.ndarray,
    fwd_ret_matrix: np.ndarray,
    oos_slice: slice,
    symbols: list[str],
    panel: dict[str, pd.DataFrame],
) -> np.ndarray:
    """辅助：计算复合评分的纯多头收益序列。"""
    oos_n = oos_slice.stop - oos_slice.start
    oos_composite = composite[oos_slice, :]
    oos_fwd_ret = fwd_ret_matrix[oos_slice, :]
    daily_ret = np.zeros(oos_n)
    for t in range(oos_n):
        scores = oos_composite[t, :]
        rets = oos_fwd_ret[t, :]
        valid = ~(np.isnan(scores) | np.isnan(rets))
        if np.sum(valid) < 5:
            continue
        scores_v = scores[valid]
        rets_v = rets[valid]
        sorted_idx = np.argsort(scores_v)
        top_n = max(1, int(len(sorted_idx) * TOP_PCT))
        long_ret = np.mean(rets_v[sorted_idx[-top_n:]])
        daily_ret[t] = long_ret  # 纯多头收益
    return daily_ret


# ══════════════════════════════════════════════════════════
# 3. 市场环境表现分析
# ══════════════════════════════════════════════════════════


def analyze_market_regime_performance(
    panel: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    composite: np.ndarray,
    oos_slice: slice,
    fwd_ret_matrix: np.ndarray,
) -> dict:
    """分析策略在不同市场制度下的表现。

    使用沪深300指数（000300）或第一个标的的 OHLCV 数据检测市场制度，
    然后统计策略在各制度下的收益特征。
    """
    # 用第一个标的的行情数据检测市场制度
    first_sym = sorted(panel.keys())[0]
    ohlcv = panel[first_sym]

    selector = RegimeAwareSelector(lookback_days=60)
    oos_n = oos_slice.stop - oos_slice.start
    oos_dates = common_dates[oos_slice]

    # 计算多空收益序列
    daily_ret = composite_backtest(composite, fwd_ret_matrix, oos_slice, sorted(panel.keys()), panel)

    # 对每日检测制度（使用滚动窗口的历史数据）
    regime_labels: list[str] = []
    regime_list: list[dict] = []

    for t in range(oos_n):
        current_date = oos_dates[t]
        # 找到该日期之前的数据
        hist_idx = ohlcv.index.get_loc(current_date) if current_date in ohlcv.index else -1
        if hist_idx < 10:
            continue
        hist_data = ohlcv.iloc[: hist_idx + 1]
        try:
            regime = selector.detect(hist_data)
            regime_labels.append(regime["regime"])
            regime_list.append(
                {
                    "date": str(current_date.date()),
                    "regime": regime["regime"],
                    "confidence": regime["confidence"],
                    "trend_strength": regime["features"].get("trend_strength", 0),
                    "volatility": regime["features"].get("volatility", 0),
                }
            )
        except Exception:
            continue

    # 按制度分组统计
    if not regime_labels:
        return {"regime_performance": {}, "regime_timeline": []}

    df_regime = pd.DataFrame(
        {
            "regime": regime_labels[:oos_n],
            "daily_return": daily_ret[: len(regime_labels)],
        }
    )

    regime_perf = {}
    for regime_name, group in df_regime.groupby("regime"):
        rets = group["daily_return"].values
        if len(rets) < 2:
            continue
        sharpe = _compute_sharpe(rets)
        total_ret = float(np.cumsum(rets)[-1])
        cum = np.cumsum(rets)
        mdd = _compute_max_drawdown(cum)
        win_rate = float(np.mean(rets > 0))
        avg_ret = float(np.mean(rets)) * 10000  # bp
        regime_perf[regime_name] = {
            "n_days": len(rets),
            "pct_of_total": round(len(rets) / oos_n * 100, 1),
            "sharpe": round(sharpe, 2),
            "total_return_pct": round(total_ret * 100, 2),
            "max_drawdown_pct": round(mdd * 100, 2),
            "win_rate": round(win_rate * 100, 2),
            "avg_daily_return_bp": round(avg_ret, 2),
        }

    return {
        "regime_performance": regime_perf,
        "regime_timeline": regime_list,
    }


# ─── 辅助指标 ──────────────────────────────────────────────


def _compute_sharpe(returns: np.ndarray, periods: int = PERIODS_PER_YEAR) -> float:
    if len(returns) < 2:
        return 0.0
    std = np.std(returns, ddof=1)
    return float(np.mean(returns) / std * np.sqrt(periods)) if std > 1e-10 else 0.0


def _compute_max_drawdown(cumulative: np.ndarray) -> float:
    if len(cumulative) < 2:
        return 0.0
    nav = 1.0 + cumulative
    peak = np.maximum.accumulate(nav)
    drawdown = (peak - nav) / np.maximum(peak, 1e-10)
    return float(np.max(drawdown))


# ─── 报告输出 ──────────────────────────────────────────────


def print_sector_report(sector_data: dict) -> None:
    print(f"\n  ┌─ 行业暴露分析{'─' * 38}┐")
    sectors = sector_data.get("sectors", [])
    print(f"  │ {'行业':<12} {'选中率':>8} {'日均收益':>9} {'选中天数':>8} {'总天数':>6} │")
    print(f"  │ {'─' * 12} {'─' * 8} {'─' * 9} {'─' * 8} {'─' * 6} │")
    for s in sectors[:15]:
        print(
            f"  │ {s['sector']:<12} {s['selection_pct']:>6.1f}% {s['avg_daily_return_bp']:>+7.1f}bp "
            f"{s['selected_days']:>6} {s['total_days']:>6} │"
        )
    if len(sectors) > 15:
        print(f"  │ ... 还有 {len(sectors) - 15} 个行业 ...")
    print(f"  └{'─' * 55}┘")


def print_factor_attribution_report(attr_data: dict) -> None:
    print(f"\n  ┌─ 因子归因分析{'─' * 38}┐")
    type_perf = attr_data.get("type_performance", {})
    print(f"  │ {'因子类型':<14} {'因子数':>6} {'权重':>7} {'夏普':>6} {'累计收益':>10} {'最大回撤':>9} {'胜率':>7} │")
    print(f"  │ {'─' * 14} {'─' * 6} {'─' * 7} {'─' * 6} {'─' * 10} {'─' * 9} {'─' * 7} │")
    for ftype, perf in sorted(type_perf.items(), key=lambda x: x[1].get("sharpe", 0), reverse=True):
        print(
            f"  │ {ftype:<14} {perf['n_factors']:>6} {perf['weight']:>6}% "
            f"{perf['sharpe']:>5.1f} {perf['total_return_pct']:>+8.1f}% "
            f"{perf['max_drawdown_pct']:>7.1f}% {perf['win_rate']:>5.1f}% │"
        )
    print(f"  └{'─' * 55}┘")


def print_regime_report(regime_data: dict) -> None:
    print(f"\n  ┌─ 市场环境表现{'─' * 38}┐")
    regime_perf = regime_data.get("regime_performance", {})
    timeline = regime_data.get("regime_timeline", [])

    if not regime_perf:
        print("  │ （无市场制度数据）")
        print(f"  └{'─' * 55}┘")
        return

    print(f"  │ {'制度':<12} {'天数':>6} {'占比':>7} {'夏普':>6} {'累计收益':>10} {'最大回撤':>9} {'胜率':>7} │")
    print(f"  │ {'─' * 12} {'─' * 6} {'─' * 7} {'─' * 6} {'─' * 10} {'─' * 9} {'─' * 7} │")
    for regime_name, perf in sorted(regime_perf.items(), key=lambda x: x[1].get("n_days", 0), reverse=True):
        label = {"bull": "牛市", "bear": "熊市", "oscillate": "震荡", "high_vol": "高波", "low_vol": "低波"}.get(
            regime_name, regime_name
        )
        print(
            f"  │ {label:<12} {perf['n_days']:>6} {perf['pct_of_total']:>6}% "
            f"{perf['sharpe']:>5.1f} {perf['total_return_pct']:>+8.1f}% "
            f"{perf['max_drawdown_pct']:>7.1f}% {perf['win_rate']:>5.1f}% │"
        )
    print(f"  └{'─' * 55}┘")

    # 制度时间线摘要
    if timeline:
        regime_counts = defaultdict(int)
        for r in timeline:
            regime_counts[r["regime"]] += 1
        len(timeline)
        # 简化时间线：取主要的制度切换点
        last_regime = None
        switches = []
        for r in timeline:
            if r["regime"] != last_regime:
                switches.append((r["date"], r["regime"], r.get("trend_strength", 0)))
                last_regime = r["regime"]
        if len(switches) > 1:
            print(f"\n  ┌─ 制度切换时间线{'─' * 37}┐")
            for date, regime, trend in switches[-8:]:
                label = {
                    "bull": "牛市",
                    "bear": "熊市",
                    "oscillate": "震荡",
                    "high_vol": "高波",
                    "low_vol": "低波",
                }.get(regime, regime)
                arrow = "↑" if trend > 0.02 else ("↓" if trend < -0.02 else "→")
                print(f"  │ {date} → {label} {arrow} (trend={trend:+.4f})")
            print(f"  └{'─' * 55}┘")


# ─── 主入口 ────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="L3 组合策略深度分析")
    parser.add_argument("--max-stocks", type=int, default=30, help="最大标的数")
    parser.add_argument("--days", type=int, default=800, help="回溯天数")
    parser.add_argument("--sectors", action="store_true", default=True, help="行业暴露分析")
    parser.add_argument("--attribution", action="store_true", default=True, help="因子归因分析")
    parser.add_argument("--regime", action="store_true", default=True, help="市场环境分析")
    args = parser.parse_args()

    print(f"\n{'=' * 60}")
    print("  组合策略深度分析")
    print(f"{'=' * 60}")

    # ── 加载数据 ──
    print("\n[1/4] 加载组合配置和精英因子...")
    combo = load_portfolio()
    elite_factors = load_elite_factors()
    print(f"  组合: {combo['combo_id']}, 因子: {len(elite_factors)}")

    print("\n[2/4] 获取沪深 300 面板数据...")
    provider = FTSDataProvider()
    panel, common_dates = provider.get_csi300_panel(days=args.days, max_stocks=args.max_stocks)
    symbols = sorted(panel.keys())
    n_dates = len(common_dates)
    n_stocks = len(symbols)
    print(f"  标的: {n_stocks}, 日期: {n_dates}")
    print(f"  范围: {common_dates[0].date()} ~ {common_dates[-1].date()}")

    if n_stocks < 5 or n_dates < 20:
        print("[ERROR] 数据不足")
        return 1

    # ── 计算复合评分 ──
    print("\n[3/4] 编译因子并计算复合评分...")
    signals_def = combo.get("signals", [])
    executors: dict[str, FactorExecutor] = {}
    for sig in signals_def:
        fid = sig["factor_id"]
        fp_data = elite_factors.get(fid)
        if fp_data is None:
            continue
        try:
            exc = FactorExecutor(fp_data)
            exc.compile()
            executors[fid] = exc
        except FactorCompileError:
            continue

    if len(executors) < 3:
        print("[ERROR] 可用因子不足")
        return 1
    print(f"  编译成功: {len(executors)} / {len(signals_def)}")

    # 计算信号矩阵
    signal_matrices: dict[str, np.ndarray] = {}
    for fid, executor in executors.items():
        stock_signals = []
        ok = True
        for sym in symbols:
            df = panel[sym]
            try:
                arr = executor.execute(df, {})
                if not isinstance(arr, np.ndarray) or len(arr) != len(df):
                    raise ValueError
                stock_signals.append(arr)
            except Exception:
                stock_signals.append(np.full(len(df), np.nan))
                ok = False
        if ok:
            matrix = np.zeros((n_dates, n_stocks))
            for j, sym in enumerate(symbols):
                full_sig = stock_signals[j]
                date_idx = panel[sym].index
                date_map = {d: i for i, d in enumerate(date_idx)}
                for t, d in enumerate(common_dates):
                    if d in date_map:
                        matrix[t, j] = full_sig[date_map[d]]
                    else:
                        matrix[t, j] = np.nan
            signal_matrices[fid] = matrix

    # 权重归一化
    weight_map: dict[str, float] = {}
    total_w = 0.0
    for s in signals_def:
        if s.get("retained", True) and s["factor_id"] in signal_matrices:
            weight_map[s["factor_id"]] = s.get("weight", 0)
            total_w += s.get("weight", 0)
    if total_w > 0:
        for k in weight_map:
            weight_map[k] /= total_w

    composite = np.zeros((n_dates, n_stocks))
    for fid, w in weight_map.items():
        mat = signal_matrices[fid].copy()
        for t in range(n_dates):
            row = mat[t, :]
            mu = np.nanmean(row)
            sigma = np.nanstd(row)
            mat[t, :] = (row - mu) / sigma if sigma > 1e-10 else 0.0
        composite += w * mat

    # 计算 forward returns（1日收益）
    fwd_ret_matrix = np.zeros((n_dates, n_stocks))
    for j, sym in enumerate(symbols):
        df = panel[sym]
        closes = df["close"].values
        fwd = np.zeros(len(closes))
        if len(closes) > 1:
            fwd[:-1] = (closes[1:] - closes[:-1]) / np.maximum(closes[:-1], 1e-10)
        date_idx = df.index
        date_map = {d: i for i, d in enumerate(date_idx)}
        for t, d in enumerate(common_dates):
            if d in date_map:
                fwd_ret_matrix[t, j] = fwd[date_map[d]]

    oos_n = max(int(n_dates * OOS_RATIO), 20)
    oos_slice = slice(n_dates - oos_n, n_dates)

    # ── 4. 执行分析 ──
    print(f"\n[4/4] 执行深度分析 (OOS: {oos_n} 天)...")

    results = {}

    if args.sectors:
        print("\n  → 行业暴露分析...")
        sector_data = analyze_sector_exposure(panel, common_dates, composite, oos_slice, fwd_ret_matrix)
        results["sector"] = sector_data
        print_sector_report(sector_data)

    if args.attribution:
        print("\n  → 因子归因分析...")
        attr_data = analyze_factor_attribution(
            combo,
            elite_factors,
            panel,
            common_dates,
            symbols,
            composite,
            oos_slice,
            fwd_ret_matrix,
        )
        results["attribution"] = attr_data
        print_factor_attribution_report(attr_data)

    if args.regime:
        print("\n  → 市场环境分析...")
        regime_data = analyze_market_regime_performance(
            panel,
            common_dates,
            composite,
            oos_slice,
            fwd_ret_matrix,
        )
        results["regime"] = regime_data
        print_regime_report(regime_data)

    # 保存结果
    out_path = PROJECT_ROOT / "memory" / "portfolio" / "analysis_result.json"
    # 只保存摘要，不保存原始数据
    report = {}
    for k, v in results.items():
        if isinstance(v, dict):
            report[k] = {sk: sv for sk, sv in v.items() if sk != "regime_timeline"}
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n分析结果已保存到: {out_path}")

    print(f"\n{'=' * 60}")
    print("  分析完成")
    print(f"{'=' * 60}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
