"""FTS-Expr 算子注册表 — 定义算子语义/梯度/边界 (Phase C.2)。

设计要点:
    - 实现复用 feature_ops.py 的既有 50 个算子，DSL 名经 lambda 薄包装映射
    - 每个算子声明: category(L0-L5 分层) / params / int|float 参数 /
      param_bounds(边界, 防微观演化越界) / lookback_param(PIT 静态分析) /
      differentiable(梯度可导声明) / economic_meaning(经济语义标签)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from ..feature_ops import CompositeOps, PriceOps, RollingOps, TimeSeriesOps, _rolling_series_out, _ts_argmax_vec, _ts_decay_linear_vec

# L0 基础数据字段（数据访问层）
# F.1 契约拆分: hold/settle 为期货专用字段（FuturesOHLCV），vwap/amount/returns 双市场通用
L0_FIELDS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "amount",
    "returns",
    "hold",  # 期货专用（持仓量）
    "settle",  # 期货专用（结算价）
)

# GAP-S12 (v2.67.0): A 股特有数据字段（北向/两融/股东户数/分析师预期）
A_SHARE_FIELDS: tuple[str, ...] = (
    "northbound_flow",
    "northbound_hold_pct",
    "margin_balance",
    "margin_net_buy",
    "margin_short_balance",
    "holder_count",
    "analyst_up_count",
    "analyst_down_count",
    "analyst_total_count",
    "analyst_eps_revision",
)


@dataclass(frozen=True)
class OperatorMeta:
    """算子元数据（语义/梯度/边界）。"""

    name: str
    func: Callable
    category: str  # L0-L5 分层
    params: tuple[str, ...]  # 参数名，首个为序列输入
    int_params: frozenset[str] = frozenset()  # 整数参数（如 window/n）
    float_params: frozenset[str] = frozenset()  # 浮点参数（如 q/n_std/lo/hi）
    param_bounds: dict[str, tuple[float, float]] = field(default_factory=dict)
    lookback_param: Optional[str] = None  # 最大 lookback 来源参数 (PIT)
    differentiable: bool = True  # 梯度可导声明
    economic_meaning: str = ""  # 经济语义标签


def build_registry() -> dict[str, OperatorMeta]:
    """构建 DSL 算子注册表（实现复用 feature_ops + 少量领域新增）。"""
    registry: dict[str, OperatorMeta] = {}

    def add(
        name,
        func,
        category,
        params,
        *,
        int_params=(),
        float_params=(),
        bounds=None,
        lookback=None,
        diff=True,
        meaning="",
    ):
        registry[name] = OperatorMeta(
            name=name,
            func=func,
            category=category,
            params=tuple(params),
            int_params=frozenset(int_params),
            float_params=frozenset(float_params),
            param_bounds=dict(bounds or {}),
            lookback_param=lookback,
            differentiable=diff,
            economic_meaning=meaning,
        )

    # ── L0 基础数据字段（数据访问层） ──
    for _f in L0_FIELDS:
        add(_f, lambda x: x, "L0", ("x",), meaning="基础数据字段")

    # ── L0b A 股特有数据字段 (GAP-S12) ──
    _a_share_meanings = {
        "northbound_flow": "北向资金当日净流入(元)",
        "northbound_hold_pct": "北向持股占流通市值比例",
        "margin_balance": "融资余额(元)",
        "margin_net_buy": "融资净买入(元)",
        "margin_short_balance": "融券余量(股)",
        "holder_count": "股东户数(户)",
        "analyst_up_count": "分析师上调家数",
        "analyst_down_count": "分析师下调家数",
        "analyst_total_count": "分析师覆盖总家数",
        "analyst_eps_revision": "EPS 一致预期修正幅度",
    }
    for _f in A_SHARE_FIELDS:
        add(_f, lambda x: x, "L0", ("x",), meaning=_a_share_meanings.get(_f, "A股特有数据字段"))

    # ── L1 时序算子（单/双序列变换） ──
    add(
        "ts_mean",
        lambda x, n: TimeSeriesOps.ts_mean(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动均值(趋势基准)",
    )
    add(
        "ts_std",
        lambda x, n: TimeSeriesOps.ts_std(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动波动",
    )
    add(
        "ts_zscore",
        lambda x, n: RollingOps.ts_zscore(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动标准化",
    )
    add(
        "ts_rank",
        lambda x, n: RollingOps.ts_rank(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动窗口内排名",
    )
    add(
        "ts_min",
        lambda x, n: TimeSeriesOps.ts_min(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动最小值",
    )
    add(
        "ts_max",
        lambda x, n: TimeSeriesOps.ts_max(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动最大值",
    )
    add(
        "ts_sum",
        lambda x, n: TimeSeriesOps.ts_sum(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动求和",
    )
    add(
        "ts_skewness",
        lambda x, n: RollingOps.ts_skewness(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动偏度",
    )
    add(
        "ts_kurtosis",
        lambda x, n: RollingOps.ts_kurtosis(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动峰度",
    )
    add(
        "ts_median",
        lambda x, n: RollingOps.ts_median(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动中位数",
    )
    add(
        "ts_delay",
        lambda x, n: x.shift(n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (1, 250)},
        lookback="n",
        meaning="滞后 n 期",
    )
    add(
        "ts_delta",
        lambda x, n: PriceOps.delta(x, periods=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (1, 250)},
        lookback="n",
        meaning="差分",
    )
    add(
        "ts_pct_change",
        lambda x, n: PriceOps.pct_change(x, periods=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (1, 250)},
        lookback="n",
        meaning="百分比变化",
    )
    add(
        "ts_momentum",
        lambda x, n: RollingOps.ts_momentum(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="动量(当前/前n)",
    )
    add(
        "ts_volatility",
        lambda x, n: RollingOps.ts_volatility(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动年化波动率",
    )
    add(
        "ts_covariance",
        lambda x, y, n: x.rolling(n).cov(y),
        "L1",
        ("x", "y", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动协方差",
    )
    add(
        "ts_correlation",
        lambda x, y, n: x.rolling(n).corr(y),
        "L1",
        ("x", "y", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="滚动相关系数",
    )
    add(
        "ts_decay_linear",
        lambda x, n: _rolling_series_out(_ts_decay_linear_vec, x, n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="线性衰减加权",
    )
    add(
        "ts_argmax",
        lambda x, n: _rolling_series_out(_ts_argmax_vec, x, n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="窗口内最大值位置",
    )
    # GAP-I202 (v2.75.0): 时序组合算子扩充（与 feature_ops 共用原语，单一事实源）
    add(
        "ts_slope",
        lambda x, n: RollingOps.ts_slope(x, window=n),
        "L1",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        diff=False,
        meaning="滚动回归斜率(局部趋势强度与方向)",
    )
    add(
        "ts_quantile",
        lambda x, n, q: RollingOps.ts_quantile(x, window=n, q=q),
        "L1",
        ("x", "n", "q"),
        int_params=("n",),
        float_params=("q",),
        bounds={"n": (2, 250), "q": (0.0, 1.0)},
        lookback="n",
        diff=False,
        meaning="滚动分位数(尾部/中枢水平)",
    )

    # ── L2 横截面算子（跨截面变换） ──
    add("rank", lambda x: PriceOps.rank(x), "L2", ("x",), meaning="截面排名(0-1)")
    add("zscore", lambda x: PriceOps.zscore(x), "L2", ("x",), meaning="截面 Z-Score")
    add(
        "normalize",
        lambda x: (x - x.min()) / (x.max() - x.min()) if x.max() != x.min() else x * 0.0,
        "L2",
        ("x",),
        meaning="min-max 归一化",
    )
    add(
        "quantile",
        lambda x, q: x.quantile(q),
        "L2",
        ("x", "q"),
        float_params=("q",),
        bounds={"q": (0.0, 1.0)},
        meaning="分位数",
    )
    add(
        "winsorize",
        lambda x, n_std: x.clip(x.mean() - n_std * x.std(), x.mean() + n_std * x.std()),
        "L2",
        ("x", "n_std"),
        float_params=("n_std",),
        bounds={"n_std": (0.0, 10.0)},
        meaning="缩尾",
    )

    # ── L3 逻辑算子（受控条件） ──
    add("where", lambda cond, x, y: x.where(cond.astype(bool), y), "L3", ("cond", "x", "y"), meaning="条件选择")
    add("gt", lambda a, b: a > b, "L3", ("a", "b"), meaning="大于")
    add("lt", lambda a, b: a < b, "L3", ("a", "b"), meaning="小于")
    add("and_", lambda a, b: a.astype(bool) & b.astype(bool), "L3", ("a", "b"), meaning="逻辑与")
    add("or_", lambda a, b: a.astype(bool) | b.astype(bool), "L3", ("a", "b"), meaning="逻辑或")
    add("not_", lambda a: ~a.astype(bool), "L3", ("a",), meaning="逻辑非")

    # ── L4 组合算子（高阶） ──
    add("add", CompositeOps.add, "L4", ("a", "b"), meaning="加法")
    add("sub", CompositeOps.sub, "L4", ("a", "b"), meaning="减法")
    add("mul", CompositeOps.mul, "L4", ("a", "b"), meaning="乘法")
    add(
        "div",
        lambda a, b: a / b.replace(0, np.nan) if isinstance(b, pd.Series) else a / b,
        "L4",
        ("a", "b"),
        meaning="除法(0 安全)",
    )
    add("neg", lambda x: -x, "L4", ("x",), meaning="取负")
    add("abs", lambda x: x.abs(), "L4", ("x",), meaning="绝对值")
    add("sign", lambda x: np.sign(x), "L4", ("x",), meaning="符号")
    add("sqrt", lambda x: np.sqrt(x.abs()), "L4", ("x",), meaning="平方根")
    add("log", lambda x: np.log(x.abs() + 1e-10), "L4", ("x",), meaning="对数")
    add("exp", lambda x: np.exp(x), "L4", ("x",), meaning="指数")
    add("min", lambda a, b: np.minimum(a, b), "L4", ("a", "b"), meaning="取小")
    add("max", lambda a, b: np.maximum(a, b), "L4", ("a", "b"), meaning="取大")
    add("clip", lambda x, lo, hi: x.clip(lo, hi), "L4", ("x", "lo", "hi"), float_params=("lo", "hi"), meaning="截断")
    add("pow", lambda a, b: np.power(a, b), "L4", ("a", "b"), meaning="幂")

    # ── L4 高阶双序列/横截面/条件算子（GAP-L401，v2.66.0）──
    # 双序列: 滚动线性回归残差（回归 alpha，去趋势/去 beta）
    add(
        "regression_residual",
        lambda x, y, n: RollingOps.ts_regression_residual(x, y, window=n),
        "L4",
        ("x", "y", "n"),
        int_params=("n",),
        bounds={"n": (20, 250)},
        lookback="n",
        diff=False,
        meaning="滚动回归残差(去 beta 的 alpha)",
    )
    # 横截面: 分位桶（0~n_buckets-1，截面分位映射）
    add(
        "quantile_bucket",
        lambda x, n_buckets: RollingOps.ts_quantile_bucket(x, n_buckets=n_buckets),
        "L4",
        ("x", "n_buckets"),
        int_params=("n_buckets",),
        bounds={"n_buckets": (2, 10)},
        diff=False,
        meaning="分位桶(截面 0~n-1)",
    )
    # 横截面: 去均值（demean）
    add("cross_section_demean", lambda x: x - x.mean(), "L4", ("x",), diff=True, meaning="横截面去均值(暴露剥离)")

    # 条件: if_else（NaN 安全：条件为 NaN 视为 False）
    def _if_else(cond, x, y):
        if isinstance(cond, pd.Series):
            c = cond.fillna(False).astype(bool)
        else:
            c = bool(cond)
        return x.where(c, y)

    add("if_else", _if_else, "L4", ("cond", "x", "y"), diff=False, meaning="条件选择(NaN 安全)")
    # 双序列: 滚动窗口相关系数（信号共动/风格一致强度，GAP-L401 补齐 corr）
    add(
        "corr",
        lambda x, y, n: x.rolling(n).corr(y),
        "L4",
        ("x", "y", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        diff=False,
        meaning="滚动相关系数(双序列共动)",
    )
    # 横截面: 截面排名（0-1 归一化，GAP-L401 补齐 cross_section_rank）
    add("cross_section_rank", lambda x: PriceOps.rank(x), "L4", ("x",), diff=False, meaning="横截面排名(0-1 归一化)")

    # ── L5 领域算子（金融语义组合） ──
    add(
        "momentum",
        lambda p, n: p.pct_change(n),
        "L5",
        ("p", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="价格动量",
    )
    add(
        "reversal",
        lambda p, n: -p.pct_change(n),
        "L5",
        ("p", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="价格反转",
    )
    add(
        "liquidity",
        lambda p, v, n: v / v.rolling(n).mean(),
        "L5",
        ("p", "v", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="流动性(量/均量)",
    )
    add(
        "volatility",
        lambda p, n: p.pct_change().rolling(n).std(),
        "L5",
        ("p", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="波动率",
    )

    # ── L5b A 股特有领域算子 (GAP-S12)：北向/两融/股东户数/分析师预期 ──
    def _ts_mean(x, n):  # 滚动均值（min_periods=1 对齐 L1 ts_mean）
        return x.rolling(n, min_periods=1).mean()

    add(
        "nb_momentum",
        lambda x, n: x - _ts_mean(x, n),
        "L5",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="北向资金动量(当前-滚动均值)",
    )
    add(
        "margin_change",
        lambda x, n: PriceOps.pct_change(x, periods=n),
        "L5",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (1, 250)},
        lookback="n",
        meaning="融资余额变化率",
    )
    add(
        "holder_concentration",
        lambda x, n: -_ts_mean(PriceOps.delta(x, 1), n),
        "L5",
        ("x", "n"),
        int_params=("n",),
        bounds={"n": (2, 250)},
        lookback="n",
        meaning="筹码集中度(股东户数下降取正)",
    )
    add(
        "analyst_revision_ratio",
        lambda up, total: (up / total.replace(0, np.nan)) if isinstance(total, pd.Series) else (up / max(total, 1.0)),
        "L5",
        ("up", "total"),
        meaning="分析师上调比率(上调家数/覆盖家数)",
    )

    # ── C8 算子扩容（2026-08-11）：22 个高价值算子，与 feature_ops 双注册表共享 ──
    from ..feature_ops import C8Ops as _C8

    _c8_ops = [
        # (name, func, category, params, int_params, float_params, bounds, lookback, meaning)
        ("ts_argmin", _C8.ts_argmin, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "窗口内最小值位置(滞后形态)"),
        ("ts_ema", _C8.ts_ema, "L1", ("x", "span"), ("span",), (), {"span": (2, 250)}, "span", "指数移动平均(半衰期平滑)"),
        ("ts_mad", _C8.ts_mad, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "滚动中位数绝对偏差(稳健离散)"),
        ("ts_range", _C8.ts_range, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "滚动振幅 (max-min)/mean"),
        ("ts_iqr", _C8.ts_iqr, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "滚动四分位距(q75-q25)"),
        ("ts_quantile_range", _C8.ts_quantile_range, "L1", ("x", "n", "q_hi", "q_lo"), ("n",), ("q_hi", "q_lo"), {"n": (2, 250), "q_hi": (0.0, 1.0), "q_lo": (0.0, 1.0)}, "n", "滚动分位差(尾部宽度)"),
        ("ts_return_over_max", _C8.ts_return_over_max, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "距滚动高点回撤(回调深度)"),
        ("ts_min_max_ratio", _C8.ts_min_max_ratio, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "滚动 max/min-1(区间幅度)"),
        ("ts_std_ratio", _C8.ts_std_ratio, "L1", ("x", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "短/长波动比(均值回归强度)"),
        ("ts_roc_sum", _C8.ts_roc_sum, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "窗口收益率累加(累积动量)"),
        ("ts_breakout", _C8.ts_breakout, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "突破滚动新高(事件信号)"),
        ("ts_cumulative_return", _C8.ts_cumulative_return, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "n 期累计收益"),
        ("cs_rank_diff", _C8.cs_rank_diff, "L2", ("x", "n"), ("n",), (), {"n": (1, 250)}, "n", "截面排名变化(排名动量)"),
        ("cs_zscore_diff", _C8.cs_zscore_diff, "L2", ("x", "n"), ("n",), (), {"n": (1, 250)}, "n", "截面 zscore 变化"),
        ("cs_extreme_ratio", _C8.cs_extreme_ratio, "L2", ("x", "n", "n_std"), ("n",), ("n_std",), {"n": (2, 250), "n_std": (0.5, 5.0)}, "n", "窗口极端值占比(|z|>n_std)"),
        ("cs_median_dev", _C8.cs_median_dev, "L2", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "与滚动中位数偏离"),
        ("where_gt", _C8.where_gt, "L3", ("x", "threshold", "a", "b"), (), ("threshold", "a", "b"), {"threshold": (-10.0, 10.0), "a": (-10.0, 10.0), "b": (-10.0, 10.0)}, None, "条件选值(x>阈值取 a 否则 b)"),
        ("consecutive_true", _C8.consecutive_true, "L3", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "连续满足条件计数(持续性)"),
        ("sign_flip", _C8.sign_flip, "L3", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "窗口符号翻转计数(方向稳定)"),
        ("mean_reversion_z", _C8.mean_reversion_z, "L5", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "均值回归强度(-滚动 zscore)"),
        ("trend_strength", _C8.trend_strength, "L5", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "趋势强度(|斜率| 归一化)"),
        ("volume_pressure", _C8.volume_pressure, "L5", ("close", "volume", "n"), ("n",), (), {"n": (2, 250)}, "n", "量价压力(量比×涨跌幅)"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _c8_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    # ── C9 算子扩容（2026-08-11）：30 个高价值算子，与 feature_ops 双注册表共享（102→132） ──
    from ..feature_ops import C9Ops as _C9

    _c9_ops = [
        # (name, func, category, params, int_params, float_params, bounds, lookback, meaning)
        ("ts_pct_rank_window", _C9.ts_pct_rank_window, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "窗口内当前值百分位(相对位置)"),
        ("ts_zscore_rolling", _C9.ts_zscore_rolling, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "滚动zscore(均值回归强度)"),
        ("ts_skew", _C9.ts_skew, "L1", ("x", "n"), ("n",), (), {"n": (3, 250)}, "n", "滚动偏度(分布不对称)"),
        ("ts_kurt", _C9.ts_kurt, "L1", ("x", "n"), ("n",), (), {"n": (4, 250)}, "n", "滚动峰度(分布厚尾)"),
        ("ts_slope_pct", _C9.ts_slope_pct, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "回归斜率占价格比(无量纲趋势)"),
        ("ts_position_in_range", _C9.ts_position_in_range, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "窗口区间位置[0,1](现价相对高低)"),
        ("ts_down_ratio", _C9.ts_down_ratio, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "下跌天数占比(弱势强度)"),
        ("ts_up_ratio", _C9.ts_up_ratio, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "上涨天数占比(强势强度)"),
        ("ts_gain_loss_ratio", _C9.ts_gain_loss_ratio, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "涨跌幅比(平均涨/平均跌)"),
        ("ts_bias_ma", _C9.ts_bias_ma, "L1", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "乖离率(x/MA-1)"),
        ("ts_boll_position", _C9.ts_boll_position, "L1", ("x", "n", "k"), ("n",), ("k",), {"n": (2, 250), "k": (0.5, 5.0)}, "n", "布林带位置(标准化波动位置)"),
        ("ts_ma_diff", _C9.ts_ma_diff, "L1", ("x", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "双均线差(趋势强弱)"),
        ("ts_vol_shrink", _C9.ts_vol_shrink, "L1", ("x", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "波动收缩度(收敛/扩张)"),
        ("ts_tail_risk", _C9.ts_tail_risk, "L1", ("x", "n", "q"), ("n",), ("q",), {"n": (2, 250), "q": (0.01, 0.5)}, "n", "尾部风险(x-下分位)"),
        ("cs_winsor_flag", _C9.cs_winsor_flag, "L2", ("x", "n", "k"), ("n",), ("k",), {"n": (2, 250), "k": (1.0, 5.0)}, "n", "极端值标记(|z|>k)"),
        ("cs_demean_ratio", _C9.cs_demean_ratio, "L2", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "去均值比率(x/|mean|-1)"),
        ("cs_rank_norm", _C9.cs_rank_norm, "L2", ("x",), (), (), {}, None, "截面rank归一化[-1,1]"),
        ("cs_med_ratio", _C9.cs_med_ratio, "L2", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "与滚动中位数比(x/med-1)"),
        ("cs_extreme_gap", _C9.cs_extreme_gap, "L2", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "距极值缺口(远离上轨为正)"),
        ("where_between", _C9.where_between, "L3", ("x", "lo", "hi", "a", "b"), (), ("lo", "hi", "a", "b"), {"lo": (-10.0, 10.0), "hi": (-10.0, 10.0), "a": (-10.0, 10.0), "b": (-10.0, 10.0)}, None, "区间条件选值(lo≤x≤hi取a)"),
        ("cross_above", _C9.cross_above, "L3", ("x", "threshold"), (), ("threshold",), {"threshold": (-10.0, 10.0)}, None, "上穿阈值事件"),
        ("cross_below", _C9.cross_below, "L3", ("x", "threshold"), (), ("threshold",), {"threshold": (-10.0, 10.0)}, None, "下穿阈值事件"),
        ("momentum_break", _C9.momentum_break, "L3", ("x", "n", "k"), ("n",), ("k",), {"n": (2, 250), "k": (0.1, 5.0)}, "n", "动量突破(超自身std·k)"),
        ("vol_regime", _C9.vol_regime, "L5", ("x", "n"), ("n",), (), {"n": (3, 250)}, "n", "波动率制度(高/低/中)"),
        ("mean_reversion_signal", _C9.mean_reversion_signal, "L5", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "均值回归触发信号"),
        ("price_volume_div", _C9.price_volume_div, "L5", ("close", "volume", "n"), ("n",), (), {"n": (2, 250)}, "n", "价量背离占比"),
        ("liquidity_dryup", _C9.liquidity_dryup, "L5", ("volume", "n"), ("n",), (), {"n": (2, 250)}, "n", "流动性枯竭(量<均值0.5)"),
        ("self_corr", _C9.self_corr, "L5", ("x", "n"), ("n",), (), {"n": (3, 250)}, "n", "lag-1自相关(趋势持续)"),
        ("sign_entropy", _C9.sign_entropy, "L5", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "方向熵(无序度)"),
        ("reversal_strength", _C9.reversal_strength, "L5", ("x", "n"), ("n",), (), {"n": (2, 250)}, "n", "反转强度(-动量/波动)"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _c9_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    # ── D10 算子族扩容（2026-08-11 二期）：波动/风险族 55 算子，与 feature_ops 双注册表共享 ──
    from ..ops_library import D10Ops as _D10

    _d10_ops = [
        # (name, func, category, params, int_params, float_params, bounds, lookback, meaning)
        ("ts_realized_vol", _D10.ts_realized_vol, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "已实现波动率(滚动收益标准差)"),
        ("ts_ewma_vol", _D10.ts_ewma_vol, "L1", ("series", "span"), ("span",), (), {"span": (2, 250)}, "span", "指数加权波动率(EWMA半衰期)"),
        ("ts_parkinson", _D10.ts_parkinson, "L1", ("high", "low", "window"), ("window",), (), {"window": (2, 250)}, "window", "Parkinson高低价波动率"),
        ("ts_garman_klass", _D10.ts_garman_klass, "L1", ("open_p", "high", "low", "close", "window"), ("window",), (), {"window": (2, 250)}, "window", "Garman-Klass波动率(含跳空)"),
        ("ts_rogers_satchell", _D10.ts_rogers_satchell, "L1", ("open_p", "high", "low", "close", "window"), ("window",), (), {"window": (2, 250)}, "window", "Rogers-Satchell波动率(含漂移)"),
        ("ts_yang_zhang", _D10.ts_yang_zhang, "L1", ("open_p", "high", "low", "close", "window"), ("window",), (), {"window": (2, 250)}, "window", "Yang-Zhang波动率(隔夜+日内加权)"),
        ("ts_downside_vol", _D10.ts_downside_vol, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "下行波动率(仅负收益)"),
        ("ts_upside_vol", _D10.ts_upside_vol, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "上行波动率(仅正收益)"),
        ("ts_vol_of_vol", _D10.ts_vol_of_vol, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "波动率的波动(波动聚集)"),
        ("ts_bipower_var", _D10.ts_bipower_var, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "双幂变差(跳跃稳健波动)"),
        ("ts_range_vol", _D10.ts_range_vol, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 250)}, "window", "振幅波动率((H-L)/C)"),
        ("ts_harmonic_vol", _D10.ts_harmonic_vol, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "调和波动率(收益绝对值调和均值)"),
        ("ts_drawdown", _D10.ts_drawdown, "L1", ("series", "window"), ("window",), (), {"window": (0, 500)}, "window", "当前回撤(相对历史峰值)"),
        ("ts_max_drawdown", _D10.ts_max_drawdown, "L1", ("series", "window"), ("window",), (), {"window": (2, 500)}, "window", "窗口最大回撤(最深跌幅)"),
        ("ts_avg_drawdown", _D10.ts_avg_drawdown, "L1", ("series", "window"), ("window",), (), {"window": (2, 500)}, "window", "窗口平均回撤(回撤深度均值)"),
        ("ts_drawdown_duration", _D10.ts_drawdown_duration, "L1", ("series", "window"), ("window",), (), {"window": (2, 500)}, "window", "回撤持续期(回撤天数)"),
        ("ts_ulcer_index", _D10.ts_ulcer_index, "L1", ("series", "window"), ("window",), (), {"window": (2, 500)}, "window", "溃疡指数(回撤平方均值开方)"),
        ("ts_var_95", _D10.ts_var_95, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "95% VaR(5%分位收益)"),
        ("ts_var_99", _D10.ts_var_99, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "99% VaR(1%分位收益)"),
        ("ts_cvar_95", _D10.ts_cvar_95, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "95% CVaR(条件尾部损失)"),
        ("ts_cvar_99", _D10.ts_cvar_99, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "99% CVaR(条件尾部损失)"),
        ("ts_semi_std", _D10.ts_semi_std, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "半标准差(下行半方差)"),
        ("ts_lpm_2", _D10.ts_lpm_2, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "二阶下偏矩(负收益平方均值)"),
        ("ts_hpm_2", _D10.ts_hpm_2, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "二阶上偏矩(正收益平方均值)"),
        ("ts_gain_std", _D10.ts_gain_std, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "正收益波动(正收益段标准差)"),
        ("ts_loss_std", _D10.ts_loss_std, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "负收益波动(负收益段标准差)"),
        ("ts_sharpe_ratio", _D10.ts_sharpe_ratio, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "滚动夏普比率(收益均值/标准差)"),
        ("ts_sortino_ratio", _D10.ts_sortino_ratio, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "Sortino比率(收益均值/下行偏差)"),
        ("ts_calmar_ratio", _D10.ts_calmar_ratio, "L1", ("series", "window"), ("window",), (), {"window": (20, 500)}, "window", "Calmar比率(年化收益/最大回撤)"),
        ("ts_profit_factor", _D10.ts_profit_factor, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "盈亏比(正收益和/负损失和)"),
        ("ts_omega_ratio", _D10.ts_omega_ratio, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "Omega比率(收益加权概率比)"),
        ("ts_kelly_fraction", _D10.ts_kelly_fraction, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "Kelly比例(胜率-败率)"),
        ("ts_worst_day", _D10.ts_worst_day, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "窗口最差日收益"),
        ("ts_best_day", _D10.ts_best_day, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "窗口最佳日收益"),
        ("ts_win_rate", _D10.ts_win_rate, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "胜率(正收益占比)"),
        ("ts_loss_rate", _D10.ts_loss_rate, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "败率(负收益占比)"),
        ("ts_avg_gain", _D10.ts_avg_gain, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "平均盈利(正收益均值)"),
        ("ts_avg_loss", _D10.ts_avg_loss, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "平均亏损(负收益均值)"),
        ("ts_expectancy", _D10.ts_expectancy, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "期望收益(窗口收益均值)"),
        ("ts_recovery_factor", _D10.ts_recovery_factor, "L1", ("series", "window"), ("window",), (), {"window": (20, 500)}, "window", "恢复因子(收益均值/最大回撤)"),
        ("ts_risk_return_ratio", _D10.ts_risk_return_ratio, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "风险收益比(收益均值/收益波动)"),
        ("ts_downside_deviation", _D10.ts_downside_deviation, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "下行偏差(目标0半方差开方)"),
        ("ts_vol_ratio_ewma", _D10.ts_vol_ratio_ewma, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "EWMA波动比(短/长指数波动)"),
        ("ts_realized_vol_pct", _D10.ts_realized_vol_pct, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "波动率百分比(波动/价格水平)"),
        ("ts_vol_zscore", _D10.ts_vol_zscore, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "波动率zscore(相对长期波动偏离)"),
        ("ts_vol_percentile", _D10.ts_vol_percentile, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "波动率分位(当前波动历史百分位)"),
        ("ts_garch_proxy", _D10.ts_garch_proxy, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "波动聚集代理(|r|滚动均值)"),
        ("ts_vol_asymmetry", _D10.ts_vol_asymmetry, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "波动不对称(下行-上行波动)"),
        ("ts_leverage_effect", _D10.ts_leverage_effect, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "杠杆效应(收益与后续波动负相关)"),
        ("ts_baseline_vol", _D10.ts_baseline_vol, "L1", ("series", "window"), ("window",), (), {"window": (20, 500)}, "window", "基准波动(长窗口已实现波动)"),
        ("ts_long_term_vol", _D10.ts_long_term_vol, "L1", ("series", "window"), ("window",), (), {"window": (20, 500)}, "window", "长期波动(120窗口)"),
        ("ts_short_term_vol", _D10.ts_short_term_vol, "L1", ("series", "window"), ("window",), (), {"window": (2, 60)}, "window", "短期波动(10窗口)"),
        ("ts_vol_term_structure", _D10.ts_vol_term_structure, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (20, 500)}, "long", "波动期限结构(短/长波动比)"),
        ("ts_max_loss_ratio", _D10.ts_max_loss_ratio, "L1", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "最大损失占比(最差日/总波动)"),
        ("ts_beta_vol", _D10.ts_beta_vol, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "波动率beta(短波动对长波动斜率)"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _d10_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    # ── D11 算子族扩容（2026-08-11 二期）：技术指标族 60 算子，与 feature_ops 双注册表共享 ──
    from ..ops_library import D11Ops as _D11

    _d11_ops = [
        ("ts_ema_fast_slow", _D11.ts_ema_fast_slow, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "快慢EMA差(趋势强度)"),
        ("ts_macd", _D11.ts_macd, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "MACD线(快减慢EMA)"),
        ("ts_macd_signal", _D11.ts_macd_signal, "L1", ("series", "short", "long", "signal"), ("short", "long", "signal"), (), {"short": (2, 60), "long": (5, 250), "signal": (2, 60)}, "long", "MACD信号线"),
        ("ts_macd_hist", _D11.ts_macd_hist, "L1", ("series", "short", "long", "signal"), ("short", "long", "signal"), (), {"short": (2, 60), "long": (5, 250), "signal": (2, 60)}, "long", "MACD柱(动能)"),
        ("ts_dema", _D11.ts_dema, "L1", ("series", "span"), ("span",), (), {"span": (2, 250)}, "span", "双重指数平均"),
        ("ts_tema", _D11.ts_tema, "L1", ("series", "span"), ("span",), (), {"span": (2, 250)}, "span", "三重指数平均"),
        ("ts_kama", _D11.ts_kama, "L1", ("series", "window", "fast", "slow"), ("window", "fast", "slow"), (), {"window": (5, 250), "fast": (2, 10), "slow": (10, 60)}, "window", "自适应均线KAMA"),
        ("ts_vwap", _D11.ts_vwap, "L1", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "成交量加权均价"),
        ("ts_rsi", _D11.ts_rsi, "L1", ("series", "window"), ("window",), (), {"window": (2, 100)}, "window", "RSI相对强弱(超买超卖)"),
        ("ts_rsi_smoothed", _D11.ts_rsi_smoothed, "L1", ("series", "window"), ("window",), (), {"window": (2, 100)}, "window", "平滑RSI(Wilder)"),
        ("ts_stoch_k", _D11.ts_stoch_k, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "随机指标%K"),
        ("ts_stoch_d", _D11.ts_stoch_d, "L1", ("high", "low", "close", "window", "smooth"), ("window", "smooth"), (), {"window": (2, 100), "smooth": (2, 30)}, "window", "随机指标%D"),
        ("ts_williams_r", _D11.ts_williams_r, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "威廉%R"),
        ("ts_cci", _D11.ts_cci, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "CCI顺势指标"),
        ("ts_trix", _D11.ts_trix, "L1", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "TRIX三重指数平均变化率"),
        ("ts_ppo", _D11.ts_ppo, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "PPO百分比价格振荡"),
        ("ts_tsi", _D11.ts_tsi, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "TSI真实强弱指数"),
        ("ts_awesome", _D11.ts_awesome, "L1", ("high", "low", "short", "long"), ("short", "long"), (), {"short": (2, 30), "long": (5, 100)}, "long", "AO动量振荡器"),
        ("ts_ultimate_osc", _D11.ts_ultimate_osc, "L1", ("high", "low", "close", "short", "mid", "long"), ("short", "mid", "long"), (), {"short": (3, 20), "mid": (5, 40), "long": (10, 100)}, "long", "UO终极振荡器"),
        ("ts_roc", _D11.ts_roc, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "ROC变动率"),
        ("ts_momentum_index", _D11.ts_momentum_index, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "动量指标"),
        ("ts_rate_of_change_ma", _D11.ts_rate_of_change_ma, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "ROC均线"),
        ("ts_fisher_transform", _D11.ts_fisher_transform, "L1", ("series", "window"), ("window",), (), {"window": (2, 100)}, "window", "Fisher变换"),
        ("ts_stoch_rsi", _D11.ts_stoch_rsi, "L1", ("series", "window"), ("window",), (), {"window": (2, 100)}, "window", "随机RSI"),
        ("ts_rvi", _D11.ts_rvi, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "RVI相对活力指数"),
        ("ts_obv", _D11.ts_obv, "L1", ("close", "volume"), (), (), {}, None, "OBV能量潮"),
        ("ts_obv_ma", _D11.ts_obv_ma, "L1", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "OBV均线"),
        ("ts_mfi", _D11.ts_mfi, "L1", ("high", "low", "close", "volume", "window"), ("window",), (), {"window": (2, 100)}, "window", "MFI资金流量指数"),
        ("ts_adi", _D11.ts_adi, "L1", ("high", "low", "close", "volume"), (), (), {}, None, "ADI累积派发线"),
        ("ts_cmf", _D11.ts_cmf, "L1", ("high", "low", "close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "CMF蔡金资金流"),
        ("ts_chaikin_vol", _D11.ts_chaikin_vol, "L1", ("high", "low", "window"), ("window",), (), {"window": (2, 100)}, "window", "蔡金波动率"),
        ("ts_chaikin_osc", _D11.ts_chaikin_osc, "L1", ("high", "low", "close", "volume", "short", "long"), ("short", "long"), (), {"short": (2, 30), "long": (5, 100)}, "long", "蔡金振荡"),
        ("ts_volume_oscillator", _D11.ts_volume_oscillator, "L1", ("volume", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "量振荡器"),
        ("ts_market_facilitation", _D11.ts_market_facilitation, "L1", ("high", "low", "volume"), (), (), {}, None, "市场便利指数"),
        ("ts_atr", _D11.ts_atr, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "ATR平均真实波幅"),
        ("ts_natr", _D11.ts_natr, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "归一化ATR"),
        ("ts_bb_width", _D11.ts_bb_width, "L1", ("series", "window", "k"), ("window",), ("k",), {"window": (2, 250), "k": (0.5, 5.0)}, "window", "布林带宽度"),
        ("ts_bb_percent_b", _D11.ts_bb_percent_b, "L1", ("series", "window", "k"), ("window",), ("k",), {"window": (2, 250), "k": (0.5, 5.0)}, "window", "布林%B"),
        ("ts_bb_bandwidth", _D11.ts_bb_bandwidth, "L1", ("series", "window", "k"), ("window",), ("k",), {"window": (2, 250), "k": (0.5, 5.0)}, "window", "布林带宽"),
        ("ts_price_channel", _D11.ts_price_channel, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "价格通道位置"),
        ("ts_aroon_up", _D11.ts_aroon_up, "L1", ("series", "window"), ("window",), (), {"window": (5, 100)}, "window", "Aroon上升"),
        ("ts_aroon_down", _D11.ts_aroon_down, "L1", ("series", "window"), ("window",), (), {"window": (5, 100)}, "window", "Aroon下降"),
        ("ts_aroon_osc", _D11.ts_aroon_osc, "L1", ("series", "window"), ("window",), (), {"window": (5, 100)}, "window", "Aroon振荡"),
        ("ts_dpo", _D11.ts_dpo, "L1", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "DPO去趋势振荡"),
        ("ts_kst", _D11.ts_kst, "L1", ("series", "window"), ("window",), (), {"window": (10, 250)}, "window", "KST综合振荡"),
        ("ts_kst_signal", _D11.ts_kst_signal, "L1", ("series", "window"), ("window",), (), {"window": (10, 250)}, "window", "KST信号线"),
        ("ts_mass_index", _D11.ts_mass_index, "L1", ("high", "low", "window"), ("window",), (), {"window": (2, 60)}, "window", "质量指数"),
        ("ts_vortex_pos", _D11.ts_vortex_pos, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "Vortex正向"),
        ("ts_vortex_neg", _D11.ts_vortex_neg, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "Vortex负向"),
        ("ts_vortex_ratio", _D11.ts_vortex_ratio, "L1", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "Vortex比率"),
        ("ts_ichimoku_conv", _D11.ts_ichimoku_conv, "L1", ("high", "low", "window"), ("window",), (), {"window": (2, 100)}, "window", "云图转换线"),
        ("ts_ichimoku_base", _D11.ts_ichimoku_base, "L1", ("high", "low", "window"), ("window",), (), {"window": (2, 100)}, "window", "云图基准线"),
        ("ts_ichimoku_span_a", _D11.ts_ichimoku_span_a, "L1", ("high", "low", "window"), ("window",), (), {"window": (5, 100)}, "window", "云图A"),
        ("ts_ichimoku_span_b", _D11.ts_ichimoku_span_b, "L1", ("high", "low", "window"), ("window",), (), {"window": (10, 250)}, "window", "云图B"),
        ("ts_sma_cross_signal", _D11.ts_sma_cross_signal, "L3", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "双SMA交叉信号"),
        ("ts_ema_cross_signal", _D11.ts_ema_cross_signal, "L3", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "双EMA交叉信号"),
        ("ts_parabolic_sar", _D11.ts_parabolic_sar, "L1", ("high", "low", "step", "max_step"), (), ("step", "max_step"), {"step": (0.001, 0.1), "max_step": (0.05, 0.5)}, None, "抛物线SAR"),
        ("ts_price_oscillator", _D11.ts_price_oscillator, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "价格振荡器"),
        ("ts_trend_score", _D11.ts_trend_score, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "趋势得分"),
        ("ts_cycle_score", _D11.ts_cycle_score, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "周期得分"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _d11_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    # ── D12 算子族扩容（2026-08-11 二期）：动量/趋势族 55 算子，与 feature_ops 双注册表共享 ──
    from ..ops_library import D12Ops as _D12

    _d12_ops = [
        ("ts_velocity", _D12.ts_velocity, "L1", ("series",), (), (), {}, None, "速度(一阶差分)"),
        ("ts_acceleration", _D12.ts_acceleration, "L1", ("series", "window"), ("window",), (), {"window": (1, 60)}, "window", "加速度(速度平滑差分)"),
        ("ts_jerk", _D12.ts_jerk, "L1", ("series", "window"), ("window",), (), {"window": (1, 60)}, "window", "急动度(三阶差分)"),
        ("ts_momentum_ratio", _D12.ts_momentum_ratio, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "动量比(x/x_{t-n})"),
        ("ts_momentum_breakout_ratio", _D12.ts_momentum_breakout_ratio, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "动量突破比"),
        ("ts_ewm_momentum", _D12.ts_ewm_momentum, "L1", ("series", "span"), ("span",), (), {"span": (2, 250)}, "span", "指数动量"),
        ("ts_momentum_vol_adj", _D12.ts_momentum_vol_adj, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "波动调整动量"),
        ("ts_roc_zscore", _D12.ts_roc_zscore, "L1", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "ROC标准化"),
        ("ts_velocity_zscore", _D12.ts_velocity_zscore, "L1", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "速度zscore"),
        ("ts_trend_angle", _D12.ts_trend_angle, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "趋势角度"),
        ("ts_linear_trend_score", _D12.ts_linear_trend_score, "L1", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "线性趋势R²"),
        ("ts_trend_strength_pct", _D12.ts_trend_strength_pct, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "趋势强度百分比"),
        ("ts_above_ma_ratio", _D12.ts_above_ma_ratio, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "MA上方占比"),
        ("ts_below_ma_ratio", _D12.ts_below_ma_ratio, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "MA下方占比"),
        ("ts_slope_change", _D12.ts_slope_change, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "斜率变化"),
        ("ts_curvature", _D12.ts_curvature, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "曲率"),
        ("ts_momentum_consistency", _D12.ts_momentum_consistency, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "动量一致性"),
        ("ts_trend_persistence", _D12.ts_trend_persistence, "L1", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "趋势持续"),
        ("ts_reversal_signal_z", _D12.ts_reversal_signal_z, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "反转zscore信号"),
        ("ts_trend_strength_ma", _D12.ts_trend_strength_ma, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "均线趋势强度"),
        ("ts_relative_strength", _D12.ts_relative_strength, "L1", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "相对强度"),
        ("ts_cross_momentum", _D12.ts_cross_momentum, "L1", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "交叉动量"),
        ("ts_momentum_regime", _D12.ts_momentum_regime, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "动量制度评分"),
        ("ts_trend_filter", _D12.ts_trend_filter, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "趋势过滤器"),
        ("ts_higher_high_count", _D12.ts_higher_high_count, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "新高计数"),
        ("ts_lower_low_count", _D12.ts_lower_low_count, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "新低计数"),
        ("ts_new_high_ratio", _D12.ts_new_high_ratio, "L3", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "新高占比"),
        ("ts_new_low_ratio", _D12.ts_new_low_ratio, "L3", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "新低占比"),
        ("ts_range_expansion", _D12.ts_range_expansion, "L1", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "区间扩张"),
        ("ts_breakout_distance", _D12.ts_breakout_distance, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "距突破位距离"),
        ("ts_pullback_depth", _D12.ts_pullback_depth, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "回踩深度"),
        ("ts_continuation_signal", _D12.ts_continuation_signal, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "延续信号"),
        ("ts_exhaustion_signal", _D12.ts_exhaustion_signal, "L3", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "衰竭信号"),
        ("ts_donchian_break", _D12.ts_donchian_break, "L3", ("high", "low", "window"), ("window",), (), {"window": (2, 250)}, "window", "唐奇安突破"),
        ("ts_donchian_mid", _D12.ts_donchian_mid, "L1", ("high", "low", "window"), ("window",), (), {"window": (2, 250)}, "window", "唐奇安中轨"),
        ("ts_supertrend_signal", _D12.ts_supertrend_signal, "L3", ("series", "high", "low", "window", "mult"), ("window",), ("mult",), {"window": (2, 100), "mult": (1.0, 5.0)}, "window", "超级趋势信号"),
        ("ts_psar_position", _D12.ts_psar_position, "L3", ("high", "low", "step", "max_step"), (), ("step", "max_step"), {"step": (0.001, 0.1), "max_step": (0.05, 0.5)}, None, "SAR位置"),
        ("ts_uptrend_flag", _D12.ts_uptrend_flag, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "上升趋势标志"),
        ("ts_downtrend_flag", _D12.ts_downtrend_flag, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "下降趋势标志"),
        ("ts_sideways_flag", _D12.ts_sideways_flag, "L3", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "横盘标志"),
        ("ts_trend_direction_strength", _D12.ts_trend_direction_strength, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "趋势方向强度"),
        ("ts_multi_tf_trend", _D12.ts_multi_tf_trend, "L5", ("series", "short", "mid", "long"), ("short", "mid", "long"), (), {"short": (2, 30), "mid": (10, 80), "long": (30, 250)}, "long", "多周期趋势一致"),
        ("ts_fractal_up", _D12.ts_fractal_up, "L3", ("high", "window"), ("window",), (), {"window": (2, 20)}, None, "分形向上"),
        ("ts_fractal_down", _D12.ts_fractal_down, "L3", ("low", "window"), ("window",), (), {"window": (2, 20)}, None, "分形向下"),
        ("ts_support_proximity", _D12.ts_support_proximity, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "支撑接近度"),
        ("ts_resistance_proximity", _D12.ts_resistance_proximity, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "压力接近度"),
        ("ts_breakout_pullback_signal", _D12.ts_breakout_pullback_signal, "L3", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "突破回踩信号"),
        ("ts_directional_up", _D12.ts_directional_up, "L1", ("high", "low", "window"), ("window",), (), {"window": (2, 100)}, "window", "+DM方向运动"),
        ("ts_directional_down", _D12.ts_directional_down, "L1", ("high", "low", "window"), ("window",), (), {"window": (2, 100)}, "window", "-DM方向运动"),
        ("ts_adx_pos", _D12.ts_adx_pos, "L5", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "+DI正向指标"),
        ("ts_adx_neg", _D12.ts_adx_neg, "L5", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "-DI负向指标"),
        ("ts_adx", _D12.ts_adx, "L5", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 100)}, "window", "ADX趋势强度"),
        ("ts_trend_vol_ratio", _D12.ts_trend_vol_ratio, "L5", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "趋势波动比"),
        ("ts_trend_entropy", _D12.ts_trend_entropy, "L5", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "趋势熵"),
        ("ts_up_down_strength", _D12.ts_up_down_strength, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "涨跌强度差"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _d12_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    # ── D13 算子族扩容（2026-08-11 二期）：截面/排名族 45 算子，与 feature_ops 双注册表共享 ──
    from ..ops_library import D13Ops as _D13

    _d13_ops = [
        ("cs_rank_pct", _D13.cs_rank_pct, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "滚动排名百分位"),
        ("cs_percent_rank", _D13.cs_percent_rank, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "百分比排名"),
        ("cs_rank_demean", _D13.cs_rank_demean, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "排名去均值"),
        ("cs_inverse_rank", _D13.cs_inverse_rank, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "逆排名"),
        ("cs_signed_rank", _D13.cs_signed_rank, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "带符号排名"),
        ("cs_rank_ratio", _D13.cs_rank_ratio, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "排名比"),
        ("cs_cross_rank_diff", _D13.cs_cross_rank_diff, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "截面排名差"),
        ("cs_rank_momentum", _D13.cs_rank_momentum, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "排名动量"),
        ("cs_rank_volatility", _D13.cs_rank_volatility, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "排名波动"),
        ("cs_rank_stability", _D13.cs_rank_stability, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "排名稳定性"),
        ("cs_ewm_rank", _D13.cs_ewm_rank, "L2", ("series", "span"), ("span",), (), {"span": (2, 250)}, "span", "指数排名"),
        ("cs_smooth_rank", _D13.cs_smooth_rank, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "平滑排名"),
        ("cs_robust_rank", _D13.cs_robust_rank, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "稳健排名(MAD)"),
        ("cs_quantile_rank", _D13.cs_quantile_rank, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "分位排名"),
        ("cs_cross_section_bucket", _D13.cs_cross_section_bucket, "L2", ("series", "window", "n_buckets"), ("window", "n_buckets"), (), {"window": (2, 250), "n_buckets": (2, 20)}, "window", "截面分桶"),
        ("cs_zscore_med", _D13.cs_zscore_med, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "中位数zscore"),
        ("cs_mad_zscore", _D13.cs_mad_zscore, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "MAD稳健zscore"),
        ("cs_winsor_z", _D13.cs_winsor_z, "L2", ("series", "window", "k"), ("window",), ("k",), {"window": (2, 250), "k": (1.0, 6.0)}, "window", "Winsorize后zscore"),
        ("cs_normalize_01", _D13.cs_normalize_01, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "0-1归一化"),
        ("cs_minmax_norm", _D13.cs_minmax_norm, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "Min-Max归一化"),
        ("cs_softmax_weight", _D13.cs_softmax_weight, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "Softmax权重"),
        ("cs_distance_median", _D13.cs_distance_median, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "距中位数距离"),
        ("cs_distance_mean", _D13.cs_distance_mean, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "距均值距离"),
        ("cs_relative_to_max", _D13.cs_relative_to_max, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "相对最大值"),
        ("cs_relative_to_min", _D13.cs_relative_to_min, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "相对最小值"),
        ("cs_max_share", _D13.cs_max_share, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "占最大比例"),
        ("cs_trim_mean_diff", _D13.cs_trim_mean_diff, "L2", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "与修剪均值差"),
        ("cs_market_relative", _D13.cs_market_relative, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "相对市场超额"),
        ("cs_dispersion", _D13.cs_dispersion, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "截面离散度"),
        ("cs_coefficient_variation", _D13.cs_coefficient_variation, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "变异系数"),
        ("cs_gini_score", _D13.cs_gini_score, "L2", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "基尼系数"),
        ("cs_herfindahl", _D13.cs_herfindahl, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "赫芬达尔指数"),
        ("cs_concentration", _D13.cs_concentration, "L2", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "集中度"),
        ("cs_top_bottom_spread", _D13.cs_top_bottom_spread, "L2", ("series", "window"), ("window",), (), {"window": (10, 250)}, "window", "高低分位差"),
        ("cs_winner_loser_gap", _D13.cs_winner_loser_gap, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "赢家输家差"),
        ("cs_median_gap", _D13.cs_median_gap, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "中位差"),
        ("cs_extreme_strength", _D13.cs_extreme_strength, "L2", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "极端强度"),
        ("cs_outlier_flag", _D13.cs_outlier_flag, "L2", ("series", "window", "k"), ("window",), ("k",), {"window": (2, 250), "k": (1.0, 6.0)}, "window", "异常点标记"),
        ("cs_tail_weight", _D13.cs_tail_weight, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "尾部权重"),
        ("cs_skewness_score", _D13.cs_skewness_score, "L2", ("series", "window"), ("window",), (), {"window": (3, 250)}, "window", "偏度得分"),
        ("cs_kurtosis_score", _D13.cs_kurtosis_score, "L2", ("series", "window"), ("window",), (), {"window": (4, 250)}, "window", "峰度得分"),
        ("cs_extreme_skew", _D13.cs_extreme_skew, "L2", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "极端偏度"),
        ("cs_breadth_position", _D13.cs_breadth_position, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "广度位置"),
        ("cs_entropy_rank", _D13.cs_entropy_rank, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "排名熵"),
        ("cs_outlier_ratio", _D13.cs_outlier_ratio, "L2", ("series", "window", "k"), ("window",), ("k",), {"window": (2, 250), "k": (1.0, 6.0)}, "window", "异常占比"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _d13_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    # ── D14 算子族扩容（2026-08-11 二期）：条件/事件族 40 算子，与 feature_ops 双注册表共享 ──
    from ..ops_library import D14Ops as _D14

    _d14_ops = [
        ("ts_cross_threshold_up", _D14.ts_cross_threshold_up, "L3", ("series", "threshold"), (), ("threshold",), {"threshold": (-10.0, 10.0)}, None, "上穿阈值事件"),
        ("ts_cross_threshold_down", _D14.ts_cross_threshold_down, "L3", ("series", "threshold"), (), ("threshold",), {"threshold": (-10.0, 10.0)}, None, "下穿阈值事件"),
        ("ts_threshold_band", _D14.ts_threshold_band, "L3", ("series", "lo", "hi"), (), ("lo", "hi"), {"lo": (-10.0, 10.0), "hi": (-10.0, 10.0)}, None, "阈值带内"),
        ("ts_range_condition", _D14.ts_range_condition, "L3", ("series", "lo", "hi"), (), ("lo", "hi"), {"lo": (-10.0, 10.0), "hi": (-10.0, 10.0)}, None, "区间内状态"),
        ("ts_condition_count", _D14.ts_condition_count, "L3", ("series", "threshold", "window"), ("window",), ("threshold",), {"threshold": (-10.0, 10.0), "window": (2, 250)}, "window", "条件满足计数"),
        ("ts_condition_ratio", _D14.ts_condition_ratio, "L3", ("series", "threshold", "window"), ("window",), ("threshold",), {"threshold": (-10.0, 10.0), "window": (2, 250)}, "window", "条件占比"),
        ("ts_consecutive_above", _D14.ts_consecutive_above, "L3", ("series", "threshold"), (), ("threshold",), {"threshold": (-10.0, 10.0)}, None, "连续高于阈值"),
        ("ts_consecutive_below", _D14.ts_consecutive_below, "L3", ("series", "threshold"), (), ("threshold",), {"threshold": (-10.0, 10.0)}, None, "连续低于阈值"),
        ("ts_consecutive_increase", _D14.ts_consecutive_increase, "L3", ("series",), (), (), {}, None, "连续上涨天数"),
        ("ts_consecutive_decrease", _D14.ts_consecutive_decrease, "L3", ("series",), (), (), {}, None, "连续下跌天数"),
        ("ts_consecutive_same_sign", _D14.ts_consecutive_same_sign, "L3", ("series",), (), (), {}, None, "连续同号天数"),
        ("ts_condition_change", _D14.ts_condition_change, "L3", ("series", "threshold"), (), ("threshold",), {"threshold": (-10.0, 10.0)}, None, "条件切换事件"),
        ("ts_condition_switch_rate", _D14.ts_condition_switch_rate, "L3", ("series", "threshold", "window"), ("window",), ("threshold",), {"threshold": (-10.0, 10.0), "window": (2, 250)}, "window", "条件切换率"),
        ("ts_state_duration", _D14.ts_state_duration, "L3", ("series", "threshold"), (), ("threshold",), {"threshold": (-10.0, 10.0)}, None, "状态持续期"),
        ("ts_state_age", _D14.ts_state_age, "L3", ("series", "threshold"), (), ("threshold",), {"threshold": (-10.0, 10.0)}, None, "状态年龄"),
        ("ts_breakout_event", _D14.ts_breakout_event, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "突破事件"),
        ("ts_breakdown_event", _D14.ts_breakdown_event, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "跌破事件"),
        ("ts_cross_ma_event", _D14.ts_cross_ma_event, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "穿均线事件"),
        ("ts_golden_cross_event", _D14.ts_golden_cross_event, "L3", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "金叉事件"),
        ("ts_death_cross_event", _D14.ts_death_cross_event, "L3", ("series", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "死叉事件"),
        ("ts_turning_point", _D14.ts_turning_point, "L3", ("series", "window"), ("window",), (), {"window": (2, 30)}, "window", "转折点"),
        ("ts_zigzag_direction", _D14.ts_zigzag_direction, "L3", ("series", "window"), ("window",), (), {"window": (2, 30)}, "window", "之字形方向"),
        ("ts_event_density", _D14.ts_event_density, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "事件密度"),
        ("ts_event_count_n", _D14.ts_event_count_n, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "n期事件数"),
        ("ts_signal_persistence", _D14.ts_signal_persistence, "L3", ("series", "threshold", "window"), ("window",), ("threshold",), {"threshold": (-10.0, 10.0), "window": (2, 250)}, "window", "信号持续"),
        ("ts_signal_decay", _D14.ts_signal_decay, "L3", ("series", "threshold", "window"), ("window",), ("threshold",), {"threshold": (-10.0, 10.0), "window": (2, 250)}, "window", "信号衰减"),
        ("ts_condition_entropy", _D14.ts_condition_entropy, "L3", ("series", "threshold", "window"), ("window",), ("threshold",), {"threshold": (-10.0, 10.0), "window": (2, 250)}, "window", "条件熵"),
        ("ts_pattern_continuation", _D14.ts_pattern_continuation, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "形态延续"),
        ("ts_pattern_reversal", _D14.ts_pattern_reversal, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "形态反转"),
        ("ts_momentum_filter", _D14.ts_momentum_filter, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "动量过滤"),
        ("ts_volatility_filter", _D14.ts_volatility_filter, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "波动过滤"),
        ("ts_liquidity_filter", _D14.ts_liquidity_filter, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "流动性过滤"),
        ("ts_trend_condition", _D14.ts_trend_condition, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "趋势条件"),
        ("ts_breakout_condition", _D14.ts_breakout_condition, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "突破条件"),
        ("ts_reversal_condition", _D14.ts_reversal_condition, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "反转条件"),
        ("ts_level_test", _D14.ts_level_test, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "水平测试"),
        ("ts_support_break", _D14.ts_support_break, "L3", ("series", "window"), ("window",), (), {"window": (5, 500)}, "window", "支撑跌破"),
        ("ts_resistance_break", _D14.ts_resistance_break, "L3", ("series", "window"), ("window",), (), {"window": (5, 500)}, "window", "压力突破"),
        ("ts_condition_combo", _D14.ts_condition_combo, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "条件组合"),
        ("ts_breakout_strength", _D14.ts_breakout_strength, "L3", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "突破强度"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _d14_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    # ── D15 算子族扩容（2026-08-11 二期）：组合/跨序列族 50 算子，与 feature_ops 双注册表共享 ──
    from ..ops_library import D15Ops as _D15

    _d15_ops = [
        ("cs_ratio", _D15.cs_ratio, "L4", ("x", "y"), (), (), {}, None, "比率x/y"),
        ("cs_diff", _D15.cs_diff, "L4", ("x", "y"), (), (), {}, None, "差x-y"),
        ("cs_sum", _D15.cs_sum, "L4", ("x", "y"), (), (), {}, None, "和x+y"),
        ("cs_product", _D15.cs_product, "L4", ("x", "y"), (), (), {}, None, "积x·y"),
        ("cs_min", _D15.cs_min, "L4", ("x", "y"), (), (), {}, None, "两序列小者"),
        ("cs_max", _D15.cs_max, "L4", ("x", "y"), (), (), {}, None, "两序列大者"),
        ("cs_spread", _D15.cs_spread, "L4", ("x", "y"), (), (), {}, None, "价差"),
        ("cs_return_spread", _D15.cs_return_spread, "L4", ("x", "y"), (), (), {}, None, "收益差"),
        ("cs_relative_ratio", _D15.cs_relative_ratio, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "相对比率"),
        ("cs_log_ratio", _D15.cs_log_ratio, "L4", ("x", "y"), (), (), {}, None, "对数比率"),
        ("cs_pct_diff", _D15.cs_pct_diff, "L4", ("x", "y"), (), (), {}, None, "百分比差"),
        ("cs_weighted_average", _D15.cs_weighted_average, "L4", ("x", "y", "w"), (), ("w",), {"w": (0.0, 1.0)}, None, "加权平均"),
        ("cs_composite_score", _D15.cs_composite_score, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "复合得分"),
        ("cs_normalized_ratio", _D15.cs_normalized_ratio, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "归一化比率"),
        ("cs_smoothed_ratio", _D15.cs_smoothed_ratio, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "平滑比率"),
        ("cs_exponential_ratio", _D15.cs_exponential_ratio, "L4", ("x", "y", "span"), ("span",), (), {"span": (2, 250)}, "span", "指数比率"),
        ("cs_ratio_ma", _D15.cs_ratio_ma, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "比率均线偏离"),
        ("cs_ratio_zscore", _D15.cs_ratio_zscore, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "比率zscore"),
        ("cs_relative_strength_ratio", _D15.cs_relative_strength_ratio, "L4", ("x", "y", "window"), ("window",), (), {"window": (5, 250)}, "window", "相对强弱比率"),
        ("ts_pair_corr", _D15.ts_pair_corr, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "滚动相关"),
        ("ts_cov", _D15.ts_cov, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "滚动协方差"),
        ("ts_beta", _D15.ts_beta, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "滚动beta"),
        ("ts_alpha", _D15.ts_alpha, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "滚动alpha"),
        ("ts_lead_lag_corr", _D15.ts_lead_lag_corr, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "前导滞后相关"),
        ("ts_cross_corr_lag1", _D15.ts_cross_corr_lag1, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "滞后互相关"),
        ("ts_granger_proxy", _D15.ts_granger_proxy, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "格兰杰代理"),
        ("ts_hedge_ratio", _D15.ts_hedge_ratio, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "对冲比率"),
        ("ts_cointegration_proxy", _D15.ts_cointegration_proxy, "L4", ("x", "y", "window"), ("window",), (), {"window": (10, 500)}, "window", "协整代理"),
        ("ts_spread_zscore", _D15.ts_spread_zscore, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "价差zscore"),
        ("ts_spread_band", _D15.ts_spread_band, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "价差带位置"),
        ("ts_pair_divergence", _D15.ts_pair_divergence, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "配对背离"),
        ("ts_pair_convergence", _D15.ts_pair_convergence, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "配对收敛"),
        ("ts_convergence_rate", _D15.ts_convergence_rate, "L4", ("x", "y", "window"), ("window",), (), {"window": (2, 250)}, "window", "收敛速度"),
        ("ts_pair_trade_signal", _D15.ts_pair_trade_signal, "L4", ("x", "y", "window", "k"), ("window",), ("k",), {"window": (2, 250), "k": (0.5, 5.0)}, "window", "配对交易信号"),
        ("ts_price_gap", _D15.ts_price_gap, "L4", ("open_p", "close"), (), (), {}, None, "价格缺口"),
        ("ts_overnight_return", _D15.ts_overnight_return, "L4", ("open_p", "close"), (), (), {}, None, "隔夜收益"),
        ("ts_intraday_return", _D15.ts_intraday_return, "L4", ("open_p", "close"), (), (), {}, None, "日内收益"),
        ("ts_open_close_diff", _D15.ts_open_close_diff, "L4", ("open_p", "close"), (), (), {}, None, "开盘收盘差"),
        ("ts_high_low_ratio", _D15.ts_high_low_ratio, "L4", ("high", "low"), (), (), {}, None, "高低比"),
        ("ts_range_ratio", _D15.ts_range_ratio, "L4", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 250)}, "window", "区间比"),
        ("ts_basis", _D15.ts_basis, "L4", ("spot", "future"), (), (), {}, None, "基差"),
        ("ts_basis_ratio", _D15.ts_basis_ratio, "L4", ("spot", "future"), (), (), {}, None, "基差率"),
        ("ts_term_spread", _D15.ts_term_spread, "L4", ("near", "far"), (), (), {}, None, "期限价差"),
        ("ts_roll_yield", _D15.ts_roll_yield, "L4", ("near", "far", "window"), ("window",), (), {"window": (1, 60)}, "window", "展期收益"),
        ("ts_volume_price_corr", _D15.ts_volume_price_corr, "L4", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量价相关"),
        ("ts_volume_ratio_vs_avg", _D15.ts_volume_ratio_vs_avg, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量比"),
        ("ts_volume_breakout", _D15.ts_volume_breakout, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量突破"),
        ("ts_volume_zscore", _D15.ts_volume_zscore, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量zscore"),
        ("ts_price_volume_sync", _D15.ts_price_volume_sync, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量价同步"),
        ("ts_amount_velocity", _D15.ts_amount_velocity, "L5", ("amount", "window"), ("window",), (), {"window": (2, 250)}, "window", "成交额速度"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _d15_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    # ── D16 算子族扩容（2026-08-11 二期）：量价/流动性族 40 算子，与 feature_ops 双注册表共享 ──
    from ..ops_library import D16Ops as _D16

    _d16_ops = [
        ("ts_amihud_illiquidity", _D16.ts_amihud_illiquidity, "L5", ("close", "amount", "window"), ("window",), (), {"window": (2, 250)}, "window", "Amihud非流动性"),
        ("ts_turnover", _D16.ts_turnover, "L5", ("volume", "float_shares", "window"), ("window",), (), {"window": (2, 250)}, "window", "换手率"),
        ("ts_liquidity_ratio", _D16.ts_liquidity_ratio, "L5", ("volume", "close", "window"), ("window",), (), {"window": (2, 250)}, "window", "流动性比率"),
        ("ts_liquidity_zscore", _D16.ts_liquidity_zscore, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "流动性zscore"),
        ("ts_liquidity_risk", _D16.ts_liquidity_risk, "L5", ("volume", "window"), ("window",), (), {"window": (10, 500)}, "window", "流动性风险"),
        ("ts_float_turnover", _D16.ts_float_turnover, "L5", ("volume", "float_shares"), (), (), {}, None, "流通换手"),
        ("ts_dollar_volume", _D16.ts_dollar_volume, "L5", ("close", "volume"), (), (), {}, None, "成交额"),
        ("ts_bid_ask_spread_proxy", _D16.ts_bid_ask_spread_proxy, "L5", ("high", "low", "close", "window"), ("window",), (), {"window": (2, 250)}, "window", "买卖价差代理"),
        ("ts_trading_intensity", _D16.ts_trading_intensity, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "交易强度"),
        ("ts_tick_size_proxy", _D16.ts_tick_size_proxy, "L5", ("close", "window"), ("window",), (), {"window": (2, 250)}, "window", "最小变动代理"),
        ("ts_price_impact", _D16.ts_price_impact, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "价格冲击"),
        ("ts_liquidity_premium", _D16.ts_liquidity_premium, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "流动性溢价"),
        ("ts_volume_price_trend", _D16.ts_volume_price_trend, "L5", ("close", "volume"), (), (), {}, None, "VPT量价趋势"),
        ("ts_money_flow_ratio", _D16.ts_money_flow_ratio, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "资金流比率"),
        ("ts_force_index", _D16.ts_force_index, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 100)}, "window", "强力指数"),
        ("ts_ease_of_movement", _D16.ts_ease_of_movement, "L5", ("high", "low", "close", "volume", "window"), ("window",), (), {"window": (2, 100)}, "window", "EMV易动度"),
        ("ts_volume_price_regime", _D16.ts_volume_price_regime, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量价制度"),
        ("ts_volume_pressure_ratio", _D16.ts_volume_pressure_ratio, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量压比"),
        ("ts_volume_price_corr_lag", _D16.ts_volume_price_corr_lag, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "滞后量价相关"),
        ("ts_order_flow_proxy", _D16.ts_order_flow_proxy, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "订单流代理"),
        ("ts_volume_change_rate", _D16.ts_volume_change_rate, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量变化率"),
        ("ts_volume_momentum", _D16.ts_volume_momentum, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量动量"),
        ("ts_volume_acceleration", _D16.ts_volume_acceleration, "L5", ("volume", "window"), ("window",), (), {"window": (2, 60)}, "window", "量加速度"),
        ("ts_volume_ma_ratio", _D16.ts_volume_ma_ratio, "L5", ("volume", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "量均线比"),
        ("ts_volume_std_ratio", _D16.ts_volume_std_ratio, "L5", ("volume", "short", "long"), ("short", "long"), (), {"short": (2, 60), "long": (5, 250)}, "long", "量波动比"),
        ("ts_volume_skewness", _D16.ts_volume_skewness, "L5", ("volume", "window"), ("window",), (), {"window": (3, 250)}, "window", "量偏度"),
        ("ts_volume_kurtosis", _D16.ts_volume_kurtosis, "L5", ("volume", "window"), ("window",), (), {"window": (4, 250)}, "window", "量峰度"),
        ("ts_volume_autocorr", _D16.ts_volume_autocorr, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量自相关"),
        ("ts_volume_entropy", _D16.ts_volume_entropy, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量熵"),
        ("ts_volume_concentration", _D16.ts_volume_concentration, "L5", ("volume", "window"), ("window",), (), {"window": (5, 250)}, "window", "量集中度"),
        ("ts_volume_cycle", _D16.ts_volume_cycle, "L5", ("volume", "window"), ("window",), (), {"window": (5, 250)}, "window", "量周期"),
        ("ts_volume_breakout_ratio", _D16.ts_volume_breakout_ratio, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量突破比"),
        ("ts_volume_surge", _D16.ts_volume_surge, "L5", ("volume", "window", "k"), ("window",), ("k",), {"window": (2, 250), "k": (1.0, 5.0)}, "window", "量激增"),
        ("ts_volume_shrinkage", _D16.ts_volume_shrinkage, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量萎缩"),
        ("ts_volume_spike", _D16.ts_volume_spike, "L5", ("volume", "window"), ("window",), (), {"window": (5, 250)}, "window", "量尖峰"),
        ("ts_volume_cluster", _D16.ts_volume_cluster, "L5", ("volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量聚集"),
        ("ts_trade_value_ratio", _D16.ts_trade_value_ratio, "L5", ("amount", "window"), ("window",), (), {"window": (2, 250)}, "window", "成交额比"),
        ("ts_turnover_zscore", _D16.ts_turnover_zscore, "L5", ("volume", "float_shares", "window"), ("window",), (), {"window": (2, 250)}, "window", "换手zscore"),
        ("ts_volume_weighted_return", _D16.ts_volume_weighted_return, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "量加权收益"),
        ("ts_price_volume_divergence_score", _D16.ts_price_volume_divergence_score, "L5", ("close", "volume", "window"), ("window",), (), {"window": (2, 250)}, "window", "价量背离得分"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _d16_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    # ── D17 算子族扩容（2026-08-11 二期）：市场结构/分布族 35 算子，与 feature_ops 双注册表共享 ──
    from ..ops_library import D17Ops as _D17

    _d17_ops = [
        ("ts_market_breadth", _D17.ts_market_breadth, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "市场广度"),
        ("ts_advance_decline_ratio", _D17.ts_advance_decline_ratio, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "涨跌比"),
        ("ts_new_high_low_ratio", _D17.ts_new_high_low_ratio, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "新高新低比"),
        ("ts_breadth_momentum", _D17.ts_breadth_momentum, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "广度动量"),
        ("ts_breadth_divergence", _D17.ts_breadth_divergence, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "广度背离"),
        ("ts_sector_rotation_score", _D17.ts_sector_rotation_score, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "板块轮动得分"),
        ("ts_concentration_index", _D17.ts_concentration_index, "L5", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "集中度指数"),
        ("ts_diversification_index", _D17.ts_diversification_index, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "分散度指数"),
        ("ts_correlation_regime", _D17.ts_correlation_regime, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "相关制度"),
        ("ts_market_dispersion", _D17.ts_market_dispersion, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "市场离散"),
        ("ts_cross_section_momentum", _D17.ts_cross_section_momentum, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "截面动量"),
        ("ts_cross_section_reversal", _D17.ts_cross_section_reversal, "L2", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "截面反转"),
        ("ts_size_premium_proxy", _D17.ts_size_premium_proxy, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "市值溢价代理"),
        ("ts_value_premium_proxy", _D17.ts_value_premium_proxy, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "价值溢价代理"),
        ("ts_momentum_factor_proxy", _D17.ts_momentum_factor_proxy, "L5", ("series", "window"), ("window",), (), {"window": (5, 500)}, "window", "动量因子代理"),
        ("ts_low_vol_factor_proxy", _D17.ts_low_vol_factor_proxy, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "低波因子代理"),
        ("ts_quality_factor_proxy", _D17.ts_quality_factor_proxy, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "质量因子代理"),
        ("ts_sentiment_score", _D17.ts_sentiment_score, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "情绪得分"),
        ("ts_risk_appetite", _D17.ts_risk_appetite, "L5", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "风险偏好"),
        ("ts_fear_greed_index", _D17.ts_fear_greed_index, "L5", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "恐惧贪婪指数"),
        ("ts_momentum_crowding", _D17.ts_momentum_crowding, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "动量拥挤度"),
        ("ts_position_extreme", _D17.ts_position_extreme, "L5", ("series", "window"), ("window",), (), {"window": (5, 500)}, "window", "仓位极端度"),
        ("ts_herding_proxy", _D17.ts_herding_proxy, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "羊群代理"),
        ("ts_implied_vol_proxy", _D17.ts_implied_vol_proxy, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "隐含波动代理"),
        ("ts_risk_reversal_proxy", _D17.ts_risk_reversal_proxy, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "风险逆转代理"),
        ("ts_smile_proxy", _D17.ts_smile_proxy, "L5", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "波动微笑代理"),
        ("ts_market_regime_score", _D17.ts_market_regime_score, "L5", ("series", "window"), ("window",), (), {"window": (5, 250)}, "window", "市场制度得分"),
        ("ts_trend_regime_proxy", _D17.ts_trend_regime_proxy, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "趋势制度代理"),
        ("ts_volatility_regime_proxy", _D17.ts_volatility_regime_proxy, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "波动制度代理"),
        ("ts_liquidity_regime_proxy", _D17.ts_liquidity_regime_proxy, "L5", ("volume", "window"), ("window",), (), {"window": (10, 500)}, "window", "流动性制度代理"),
        ("ts_market_timing_score", _D17.ts_market_timing_score, "L5", ("series", "window"), ("window",), (), {"window": (2, 250)}, "window", "择时得分"),
        ("ts_regime_confidence", _D17.ts_regime_confidence, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "制度置信度"),
        ("ts_regime_persistence", _D17.ts_regime_persistence, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "制度持续"),
        ("ts_regime_transition_prob", _D17.ts_regime_transition_prob, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "制度转移概率"),
        ("ts_market_phase", _D17.ts_market_phase, "L5", ("series", "window"), ("window",), (), {"window": (10, 500)}, "window", "市场阶段"),
    ]
    for _name, _func, _cat, _params, _intp, _floatp, _bounds, _lb, _meaning in _d17_ops:
        add(
            _name,
            _func,
            _cat,
            _params,
            int_params=_intp,
            float_params=_floatp,
            bounds=_bounds,
            lookback=_lb,
            meaning=_meaning,
        )

    return registry


def verify_registry_consistency() -> dict[str, Any]:
    """双注册表一致性校验 (GAP-S10)。

    expr_dsl 注册表与 feature_ops.OperatorRegistry（GP 演化用）在实现层
    共用同一批底层原语（TimeSeriesOps/PriceOps/RollingOps/CompositeOps），
    但算子名称/参数定义分处两处，存在漂移风险。

    本函数对重叠算子在相同输入下逐一执行并断言输出一致，作为
    "单一算子事实源"的强制防漂移校验：
        - 输出差异 > rtol 记为 mismatch（需修复）
        - 仅存在于单侧的算子记入 only_*，不判错（各自专属算子合法）
    """
    from ..feature_ops import OperatorRegistry

    dsl_reg = build_registry()
    gp_reg = OperatorRegistry()
    gp_names = {op.name for op in gp_reg.list_operators()}

    overlapping = sorted(set(dsl_reg) & gp_names)
    only_dsl = sorted(set(dsl_reg) - gp_names)
    only_gp = sorted(gp_names - set(dsl_reg))

    # GAP-I202 (v2.75.0): 组合/跨标的算子必须双注册表共享（单一事实源硬约束）
    # C8 (2026-08-11): 22 个扩容算子全部并入强制共享（feature_ops 与 expr_dsl 同实现）
    required_shared = (
        "ts_slope",
        "ts_quantile",
        "regression_residual",
        "quantile_bucket",
        "cross_section_demean",
        "if_else",
        "corr",
        "cross_section_rank",
        # C8 扩容 22 算子
        "ts_argmin",
        "ts_ema",
        "ts_mad",
        "ts_range",
        "ts_iqr",
        "ts_quantile_range",
        "ts_return_over_max",
        "ts_min_max_ratio",
        "ts_std_ratio",
        "ts_roc_sum",
        "ts_breakout",
        "ts_cumulative_return",
        "cs_rank_diff",
        "cs_zscore_diff",
        "cs_extreme_ratio",
        "cs_median_dev",
        "where_gt",
        "consecutive_true",
        "sign_flip",
        "mean_reversion_z",
        "trend_strength",
        "volume_pressure",
        # C9 扩容 30 算子
        "ts_pct_rank_window",
        "ts_zscore_rolling",
        "ts_skew",
        "ts_kurt",
        "ts_slope_pct",
        "ts_position_in_range",
        "ts_down_ratio",
        "ts_up_ratio",
        "ts_gain_loss_ratio",
        "ts_bias_ma",
        "ts_boll_position",
        "ts_ma_diff",
        "ts_vol_shrink",
        "ts_tail_risk",
        "cs_winsor_flag",
        "cs_demean_ratio",
        "cs_rank_norm",
        "cs_med_ratio",
        "cs_extreme_gap",
        "where_between",
        "cross_above",
        "cross_below",
        "momentum_break",
        "vol_regime",
        "mean_reversion_signal",
        "price_volume_div",
        "liquidity_dryup",
        "self_corr",
        "sign_entropy",
        "reversal_strength",
        # D10 波动/风险族 55 算子
        "ts_realized_vol",
        "ts_ewma_vol",
        "ts_parkinson",
        "ts_garman_klass",
        "ts_rogers_satchell",
        "ts_yang_zhang",
        "ts_downside_vol",
        "ts_upside_vol",
        "ts_vol_of_vol",
        "ts_bipower_var",
        "ts_range_vol",
        "ts_harmonic_vol",
        "ts_drawdown",
        "ts_max_drawdown",
        "ts_avg_drawdown",
        "ts_drawdown_duration",
        "ts_ulcer_index",
        "ts_var_95",
        "ts_var_99",
        "ts_cvar_95",
        "ts_cvar_99",
        "ts_semi_std",
        "ts_lpm_2",
        "ts_hpm_2",
        "ts_gain_std",
        "ts_loss_std",
        "ts_sharpe_ratio",
        "ts_sortino_ratio",
        "ts_calmar_ratio",
        "ts_profit_factor",
        "ts_omega_ratio",
        "ts_kelly_fraction",
        "ts_worst_day",
        "ts_best_day",
        "ts_win_rate",
        "ts_loss_rate",
        "ts_avg_gain",
        "ts_avg_loss",
        "ts_expectancy",
        "ts_recovery_factor",
        "ts_risk_return_ratio",
        "ts_downside_deviation",
        "ts_vol_ratio_ewma",
        "ts_realized_vol_pct",
        "ts_vol_zscore",
        "ts_vol_percentile",
        "ts_garch_proxy",
        "ts_vol_asymmetry",
        "ts_leverage_effect",
        "ts_baseline_vol",
        "ts_long_term_vol",
        "ts_short_term_vol",
        "ts_vol_term_structure",
        "ts_max_loss_ratio",
        "ts_beta_vol",
        # D11 技术指标族 60 算子
        "ts_ema_fast_slow",
        "ts_macd",
        "ts_macd_signal",
        "ts_macd_hist",
        "ts_dema",
        "ts_tema",
        "ts_kama",
        "ts_vwap",
        "ts_rsi",
        "ts_rsi_smoothed",
        "ts_stoch_k",
        "ts_stoch_d",
        "ts_williams_r",
        "ts_cci",
        "ts_trix",
        "ts_ppo",
        "ts_tsi",
        "ts_awesome",
        "ts_ultimate_osc",
        "ts_roc",
        "ts_momentum_index",
        "ts_rate_of_change_ma",
        "ts_fisher_transform",
        "ts_stoch_rsi",
        "ts_rvi",
        "ts_obv",
        "ts_obv_ma",
        "ts_mfi",
        "ts_adi",
        "ts_cmf",
        "ts_chaikin_vol",
        "ts_chaikin_osc",
        "ts_volume_oscillator",
        "ts_market_facilitation",
        "ts_atr",
        "ts_natr",
        "ts_bb_width",
        "ts_bb_percent_b",
        "ts_bb_bandwidth",
        "ts_price_channel",
        "ts_aroon_up",
        "ts_aroon_down",
        "ts_aroon_osc",
        "ts_dpo",
        "ts_kst",
        "ts_kst_signal",
        "ts_mass_index",
        "ts_vortex_pos",
        "ts_vortex_neg",
        "ts_vortex_ratio",
        "ts_ichimoku_conv",
        "ts_ichimoku_base",
        "ts_ichimoku_span_a",
        "ts_ichimoku_span_b",
        "ts_sma_cross_signal",
        "ts_ema_cross_signal",
        "ts_parabolic_sar",
        "ts_price_oscillator",
        "ts_trend_score",
        "ts_cycle_score",
        # D12 动量/趋势族 55 算子
        "ts_velocity",
        "ts_acceleration",
        "ts_jerk",
        "ts_momentum_ratio",
        "ts_momentum_breakout_ratio",
        "ts_ewm_momentum",
        "ts_momentum_vol_adj",
        "ts_roc_zscore",
        "ts_velocity_zscore",
        "ts_trend_angle",
        "ts_linear_trend_score",
        "ts_trend_strength_pct",
        "ts_above_ma_ratio",
        "ts_below_ma_ratio",
        "ts_slope_change",
        "ts_curvature",
        "ts_momentum_consistency",
        "ts_trend_persistence",
        "ts_reversal_signal_z",
        "ts_trend_strength_ma",
        "ts_relative_strength",
        "ts_cross_momentum",
        "ts_momentum_regime",
        "ts_trend_filter",
        "ts_higher_high_count",
        "ts_lower_low_count",
        "ts_new_high_ratio",
        "ts_new_low_ratio",
        "ts_range_expansion",
        "ts_breakout_distance",
        "ts_pullback_depth",
        "ts_continuation_signal",
        "ts_exhaustion_signal",
        "ts_donchian_break",
        "ts_donchian_mid",
        "ts_supertrend_signal",
        "ts_psar_position",
        "ts_uptrend_flag",
        "ts_downtrend_flag",
        "ts_sideways_flag",
        "ts_trend_direction_strength",
        "ts_multi_tf_trend",
        "ts_fractal_up",
        "ts_fractal_down",
        "ts_support_proximity",
        "ts_resistance_proximity",
        "ts_breakout_pullback_signal",
        "ts_directional_up",
        "ts_directional_down",
        "ts_adx_pos",
        "ts_adx_neg",
        "ts_adx",
        "ts_trend_vol_ratio",
        "ts_trend_entropy",
        "ts_up_down_strength",
        # D13 截面/排名族 45 算子
        "cs_rank_pct",
        "cs_percent_rank",
        "cs_rank_demean",
        "cs_inverse_rank",
        "cs_signed_rank",
        "cs_rank_ratio",
        "cs_cross_rank_diff",
        "cs_rank_momentum",
        "cs_rank_volatility",
        "cs_rank_stability",
        "cs_ewm_rank",
        "cs_smooth_rank",
        "cs_robust_rank",
        "cs_quantile_rank",
        "cs_cross_section_bucket",
        "cs_zscore_med",
        "cs_mad_zscore",
        "cs_winsor_z",
        "cs_normalize_01",
        "cs_minmax_norm",
        "cs_softmax_weight",
        "cs_distance_median",
        "cs_distance_mean",
        "cs_relative_to_max",
        "cs_relative_to_min",
        "cs_max_share",
        "cs_trim_mean_diff",
        "cs_market_relative",
        "cs_dispersion",
        "cs_coefficient_variation",
        "cs_gini_score",
        "cs_herfindahl",
        "cs_concentration",
        "cs_top_bottom_spread",
        "cs_winner_loser_gap",
        "cs_median_gap",
        "cs_extreme_strength",
        "cs_outlier_flag",
        "cs_tail_weight",
        "cs_skewness_score",
        "cs_kurtosis_score",
        "cs_extreme_skew",
        "cs_breadth_position",
        "cs_entropy_rank",
        "cs_outlier_ratio",
        # D14 条件/事件族 40 算子
        "ts_cross_threshold_up",
        "ts_cross_threshold_down",
        "ts_threshold_band",
        "ts_range_condition",
        "ts_condition_count",
        "ts_condition_ratio",
        "ts_consecutive_above",
        "ts_consecutive_below",
        "ts_consecutive_increase",
        "ts_consecutive_decrease",
        "ts_consecutive_same_sign",
        "ts_condition_change",
        "ts_condition_switch_rate",
        "ts_state_duration",
        "ts_state_age",
        "ts_breakout_event",
        "ts_breakdown_event",
        "ts_cross_ma_event",
        "ts_golden_cross_event",
        "ts_death_cross_event",
        "ts_turning_point",
        "ts_zigzag_direction",
        "ts_event_density",
        "ts_event_count_n",
        "ts_signal_persistence",
        "ts_signal_decay",
        "ts_condition_entropy",
        "ts_pattern_continuation",
        "ts_pattern_reversal",
        "ts_momentum_filter",
        "ts_volatility_filter",
        "ts_liquidity_filter",
        "ts_trend_condition",
        "ts_breakout_condition",
        "ts_reversal_condition",
        "ts_level_test",
        "ts_support_break",
        "ts_resistance_break",
        "ts_condition_combo",
        "ts_breakout_strength",
        # D15 组合/跨序列族 50 算子
        "cs_ratio",
        "cs_diff",
        "cs_sum",
        "cs_product",
        "cs_min",
        "cs_max",
        "cs_spread",
        "cs_return_spread",
        "cs_relative_ratio",
        "cs_log_ratio",
        "cs_pct_diff",
        "cs_weighted_average",
        "cs_composite_score",
        "cs_normalized_ratio",
        "cs_smoothed_ratio",
        "cs_exponential_ratio",
        "cs_ratio_ma",
        "cs_ratio_zscore",
        "cs_relative_strength_ratio",
        "ts_pair_corr",
        "ts_cov",
        "ts_beta",
        "ts_alpha",
        "ts_lead_lag_corr",
        "ts_cross_corr_lag1",
        "ts_granger_proxy",
        "ts_hedge_ratio",
        "ts_cointegration_proxy",
        "ts_spread_zscore",
        "ts_spread_band",
        "ts_pair_divergence",
        "ts_pair_convergence",
        "ts_convergence_rate",
        "ts_pair_trade_signal",
        "ts_price_gap",
        "ts_overnight_return",
        "ts_intraday_return",
        "ts_open_close_diff",
        "ts_high_low_ratio",
        "ts_range_ratio",
        "ts_basis",
        "ts_basis_ratio",
        "ts_term_spread",
        "ts_roll_yield",
        "ts_volume_price_corr",
        "ts_volume_ratio_vs_avg",
        "ts_volume_breakout",
        "ts_volume_zscore",
        "ts_price_volume_sync",
        "ts_amount_velocity",
        # D16 量价/流动性族 40 算子
        "ts_amihud_illiquidity",
        "ts_turnover",
        "ts_liquidity_ratio",
        "ts_liquidity_zscore",
        "ts_liquidity_risk",
        "ts_float_turnover",
        "ts_dollar_volume",
        "ts_bid_ask_spread_proxy",
        "ts_trading_intensity",
        "ts_tick_size_proxy",
        "ts_price_impact",
        "ts_liquidity_premium",
        "ts_volume_price_trend",
        "ts_money_flow_ratio",
        "ts_force_index",
        "ts_ease_of_movement",
        "ts_volume_price_regime",
        "ts_volume_pressure_ratio",
        "ts_volume_price_corr_lag",
        "ts_order_flow_proxy",
        "ts_volume_change_rate",
        "ts_volume_momentum",
        "ts_volume_acceleration",
        "ts_volume_ma_ratio",
        "ts_volume_std_ratio",
        "ts_volume_skewness",
        "ts_volume_kurtosis",
        "ts_volume_autocorr",
        "ts_volume_entropy",
        "ts_volume_concentration",
        "ts_volume_cycle",
        "ts_volume_breakout_ratio",
        "ts_volume_surge",
        "ts_volume_shrinkage",
        "ts_volume_spike",
        "ts_volume_cluster",
        "ts_trade_value_ratio",
        "ts_turnover_zscore",
        "ts_volume_weighted_return",
        "ts_price_volume_divergence_score",
        # D17 市场结构/分布族 35 算子
        "ts_market_breadth",
        "ts_advance_decline_ratio",
        "ts_new_high_low_ratio",
        "ts_breadth_momentum",
        "ts_breadth_divergence",
        "ts_sector_rotation_score",
        "ts_concentration_index",
        "ts_diversification_index",
        "ts_correlation_regime",
        "ts_market_dispersion",
        "ts_cross_section_momentum",
        "ts_cross_section_reversal",
        "ts_size_premium_proxy",
        "ts_value_premium_proxy",
        "ts_momentum_factor_proxy",
        "ts_low_vol_factor_proxy",
        "ts_quality_factor_proxy",
        "ts_sentiment_score",
        "ts_risk_appetite",
        "ts_fear_greed_index",
        "ts_momentum_crowding",
        "ts_position_extreme",
        "ts_herding_proxy",
        "ts_implied_vol_proxy",
        "ts_risk_reversal_proxy",
        "ts_smile_proxy",
        "ts_market_regime_score",
        "ts_trend_regime_proxy",
        "ts_volatility_regime_proxy",
        "ts_liquidity_regime_proxy",
        "ts_market_timing_score",
        "ts_regime_confidence",
        "ts_regime_persistence",
        "ts_regime_transition_prob",
        "ts_market_phase",
    )
    missing_in_gp = [n for n in required_shared if n not in gp_names]
    missing_in_dsl = [n for n in required_shared if n not in set(dsl_reg)]
    unshared = missing_in_gp + missing_in_dsl

    rng = np.random.default_rng(42)
    x = pd.Series(rng.normal(0, 1, 120))
    y = pd.Series(rng.normal(0, 1, 120))

    matched: list[str] = []
    mismatched: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for name in overlapping:
        meta = dsl_reg[name]
        # 按 expr_dsl 参数名构造统一位置参数（两个注册表均按位置调用）
        args: list[Any] = []
        for p in meta.params:
            if p in ("x", "a", "p"):
                args.append(x)
            elif p in ("y", "b"):
                args.append(y)
            elif p in ("up",):
                args.append(x)
            elif p in ("total",):
                args.append(y)
            elif p in meta.int_params:
                args.append(20)
            elif p in meta.float_params:
                args.append(1.0)
            else:
                args.append(x)
        try:
            out_dsl = meta.func(*args)
            out_gp = gp_reg.call(name, *args)
            if isinstance(out_dsl, pd.Series):
                a = out_dsl.to_numpy(dtype=np.float64)
                b = out_gp.to_numpy(dtype=np.float64)
                if a.shape != b.shape:
                    mismatched.append({"op": name, "reason": "shape 不一致"})
                    continue
                scale = max(1.0, float(np.nanmax(np.abs(a))) or 1.0)
                diff = np.abs(a - b)
                if float(np.nanmax(diff)) > 1e-6 * scale:
                    mismatched.append({"op": name, "reason": f"max_diff={float(np.nanmax(diff)):.3g}"})
                    continue
                matched.append(name)
            else:
                # 标量结果（如 quantile）直接比较
                if np.allclose(float(out_dsl), float(out_gp), rtol=1e-6, atol=1e-8):
                    matched.append(name)
                else:
                    mismatched.append({"op": name, "reason": f"dsl={out_dsl}, gp={out_gp}"})
        except Exception as e:  # noqa: BLE001
            errors.append({"op": name, "error": str(e)})

    return {
        "overlapping": len(overlapping),
        "matched": matched,
        "mismatched": mismatched,
        "errors": errors,
        "only_dsl": only_dsl,
        "only_gp": only_gp,
        "unshared_required": unshared,
        "consistent": len(mismatched) == 0 and len(errors) == 0 and len(unshared) == 0,
    }
