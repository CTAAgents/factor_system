"""
scripts/futures_signal_pipeline.py — 期货每日信号生成管道

从 FTS 期货 Elite 因子库，生成期货横截面交易信号。

用法:
    python scripts/futures_signal_pipeline.py [--max-symbols 25] [--days 120]

输出:
    - 控制台: 信号排名表
    - 文件:     reports/{date}/futures_signals_{date}.md

方向校正方法（v2）:
    期货是多空双向，因子在期货上的 IC 方向可能为负。
    校正方法：计算每个因子最近 N 天的**每日截面 IC**（因子信号与
    未来 5 日收益的 Spearman 秩相关性），如果平均 IC < 0 则反转信号。
    这比 v1 的时序相关性方法更符合横截面因子投资逻辑。

排名方法（v5 — 多空双向 + 信号增量）:
    期货支持多空双向交易，排名按信号强度（绝对值）排序，
    输出分多头信号 (做多) 和空头信号 (做空) 两部分。
    新增信号增量追踪（较昨日变化），用于判断趋势加速/衰竭。

因子加权方法（v3 — Ridge 回归）:
    基于 Shen & Xiu 的弱信号理论：当因子信号普遍较弱时，
    L2 正则化（Ridge）优于 L1 选择（Lasso/硬阈值）。
    使用全部精英因子（不按 IC 过滤），以 Ridge 回归学习差异化权重：
    强因子自动获得高权重，弱因子获得接近零的权重但不被丢弃。
    这替代了 v2 的 IC>0.3 硬过滤 + 等权合成。
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 抑制 numpy/scipy 运行时警告
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/futures_elite"
REPORTS_ROOT = PROJECT_ROOT / "reports"


def _yesterday_str() -> str:
    """返回昨日日期字符串 (YYYY-MM-DD)。"""
    from datetime import timedelta
    return (date.today() - timedelta(days=1)).isoformat()


def _build_composite_ohlcv(
    panel: dict[str, "pd.DataFrame"],
    common_dates: list[str],
) -> "pd.DataFrame":
    """从品种面板构建市场综合 OHLCV（用于 Regime 检测）。

    方法：取所有品种 close 的截面均值作为市场综合价格序列，
    构建合成 OHLCV（open/high/low 用 close 近似，volume 取截面和）。

    Args:
        panel: 品种行情面板 (symbol → DataFrame)
        common_dates: 共同交易日列表

    Returns:
        pd.DataFrame with columns open/high/low/close/volume, DatetimeIndex
    """
    # 收集所有品种的 close 序列，对齐到 common_dates
    close_matrix: dict[str, pd.Series] = {}
    for sym, df in panel.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        close_s = df["close"].reindex(common_dates)
        close_matrix[sym] = close_s

    if not close_matrix:
        return pd.DataFrame()

    # 截面均值作为市场综合 close
    close_df = pd.DataFrame(close_matrix)
    composite_close = close_df.mean(axis=1).dropna()

    if len(composite_close) < 20:
        return pd.DataFrame()

    # 合成 OHLCV（open/high/low 用 close 近似，volume 取截面和）
    volume_df = pd.DataFrame({
        sym: df["volume"].reindex(common_dates)
        for sym, df in panel.items()
        if "volume" in df.columns
    })
    composite_volume = volume_df.sum(axis=1).reindex(composite_close.index).fillna(0)

    ohlcv = pd.DataFrame({
        "open": composite_close.shift(1).fillna(composite_close),
        "high": composite_close,
        "low": composite_close,
        "close": composite_close,
        "volume": composite_volume,
    }, index=composite_close.index)

    return ohlcv


def load_futures_elite_factors(ic_threshold: float = 0.3) -> list[dict[str, Any]]:
    """加载期货顶级 Elite 因子（IC>{threshold}）。"""
    factors: list[dict[str, Any]] = []
    for fp in sorted(ELITE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            ev = data.get("evaluation", {})
            bt = ev.get("level_1_backtest", {})
            ic = bt.get("ic", 0)
            if abs(ic) < ic_threshold:
                continue
            factors.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return factors


def _compute_signal_matrix(
    panel: dict[str, "pd.DataFrame"],
    factors: list[dict[str, Any]],
) -> dict[str, dict[str, np.ndarray]]:
    """一次性计算所有因子 × 所有品种的信号矩阵。

    Returns:
        signal_matrix[symbol][factor_name] = np.ndarray (信号值时间序列)
    """
    from fts.factor_engine.factor_program import FactorExecutor

    signal_matrix: dict[str, dict[str, np.ndarray]] = {}
    n_errors = 0

    # 抑制因子编译/执行时的运行时警告（除零等，已通过 NaN 处理防御）
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    for sym, df in panel.items():
        if df.empty or len(df) < 20:
            continue
        sym_signals: dict[str, np.ndarray] = {}
        for factor_data in factors:
            name = factor_data.get("name", "?")
            try:
                executor = FactorExecutor(factor_data)
                sig = executor.execute(df, factor_data.get("params", {}))
                arr = np.array(sig, dtype=float)
                # 只保留有限数值
                arr = np.where(np.isfinite(arr), arr, np.nan)
                sym_signals[name] = arr
            except Exception:
                n_errors += 1
                continue
        if sym_signals:
            signal_matrix[sym] = sym_signals

    if n_errors > 0:
        print(f"      [警告] 信号计算错误: {n_errors} 次")
    return signal_matrix


def _compute_factor_sign_flips(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    panel: dict[str, "pd.DataFrame"],
    common_dates: list[str],
    ic_lookback: int = 20,
) -> dict[str, float]:
    """用截面 IC 法计算每个因子是否需要反转信号。

    方法：
        对每个因子，遍历最近 ic_lookback 个交易日，收集该日所有品种的
        因子信号值与未来 5 日收益率，计算 Spearman 秩相关性（截面 IC），
        取平均。如果平均 IC < 0，反转因子信号（flip = -1.0）。

    Args:
        signal_matrix: 信号矩阵 (symbol → factor_name → array)
        panel: 品种行情面板 (symbol → DataFrame)
        common_dates: 共同交易日列表（字符串格式）
        ic_lookback: 使用最近多少天的数据计算截面 IC

    Returns:
        dict[factor_name, sign_flip]  # +1=正常, -1=需反转
    """
    from scipy.stats import spearmanr

    # 获取所有因子名称
    first_sym = next(iter(signal_matrix))
    factor_names = list(signal_matrix[first_sym].keys())

    n_dates = len(common_dates)
    # 多留 5 天给未来收益计算
    start_idx = max(0, n_dates - ic_lookback - 5)

    factor_sign_flips: dict[str, float] = {}
    for fname in factor_names:
        daily_ics: list[float] = []
        for t in range(start_idx, n_dates - 5):
            # 收集该日所有品种的信号值和未来 5 日收益（按日期定位，防错位）
            signals_t: dict[str, float] = {}
            future_rets: dict[str, float] = {}
            t_date = common_dates[t]
            for sym in signal_matrix:
                sig = signal_matrix[sym].get(fname)
                df = panel.get(sym)
                if df is None or df.empty:
                    continue
                # 按日期定位品种内位置（品种日期集可能与 common_dates 不完全对齐）
                try:
                    pos = df.index.get_loc(t_date)
                except (KeyError, TypeError):
                    continue
                if pos >= len(sig) or not np.isfinite(sig[pos]):
                    continue
                signals_t[sym] = float(sig[pos])

                closes = df["close"].values
                if pos + 5 >= len(closes):
                    continue
                p_t = closes[pos]
                if not np.isfinite(p_t) or p_t <= 1e-10:
                    continue
                ret = (closes[pos + 5] - p_t) / p_t
                if np.isfinite(ret):
                    future_rets[sym] = ret

            # 计算截面 Spearman 相关性（抑制常量输入警告）
            common = set(signals_t.keys()) & set(future_rets.keys())
            if len(common) >= 5:
                s_vals = [signals_t[s] for s in common]
                r_vals = [future_rets[s] for s in common]
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=RuntimeWarning)
                    r, _ = spearmanr(s_vals, r_vals)
                if not np.isnan(r):
                    daily_ics.append(r)

        if daily_ics:
            avg_ic = np.mean(daily_ics)
            factor_sign_flips[fname] = -1.0 if avg_ic < 0 else 1.0
        else:
            factor_sign_flips[fname] = 1.0

    return factor_sign_flips


def _compute_ridge_weights(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    panel: dict[str, "pd.DataFrame"],
    common_dates: list[str],
    factor_sign_flips: dict[str, float],
    lookback: int = 60,
    alpha: float | None = None,
) -> dict[str, float]:
    """用 Ridge 回归学习因子权重（替代等权合成）。

    以方向校正后的因子信号值为特征、未来 5 日收益为目标，
    Ridge 回归拟合系数取绝对值作为因子权重。
    强因子自动获得高权重，弱因子获得接近零的权重但不被丢弃。

    Args:
        signal_matrix: 信号矩阵 (symbol → factor_name → array)
        panel: 品种行情面板
        common_dates: 共同交易日列表
        factor_sign_flips: 方向校正（+1=正常, -1=需反转）
        lookback: 训练窗口天数
        alpha: Ridge 正则化强度，None 则用 RidgeCV 自动选择

    Returns:
        dict[factor_name, weight]  权重（已归一化，和为 1）
    """
    try:
        from sklearn.linear_model import RidgeCV
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("      [Ridge] sklearn 不可用，回退到等权")
        first_sym = next(iter(signal_matrix))
        factor_names = list(signal_matrix[first_sym].keys())
        n = len(factor_names)
        return {f: 1.0 / n for f in factor_names}

    first_sym = next(iter(signal_matrix))
    factor_names = list(signal_matrix[first_sym].keys())
    n_factors = len(factor_names)

    if n_factors <= 1:
        return {f: 1.0 for f in factor_names}

    # 取所有品种共有的因子交集（不同品种可能因子执行成功集合不同）
    common_factors = set(factor_names)
    for sym in signal_matrix:
        common_factors &= set(signal_matrix[sym].keys())
    factor_names = sorted(common_factors)
    n_factors = len(factor_names)
    if n_factors <= 1:
        fallback = {f: 1.0 / n_factors for f in factor_names} if n_factors > 0 else {}
        return fallback

    # 过滤训练窗口内 NaN 率过高的因子（弱因子在早期位置常全为 NaN）
    n_dates = len(common_dates)
    train_start = max(0, n_dates - lookback - 5)
    train_end = n_dates - 5
    valid_factor_names: list[str] = []
    for fname in factor_names:
        nan_count = 0
        total = 0
        for sym in signal_matrix:
            arr = signal_matrix[sym].get(fname)
            if arr is None:
                continue
            df = panel.get(sym)
            if df is None or df.empty:
                continue
            # 抽样检查训练窗口首尾两个位置
            for t in (train_start, train_end - 1):
                if t >= train_end:
                    continue
                try:
                    pos = df.index.get_loc(common_dates[t])
                except (KeyError, TypeError):
                    continue
                if pos < len(arr):
                    total += 1
                    if not np.isfinite(arr[pos]):
                        nan_count += 1
        if total > 0 and nan_count / total < 0.5:
            valid_factor_names.append(fname)
        else:
            pass  # 因子被排除（NaN 率过高）

    if valid_factor_names:
        dropped = set(factor_names) - set(valid_factor_names)
        if dropped:
            print(f"      [Ridge] 排除 {len(dropped)} 个高 NaN 因子: "
                  f"{', '.join(sorted(dropped))}")
        factor_names = valid_factor_names
    n_factors = len(factor_names)
    if n_factors <= 1:
        fallback = {f: 1.0 / n_factors for f in factor_names} if n_factors > 0 else {}
        return fallback

    # 构建训练数据：每个交易日 × 每个品种 = 一个样本
    X_list: list[list[float]] = []
    y_list: list[float] = []

    n_dates = len(common_dates)
    start_idx = max(0, n_dates - lookback - 5)

    for t in range(start_idx, n_dates - 5):
        for sym in signal_matrix:
            sig = signal_matrix[sym]
            df = panel.get(sym)
            if df is None or df.empty:
                continue
            try:
                pos = df.index.get_loc(common_dates[t])
            except (KeyError, TypeError):
                continue

            # 特征：方向校正后的因子信号值
            features: list[float] = []
            valid = True
            for fname in factor_names:
                arr = sig.get(fname)
                if arr is None or pos >= len(arr) or not np.isfinite(arr[pos]):
                    valid = False
                    break
                flip = factor_sign_flips.get(fname, 1.0)
                features.append(float(arr[pos]) * flip)
            if not valid:
                continue

            # 目标：未来 5 日收益
            closes = df["close"].values
            if pos + 5 >= len(closes):
                continue
            p_t = closes[pos]
            if not np.isfinite(p_t) or p_t <= 1e-10:
                continue
            ret = (closes[pos + 5] - p_t) / p_t
            if not np.isfinite(ret):
                continue

            X_list.append(features)
            y_list.append(ret)

    min_samples = max(n_factors * 3, 30)
    if len(X_list) < min_samples:
        print(f"      [Ridge] 训练样本不足 ({len(X_list)} < {min_samples})，"
              f"回退到等权")
        return {f: 1.0 / n_factors for f in factor_names}

    X = np.array(X_list)
    y = np.array(y_list)

    # 标准化特征
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Ridge 回归（自动选择 alpha）
    if alpha is not None:
        from sklearn.linear_model import Ridge
        ridge = Ridge(alpha=alpha, fit_intercept=True)
    else:
        ridge = RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0], fit_intercept=True)
    ridge.fit(X_scaled, y)

    # 系数取绝对值 → 权重（方向校正已处理符号，权重只反映预测强度）
    coefs = np.abs(ridge.coef_)
    total = coefs.sum()
    if total < 1e-10:
        return {f: 1.0 / n_factors for f in factor_names}

    weights = {fname: float(coef) / float(total)
               for fname, coef in zip(factor_names, coefs)}

    # 输出权重分布
    w_sorted = sorted(weights.items(), key=lambda x: -x[1])
    top3_str = ", ".join(f"{n}({w:.3f})" for n, w in w_sorted[:3])
    bottom3_str = ", ".join(f"{n}({w:.3f})" for n, w in w_sorted[-3:])
    alpha_used = ridge.alpha_ if hasattr(ridge, 'alpha_') else alpha
    print(f"      Ridge α={alpha_used:.2f} | 权重 Top3: {top3_str} | "
          f"Bottom3: {bottom3_str}")

    return weights


def _compute_composite_scores(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    factor_sign_flips: dict[str, float],
    factors: list[dict[str, Any]],
    factor_weights: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """合成因子信号（带方向校正 + 可选 Ridge 权重）。

    Args:
        factor_weights: 因子权重字典（Ridge 学习结果），None 则等权。

    Returns:
        sym_scores: 品种 → 综合得分
        sym_details: 品种 → {因子名 → 信号值}
    """
    n_factors = len(factors)
    default_weight = 1.0 / n_factors

    sym_scores: dict[str, float] = {}
    sym_details: dict[str, dict[str, float]] = {}

    for sym, sym_signals in signal_matrix.items():
        signal_sum = 0.0
        weight_sum = 0.0
        details: dict[str, float] = {}

        for factor_data in factors:
            name = factor_data.get("name", "?")
            sig = sym_signals.get(name)
            if sig is None or len(sig) == 0:
                continue
            val = float(sig[-1]) if np.isfinite(sig[-1]) else 0.0
            # 方向校正
            flip = factor_sign_flips.get(name, 1.0)
            val *= flip
            w = factor_weights.get(name, default_weight) if factor_weights else default_weight
            signal_sum += val * w
            weight_sum += w
            details[name] = val

        if weight_sum > 0:
            composite = signal_sum / weight_sum
            sym_scores[sym] = composite
            sym_details[sym] = details

    return sym_scores, sym_details


def main(max_symbols: int = 25, days: int = 120, universe: str = "core") -> int:
    t0 = time.time()
    today = date.today().isoformat()
    print("=" * 60)
    print(f"  期货信号生成管道 v5 (多空双向 + 信号增量) — {today}")
    print("=" * 60)

    # ── Step 1: 加载全部期货 Elite 因子（Ridge 加权模式下不过滤 IC）──
    factors = load_futures_elite_factors(ic_threshold=0)
    print(f"\n[1/5] 加载全部期货 Elite 因子: {len(factors)} 个")

    if not factors:
        print("[ERROR] 无期货 Elite 因子，退出")
        return 1

    # ── Step 2: 获取期货数据 ──
    from fts.data import FTSDataProvider
    from fts.data_futures import FUTURES_CORE_SUBSET, FUTURES_SUBSET

    provider = FTSDataProvider()

    if universe == "all":
        # 全量商品期货：FUTURES_SUBSET 剔除中金所金融期货
        FINANCIAL = {"IF0", "TF0", "IH0", "IC0", "TS0", "IM0"}
        symbols = [s for s in FUTURES_SUBSET if s not in FINANCIAL][:max_symbols]
        print(f"[2/4] 获取期货数据: 全量商品 {len(symbols)} 个品种, days={days}")
    else:
        symbols = FUTURES_CORE_SUBSET[:max_symbols]
        print(f"[2/4] 获取期货数据: {len(symbols)} 个品种, days={days}")

    panel, common_dates = provider.get_futures_panel(
        symbols=symbols, days=days,
    )
    print(f"      面板: {len(panel)} 个品种, {len(common_dates)} 个交易日")

    if not panel:
        print("[ERROR] 无数据，退出")
        return 1

    # 过滤数据陈旧的品种（最新交易日不在共同日期末端，如已停更品种）
    if len(common_dates) > 0:
        last_common = common_dates[-1]
        stale = [
            sym for sym, df in panel.items()
            if df.index[-1] < last_common
        ]
        for sym in stale:
            panel.pop(sym)
        if stale:
            print(f"      [提示] 剔除 {len(stale)} 个停更/陈旧品种: "
                  f"{', '.join(stale)} (数据止于共同交易日之前)")

    # ── Step 2b: Market Regime 检测 ──
    from fts.factor_engine.regime import RegimeAwareSelector

    regime_selector = RegimeAwareSelector(lookback_days=60)
    composite_ohlcv = _build_composite_ohlcv(panel, common_dates)
    if not composite_ohlcv.empty:
        market_regime = regime_selector.detect(composite_ohlcv)
    else:
        market_regime = {"regime": "unknown", "confidence": 0.0,
                         "detected_at": datetime.now().isoformat(), "features": {}}

    _REGIME_LABELS = {
        "bull": "趋势上涨 (bull)",
        "bear": "趋势下跌 (bear)",
        "high_vol": "高波动 (high_vol)",
        "low_vol": "低波动 (low_vol)",
        "oscillate": "震荡 (oscillate)",
        "unknown": "未知",
    }
    regime_label = _REGIME_LABELS.get(market_regime["regime"], market_regime["regime"])
    features = market_regime.get("features", {})
    print(f"\n[Regime] 当前市场制度: {regime_label}")
    print(f"         置信度: {market_regime['confidence']:.2%}")
    if features:
        print(f"         特征: trend={features.get('trend_strength', '?'):.4f} "
              f"vol={features.get('volatility', '?'):.4f} "
              f"vol_ratio={features.get('volume_ratio', '?'):.2f} "
              f"breadth={features.get('breadth', '?'):.4f}")

    # ── Step 3: 计算信号 ──
    n_factors = len(factors)
    print(f"\n[3/5] 计算信号 ({n_factors} 因子 × {len(panel)} 品种)...")

    # 3a: 一次性计算所有因子×品种的信号矩阵
    signal_matrix = _compute_signal_matrix(panel, factors)
    print(f"      信号矩阵: {sum(len(v) for v in signal_matrix.values())} 项")

    # 3b: 方向校正（截面 IC 法）
    print("      方向校正: 截面 IC 法（因子信号 vs 未来 5 日收益的 Spearman 秩相关）...")
    factor_sign_flips = _compute_factor_sign_flips(signal_matrix, panel, common_dates)

    n_flipped = sum(1 for v in factor_sign_flips.values() if v < 0)
    if n_flipped > 0:
        print(f"      方向反转: {n_flipped}/{n_factors} 个因子 (截面 IC<0)")

    # 3c: Ridge 回归学习因子权重（替代等权合成）
    print("      权重学习: Ridge 回归（L2 正则化，弱因子保留不丢弃）...")
    factor_weights = _compute_ridge_weights(
        signal_matrix, panel, common_dates, factor_sign_flips,
    )

    # 3d: 加权合成（方向校正 + Ridge 权重）
    sym_scores, sym_details = _compute_composite_scores(
        signal_matrix, factor_sign_flips, factors, factor_weights,
    )

    elapsed = time.time() - t0
    print(f"\n  耗时: {elapsed:.1f}s, 成功: {len(sym_scores)} 个品种")

    # ── Step 4: 保存信号快照 + 加载昨日信号计算增量 ──
    report_dir = REPORTS_ROOT / today
    report_dir.mkdir(parents=True, exist_ok=True)

    # 保存今日信号快照 (JSON)
    snapshot_path = report_dir / "signal_scores.json"
    snapshot_path.write_text(
        json.dumps({"date": today, "scores": sym_scores}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 追加到历史 JSONL
    history_path = REPORTS_ROOT / "signal_scores_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "a", encoding="utf-8") as hf:
        hf.write(json.dumps({"date": today, "scores": sym_scores}, ensure_ascii=False) + "\n")

    # 加载昨日信号，计算增量
    try:
        yesterday_snapshot = report_dir.parent / _yesterday_str() / "signal_scores.json"
        if yesterday_snapshot.exists():
            prev_data = json.loads(yesterday_snapshot.read_text(encoding="utf-8"))
            prev_scores: dict[str, float] = prev_data.get("scores", {})
            # 计算每个品种的信号增量
            sym_deltas: dict[str, float] = {}
            for sym, score in sym_scores.items():
                prev = prev_scores.get(sym)
                if prev is not None:
                    sym_deltas[sym] = score - prev
            has_delta = len(sym_deltas) > 0
        else:
            prev_scores = {}
            sym_deltas = {}
            has_delta = False
    except Exception:
        prev_scores = {}
        sym_deltas = {}
        has_delta = False

    # ── Step 5: 输出信号排名 ──
    if not sym_scores:
        print("[ERROR] 无有效信号")
        return 1

    # 多空双向排名：按信号强度（绝对值）排序
    ranked = sorted(sym_scores.items(), key=lambda x: -abs(x[1]))
    long_signals = [(s, sc) for s, sc in ranked if sc > 0]
    short_signals = [(s, sc) for s, sc in ranked if sc < 0]

    # 4a: 品种元数据（名称 / 主力合约 / 盘中实时价）
    from fts.data_futures import (
        FUTURES_SYMBOL_NAMES,
        get_dominant_contracts,
        get_realtime_prices,
    )
    sym_list = [s for s, _ in ranked]
    dominant = get_dominant_contracts(sym_list)
    print("      获取盘中实时价（AKShare 分时）...")
    rt_prices = get_realtime_prices(sym_list)
    rt_hit = len(rt_prices)
    print(f"      实时价: {rt_hit}/{len(sym_list)} 个品种可用")

    def _name(sym: str) -> str:
        return FUTURES_SYMBOL_NAMES.get(sym, sym)

    def _contract(sym: str) -> str:
        return dominant.get(sym, "")

    def _price(sym: str, df) -> float:
        # 优先盘中实时价，缺失则用面板最新收盘价
        if sym in rt_prices:
            return rt_prices[sym]
        return df.iloc[-1]["close"] if df is not None and not df.empty else 0.0

    # 控制台输出 — 多空双向
    header = f"{'排名':>4s} {'品种':>6s} {'名称':>8s} {'主力合约':>9s} {'得分':>10s} {'实时价':>10s} {'Top因子':>28s}"
    sep = f"{'-'*4} {'-'*6} {'-'*8} {'-'*9} {'-'*10} {'-'*10} {'-'*28}"

    def _print_signal_rows(signals, label, show_n=20):
        if not signals:
            print(f"\n  [{label}] 无信号")
            return
        print(f"\n{'=' * 76}")
        print(f"  {label} (按信号强度排序)")
        print(f"{'=' * 76}")
        print(header)
        print(sep)
        for i, (sym, score) in enumerate(signals[:show_n], 1):
            df = panel.get(sym)
            price = _price(sym, df)
            details = sym_details.get(sym, {})
            top_factors = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
            top_str = ", ".join(f"{n}({v:+.3f})" for n, v in top_factors)
            print(f"{i:>4d} {sym:>6s} {_name(sym):>8s} {_contract(sym):>9s} "
                  f"{score:>+10.4f} {price:>10.2f} {top_str:<28s}")

    _print_signal_rows(long_signals, "多头信号 (做多)")
    _print_signal_rows(short_signals, "空头信号 (做空)")

    # 信号增量控制台输出
    if has_delta:
        delta_ranked = sorted(sym_deltas.items(), key=lambda x: x[1])
        accel = [(s, d) for s, d in delta_ranked if d < 0][:5]
        decel = [(s, d) for s, d in delta_ranked if d > 0][:5]
        print(f"\n  [信号增量] 空头加速 Top5: {', '.join(f'{s}({d:+.3f})' for s, d in accel)}")
        print(f"  [信号增量] 减速/反转 Top5: {', '.join(f'{s}({d:+.3f})' for s, d in decel)}")

    # ── Step 6: 写入 Markdown 报告 ──
    suffix = "_all_commodities" if universe == "all" else ""
    out_path = report_dir / f"futures_signals{suffix}_{today}.md"
    lines: list[str] = []
    def w(s=""):
        lines.append(s)

    w(f"# 期货信号报告 — {today}")
    w()
    w(f"生成时间: {today} | 耗时: {elapsed:.1f}s")
    w(f"因子池: {len(factors)} 个（全部精英因子） | 覆盖品种: {len(sym_scores)} 个")
    w(f"合成方法: Ridge 回归加权（L2 正则化） | 方向校正: 截面 IC 法")
    flips_info = f" | 方向反转: {n_flipped} 个因子 (截面 IC<0)"
    w(f"方向校正: 截面 IC 法（因子信号 vs 未来 5 日收益的 Spearman 秩相关）{flips_info}")
    w(f"最新价: 盘中实时价（AKShare 分时）优先，缺失用日线收盘 | 实时价覆盖 {rt_hit}/{len(sym_list)} 个品种")
    w()
    w()
    w("## 市场制度 (Market Regime)")
    w()
    w(f"- **当前制度**: {regime_label}")
    w(f"- **置信度**: {market_regime['confidence']:.2%}")
    if features:
        w(f"- **趋势强度**: {features.get('trend_strength', 0):.4f} (MA20 斜率)")
        w(f"- **波动率**: {features.get('volatility', 0):.4f} (ATR/价格)")
        w(f"- **量比**: {features.get('volume_ratio', 1.0):.2f} (当前量/20日均量)")
        w(f"- **市场广度**: {features.get('breadth', 0):.4f} (收益自相关)")
    w()

    # ── Regime 调整后的交易建议 ──
    regime_type = market_regime["regime"]
    if regime_type in ("bull", "bear"):
        w("> **Regime 解读 (趋势友好)**")
        w("> 市场处于明确趋势中，优先做空/做多增量最强的品种，可适当放大仓位。")
        w("> 趋势延续概率高，逆势交易风险大。")
    elif regime_type == "oscillate":
        w("> **Regime 解读 (均值回归)**")
        w("> 市场处于震荡状态，反向操作更优：做空减速品种（即将反转），做多加速品种。")
        w("> 趋势持续性弱，应以区间交易为主。")
    elif regime_type in ("high_vol",):
        w("> **Regime 解读 (高波动/混沌)**")
        w("> 市场波动率异常偏高，缩小仓位，只做增量绝对值 > 0.15 的品种。")
        w("> 高波动环境下信号噪音大，需严格止损。")
    elif regime_type == "low_vol":
        w("> **Regime 解读 (低波动)**")
        w("> 市场波动率偏低，信号可信度较高，可正常仓位操作。")
        w("> 关注波动率突破信号，低波环境可能孕育趋势行情。")
    w()
    w("## 多头信号 (做多) — Top 20")
    w()
    w("| 排名 | 品种 | 名称 | 主力合约 | 方向 | 信号强度 | 最新价 | Top 3 因子贡献 |")
    w("|------|------|------|----------|------|----------|--------|----------------|")
    for i, (sym, score) in enumerate(long_signals[:20], 1):
        df = panel.get(sym)
        price = _price(sym, df)
        details = sym_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = " ".join(f"{n}({v:+.3f})" for n, v in top3)
        w(f"| {i} | {sym} | {_name(sym)} | {_contract(sym)} | 多 | {abs(score):.4f} | {price:.2f} | {top_str} |")
    if not long_signals:
        w("| — | — | 无多头信号 | — | — | — | — | — |")
    w()

    w("## 空头信号 (做空) — Top 20")
    w()
    w("| 排名 | 品种 | 名称 | 主力合约 | 方向 | 信号强度 | 最新价 | Top 3 因子贡献 |")
    w("|------|------|------|----------|------|----------|--------|----------------|")
    for i, (sym, score) in enumerate(short_signals[:20], 1):
        df = panel.get(sym)
        price = _price(sym, df)
        details = sym_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = " ".join(f"{n}({v:+.3f})" for n, v in top3)
        w(f"| {i} | {sym} | {_name(sym)} | {_contract(sym)} | 空 | {abs(score):.4f} | {price:.2f} | {top_str} |")
    if not short_signals:
        w("| — | — | 无空头信号 | — | — | — | — | — |")
    w()

    # 信号分布
    scores = [s for _, s in ranked]
    abs_scores = [abs(s) for s in scores]
    w("## 信号分布")
    w()
    w(f"- 多头信号: {len(long_signals)} 个  |  空头信号: {len(short_signals)} 个")
    w(f"- 信号强度均值: {np.mean(abs_scores):.4f}")
    w(f"- 信号强度中位数: {np.median(abs_scores):.4f}")
    w(f"- 信号强度标准差: {np.std(abs_scores):.4f}")
    w(f"- 最强信号: {max(abs_scores):.4f}")
    w(f"- 最弱信号: {min(abs_scores):.4f}")
    w(f"- 综合得分范围: [{min(scores):+.4f}, {max(scores):+.4f}]")
    w()

    # 信号变化（增量）— 用于判断趋势加速/衰竭
    if has_delta:
        delta_ranked = sorted(sym_deltas.items(), key=lambda x: -x[1])
        accelerating = [(s, d) for s, d in delta_ranked if d < 0][:10]  # 空头加速
        decelerating = [(s, d) for s, d in delta_ranked if d > 0][:10]  # 空头减速/多头萌芽
        w("## 信号变化 (较昨日增量)")
        w()
        w("> 增量 = 今日得分 - 昨日得分。负增量 = 空头信号加强（加速下跌），")
        w("> 正增量 = 空头信号减弱或向多头方向移动（减速/反转萌芽）。")
        w("> **交易含义**：做空选加速品种（增量最负），做多关注减速品种（增量最正），")
        w("> 避免追已到极值但增量停滞的品种（趋势衰竭）。")
        w()
        w("### 空头加速 Top 10（做空优先关注）")
        w()
        w("| 品种 | 名称 | 今日得分 | 昨日得分 | 增量 | 方向 |")
        w("|------|------|----------|----------|------|------|")
        for sym, delta in accelerating:
            today_score = sym_scores.get(sym, 0)
            prev_score = prev_scores.get(sym, 0)
            direction = "加速下跌" if delta < 0 else "减速"
            w(f"| {sym} | {_name(sym)} | {today_score:+.4f} | {prev_score:+.4f} | {delta:+.4f} | {direction} |")
        w()
        w("### 空头减速/反转萌芽 Top 10（做多关注）")
        w()
        w("| 品种 | 名称 | 今日得分 | 昨日得分 | 增量 | 方向 |")
        w("|------|------|----------|----------|------|------|")
        for sym, delta in decelerating:
            today_score = sym_scores.get(sym, 0)
            prev_score = prev_scores.get(sym, 0)
            direction = "反转萌芽" if today_score > 0 else "空头减弱"
            w(f"| {sym} | {_name(sym)} | {today_score:+.4f} | {prev_score:+.4f} | {delta:+.4f} | {direction} |")
        w()

    # 因子贡献排名
    w("## 因子贡献排名（当前市场最有效的因子）")
    w()
    w("> 注：方向校正基于截面 IC。因子信号值已根据截面 IC 方向校正，")
    w("> IC<0 的因子信号已反转，使信号方向与未来收益方向一致。")
    w()
    factor_contribs: dict[str, list[float]] = {}
    for sym, details in sym_details.items():
        for name, val in details.items():
            if name not in factor_contribs:
                factor_contribs[name] = []
            factor_contribs[name].append(val)
    factor_avg = {n: np.mean(v) for n, v in factor_contribs.items()}
    factor_ranked = sorted(factor_avg.items(), key=lambda x: -abs(x[1]))[:20]
    w("| 排名 | 因子名称 | 平均信号值 | 标准差 |")
    w("|------|----------|------------|--------|")
    for i, (name, avg) in enumerate(factor_ranked, 1):
        std = np.std(factor_contribs[name])
        w(f"| {i} | {name} | {avg:+.4f} | {std:.4f} |")
    w()

    # 全部品种信号排名（按信号强度，含多空方向）
    w("## 全部品种信号排名")
    w()
    w("| 排名 | 品种 | 名称 | 主力合约 | 方向 | 信号强度 | 最新价 |")
    w("|------|------|------|----------|------|----------|--------|")
    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = _price(sym, df)
        direction = "多" if score > 0 else "空"
        w(f"| {i} | {sym} | {_name(sym)} | {_contract(sym)} | {direction} | {abs(score):.4f} | {price:.2f} |")
    w()

    report_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] 报告已保存: {out_path}")

    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="期货信号生成管道")
    parser.add_argument("--max-symbols", type=int, default=25, help="最大品种数")
    parser.add_argument("--days", type=int, default=120, help="回溯天数")
    parser.add_argument(
        "--universe", type=str, default="core",
        choices=["core", "all"],
        help="品种池: core=25 核心品种 / all=全量商品期货（FUTURES_SUBSET 剔除金融期货）",
    )
    args = parser.parse_args()
    sys.exit(main(
        max_symbols=args.max_symbols, days=args.days, universe=args.universe,
    ))