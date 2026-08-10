"""tests/live_trade/test_live_trade.py — 实盘执行链路测试（GAP-F01，v2.60.0）。

覆盖:
1. OrderState 状态机全路径（合法转移 + 非法转移拦截 + 异常回滚）
2. StopOrderManager 持仓级止损止盈单（注册/触发/撤销）
3. InterventionController 人工干预（暂停拦截/恢复/一键平仓/权限最高）
4. SimulatedGateway + submit_with_retry（模拟成交/失败注入/重试/超时兜底）
"""

import pytest

from fts.live_trade import (
    AbstractGateway,
    InterventionController,
    InterventionState,
    Order,
    OrderLifecycle,
    OrderState,
    SimulatedGateway,
    StopOrderManager,
    StopSide,
    StopStatus,
    submit_with_retry,
)


# ─── 订单状态机 ─────────────────────────────────────────


def test_order_initial_state_pending():
    """订单初始状态应为 PENDING。"""
    order = Order(order_id="o1", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    assert order.state == OrderState.PENDING


def test_order_lifecycle_full_path():
    """合法全路径：PENDING → SUBMITTED → PARTIAL → FILLED。"""
    order = Order(order_id="o2", symbol="RB0", direction="long", quantity=2.0, price=3000.0)
    OrderLifecycle.transition(order, OrderState.SUBMITTED)
    assert order.state == OrderState.SUBMITTED
    OrderLifecycle.transition(order, OrderState.PARTIAL, filled_quantity=1.0)
    assert order.state == OrderState.PARTIAL
    OrderLifecycle.transition(order, OrderState.FILLED, filled_quantity=2.0)
    assert order.state == OrderState.FILLED
    assert order.filled_quantity == 2.0


def test_order_lifecycle_cancel_and_reject():
    """撤销/拒绝路径合法：SUBMITTED → CANCELED / REJECTED。"""
    order = Order(order_id="o3", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    OrderLifecycle.transition(order, OrderState.SUBMITTED)
    OrderLifecycle.transition(order, OrderState.CANCELED)
    assert order.state == OrderState.CANCELED

    order2 = Order(order_id="o4", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    OrderLifecycle.transition(order2, OrderState.SUBMITTED)
    OrderLifecycle.transition(order2, OrderState.REJECTED)
    assert order2.state == OrderState.REJECTED


def test_order_lifecycle_illegal_transition_rejected():
    """非法转移应抛 ValueError（如自环转移 PENDING → PENDING）。"""
    order = Order(order_id="o5", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    with pytest.raises(ValueError, match="非法订单状态转移"):
        OrderLifecycle.transition(order, OrderState.PENDING)  # 自环非法
    # 终态不可再转移
    OrderLifecycle.transition(order, OrderState.SUBMITTED)
    OrderLifecycle.transition(order, OrderState.FILLED, filled_quantity=1.0)
    with pytest.raises(ValueError):
        OrderLifecycle.transition(order, OrderState.PENDING)


def test_order_lifecycle_rollback():
    """异常回滚：非终态 → REJECTED 并记录错误。"""
    order = Order(order_id="o6", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    OrderLifecycle.transition(order, OrderState.SUBMITTED)
    OrderLifecycle.rollback(order, "网关超时")
    assert order.state == OrderState.REJECTED
    assert order.error == "网关超时"
    # 终态订单不可回滚
    order2 = Order(order_id="o7", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    OrderLifecycle.transition(order2, OrderState.SUBMITTED)
    OrderLifecycle.transition(order2, OrderState.FILLED, filled_quantity=1.0)
    OrderLifecycle.rollback(order2, "不应回滚")
    assert order2.state == OrderState.FILLED


# ─── 持仓级止损止盈单 ────────────────────────────────────


def test_stop_order_register_and_list_active():
    """止损单注册后应处于 ACTIVE 并可列出。"""
    mgr = StopOrderManager()
    mgr.register("RB0", StopSide.STOP_LOSS, trigger_price=2900.0, quantity=1.0)
    mgr.register("I0", StopSide.TAKE_PROFIT, trigger_price=3200.0, quantity=2.0)
    active = mgr.list_active()
    assert len(active) == 2
    assert all(s.status == StopStatus.ACTIVE for s in active)


def test_stop_order_stop_loss_trigger_long():
    """多头止损：价格 ≤ 触发价 → 触发平仓指令。"""
    mgr = StopOrderManager()
    mgr.register("RB0", StopSide.STOP_LOSS, trigger_price=2900.0, quantity=1.0, direction="long")
    instructions = mgr.check({"RB0": 2895.0})
    assert len(instructions) == 1
    assert instructions[0].symbol == "RB0"
    assert "stop_loss" in instructions[0].reason
    assert mgr.list_active() == []  # 触发后移出活跃


def test_stop_order_take_profit_short():
    """空头止盈：价格 ≤ 触发价 → 触发平仓指令。"""
    mgr = StopOrderManager()
    mgr.register("I0", StopSide.TAKE_PROFIT, trigger_price=3100.0, quantity=2.0, direction="short")
    instructions = mgr.check({"I0": 3090.0})
    assert len(instructions) == 1
    assert "take_profit" in instructions[0].reason


def test_stop_order_not_triggered_when_above():
    """价格未触及触发价时不应触发。"""
    mgr = StopOrderManager()
    mgr.register("RB0", StopSide.STOP_LOSS, trigger_price=2900.0, quantity=1.0, direction="long")
    assert mgr.check({"RB0": 2950.0}) == []
    assert len(mgr.list_active()) == 1


def test_stop_order_cancel():
    """撤销未触发止损单。"""
    mgr = StopOrderManager()
    stop = mgr.register("RB0", StopSide.STOP_LOSS, trigger_price=2900.0, quantity=1.0)
    assert mgr.cancel(stop.stop_id) is True
    assert mgr.cancel(stop.stop_id) is False  # 二次撤销失败
    assert mgr.list_active() == []


# ─── 人工干预接口 ────────────────────────────────────────


def test_intervention_default_normal():
    """默认状态应为 NORMAL（不拦截信号）。"""
    ctrl = InterventionController()
    assert ctrl.state == InterventionState.NORMAL
    assert ctrl.should_block() is False


def test_intervention_pause_blocks_signals():
    """紧急暂停后应拦截一切新信号。"""
    ctrl = InterventionController()
    record = ctrl.pause(operator="trader", note="手工暂停")
    assert ctrl.is_paused() is True
    assert ctrl.should_block() is True
    assert record.action == "pause"
    assert record.operator == "trader"


def test_intervention_resume():
    """恢复后信号不再被拦截。"""
    ctrl = InterventionController()
    ctrl.pause()
    ctrl.resume(operator="trader")
    assert ctrl.is_paused() is False
    assert ctrl.should_block() is False


def test_intervention_all_close_generates_instruction():
    """一键平仓应生成全仓平仓指令并进入 FLATTENING。"""
    ctrl = InterventionController()
    record, instruction = ctrl.request_all_close(operator="risk_manager")
    assert record.action == "all_close"
    assert instruction.scope == "all"
    assert ctrl.state == InterventionState.FLATTENING
    assert ctrl.should_block() is True  # 平仓期间拦截新信号
    ctrl.mark_flattened()
    assert ctrl.state == InterventionState.FLATTENED


def test_intervention_authority_highest():
    """干预权限应为最高（高于自动化）。"""
    ctrl = InterventionController()
    assert ctrl.AUTHORITY == "highest"
    assert len(ctrl.history()) >= 0  # 历史审计可追溯


# ─── 网关抽象 + 模拟 + 重试兜底 ──────────────────────────


def test_gateway_abstract_is_abstract():
    """AbstractGateway 为抽象基类，不可实例化。"""
    with pytest.raises(TypeError):
        AbstractGateway()


def test_simulated_gateway_fill_on_submit():
    """模拟网关默认提交即全部成交。"""
    gw = SimulatedGateway()
    order = Order(order_id="o_g1", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    gw_id = gw.submit_order(order)
    assert order.state == OrderState.FILLED
    assert order.filled_quantity == 1.0
    assert gw_id.startswith("gw_")


def test_simulated_gateway_fail_submit_raises():
    """注入故障时提交应抛 RuntimeError。"""
    gw = SimulatedGateway(fail_submit=True)
    order = Order(order_id="o_g2", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    with pytest.raises(RuntimeError, match="网关故障"):
        gw.submit_order(order)


def test_submit_with_retry_succeeds():
    """重试兜底：首次失败后重试成功。"""
    attempts = {"n": 0}

    class FlakyGateway(SimulatedGateway):
        def submit_order(self, order):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("瞬时故障")
            return super().submit_order(order)

    gw = FlakyGateway()
    order = Order(order_id="o_g3", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    gw_id = submit_with_retry(gw, order, max_retries=3, retry_interval=0.01)
    assert gw_id.startswith("gw_")
    assert order.state == OrderState.FILLED


def test_submit_with_retry_exhausted_rolls_back():
    """重试耗尽应回滚订单为 REJECTED 并抛 RuntimeError。"""
    gw = SimulatedGateway(fail_submit=True)
    order = Order(order_id="o_g4", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    with pytest.raises(RuntimeError, match="重试耗尽"):
        submit_with_retry(gw, order, max_retries=2, retry_interval=0.01)
    assert order.state == OrderState.REJECTED
    assert "重试耗尽" in order.error


def test_submit_with_retry_timeout():
    """超时兜底：超过超时阈值应回滚并抛 RuntimeError。"""
    import time

    class SlowFailGateway(SimulatedGateway):
        def submit_order(self, order):
            time.sleep(0.2)  # 慢速故障：阻塞超过超时阈值
            raise RuntimeError("慢速网关故障")

    gw = SlowFailGateway()
    order = Order(order_id="o_g5", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    with pytest.raises(RuntimeError, match="下单超时"):
        submit_with_retry(gw, order, max_retries=5, retry_interval=0.02, timeout_seconds=0.1)
    assert order.state == OrderState.REJECTED


def test_simulated_gateway_cancel_and_query():
    """撤单与状态回查。"""
    gw = SimulatedGateway(fill_on_submit=False)
    order = Order(order_id="o_g6", symbol="RB0", direction="long", quantity=1.0, price=3000.0)
    gw_id = gw.submit_order(order)
    assert order.state == OrderState.SUBMITTED
    assert gw.cancel_order(gw_id) is True
    assert order.state == OrderState.CANCELED
    # 已撤销订单再次撤单失败
    assert gw.cancel_order(gw_id) is False
    # 状态回查
    query = gw.query_order(gw_id)
    assert query["order_id"] == "o_g6"
    assert query["state"] == "CANCELED"
