"""
fts.factor_engine.feature_ops — 特征算子库 (Phase C.1)。

提供 50+ 特征算子，分为:
- 基础算子 (BasicOps): 时序/价格/滚动/截面
- 组合算子 (CompositeOps): 嵌套/条件/运算
- 算子注册表: 管理和调用所有算子

版本: v0.1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

OperatorCategory = str
"""算子类别: time_series / price / rolling / cross_section / composite / cross_symbol"""


@dataclass
class OperatorInfo:
    """算子元数据。"""

    name: str
    category: str
    params: list[str] = field(default_factory=list)
    description: str = ""
    signature: str = ""
    version: str = "0.1.0"
    added_at: str = ""


# ─── 时序算子 ───────────────────────────────────────────────


class TimeSeriesOps:
    """时序算子集合。"""

    @staticmethod
    def ts_mean(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动均值。"""
        return series.rolling(window=window).mean()

    @staticmethod
    def ts_std(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动标准差。"""
        return series.rolling(window=window).std()

    @staticmethod
    def ts_max(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动最大值。"""
        return series.rolling(window=window).max()

    @staticmethod
    def ts_min(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动最小值。"""
        return series.rolling(window=window).min()

    @staticmethod
    def ts_sum(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动求和。"""
        return series.rolling(window=window).sum()

    @staticmethod
    def ts_product(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动乘积。"""
        return series.rolling(window=window).apply(np.prod, raw=True)


# ─── 价格算子 ───────────────────────────────────────────────


class PriceOps:
    """价格算子集合。"""

    @staticmethod
    def rank(series: pd.Series) -> pd.Series:
        """截面排名 (0-1 归一化)。"""
        return series.rank(pct=True)

    @staticmethod
    def zscore(series: pd.Series) -> pd.Series:
        """Z-Score 标准化。"""
        mean = series.mean()
        std = series.std()
        return (series - mean) / std if std > 0 else series

    @staticmethod
    def delta(series: pd.Series, periods: int = 1) -> pd.Series:
        """变化量。"""
        return series.diff(periods)

    @staticmethod
    def pct_change(series: pd.Series, periods: int = 1) -> pd.Series:
        """百分比变化。"""
        return series.pct_change(periods)

    @staticmethod
    def log_return(series: pd.Series) -> pd.Series:
        """对数收益。"""
        return np.log(series / series.shift(1))


# ─── 滚动算子 ───────────────────────────────────────────────


class RollingOps:
    """滚动算子集合。"""

    @staticmethod
    def ts_rank(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动窗口内排名。"""
        return series.rolling(window=window).rank(pct=True)

    @staticmethod
    def ts_zscore(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动 Z-Score。"""
        return series.rolling(window=window).apply(
            lambda x: (x.iloc[-1] - x.mean()) / x.std() if len(x) > 1 and x.std() > 0 else 0
        )

    @staticmethod
    def ts_momentum(series: pd.Series, window: int = 20) -> pd.Series:
        """动量指标 (当前值 / window 前的值 - 1)。"""
        return series / series.shift(window) - 1

    @staticmethod
    def ts_volatility(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动波动率 (年化)。"""
        return series.pct_change().rolling(window=window).std() * np.sqrt(252)

    @staticmethod
    def ts_skewness(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动偏度。"""
        return series.rolling(window=window).skew()

    @staticmethod
    def ts_kurtosis(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动峰度。"""
        return series.rolling(window=window).kurt()

    @staticmethod
    def ts_median(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动中位数。"""
        return series.rolling(window=window).median()

    @staticmethod
    def ts_min_max_diff(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动极差。"""
        return series.rolling(window=window).apply(lambda x: x.max() - x.min())

    @staticmethod
    def ts_cum_max(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动累计最大值。"""
        return series.rolling(window=window).apply(lambda x: x.cummax().iloc[-1])


# ─── 技术指标算子 ──────────────────────────────────────────


class TechnicalOps:
    """技术指标算子集合。"""

    @staticmethod
    def rsi(series: pd.Series, window: int = 14) -> pd.Series:
        """RSI 相对强弱指数。"""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=window).mean()
        avg_loss = loss.rolling(window=window).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def bollinger_upper(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
        """布林带上轨。"""
        ma = series.rolling(window=window).mean()
        std = series.rolling(window=window).std()
        return ma + num_std * std

    @staticmethod
    def bollinger_lower(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
        """布林带下轨。"""
        ma = series.rolling(window=window).mean()
        std = series.rolling(window=window).std()
        return ma - num_std * std

    @staticmethod
    def bollinger_width(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
        """布林带宽度。"""
        upper = TechnicalOps.bollinger_upper(series, window, num_std)
        lower = TechnicalOps.bollinger_lower(series, window, num_std)
        return (upper - lower) / series.rolling(window=window).mean()

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """平均真实波幅。"""
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=window).mean()

    @staticmethod
    def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
        """MACD 指标。"""
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        return dif.ewm(span=signal, adjust=False).mean()

    @staticmethod
    def max_drawdown(series: pd.Series, window: int = 252) -> pd.Series:
        """滚动最大回撤。"""
        return series.rolling(window=window).apply(
            lambda x: (x / x.cummax() - 1).min()
        )


# ─── 截面算子 ───────────────────────────────────────────────


class CrossSectionOps:
    """截面算子集合。"""

    @staticmethod
    def cross_rank(
        panel: pd.DataFrame,
        group_col: str = "date",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """截面排名 (按日期分组)。"""
        result = panel.copy()
        result["cross_rank"] = result.groupby(group_col)[value_col].rank(pct=True)
        return result

    @staticmethod
    def cross_zscore(
        panel: pd.DataFrame,
        group_col: str = "date",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """截面 Z-Score。"""
        result = panel.copy()
        result["cross_zscore"] = result.groupby(group_col)[value_col].transform(
            lambda x: (x - x.mean()) / x.std() if x.std() > 0 else 0.0
        )
        return result

    @staticmethod
    def industry_neutral(
        panel: pd.DataFrame,
        group_col: str = "date",
        industry_col: str = "industry",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """行业中性化。"""
        result = panel.copy()
        result["industry_mean"] = result.groupby([group_col, industry_col])[
            value_col
        ].transform("mean")
        result["neutralized"] = result[value_col] - result["industry_mean"]
        return result


# ─── 跨品种算子 ─────────────────────────────────────────────


class CrossSymbolOps:
    """跨品种算子集合。"""

    @staticmethod
    def industry_demean(
        panel: pd.DataFrame,
        group_col: str = "date",
        industry_col: str = "industry",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """行业去均值 (中性化)。"""
        result = panel.copy()
        result["industry_mean"] = result.groupby([group_col, industry_col])[
            value_col
        ].transform("mean")
        result[value_col] = result[value_col] - result["industry_mean"]
        return result

    @staticmethod
    def cap_demean(
        panel: pd.DataFrame,
        group_col: str = "date",
        cap_col: str = "market_cap",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """市值去均值 (中性化)。"""
        result = panel.copy()
        result["cap_mean"] = result.groupby(group_col)[value_col].transform("mean")
        result["cap_weight"] = result[cap_col] / result.groupby(group_col)[cap_col].transform("sum")
        result[value_col] = result[value_col] - result["cap_mean"] * result["cap_weight"]
        return result

    @staticmethod
    def region_demean(
        panel: pd.DataFrame,
        group_col: str = "date",
        region_col: str = "region",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """区域去均值 (中性化)。"""
        result = panel.copy()
        result["region_mean"] = result.groupby([group_col, region_col])[
            value_col
        ].transform("mean")
        result[value_col] = result[value_col] - result["region_mean"]
        return result


# ─── 组合算子 ───────────────────────────────────────────────


class CompositeOps:
    """组合算子集合。"""

    @staticmethod
    def add(a: pd.Series, b: pd.Series) -> pd.Series:
        """加法。"""
        return a + b

    @staticmethod
    def sub(a: pd.Series, b: pd.Series) -> pd.Series:
        """减法。"""
        return a - b

    @staticmethod
    def mul(a: pd.Series, b: pd.Series) -> pd.Series:
        """乘法。"""
        return a * b

    @staticmethod
    def div(a: pd.Series, b: pd.Series) -> pd.Series:
        """除法 (安全除零保护)。"""
        return a / b.replace(0, np.nan)

    @staticmethod
    def scale(series: pd.Series, factor: float = 1.0) -> pd.Series:
        """缩放。"""
        return series * factor

    @staticmethod
    def if_then_else(
        condition: pd.Series,
        then_value: pd.Series | float,
        else_value: pd.Series | float,
    ) -> pd.Series:
        """条件算子。"""
        return pd.Series(
            np.where(condition, then_value, else_value),
            index=condition.index,
        )

    @staticmethod
    def conditional_weight(
        series: pd.Series,
        weight: pd.Series,
        threshold: float = 0.0,
    ) -> pd.Series:
        """条件加权。"""
        return pd.Series(
            np.where(series > threshold, series * weight, 0.0),
            index=series.index,
        )


# ─── 算子注册表 ─────────────────────────────────────────────


class OperatorRegistry:
    """特征算子注册表。

    管理所有可用算子，支持运行时查询和调用。

    Usage:
        registry = OperatorRegistry()
        result = registry.call("ts_mean", series, window=20)
    """

    def __init__(self) -> None:
        self._operators: dict[str, tuple[OperatorInfo, Callable]] = {}
        self._initialize_builtin()

    def register(
        self,
        name: str,
        func: Callable,
        category: str,
        params: list[str],
        description: str = "",
    ) -> None:
        """注册新算子。"""
        import datetime

        info = OperatorInfo(
            name=name,
            category=category,
            params=params,
            description=description,
            signature=f"{name}({', '.join(params)})",
            added_at=datetime.datetime.now().isoformat(),
        )
        self._operators[name] = (info, func)
        logger.debug("注册算子: %s [%s]", name, category)

    def call(self, name: str, *args: Any, **kwargs: Any) -> pd.Series:
        """调用算子。"""
        if name not in self._operators:
            raise KeyError(f"算子未注册: {name}")
        _, func = self._operators[name]
        return func(*args, **kwargs)

    def list_operators(self, category: Optional[str] = None) -> list[OperatorInfo]:
        """列出所有算子。"""
        operators = [info for info, _ in self._operators.values()]
        if category:
            operators = [op for op in operators if op.category == category]
        return sorted(operators, key=lambda x: x.name)

    def get_operator(self, name: str) -> Optional[OperatorInfo]:
        """获取算子信息。"""
        info, _ = self._operators.get(name, (None, None))
        return info

    def list_categories(self) -> list[str]:
        """列出所有算子类别。"""
        categories = {info.category for info, _ in self._operators.values()}
        return sorted(categories)

    @property
    def operator_count(self) -> int:
        """已注册算子数量。"""
        return len(self._operators)

    def _initialize_builtin(self) -> None:
        """初始化内置算子。"""
        # 时序算子
        ts_ops = [
            ("ts_mean", TimeSeriesOps.ts_mean, ["series", "window"]),
            ("ts_std", TimeSeriesOps.ts_std, ["series", "window"]),
            ("ts_max", TimeSeriesOps.ts_max, ["series", "window"]),
            ("ts_min", TimeSeriesOps.ts_min, ["series", "window"]),
            ("ts_sum", TimeSeriesOps.ts_sum, ["series", "window"]),
            ("ts_product", TimeSeriesOps.ts_product, ["series", "window"]),
        ]
        for name, func, params in ts_ops:
            self.register(name, func, "time_series", params)

        # 价格算子
        price_ops = [
            ("rank", PriceOps.rank, ["series"]),
            ("zscore", PriceOps.zscore, ["series"]),
            ("delta", PriceOps.delta, ["series", "periods"]),
            ("pct_change", PriceOps.pct_change, ["series", "periods"]),
            ("log_return", PriceOps.log_return, ["series"]),
            ("abs", lambda s: s.abs(), ["series"]),
            ("sign", lambda s: np.sign(s), ["series"]),
        ]
        for name, func, params in price_ops:
            self.register(name, func, "price", params)

        # 滚动算子
        rolling_ops = [
            ("ts_rank", RollingOps.ts_rank, ["series", "window"]),
            ("ts_zscore", RollingOps.ts_zscore, ["series", "window"]),
            ("ts_momentum", RollingOps.ts_momentum, ["series", "window"]),
            ("ts_volatility", RollingOps.ts_volatility, ["series", "window"]),
            ("ts_skewness", RollingOps.ts_skewness, ["series", "window"]),
            ("ts_kurtosis", RollingOps.ts_kurtosis, ["series", "window"]),
            ("ts_median", RollingOps.ts_median, ["series", "window"]),
            ("ts_min_max_diff", RollingOps.ts_min_max_diff, ["series", "window"]),
            ("ts_cum_max", RollingOps.ts_cum_max, ["series", "window"]),
        ]
        for name, func, params in rolling_ops:
            self.register(name, func, "rolling", params)

        # 技术指标算子
        tech_ops = [
            ("rsi", TechnicalOps.rsi, ["series", "window"]),
            ("bollinger_upper", TechnicalOps.bollinger_upper, ["series", "window", "num_std"]),
            ("bollinger_lower", TechnicalOps.bollinger_lower, ["series", "window", "num_std"]),
            ("bollinger_width", TechnicalOps.bollinger_width, ["series", "window", "num_std"]),
            ("atr", TechnicalOps.atr, ["high", "low", "close", "window"]),
            ("macd", TechnicalOps.macd, ["series", "fast", "slow", "signal"]),
            ("max_drawdown", TechnicalOps.max_drawdown, ["series", "window"]),
        ]
        for name, func, params in tech_ops:
            self.register(name, func, "technical", params)

        # 截面算子
        cs_ops = [
            ("cross_rank", CrossSectionOps.cross_rank, ["panel", "group_col", "value_col"]),
            ("cross_zscore", CrossSectionOps.cross_zscore, ["panel", "group_col", "value_col"]),
            ("cross_demean", lambda p, g, v: p.assign(**{v: p[v] - p.groupby(g)[v].transform("mean")}), ["panel", "group_col", "value_col"]),
            ("cross_median", lambda p, g, v: p.groupby(g)[v].transform("median"), ["panel", "group_col", "value_col"]),
            ("cross_std", lambda p, g, v: p.groupby(g)[v].transform("std"), ["panel", "group_col", "value_col"]),
        ]
        for name, func, params in cs_ops:
            self.register(name, func, "cross_section", params)

        # 跨品种算子
        csymbol_ops = [
            ("industry_demean", CrossSymbolOps.industry_demean, ["panel", "group_col", "industry_col", "value_col"]),
            ("cap_demean", CrossSymbolOps.cap_demean, ["panel", "group_col", "cap_col", "value_col"]),
            ("region_demean", CrossSymbolOps.region_demean, ["panel", "group_col", "region_col", "value_col"]),
        ]
        for name, func, params in csymbol_ops:
            self.register(name, func, "cross_symbol", params)

        # 组合算子
        comp_ops = [
            ("add", CompositeOps.add, ["a", "b"]),
            ("sub", CompositeOps.sub, ["a", "b"]),
            ("mul", CompositeOps.mul, ["a", "b"]),
            ("div", CompositeOps.div, ["a", "b"]),
            ("scale", CompositeOps.scale, ["series", "factor"]),
            ("if_then_else", CompositeOps.if_then_else, ["condition", "then_value", "else_value"]),
            ("conditional_weight", CompositeOps.conditional_weight, ["series", "weight", "threshold"]),
            ("max", lambda a, b: np.maximum(a, b), ["a", "b"]),
            ("min", lambda a, b: np.minimum(a, b), ["a", "b"]),
            ("pow", lambda a, b: np.power(a, b), ["a", "b"]),
            ("sqrt", lambda s: np.sqrt(s.abs()), ["series"]),
            ("exp", lambda s: np.exp(s), ["series"]),
            ("log", lambda s: np.log(s.abs() + 1e-10), ["series"]),
        ]
        for name, func, params in comp_ops:
            self.register(name, func, "composite", params)

        logger.info("初始化内置算子: %d 个", self.operator_count)


# ─── 特征工程中台主引擎 ─────────────────────────────────────


class FeatureOpsEngine:
    """特征工程中台主引擎。

    提供 GP 搜索、混合演化、特征重要性分析等统一入口。

    Usage:
        engine = FeatureOpsEngine()
        # 列出所有算子
        ops = engine.list_operators()
        # GP 搜索
        result = engine.run_gp_search(data, target_col='forward_return_20d')
        # 特征重要性分析
        importance = engine.analyze_importance(factor_series, data, 'forward_return_20d')
    """

    def __init__(self) -> None:
        self.registry = OperatorRegistry()

    def register_operator(
        self,
        name: str,
        func: Callable,
        category: str,
        params: list[str],
        description: str = "",
    ) -> None:
        """注册自定义算子。"""
        self.registry.register(name, func, category, params, description)

    def list_operators(self, category: Optional[str] = None) -> list[OperatorInfo]:
        """列出所有算子。"""
        return self.registry.list_operators(category)

    def get_operator(self, name: str) -> Optional[OperatorInfo]:
        """获取算子信息。"""
        return self.registry.get_operator(name)

    def list_categories(self) -> list[str]:
        """列出所有算子类别。"""
        return self.registry.list_categories()

    def run_gp_search(
        self,
        data: pd.DataFrame,
        target: str,
        config: Optional[dict[str, Any]] = None,
        train_mask: Optional[pd.Series] = None,
    ) -> Any:
        """运行 GP 演化搜索。

        Args:
            data: 特征数据面板
            target: 目标列名
            config: GP 配置覆盖
            train_mask: 训练集掩码（数据泄露防护），
                        仅当 train_mask 存在时，GPEvolver 在训练集上计算适应度

        Returns:
            GPEvolveResult
        """
        from .gp_evolver import GPEvolver, GPEvolverConfig

        gp_config = GPEvolverConfig()
        if config:
            for key, value in config.items():
                if hasattr(gp_config, key):
                    setattr(gp_config, key, value)

        gp = GPEvolver(
            operator_registry=self.registry,
            data_panel=data,
            target_col=target,
            config=gp_config,
            train_mask=train_mask,
        )
        return gp.evolve()

    def analyze_importance(
        self,
        factor_series: pd.Series,
        data: pd.DataFrame,
        target_col: str,
        feature_names: Optional[list[str]] = None,
    ) -> Any:
        """分析特征重要性。

        Args:
            factor_series: 因子值序列
            data: 原始特征数据
            target_col: 目标列名
            feature_names: 待分析特征列表 (默认所有数值列)

        Returns:
            FeatureImportanceResult
        """
        from .feature_importance import FeatureImportanceAnalyzer

        analyzer = FeatureImportanceAnalyzer()
        return analyzer.analyze(
            factor_series=factor_series,
            data=data,
            target_col=target_col,
            feature_names=feature_names,
        )