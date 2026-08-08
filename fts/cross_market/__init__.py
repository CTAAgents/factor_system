"""
fts.cross_market — 跨市场因子泛化验证模块

提供跨市场数据适配、泛化验证引擎、报告生成等功能。
支持期货→股票、期货→ETF、股票→期货三个方向的跨市场因子验证。

用法:
    from fts.cross_market import CrossMarketDataAdapter, CrossMarketEngine

    adapter = CrossMarketDataAdapter()
    engine = CrossMarketEngine(adapter)
    results = engine.run_futures_to_stock(days=120, max_stocks=50)

HARNESS §trace_id 全链路: 所有操作支持 trace_id 参数。
"""

from .data_adapter import CrossMarketDataAdapter, TARGET_MARKET_STOCK, TARGET_MARKET_ETF, TARGET_MARKET_FUTURES
from .engine import CrossMarketEngine, CrossMarketResult

__all__ = [
    "CrossMarketDataAdapter",
    "CrossMarketEngine",
    "CrossMarketResult",
    "TARGET_MARKET_STOCK",
    "TARGET_MARKET_ETF",
    "TARGET_MARKET_FUTURES",
]