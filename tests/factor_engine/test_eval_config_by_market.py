"""
tests/factor_engine/test_eval_config_by_market.py — 评估口径按市场分离测试（plans/47 §C1/§C3）。

覆盖:
    - STOCK_EVAL_CONFIG / FUTURES_EVAL_CONFIG 阈值分离（截面样本量差异）
    - get_eval_config 按市场路由（futures/energy → 期货口径；stock → 股票口径）
    - energy 路径 FactorVerifier 可实例化（cli 回归）

纯配置/契约测试，无 DB/IO。
"""

from __future__ import annotations

from fts.factor_engine.contracts import (
    FUTURES_EVAL_CONFIG,
    FUTURES_VERIFIER_CONFIG,
    STOCK_EVAL_CONFIG,
    DEFAULT_VERIFIER_CONFIG,
    get_eval_config,
)


class TestEvalConfigSeparation:
    def test_market_configs_differ(self):
        # 截面样本差一个量级 → 期货放宽、股票基准，关键阈值不可共用
        assert FUTURES_EVAL_CONFIG["min_icir"] < STOCK_EVAL_CONFIG["min_icir"]  # 0.3 < 0.5
        assert FUTURES_EVAL_CONFIG["min_sharpe"] < STOCK_EVAL_CONFIG["min_sharpe"]  # 1.0 < 1.5
        assert FUTURES_EVAL_CONFIG["min_t_stat"] < STOCK_EVAL_CONFIG["min_t_stat"]  # 2.0 < 3.0

    def test_aliases_preserve_existing_behavior(self):
        # 命名别名为既有配置语义（不改变现状行为）
        assert FUTURES_EVAL_CONFIG is FUTURES_VERIFIER_CONFIG
        assert STOCK_EVAL_CONFIG is DEFAULT_VERIFIER_CONFIG

    def test_route_futures_energy(self):
        assert get_eval_config("futures") is FUTURES_EVAL_CONFIG
        assert get_eval_config("energy") is FUTURES_EVAL_CONFIG

    def test_route_stock_and_unknown(self):
        assert get_eval_config("stock") is STOCK_EVAL_CONFIG
        assert get_eval_config("etf") is STOCK_EVAL_CONFIG  # 未知市场回退股票基准

    def test_energy_verifier_instantiable(self):
        # energy 演化路径（cli.py）回归：FactorVerifier(FUTURES_EVAL_CONFIG)
        from fts.factor_engine.verifier import FactorVerifier

        v = FactorVerifier(FUTURES_EVAL_CONFIG)
        assert v is not None
