"""制度有效性样本外验证 CLI（28 计划 T9）。

用法:
    python scripts/validate_regime.py [--data <csv|duckdb>] [--table <表名>] [--json] \
        [--window N] [--step N] [--horizon N] [--use-hmm]

流程:
    1. 加载 OHLCV（--data 缺省时使用合成数据演示）；
    2. 用 RegimeAwareSelector 对历史窗口滚动检测生成制度序列（按 t 对齐前向收益/波动）；
    3. 调用 validate_regime_predictive_power 输出制度有效性验证报告。

版本: v0.1.0
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 动态解析项目根（禁止硬编码绝对路径）
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.regime import RegimeAwareSelector
from fts.factor_engine.regime_validation import validate_regime_predictive_power


def _build_synthetic_ohlcv(n: int = 600, seed: int = 42) -> pd.DataFrame:
    """构造双制度合成 OHLCV：前段上涨（bull）→ 后段下跌（bear）。

    用于 --data 缺省时的演示数据；漂移项在中间点切换符号，
    便于规则检测器识别出 bull/bear 两类制度。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    half = n // 2
    drift = np.concatenate([np.full(half, 0.002), np.full(n - half, -0.002)])
    close = 100.0 * np.cumprod(1.0 + drift + rng.normal(0.0, 0.01, n))
    open_ = close * (1.0 + rng.normal(0.0, 0.002, n))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.002, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.002, n)))
    volume = rng.integers(1000, 10000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _load_ohlcv(data: str | None, table: str | None) -> pd.DataFrame:
    """加载 OHLCV 数据（csv / duckdb 表 / 合成数据兜底）。"""
    if data is None:
        return _build_synthetic_ohlcv()
    path = Path(data)
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
        for col in ("date", "datetime"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
                df = df.set_index(col)
                break
    else:
        import duckdb

        if table is None:
            raise SystemExit(f"--data 为 duckdb 文件时需提供 --table（文件: {path}）")
        con = duckdb.connect(str(path))
        try:
            df = con.execute(f'SELECT * FROM "{table}"').df()
        finally:
            con.close()
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"数据缺少列: {sorted(missing)}（现有: {list(df.columns)}）")
    return df[["open", "high", "low", "close", "volume"]]


def _build_regime_series(ohlcv: pd.DataFrame, window: int, step: int, use_hmm: bool) -> pd.Series:
    """滚动窗口检测制度，返回制度标签序列（索引 = 窗口末端时点）。

    参数:
        ohlcv:   OHLCV DataFrame（DatetimeIndex）。
        window:  滚动检测窗口大小。
        step:    滚动步长。
        use_hmm: 是否启用 HMM 检测（默认 False → 规则检测，速度快）。
    """
    selector = RegimeAwareSelector(use_hmm=use_hmm, use_multi_hmm=use_hmm)
    regimes: list[str] = []
    idx: list[pd.Timestamp] = []
    n = len(ohlcv)
    for i in range(window, n, step):
        det = selector.detect(ohlcv.iloc[i - window : i])
        regimes.append(det["regime"])
        idx.append(ohlcv.index[i - 1])
    return pd.Series(regimes, index=pd.DatetimeIndex(idx), name="regime")


def _forward_series(ohlcv: pd.DataFrame, horizon: int) -> tuple[pd.Series, pd.Series]:
    """计算前向收益与前向波动（t 时点之后 horizon 期）。"""
    close = ohlcv["close"].astype(float)
    rets = close.pct_change()
    fwd_ret = close.shift(-horizon) / close - 1.0  # t → t+horizon 累计收益
    fwd_vol = rets.rolling(horizon).std().shift(-horizon)  # 未来 horizon 期日收益波动
    return fwd_ret, fwd_vol


def _json_safe(obj: Any) -> Any:
    """递归将 NaN/Inf 转换为 None（保证输出合法 JSON）。"""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(description="制度有效性样本外验证（28 计划 T9）")
    parser.add_argument("--data", default=None, help="OHLCV CSV 文件或 duckdb 数据库文件（缺省用合成数据）")
    parser.add_argument("--table", default=None, help="duckdb 表名（--data 为 duckdb 文件时必填）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    parser.add_argument("--window", type=int, default=60, help="滚动检测窗口（默认 60）")
    parser.add_argument("--step", type=int, default=1, help="滚动检测步长（默认 1）")
    parser.add_argument("--horizon", type=int, default=5, help="前向收益周期（默认 5）")
    parser.add_argument("--use-hmm", action="store_true", help="启用 HMM 检测（默认规则检测，速度快）")
    args = parser.parse_args()

    if args.horizon < 1 or args.window < 20 or args.step < 1:
        raise SystemExit("参数非法: --horizon>=1, --window>=20, --step>=1")

    ohlcv = _load_ohlcv(args.data, args.table)
    if len(ohlcv) <= args.window:
        raise SystemExit(f"数据行数 {len(ohlcv)} 不足滚动窗口 {args.window}")

    regime_series = _build_regime_series(ohlcv, args.window, args.step, args.use_hmm)
    fwd_ret, fwd_vol = _forward_series(ohlcv, args.horizon)
    # 按制度序列时点对齐前向收益/波动（索引交集内缺失值由验证函数 dropna 兜底）
    aligned = pd.DataFrame({"fwd": fwd_ret, "fwd_vol": fwd_vol}).reindex(regime_series.index)
    result = validate_regime_predictive_power(regime_series, aligned["fwd"], aligned["fwd_vol"])

    source = args.data or "synthetic"
    report = {
        "source": source,
        "window": args.window,
        "step": args.step,
        "horizon": args.horizon,
        "use_hmm": args.use_hmm,
        "regime_distinct": sorted(set(regime_series)),
        "validation": result,
    }

    if args.json:
        print(json.dumps(_json_safe(report), ensure_ascii=False, indent=2))
        return 0

    print("=== Regime 制度有效性样本外验证报告 ===")
    print(f"数据源: {source}  窗口={args.window} 步长={args.step} 前向周期={args.horizon}")
    print(f"样本数: {result.get('n', 0)}  制度种类: {sorted(set(regime_series))}")
    if "error" in result:
        print(f"错误: {result['error']}")
        return 0
    if "kruskal_stat" in result:
        print(
            f"Kruskal-Wallis: stat={result['kruskal_stat']:.4f}  "
            f"p={result['kruskal_p']:.4g}  （组间前向收益分布差异）"
        )
    print("制度明细:")
    for regime in sorted(set(regime_series)):
        stats = result.get(str(regime), {})
        print(
            f"  {regime:<10} n={stats.get('count', 0):>4}  "
            f"mean_fwd_return={stats.get('mean_fwd_return', float('nan')):+.5f}  "
            f"mean_fwd_vol={stats.get('mean_fwd_vol', float('nan')):.5f}  "
            f"fwd_return_std={stats.get('fwd_return_std', float('nan')):.5f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
