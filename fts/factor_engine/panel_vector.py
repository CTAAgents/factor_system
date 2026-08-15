"""
fts/factor_engine/panel_vector.py — 全矩阵化横截面评估（预对齐面板 + 向量化 IC）

背景:
    evaluation_chain.py 横截面路径（_cs_execute_factors / _cs_build_matrices /
    _cs_compute_ics）逐品种执行 + 逐日 spearmanr 循环，单候选评估耗时秒级。
    本模块提供语义等价的全矩阵化实现：
        1. 一次性预对齐 2D 面板（连续 float64 矩阵 + 前向收益预计算）；
        2. 联合掩码（signal ∩ returns 均有限）内各自 rank（axis=1）；
        3. 行内 Pearson 一次矩阵运算产出全部截面期 IC。
    经 scripts/benchmark_panel_ic.py 实测（真实 149 品种 × 3062 日）单候选
    加速 5-13x，逐日 IC 与旧路径 max|ΔIC| ~ 1e-16 完全一致。

正确性约束（与 evaluation_chain 旧路径对齐）:
    - 联合有效子集内 rank：旧路径 spearmanr(sig_valid, ret_valid) 在「信号与
      收益均有限」的样本子集内各自排序；本实现以「mask=False → +inf 占位」
      保证有限值 rank 与该子集内 rank 一致。
    - 常数守卫：原始值 std < std_floor（默认 1e-10）或有效样本 < min_valid
      （默认 5）的行跳过（置 NaN），与旧路径跳过语义一致。
    - 行内 Pearson 必须以显式 mask 计算：rankdata 会把 NaN/∞ 占位变成有限
      rank，不能再用 isfinite 判定掩码。

作用域（角色边界）:
    本模块为独立候选实现，不改动 evaluation_chain 现有评估语义，未接入主
    链路。切换主链路前必须通过 tests/factor_engine/test_panel_vector.py
    全量对照测试（逐日 IC 一致）。本模块不依赖 fts 其他模块（纯数值层）。

版本: v1.0.0（原型基准验证通过后落库）
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy.stats import rankdata

_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume", "amount")
_DEFAULT_MIN_SYMBOLS: int = 10
_DEFAULT_MIN_VALID: int = 5
_DEFAULT_STD_FLOOR: float = 1e-10


# ══════════════════════════════════════════════════════════
# 预对齐面板
# ══════════════════════════════════════════════════════════


@dataclass
class AlignedPanel:
    """预对齐 2D 面板 — 一次性对齐，全生命周期零 reindex。

    Attributes:
        dates: 共同交易日索引（n_dates,）
        symbols: 品种列表（n_symbols,）
        values: 连续 float64 数组 (n_dates, n_symbols, n_cols)，列序 = cols
        cols: 字段列名（open/high/low/close/volume/amount）
        fwd_returns: 前向收益矩阵 (n_dates, n_symbols)，运行期只算一次
        forward_days: 前向持有期（默认 5，与 evaluation_chain 口径一致）
    """

    dates: pd.DatetimeIndex
    symbols: list[str]
    values: np.ndarray
    cols: list[str]
    fwd_returns: np.ndarray
    forward_days: int = field(default=5)

    def col(self, name: str) -> np.ndarray:
        """按列名取 (n_dates, n_symbols) 矩阵视图。"""
        idx = self.cols.index(name)
        return self.values[:, :, idx]


def prealign_panel(
    panel: dict[str, pd.DataFrame],
    min_symbols: int = _DEFAULT_MIN_SYMBOLS,
    forward_days: int = 5,
) -> AlignedPanel:
    """一次性对齐面板：共同日期（覆盖率口径）+ 连续 float64 矩阵 + 预计算。

    共同日期口径对齐生产 `scripts/run_futures_evolution.load_futures_panel`：
    取「至少 min_symbols 个品种有数据」的日期，而非全量交集（期货品种上市
    时间差异极大，全量交集常为空）。缺失日期在矩阵中留 NaN，由 IC 计算掩码
    剔除。

    前向收益口径与 evaluation_chain._cs_execute_factors 严格一致：
        fwd[t] = (close[t+forward_days] - close[t]) / max(close[t], 1e-10)

    Args:
        panel: {symbol: OHLCV DataFrame} 面板
        min_symbols: 共同日期最少品种数（默认 10）
        forward_days: 前向持有期（默认 5）

    Returns:
        AlignedPanel
    """
    dfs = list(panel.values())
    if not dfs:
        raise ValueError("面板为空")
    cols = [c for c in _COLUMNS if c in dfs[0].columns]
    date_counts = Counter()
    for df in dfs:
        date_counts.update(df.index)
    common_dates = pd.DatetimeIndex(sorted(d for d, cnt in date_counts.items() if cnt >= min_symbols))
    if len(common_dates) == 0:
        raise ValueError(f"没有找到至少 {min_symbols} 个品种共有的日期")
    symbols = list(panel.keys())

    n_dates, n_syms, n_cols = len(common_dates), len(symbols), len(cols)
    values = np.empty((n_dates, n_syms, n_cols), dtype=np.float64, order="C")
    for j, sym in enumerate(symbols):
        df = panel[sym].reindex(common_dates)
        for c, col in enumerate(cols):
            values[:, j, c] = df[col].to_numpy(dtype=np.float64)

    close = values[:, :, cols.index("close")]
    fwd = np.zeros_like(close)
    if n_dates > forward_days:
        fwd[: -forward_days] = (close[forward_days:] - close[: -forward_days]) / np.maximum(
            close[: -forward_days], 1e-10
        )

    return AlignedPanel(
        dates=common_dates,
        symbols=symbols,
        values=values,
        cols=cols,
        fwd_returns=fwd,
        forward_days=forward_days,
    )


# ══════════════════════════════════════════════════════════
# 全矩阵化截面 IC
# ══════════════════════════════════════════════════════════


def _rowwise_masked_pearson(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """行内掩码 Pearson 相关（全矩阵化）。

    对每行（一个截面期），仅在 mask 为 True 的列上计算 Pearson，输出与逐行
    scipy.stats.pearsonr 在有效列上完全一致；有效列 < 2 或零方差的行置 NaN。

    注意：a / b 应为已完成掩码隔离的输入（如 rank 后的矩阵，无效列任意值），
    行内均值/协方差/零化全部以 mask 为基准，与输入值本身是否有限无关。

    Args:
        a / b: 同形 (n_dates, n_symbols) 矩阵
        mask: 同形布尔矩阵（True = 参与计算）

    Returns:
        (n_dates,) 相关数组；有效列 < 2 或零方差的行置 NaN
    """
    cnt = mask.sum(axis=1)
    a_m = np.where(mask, a, 0.0)
    b_m = np.where(mask, b, 0.0)
    mean_a = np.where(cnt > 0, a_m.sum(axis=1) / np.maximum(cnt, 1), 0.0)
    mean_b = np.where(cnt > 0, b_m.sum(axis=1) / np.maximum(cnt, 1), 0.0)
    ca = np.where(mask, a_m - mean_a[:, None], 0.0)
    cb = np.where(mask, b_m - mean_b[:, None], 0.0)
    num = (ca * cb).sum(axis=1)
    den = np.sqrt((ca * ca).sum(axis=1) * (cb * cb).sum(axis=1))

    ics = np.full(a.shape[0], np.nan, dtype=np.float64)
    ok = (cnt >= 2) & (den > 1e-10)
    ics[ok] = num[ok] / den[ok]
    return ics


def compute_cs_ics_vectorized(
    signal: np.ndarray,
    fwd_returns: np.ndarray,
    min_valid: int = _DEFAULT_MIN_VALID,
    std_floor: float = _DEFAULT_STD_FLOOR,
) -> tuple[np.ndarray, np.ndarray]:
    """全矩阵化截面 Spearman IC（语义对齐旧路径逐日 spearmanr）。

    Spearman IC = Pearson(rank(signal), rank(return))，与旧路径完全一致：
        - 联合掩码 mask = isfinite(signal) & isfinite(fwd_returns)；
        - signal / returns 均在「联合有效」子集内各自 rank（mask=False 以 +inf
          占位，保证有限值 rank 与子集内 rank 一致，且不污染）；
        - 常数守卫对齐旧路径：原始值 std < std_floor 或有效列 < min_valid 的
          行跳过（置 NaN）；
        - 行内 Pearson 以显式 mask 计算（rank 后不能再用 isfinite 判掩码）。

    Args:
        signal: 信号矩阵 (n_dates, n_symbols)，允许 NaN
        fwd_returns: 前向收益矩阵 (n_dates, n_symbols)，允许 NaN
        min_valid: 有效列数下限（旧路径 `np.sum(valid) < 5` 语义，默认 5）
        std_floor: 原始值 std 下限（旧路径 `np.std < 1e-10` 语义，默认 1e-10）

    Returns:
        (ics, mask)：
            ics — (n_dates,) IC 数组，无效行（常数 / 有效列不足）为 NaN；
            mask — (n_dates, n_symbols) 联合有效掩码，供调用方追溯对齐关系
    """
    signal = np.asarray(signal, dtype=np.float64)
    fwd_returns = np.asarray(fwd_returns, dtype=np.float64)
    if signal.shape != fwd_returns.shape:
        raise ValueError(f"信号与收益矩阵形状不一致: {signal.shape} vs {fwd_returns.shape}")

    mask = np.isfinite(signal) & np.isfinite(fwd_returns)
    cnt = mask.sum(axis=1)

    # 原始值 std 守卫（对齐旧路径 np.std(sig_valid) 语义；nansum 版避免全 NaN 行告警）
    sig_ss = np.where(mask, signal, np.nan)
    ret_ss = np.where(mask, fwd_returns, np.nan)
    cntm = np.maximum(cnt, 1)
    sig_mean = np.where(cnt > 0, np.nansum(sig_ss, axis=1) / cntm, np.nan)
    ret_mean = np.where(cnt > 0, np.nansum(ret_ss, axis=1) / cntm, np.nan)
    std_sig = np.sqrt(np.where(cnt > 0, np.nansum((sig_ss - sig_mean[:, None]) ** 2, axis=1) / cntm, np.nan))
    std_ret = np.sqrt(np.where(cnt > 0, np.nansum((ret_ss - ret_mean[:, None]) ** 2, axis=1) / cntm, np.nan))

    ranked_signal = rankdata(np.where(mask, signal, np.inf), axis=1, method="average")
    ranked_ret = rankdata(np.where(mask, fwd_returns, np.inf), axis=1, method="average")
    ics = _rowwise_masked_pearson(ranked_signal, ranked_ret, mask)
    ics[(cnt < min_valid) | (std_sig < std_floor) | (std_ret < std_floor)] = np.nan
    return ics, mask


# ══════════════════════════════════════════════════════════
# 面板化因子执行引擎（plans/37 Phase 2 Step 1：算子因子先行）
# ══════════════════════════════════════════════════════════

_PANEL_AST_CACHE: dict[str, Any] = {}
# 验证缓存键 = (expression, n_symbols, n_dates, close 缺口数)：缺口模式影响滚动语义，
# 走航多窗口对同一 (expression, 面板缺口) 只验证一次。
_PANEL_SAFE_CACHE: dict[tuple[str, int, int, int], bool] = {}
_DEFAULT_VERIFY_SYMBOLS: int = 3


# ─── 缺口感知 DataFrame 包装器（plans/39 5.2） ─────────────
# 面板路径按列评估时，缺口列（union_dates reindex 导致的 NaN 行）上的
# pandas rolling 语义与逐品种（品种自身日历，无 NaN 行）不一致。
# _GapAwareFrame 拦截 .rolling() 调用，对每列先压缩-散射（与 _rolling_apply_native
# gap_aware_mode 同口径）再调 pandas 原生 rolling；无缺口列零漂移（走快路径
# 直接调原生 rolling，不引入额外开销）。


class _GapAwareFrame:
    """缺口感知面板 DataFrame 包装器。

    用法透明：``_read_field`` 返回本对象，算子在其上调用 ``.rolling(window).mean()``
    等操作，缺口列自动压缩-散射，无缺口列零漂移。
    """

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df
        self.columns = df.columns.tolist()
        self.index = df.index

    def __getitem__(self, key: str) -> pd.Series:
        return self._df[key]

    def __contains__(self, key: str) -> bool:
        return key in self._df.columns

    def astype(self, dtype, **kwargs):  # noqa: ARG002 — 兼容 _read_field 的 .astype(float) 调用
        return self

    def to_numpy(self, dtype=None, **kwargs) -> np.ndarray:
        return self._df.to_numpy(dtype=dtype, **kwargs)

    def rolling(self, window: int, min_periods: int | None = None, **kwargs) -> "_GapAwareRolling":
        return _GapAwareRolling(self._df, window, min_periods, **kwargs)


class _GapAwareRolling:
    """缺口感知滚动结果：对每列压缩-散射后调 pandas 原生 rolling。"""

    _METHODS = ("mean", "std", "min", "max", "sum", "var", "median", "quantile", "rank", "sem", "skew", "kurt", "count")

    def __init__(self, df: pd.DataFrame, window: int, min_periods: int | None = None, **kwargs) -> None:
        self._df = df
        self._window = window
        self._min_periods = min_periods
        self._kwargs = kwargs

    def __getattr__(self, name: str):
        if name in self._METHODS:
            return lambda *args, **kw: self._apply(name, *args, **kw)
        msg = f"缺口感知滚动不支持方法: {name}"
        raise AttributeError(msg)

    def _apply(self, method: str, *args, **kw) -> pd.DataFrame:
        result = pd.DataFrame(index=self._df.index, columns=self._df.columns, dtype=float)
        for col in self._df.columns:
            arr = self._df[col].to_numpy(dtype=np.float64)
            idx = np.flatnonzero(~np.isnan(arr))
            if len(idx) == len(arr) or len(idx) == 0:
                # 无缺口 / 全 NaN → 原生 rolling（零漂移）
                result[col] = getattr(self._df[col].rolling(self._window, min_periods=self._min_periods, **self._kwargs), method)(*args, **kw)
            else:
                dense = pd.Series(arr[idx], dtype=np.float64)
                rolled = getattr(dense.rolling(self._window, min_periods=self._min_periods, **self._kwargs), method)(*args, **kw)
                out: np.ndarray = np.full(len(arr), np.nan)
                out[idx] = rolled.to_numpy(dtype=np.float64)
                result[col] = out
        return result


# ─── 缺口感知错误标记（非缺口感知路径返回 None） ────────────
_GAP_DETECTED: bool = False


class _PanelData:
    """面板数据容器：data['close'] 返回 (union_dates × symbols) DataFrame。

    执行轴 = 全部品种日期的并集（保留每个品种完整历史，滚动窗口从品种自身
    上市日计数，与逐品种执行一致）；最终结果由调用方切片回 common_dates。
    使 expr_dsl evaluate 在面板矩阵上按列求值（DataFrame 列式运算与逐品种
    Series 运算逐列一致）；`.columns` 暴露可用字段供 _read_field 校验。

    gap_aware_mode 作用域内，__getitem__ 返回 _GapAwareFrame 包装器，
    算子调用 .rolling().method() 时自动做缺口感知压缩-散射（plans/39 5.2）。
    """

    def __init__(self, panel_data: dict[str, pd.DataFrame], common_dates: pd.DatetimeIndex) -> None:
        self.common_dates = common_dates
        union = pd.DatetimeIndex([])
        for df in panel_data.values():
            union = union.union(df.index)
        self.union_dates = pd.DatetimeIndex(sorted(union))
        self._frames: dict[str, pd.DataFrame] = {}
        for _f in _COLUMNS:
            if any(_f in df.columns for df in panel_data.values()):
                self._frames[_f] = pd.DataFrame(
                    {
                        sym: panel_data[sym][_f].reindex(self.union_dates).to_numpy(dtype=np.float64)
                        for sym in panel_data
                    },
                    index=self.union_dates,
                )
        self._columns: list[str] = list(self._frames)

    @property
    def columns(self) -> list[str]:
        return self._columns

    def __getitem__(self, name: str) -> pd.DataFrame:
        if name not in self._frames:
            raise KeyError(f"字段 '{name}' 不在面板中")
        from .feature_ops import _GAP_AWARE as _ga

        if _ga:
            return _GapAwareFrame(self._frames[name])
        return self._frames[name]

    def __contains__(self, name: str) -> bool:
        return name in self._frames


def _arr_equal_nan(a: np.ndarray, b: np.ndarray, rtol: float = 1e-9, atol: float = 1e-12) -> bool:
    """逐元素比较（含 NaN 位置一致）。"""
    if a.shape != b.shape:
        return False
    nan_a, nan_b = np.isnan(a), np.isnan(b)
    if not np.array_equal(nan_a, nan_b):
        return False
    return bool(np.allclose(np.nan_to_num(a), np.nan_to_num(b), rtol=rtol, atol=atol, equal_nan=True))


def _verify_panel_safe(
    node: Any,
    panel_data: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    registry: dict[str, Any],
    max_verify_symbols: int,
) -> bool:
    """面板化执行抽样验证：面板矩阵 vs 逐品种 evaluate 逐位比对（含 NaN 模式）。

    抽样覆盖缺口模式多样品种（最早/最晚/中位上市）；任一异常或不一致返回 False
    （调用方回退逐品种，零漂移）。列式运算对全部列均匀，抽样一致即可信整体。
    """
    from .expr_dsl import evaluate

    symbols = list(panel_data.keys())
    n_syms = len(symbols)
    try:
        pdata = _PanelData(panel_data, common_dates)
        mat_union = np.asarray(evaluate(node, pdata, registry), dtype=np.float64)
        if mat_union.shape != (len(pdata.union_dates), n_syms):
            return False
        mat = pd.DataFrame(mat_union, index=pdata.union_dates).reindex(common_dates).to_numpy(dtype=np.float64)
    except Exception:  # noqa: BLE001 — 面板化执行失败即回退
        return False

    picks: list[str] = []
    if symbols:
        picks.append(min(symbols, key=lambda s: panel_data[s].index[0]))  # 最早上市
        picks.append(max(symbols, key=lambda s: panel_data[s].index[0]))  # 最晚上市
    if len(symbols) > 1:
        picks.append(symbols[len(symbols) // 2])
    picks = list(dict.fromkeys(picks))[: max(1, max_verify_symbols)]

    for sym in picks:
        try:
            single = np.asarray(evaluate(node, panel_data[sym], registry), dtype=np.float64)
            col = pd.Series(single, index=panel_data[sym].index).reindex(common_dates).to_numpy(dtype=np.float64)
            got = mat[:, symbols.index(sym)]
        except Exception:  # noqa: BLE001
            return False
        if not _arr_equal_nan(col, got):
            return False
    return True


def execute_factor_panel(
    factor: dict[str, Any],
    panel_data: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    max_verify_symbols: int = _DEFAULT_VERIFY_SYMBOLS,
) -> Optional[np.ndarray]:
    """面板化执行算子因子（plans/37 Phase 2 Step 1；plans/39 §11 回退后保留独立模块）。

    注：plans/39 §11（v2.104.0+58）真实缺口面板实测 0.3x（<5x 门槛，98% 列
    内部缺口致压缩-散射开销反超）→ 评估链信号构建摘除本函数，恒逐品种执行；
    本函数保留为独立模块/对照测试基准（缺口感知滚动内核 gap_aware_mode +
    _GapAwareFrame 供独立调用方复用），登记豁免，不参与主链路。

    将 DSL 表达式在 (dates × symbols) 面板矩阵上按列求值，一次执行产出全部
    品种信号矩阵，消除逐品种 Python 循环 + 沙箱 exec（算子因子路径）。

    正确性保障（动态验证 + 安全回退）：
        - 仅支持 kind=="operator" 且含 expression 的因子（代码因子返回 None）；
        - 对抽样品种逐列与逐品种 evaluate 逐位比对（含 NaN 模式）；
        - 任一异常/不一致 → None（调用方回退逐品种路径，零漂移）；
        - 验证按 (expression, 面板缺口签名) 进程内缓存，走航多窗口只验证一次。

    Args:
        factor: 因子程序（kind=="operator"）
        panel_data: {symbol: OHLCV DataFrame} 面板
        common_dates: 共同交易日索引
        max_verify_symbols: 抽样验证品种数（默认 3）

    Returns:
        (n_dates, n_symbols) 信号矩阵；不支持 / 验证不通过返回 None
    """
    if factor.get("kind") != "operator" or not factor.get("expression"):
        return None
    expression = factor["expression"]
    symbols = list(panel_data.keys())
    n_dates, n_syms = len(common_dates), len(symbols)
    if n_syms < 1 or n_dates < 1:
        return None
    try:
        from .expr_dsl import build_registry, evaluate, parse_expression
        from .feature_ops import gap_aware_mode

        registry = build_registry()
        node = _PANEL_AST_CACHE.get(expression)
        if node is None:
            node = parse_expression(expression)
            _PANEL_AST_CACHE[expression] = node

        close_panel = pd.DataFrame(
            {sym: panel_data[sym]["close"].reindex(common_dates) for sym in symbols}
        )
        close_nan = int(close_panel.isna().sum().sum())
        # 缺口位置签名（plans/39 §6.4）：缺口语义取决于缺口分布而非仅计数
        # （同 NaN 数、不同位置 → 窗口回溯起点不同 → 验证结论可能不同）。
        gap_sig = hashlib.blake2b(
            close_panel.isna().to_numpy(dtype=np.uint8).tobytes(), digest_size=16
        ).digest()
        cache_key = (expression, n_syms, n_dates, close_nan, gap_sig)
        safe = _PANEL_SAFE_CACHE.get(cache_key)
        if safe is None:
            # 验证与执行均在缺口感知作用域内：缺口列压缩语义与逐品种（品种自身
            # 日历）一致，无缺口列零漂移（plans/39 5.1）。
            with gap_aware_mode():
                safe = _verify_panel_safe(node, panel_data, common_dates, registry, max_verify_symbols)
            _PANEL_SAFE_CACHE[cache_key] = safe
        if not safe:
            return None

        pdata = _PanelData(panel_data, common_dates)
        with gap_aware_mode():
            result = evaluate(node, pdata, registry)
        arr_union = np.asarray(result, dtype=np.float64)
        if arr_union.shape != (len(pdata.union_dates), n_syms):
            return None
        # union 轴（保留品种完整历史）→ 切片回 common_dates
        return pd.DataFrame(arr_union, index=pdata.union_dates).reindex(common_dates).to_numpy(dtype=np.float64)
    except Exception:  # noqa: BLE001 — 任何失败安全回退
        return None


def build_forward_return_matrix(
    panel_data: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    forward_days: int = 5,
) -> np.ndarray:
    """构建前向收益矩阵（逐品种先算后对齐，与 evaluation_chain._cs_execute_factors 同口径）。

    fwd[t] = (close[t+forward_days] - close[t]) / max(close[t], 1e-10)，在品种
    自身轴上计算后 reindex 到 common_dates（缺失留 NaN）。

    Returns:
        (n_dates, n_symbols) 收益矩阵
    """
    symbols = list(panel_data.keys())
    n_dates = len(common_dates)
    ret_matrix = np.full((n_dates, len(symbols)), np.nan, dtype=np.float64)
    for j, sym in enumerate(symbols):
        df = panel_data[sym]
        closes = df["close"].to_numpy(dtype=np.float64)
        fwd = np.zeros(len(closes))
        if len(closes) > forward_days:
            fwd[: -forward_days] = (closes[forward_days:] - closes[: -forward_days]) / np.maximum(
                closes[: -forward_days], 1e-10
            )
        ret_matrix[:, j] = pd.Series(fwd, index=df.index).reindex(common_dates).to_numpy(dtype=np.float64)
    return ret_matrix


__all__ = [
    "AlignedPanel",
    "prealign_panel",
    "compute_cs_ics_vectorized",
    "execute_factor_panel",
    "build_forward_return_matrix",
]
