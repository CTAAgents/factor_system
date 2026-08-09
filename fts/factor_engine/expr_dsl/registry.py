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

from ..feature_ops import CompositeOps, PriceOps, RollingOps, TimeSeriesOps

# L0 基础数据字段（数据访问层）
L0_FIELDS: tuple[str, ...] = (
    "open", "high", "low", "close", "volume",
    "vwap", "amount", "returns", "hold", "settle",
)

# GAP-S12 (v2.67.0): A 股特有数据字段（北向/两融/股东户数/分析师预期）
A_SHARE_FIELDS: tuple[str, ...] = (
    "northbound_flow", "northbound_hold_pct",
    "margin_balance", "margin_net_buy", "margin_short_balance",
    "holder_count",
    "analyst_up_count", "analyst_down_count",
    "analyst_total_count", "analyst_eps_revision",
)


@dataclass(frozen=True)
class OperatorMeta:
    """算子元数据（语义/梯度/边界）。"""

    name: str
    func: Callable
    category: str                                  # L0-L5 分层
    params: tuple[str, ...]                        # 参数名，首个为序列输入
    int_params: frozenset[str] = frozenset()       # 整数参数（如 window/n）
    float_params: frozenset[str] = frozenset()     # 浮点参数（如 q/n_std/lo/hi）
    param_bounds: dict[str, tuple[float, float]] = field(default_factory=dict)
    lookback_param: Optional[str] = None           # 最大 lookback 来源参数 (PIT)
    differentiable: bool = True                    # 梯度可导声明
    economic_meaning: str = ""                     # 经济语义标签


def build_registry() -> dict[str, OperatorMeta]:
    """构建 DSL 算子注册表（实现复用 feature_ops + 少量领域新增）。"""
    registry: dict[str, OperatorMeta] = {}

    def add(name, func, category, params, *, int_params=(), float_params=(),
            bounds=None, lookback=None, diff=True, meaning=""):
        registry[name] = OperatorMeta(
            name=name, func=func, category=category, params=tuple(params),
            int_params=frozenset(int_params), float_params=frozenset(float_params),
            param_bounds=dict(bounds or {}), lookback_param=lookback,
            differentiable=diff, economic_meaning=meaning,
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
        add(_f, lambda x: x, "L0", ("x",),
            meaning=_a_share_meanings.get(_f, "A股特有数据字段"))

    # ── L1 时序算子（单/双序列变换） ──
    add("ts_mean", lambda x, n: TimeSeriesOps.ts_mean(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动均值(趋势基准)")
    add("ts_std", lambda x, n: TimeSeriesOps.ts_std(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动波动")
    add("ts_zscore", lambda x, n: RollingOps.ts_zscore(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动标准化")
    add("ts_rank", lambda x, n: RollingOps.ts_rank(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动窗口内排名")
    add("ts_min", lambda x, n: TimeSeriesOps.ts_min(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动最小值")
    add("ts_max", lambda x, n: TimeSeriesOps.ts_max(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动最大值")
    add("ts_sum", lambda x, n: TimeSeriesOps.ts_sum(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动求和")
    add("ts_skewness", lambda x, n: RollingOps.ts_skewness(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动偏度")
    add("ts_kurtosis", lambda x, n: RollingOps.ts_kurtosis(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动峰度")
    add("ts_median", lambda x, n: RollingOps.ts_median(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动中位数")
    add("ts_delay", lambda x, n: x.shift(n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (1, 250)}, lookback="n", meaning="滞后 n 期")
    add("ts_delta", lambda x, n: PriceOps.delta(x, periods=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (1, 250)}, lookback="n", meaning="差分")
    add("ts_pct_change", lambda x, n: PriceOps.pct_change(x, periods=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (1, 250)}, lookback="n", meaning="百分比变化")
    add("ts_momentum", lambda x, n: RollingOps.ts_momentum(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="动量(当前/前n)")
    add("ts_volatility", lambda x, n: RollingOps.ts_volatility(x, window=n), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动年化波动率")
    add("ts_covariance", lambda x, y, n: x.rolling(n).cov(y), "L1", ("x", "y", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动协方差")
    add("ts_correlation", lambda x, y, n: x.rolling(n).corr(y), "L1", ("x", "y", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="滚动相关系数")
    add("ts_decay_linear", lambda x, n: x.rolling(n).apply(
            lambda w: float(np.dot(w, np.arange(1, n + 1)) / (n * (n + 1) / 2.0)),
            raw=True),
        "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="线性衰减加权")
    add("ts_argmax", lambda x, n: x.rolling(n).apply(np.argmax, raw=True), "L1", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="窗口内最大值位置")

    # ── L2 横截面算子（跨截面变换） ──
    add("rank", lambda x: PriceOps.rank(x), "L2", ("x",), meaning="截面排名(0-1)")
    add("zscore", lambda x: PriceOps.zscore(x), "L2", ("x",), meaning="截面 Z-Score")
    add("normalize", lambda x: (x - x.min()) / (x.max() - x.min())
        if x.max() != x.min() else x * 0.0, "L2", ("x",), meaning="min-max 归一化")
    add("quantile", lambda x, q: x.quantile(q), "L2", ("x", "q"),
        float_params=("q",), bounds={"q": (0.0, 1.0)}, meaning="分位数")
    add("winsorize", lambda x, n_std: x.clip(x.mean() - n_std * x.std(),
                                             x.mean() + n_std * x.std()),
        "L2", ("x", "n_std"),
        float_params=("n_std",), bounds={"n_std": (0.0, 10.0)}, meaning="缩尾")

    # ── L3 逻辑算子（受控条件） ──
    add("where", lambda cond, x, y: x.where(cond.astype(bool), y), "L3",
        ("cond", "x", "y"), meaning="条件选择")
    add("gt", lambda a, b: a > b, "L3", ("a", "b"), meaning="大于")
    add("lt", lambda a, b: a < b, "L3", ("a", "b"), meaning="小于")
    add("and_", lambda a, b: a.astype(bool) & b.astype(bool), "L3",
        ("a", "b"), meaning="逻辑与")
    add("or_", lambda a, b: a.astype(bool) | b.astype(bool), "L3",
        ("a", "b"), meaning="逻辑或")
    add("not_", lambda a: ~a.astype(bool), "L3", ("a",), meaning="逻辑非")

    # ── L4 组合算子（高阶） ──
    add("add", CompositeOps.add, "L4", ("a", "b"), meaning="加法")
    add("sub", CompositeOps.sub, "L4", ("a", "b"), meaning="减法")
    add("mul", CompositeOps.mul, "L4", ("a", "b"), meaning="乘法")
    add("div", lambda a, b: a / b.replace(0, np.nan)
        if isinstance(b, pd.Series) else a / b, "L4", ("a", "b"), meaning="除法(0 安全)")
    add("neg", lambda x: -x, "L4", ("x",), meaning="取负")
    add("abs", lambda x: x.abs(), "L4", ("x",), meaning="绝对值")
    add("sign", lambda x: np.sign(x), "L4", ("x",), meaning="符号")
    add("sqrt", lambda x: np.sqrt(x.abs()), "L4", ("x",), meaning="平方根")
    add("log", lambda x: np.log(x.abs() + 1e-10), "L4", ("x",), meaning="对数")
    add("exp", lambda x: np.exp(x), "L4", ("x",), meaning="指数")
    add("min", lambda a, b: np.minimum(a, b), "L4", ("a", "b"), meaning="取小")
    add("max", lambda a, b: np.maximum(a, b), "L4", ("a", "b"), meaning="取大")
    add("clip", lambda x, lo, hi: x.clip(lo, hi), "L4", ("x", "lo", "hi"),
        float_params=("lo", "hi"), meaning="截断")
    add("pow", lambda a, b: np.power(a, b), "L4", ("a", "b"), meaning="幂")

    # ── L4 高阶双序列/横截面/条件算子（GAP-L401，v2.66.0）──
    # 双序列: 滚动线性回归残差（回归 alpha，去趋势/去 beta）
    add("regression_residual", lambda x, y, n: RollingOps.ts_regression_residual(
            x, y, window=n),
        "L4", ("x", "y", "n"),
        int_params=("n",), bounds={"n": (20, 250)}, lookback="n",
        diff=False,
        meaning="滚动回归残差(去 beta 的 alpha)")
    # 横截面: 分位桶（0~n_buckets-1，截面分位映射）
    add("quantile_bucket", lambda x, n_buckets: RollingOps.ts_quantile_bucket(
            x, n_buckets=n_buckets),
        "L4", ("x", "n_buckets"),
        int_params=("n_buckets",), bounds={"n_buckets": (2, 10)},
        diff=False,
        meaning="分位桶(截面 0~n-1)")
    # 横截面: 去均值（demean）
    add("cross_section_demean", lambda x: x - x.mean(), "L4", ("x",),
        diff=True,
        meaning="横截面去均值(暴露剥离)")
    # 条件: if_else（NaN 安全：条件为 NaN 视为 False）
    def _if_else(cond, x, y):
        if isinstance(cond, pd.Series):
            c = cond.fillna(False).astype(bool)
        else:
            c = bool(cond)
        return x.where(c, y)

    add("if_else", _if_else, "L4", ("cond", "x", "y"),
        diff=False,
        meaning="条件选择(NaN 安全)")

    # ── L5 领域算子（金融语义组合） ──
    add("momentum", lambda p, n: p.pct_change(n), "L5", ("p", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="价格动量")
    add("reversal", lambda p, n: -p.pct_change(n), "L5", ("p", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="价格反转")
    add("liquidity", lambda p, v, n: v / v.rolling(n).mean(), "L5", ("p", "v", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="流动性(量/均量)")
    add("volatility", lambda p, n: p.pct_change().rolling(n).std(), "L5", ("p", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n", meaning="波动率")

    # ── L5b A 股特有领域算子 (GAP-S12)：北向/两融/股东户数/分析师预期 ──
    def _ts_mean(x, n):  # 滚动均值（min_periods=1 对齐 L1 ts_mean）
        return x.rolling(n, min_periods=1).mean()

    add("nb_momentum", lambda x, n: x - _ts_mean(x, n), "L5", ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n",
        meaning="北向资金动量(当前-滚动均值)")
    add("margin_change", lambda x, n: PriceOps.pct_change(x, periods=n), "L5", ("x", "n"),
        int_params=("n",), bounds={"n": (1, 250)}, lookback="n",
        meaning="融资余额变化率")
    add("holder_concentration", lambda x, n: -_ts_mean(PriceOps.delta(x, 1), n), "L5",
        ("x", "n"),
        int_params=("n",), bounds={"n": (2, 250)}, lookback="n",
        meaning="筹码集中度(股东户数下降取正)")
    add("analyst_revision_ratio", lambda up, total: (up / total.replace(0, np.nan))
        if isinstance(total, pd.Series) else (up / max(total, 1.0)),
        "L5", ("up", "total"), meaning="分析师上调比率(上调家数/覆盖家数)")

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
        "consistent": len(mismatched) == 0 and len(errors) == 0,
    }
