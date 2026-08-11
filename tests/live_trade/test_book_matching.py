"""
tests/live_trade/test_book_matching.py — tick 盘口撮合测试（D.2 §4.8）。

覆盖:
    - build_book_from_ticks: 五档数组/单档字段/同价聚合/档位排序/depth 截断/无数据兜底
    - OrderBookMatchingEngine: 逐档消耗/深度不足部分成交/空盘口降级/滑点自然性
    - SimulatedPortfolio 集成: 注入盘口撮合走 book 价；未注入走 bps 路径（回归）
"""

from __future__ import annotations

import pytest

from fts.live_trade.book import build_book_from_ticks
from fts.live_trade.gateway import SimulatedGateway
from fts.live_trade.matching import OrderBookMatchingEngine
from fts.live_trade.orders import Order, OrderState
from fts.live_trade.simulated_portfolio import SimulatedPortfolio

ZERO_COST = None  # 使用默认成本模型（slippage=0.5bps，可测 bps 与 book 差异）


def _book(asks=None, bids=None, last=100.0, symbol="RB0"):
    """构造盘口（None 用默认档，显式空列表保留为空）。"""
    return {
        "symbol": symbol,
        "ts": "2026-01-06T09:30:00",
        "bid_levels": bids if bids is not None else [{"price": 99.0, "quantity": 10}],
        "ask_levels": asks if asks is not None else [{"price": 101.0, "quantity": 10}],
        "last_price": last,
        "tick_size": 1.0,
    }


class TestBuildBookFromTicks:
    def test_five_level_arrays(self) -> None:
        """五档数组形式：同价聚合、bid 降序/ask 升序、depth 截断。"""
        rows = [
            {"datetime": "t1", "bid_p": [99.0, 98.0, 97.0, 96.0, 95.0], "bid_v": [1, 2, 3, 4, 5],
             "ask_p": [101.0, 102.0, 103.0, 104.0, 105.0], "ask_v": [5, 4, 3, 2, 1]},
        ]
        book = build_book_from_ticks("RB0", rows, depth=5)
        assert book is not None
        assert [lv["price"] for lv in book["bid_levels"]] == [99.0, 98.0, 97.0, 96.0, 95.0]
        assert [lv["price"] for lv in book["ask_levels"]] == [101.0, 102.0, 103.0, 104.0, 105.0]
        assert book["bid_levels"][0]["quantity"] == 1

    def test_single_field_levels(self) -> None:
        """单档字段形式（bid_price1/ask_price1...）。"""
        rows = [
            {"bid_price1": 99.0, "bid_volume1": 10, "ask_price1": 101.0, "ask_volume1": 20},
            {"bid_price1": 99.5, "bid_volume1": 5, "ask_price1": 100.5, "ask_volume1": 15},
        ]
        book = build_book_from_ticks("AU0", rows, depth=5)
        assert book is not None
        assert len(book["ask_levels"]) == 2
        assert book["ask_levels"][0]["price"] == 100.5  # ask 升序，最优在前

    def test_same_price_aggregated(self) -> None:
        """同价档位跨行累加。"""
        rows = [
            {"bid_price1": 99.0, "bid_volume1": 10, "ask_price1": 101.0, "ask_volume1": 20},
            {"bid_price1": 99.0, "bid_volume1": 5, "ask_price1": 101.0, "ask_volume1": 30},
        ]
        book = build_book_from_ticks("RB0", rows, depth=5)
        assert book["bid_levels"][0]["quantity"] == 15.0
        assert book["ask_levels"][0]["quantity"] == 50.0

    def test_depth_truncation(self) -> None:
        """depth 截断只保留最优 N 档。"""
        rows = [{"bid_p": [99.0, 98.0, 97.0], "bid_v": [1, 1, 1],
                 "ask_p": [101.0, 102.0, 103.0], "ask_v": [1, 1, 1]}]
        book = build_book_from_ticks("RB0", rows, depth=2)
        assert len(book["bid_levels"]) == 2
        assert len(book["ask_levels"]) == 2

    def test_last_price_fallback(self) -> None:
        """无盘口字段但有 last_price → 单档兜底。"""
        book = build_book_from_ticks("RB0", [{"close": 100.5}], depth=5)
        assert book is not None
        assert book["last_price"] == 100.5
        assert book["bid_levels"][0]["price"] == 100.5

    def test_empty_rows_returns_none(self) -> None:
        """无任何有效行 → None（调用方降级）。"""
        assert build_book_from_ticks("RB0", [], depth=5) is None
        assert build_book_from_ticks("RB0", [{"foo": "bar"}], depth=5) is None


class TestOrderBookMatchingEngine:
    def test_level_by_level_consumption(self) -> None:
        """buy 3 手吃 1+1+1 三档，avg_price=加权均价，unfilled=0。"""
        engine = OrderBookMatchingEngine(depth=5)
        book = _book(
            asks=[{"price": 101.0, "quantity": 1}, {"price": 102.0, "quantity": 1}, {"price": 103.0, "quantity": 1}],
            bids=[{"price": 99.0, "quantity": 10}],
        )
        res = engine.match_market(book, "buy", 3.0, base_price=100.0)
        assert res["book_used"] is True
        assert res["filled_qty"] == pytest.approx(3.0)
        assert res["avg_price"] == pytest.approx((101 + 102 + 103) / 3)
        assert res["unfilled_qty"] == pytest.approx(0.0)

    def test_insufficient_depth_partial(self) -> None:
        """buy 5 手仅 3 手盘口 → filled=3, unfilled=2（部分成交）。"""
        engine = OrderBookMatchingEngine()
        book = _book(asks=[{"price": 101.0, "quantity": 2}, {"price": 102.0, "quantity": 1}])
        res = engine.match_market(book, "buy", 5.0, base_price=100.0)
        assert res["filled_qty"] == pytest.approx(3.0)
        assert res["unfilled_qty"] == pytest.approx(2.0)
        assert res["avg_price"] == pytest.approx((101 * 2 + 102 * 1) / 3)

    def test_none_book_degrades(self) -> None:
        """book=None → book_used=False（调用方降级 bps）。"""
        res = OrderBookMatchingEngine().match_market(None, "buy", 2.0, base_price=100.0)
        assert res["book_used"] is False

    def test_empty_levels_degrades(self) -> None:
        """对手盘空档 → book_used=False。"""
        engine = OrderBookMatchingEngine()
        res = engine.match_market(_book(asks=[], bids=[]), "buy", 2.0, base_price=100.0)
        assert res["book_used"] is False

    def test_slippage_from_spread(self) -> None:
        """盘口缺口大 → 滑点显著为正，且无人工 bps 偏移。"""
        engine = OrderBookMatchingEngine()
        # 对手盘最优档比基准价高 2%（base=100, ask=102）
        book = _book(asks=[{"price": 102.0, "quantity": 5}], bids=[{"price": 98.0, "quantity": 5}])
        res = engine.match_market(book, "buy", 2.0, base_price=100.0)
        assert res["avg_price"] == pytest.approx(102.0)
        assert res["slippage_bps"] == pytest.approx(200.0, abs=0.1)  # (102-100)/100*1e4=200bps

    def test_sell_side_uses_bid(self) -> None:
        """sell 按买盘档撮合（价格降序消耗）。"""
        engine = OrderBookMatchingEngine()
        book = _book(
            asks=[{"price": 101.0, "quantity": 5}],
            bids=[{"price": 99.0, "quantity": 1}, {"price": 98.0, "quantity": 1}],
        )
        res = engine.match_market(book, "sell", 2.0, base_price=100.0)
        assert res["filled_qty"] == pytest.approx(2.0)
        assert res["avg_price"] == pytest.approx((99 + 98) / 2)
        assert res["slippage_bps"] == pytest.approx(-150.0, abs=0.1)  # (98.5-100)/100*1e4=-150bps

    def test_invalid_qty_degrades(self) -> None:
        """qty<=0 → book_used=False。"""
        res = OrderBookMatchingEngine().match_market(_book(), "buy", 0.0, base_price=100.0)
        assert res["book_used"] is False

    def test_exception_degrades(self) -> None:
        """异常输入（盘口字段损坏）→ 降级不抛错。"""
        engine = OrderBookMatchingEngine()
        bad = {"ask_levels": [{"price": "NaN", "quantity": None}], "bid_levels": []}
        res = engine.match_market(bad, "buy", 2.0, base_price=100.0)
        assert res["book_used"] is False


class TestGatewayPartialFill:
    """PARTIAL 状态机（D.2 §4.8 #6）：部分成交 → 补单 → FILLED。"""

    def test_submit_partial_then_fill(self) -> None:
        """深度不足 → PARTIAL；补单满仓 → FILLED。"""
        gw = SimulatedGateway()
        order = Order(order_id="o1", symbol="RB0", direction="long", quantity=5.0, price=100.0)
        result = {"book_used": True, "filled_qty": 3.0, "unfilled_qty": 2.0, "avg_price": 101.0}
        gid = gw.submit_order(order, match_result=result)
        assert order.state == OrderState.PARTIAL
        assert order.filled_quantity == pytest.approx(3.0)

        # 补单 2 手 → FILLED
        gw.fill_partial(gid, 2.0, 102.0)
        assert order.state == OrderState.FILLED
        assert order.filled_quantity == pytest.approx(5.0)

    def test_fill_partial_increments(self) -> None:
        """补单未满 → 仍为 PARTIAL，数量累加。"""
        gw = SimulatedGateway()
        order = Order(order_id="o2", symbol="RB0", direction="long", quantity=10.0, price=100.0)
        gid = gw.submit_order(order, match_result={"book_used": True, "filled_qty": 4.0, "unfilled_qty": 6.0, "avg_price": 101.0})
        gw.fill_partial(gid, 2.0, 102.0)
        assert order.state == OrderState.PARTIAL
        assert order.filled_quantity == pytest.approx(6.0)
        # 继续补到满
        gw.fill_partial(gid, 4.0, 102.5)
        assert order.state == OrderState.FILLED

    def test_full_fill_remains_filled(self) -> None:
        """match_result 无 unfilled → 全量 FILLED（现状路径不变）。"""
        gw = SimulatedGateway()
        order = Order(order_id="o3", symbol="RB0", direction="long", quantity=2.0, price=100.0)
        gw.submit_order(order, match_result={"book_used": True, "filled_qty": 2.0, "unfilled_qty": 0.0, "avg_price": 101.0})
        assert order.state == OrderState.FILLED

    def test_fill_partial_idempotent_on_terminal(self) -> None:
        """终态订单补单：幂等不抛错。"""
        gw = SimulatedGateway()
        order = Order(order_id="o4", symbol="RB0", direction="long", quantity=2.0, price=100.0)
        gid = gw.submit_order(order)  # 全量 FILLED
        assert order.state == OrderState.FILLED
        assert gw.fill_partial(gid, 1.0, 100.0) is order  # 终态返回原订单


class TestLimitAndAuction:
    """P2：限价单撮合 + 集合竞价开盘价。"""

    def test_limit_fills_when_tradeable(self) -> None:
        """限价买入 ≥ ask 最优价 → 按限价成交。"""
        gw = SimulatedGateway()
        order = Order(order_id="l1", symbol="RB0", direction="long", quantity=2.0, price=101.0)
        book = _book(asks=[{"price": 101.0, "quantity": 10}], bids=[{"price": 99.0, "quantity": 10}])
        gw.submit_order(order, order_type="limit", book=book)
        assert order.state == OrderState.FILLED

    def test_limit_rests_when_untradeable(self) -> None:
        """限价低于 ask 最优价 → 挂单 SUBMITTED（待撤单/后续撮合）。"""
        gw = SimulatedGateway()
        order = Order(order_id="l2", symbol="RB0", direction="long", quantity=2.0, price=100.0)
        book = _book(asks=[{"price": 101.0, "quantity": 10}], bids=[{"price": 99.0, "quantity": 10}])
        gid = gw.submit_order(order, order_type="limit", book=book)
        assert order.state == OrderState.SUBMITTED
        # 撤单时机：挂单可撤
        assert gw.cancel_order(gid) is True
        assert order.state == OrderState.CANCELED

    def test_limit_sell_uses_bid(self) -> None:
        """限价卖出 ≥ bid 最优价 → 成交。"""
        gw = SimulatedGateway()
        order = Order(order_id="l3", symbol="RB0", direction="short", quantity=2.0, price=99.0)
        book = _book(asks=[{"price": 101.0, "quantity": 10}], bids=[{"price": 99.0, "quantity": 10}])
        gw.submit_order(order, order_type="limit", book=book)
        assert order.state == OrderState.FILLED

    def test_auction_open_equilibrium(self) -> None:
        """集合竞价：最大成交量均衡价。"""
        # buy: 限价≥100 愿买 5，≥101 愿买 3；sell: 限价≤101 愿卖 4，≤100 愿卖 2
        buys = [(100.0, 5.0), (101.0, 3.0)]
        sells = [(100.0, 2.0), (101.0, 4.0)]
        price = SimulatedGateway.auction_open(buys, sells)
        # price=100: buy cum=5, sell cum=2 → vol=2；price=101: buy=3, sell=4 → vol=3
        assert price == pytest.approx(101.0)

    def test_auction_open_empty(self) -> None:
        """无有效输入 → 0.0。"""
        assert SimulatedGateway.auction_open([], []) == pytest.approx(0.0)
        assert SimulatedGateway.auction_open([(100.0, 1.0)], []) == pytest.approx(0.0)


class TestPortfolioIntegration:
    def test_default_bps_path_unchanged(self) -> None:
        """未注入 matching：成交价走 bps（slippage=0.5bps → 3000*1.00005）。"""
        pf = SimulatedPortfolio(config={"initial_cash": 100_000.0})
        pf.apply_signal(
            {"signal_id": "s", "timestamp": "2026-01-05T10:00:00", "universe": ["RB0"],
             "signals": [{"symbol": "RB0", "direction": "long", "position": 1.0, "confidence": 0.9}]},
            {"RB0": 3000.0}, "2026-01-05",
        )
        pos = pf.positions()["RB0"]
        assert pos["avg_price"] == pytest.approx(3000.0 * 1.00005, rel=1e-6)

    def test_book_path_uses_spread(self) -> None:
        """注入 matching + book_provider：成交价 = 盘口加权均价。"""
        engine = OrderBookMatchingEngine()
        pf = SimulatedPortfolio(config={"initial_cash": 100_000.0})
        pf._matching = engine  # 直接注入（等价于构造参数）

        def provider(symbol):
            return _book(asks=[{"price": 3010.0, "quantity": 10}], bids=[{"price": 2990.0, "quantity": 10}])

        pf.set_book_provider(provider)
        pf.apply_signal(
            {"signal_id": "s", "timestamp": "2026-01-05T10:00:00", "universe": ["RB0"],
             "signals": [{"symbol": "RB0", "direction": "long", "position": 1.0, "confidence": 0.9}]},
            {"RB0": 3000.0}, "2026-01-05",
        )
        pos = pf.positions()["RB0"]
        assert pos["avg_price"] == pytest.approx(3010.0)  # 盘口价，非 3000*1.00005

    def test_book_provider_none_falls_back_to_bps(self) -> None:
        """book_provider 返回 None → 自动降级 bps。"""
        engine = OrderBookMatchingEngine()
        pf = SimulatedPortfolio(config={"initial_cash": 100_000.0})
        pf._matching = engine
        pf.set_book_provider(lambda symbol: None)
        pf.apply_signal(
            {"signal_id": "s", "timestamp": "2026-01-05T10:00:00", "universe": ["RB0"],
             "signals": [{"symbol": "RB0", "direction": "long", "position": 1.0, "confidence": 0.9}]},
            {"RB0": 3000.0}, "2026-01-05",
        )
        pos = pf.positions()["RB0"]
        assert pos["avg_price"] == pytest.approx(3000.0 * 1.00005, rel=1e-6)
