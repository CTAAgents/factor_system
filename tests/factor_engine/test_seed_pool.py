"""tests/factor_engine/test_seed_pool.py — 种子池测试。"""

from __future__ import annotations

import pytest

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.seed_pool import SeedPool, get_default_seed_pool

# 内置 9 个 + 外部 WQ101 101 个 + Qlib158 158 个 + GTJA191 191 个 + 基本面 23 个 = 482
_TOTAL_SEEDS = 9 + 101 + 158 + 191 + 23
_INTERNAL_NAMES = {
    "momentum", "volatility_reversion", "volume_flow",
    "macro_regime", "rate_proxy", "pmi_proxy",
    "value_factor", "quality_factor", "size_factor",
}


def test_seed_pool_loads_all_seeds():
    """种子池必须包含全部 482 个种子因子（内置 9 + 外部 473）。"""
    pool = SeedPool()
    seeds = pool.load_all_seeds()
    assert len(seeds) == _TOTAL_SEEDS


def test_seed_pool_loads_internal_only():
    """include_external=False 时只加载 9 个内置种子。"""
    pool = SeedPool()
    seeds = pool.load_all_seeds(include_external=False)
    assert len(seeds) == 9


def test_seed_pool_count():
    pool = SeedPool()
    assert pool.count() == _TOTAL_SEEDS


def test_seed_pool_list_names():
    """种子因子名称列表必须包含所有 482 个名称。"""
    pool = SeedPool()
    names = pool.list_names()
    # 必须包含所有内置名称
    assert _INTERNAL_NAMES.issubset(set(names))
    # 必须包含 alpha_001 ~ alpha_101
    assert "alpha_001" in names
    assert "alpha_101" in names
    # 必须包含 qlib_001 ~ qlib_158
    assert "qlib_001" in names
    assert "qlib_158" in names
    # 必须包含 gtja_001 ~ gtja_191
    assert "gtja_001" in names
    assert "gtja_191" in names
    # 必须包含基本面因子
    assert "fund_val_pe" in names
    assert "fund_quality_roe" in names
    assert "fund_growth_revenue" in names
    assert "fund_macro_pmi" in names
    assert "fund_alt_val_quality" in names
    # 总数正确
    assert len(names) == _TOTAL_SEEDS


def test_seed_pool_get_by_name_internal():
    pool = SeedPool()
    seed = pool.get_seed("momentum")
    assert seed is not None
    assert seed["name"] == "momentum"
    assert "def factor_program" in seed["code"]


def test_seed_pool_get_by_name_external():
    """外部种子可通过名称查询。"""
    pool = SeedPool()
    seed = pool.get_seed("alpha_001")
    assert seed is not None
    assert seed["name"] == "alpha_001"
    assert "def factor_program" in seed["code"]

    seed = pool.get_seed("qlib_001")
    assert seed is not None
    assert seed["name"] == "qlib_001"


def test_seed_pool_get_nonexistent_returns_none():
    pool = SeedPool()
    assert pool.get_seed("nonexistent") is None


def test_seed_factor_has_valid_structure():
    """每个种子因子必须满足 FactorProgram 契约。"""
    pool = SeedPool()
    for seed in pool.load_all_seeds():
        assert "factor_id" in seed
        assert seed["factor_id"].startswith("fct_")
        assert "name" in seed
        assert "code" in seed
        assert "params" in seed
        assert "signature" in seed
        assert "economic_logic" in seed
        assert seed["source"] == "seed"
        assert seed["generation"] == 0


def test_seed_factor_code_is_compilable():
    """每个种子因子的代码必须能通过安全沙箱验证。"""
    from fts.factor_engine.factor_program import validate_factor_code
    pool = SeedPool()
    for seed in pool.load_all_seeds():
        ok, reasons = validate_factor_code(seed["code"])
        assert ok, f"种子因子 {seed['name']} 编译失败: {reasons}"


def test_seed_factor_has_economic_logic_narrative():
    """每个种子因子的经济逻辑 narrative 不能为空。"""
    pool = SeedPool()
    for seed in pool.load_all_seeds():
        el = seed["economic_logic"]
        assert el["narrative"], f"种子 {seed['name']} 缺少经济逻辑 narrative"


def test_seed_factor_has_four_economic_dimensions():
    """每个种子因子的经济逻辑必须包含四维评分。"""
    pool = SeedPool()
    for seed in pool.load_all_seeds():
        el = seed["economic_logic"]
        assert "theory" in el
        assert "behavioral" in el
        assert "microstructure" in el
        assert "institutional" in el
        # 每维评分 0-5
        for dim in ["theory", "behavioral", "microstructure", "institutional"]:
            assert 0 <= el[dim] <= 5


def test_default_seed_pool_singleton():
    """get_default_seed_pool 每次应返回新实例（无状态）。"""
    p1 = get_default_seed_pool()
    p2 = get_default_seed_pool()
    assert p1.count() == _TOTAL_SEEDS
    assert p2.count() == _TOTAL_SEEDS


def test_external_seed_code_has_alpha_ops():
    """量价外部种子代码必须包含公共操作函数（基本面种子使用不同模板）。"""
    pool = SeedPool()
    for seed in pool.load_all_seeds():
        if seed["name"].startswith("alpha_") or seed["name"].startswith("qlib_") or seed["name"].startswith("gtja_"):
            assert "rank" in seed["code"], f"{seed['name']} 缺少 rank 操作"
            assert "ts_sum" in seed["code"], f"{seed['name']} 缺少 ts_sum 操作"


def test_wq101_all_present():
    """WQ 101 因子必须全部存在。"""
    pool = SeedPool()
    names = pool.list_names()
    for i in range(1, 102):
        assert f"alpha_{i:03d}" in names, f"缺少 alpha_{i:03d}"


def test_qlib158_all_present():
    """Qlib 158 因子必须全部存在。"""
    pool = SeedPool()
    names = pool.list_names()
    for i in range(1, 159):
        assert f"qlib_{i:03d}" in names, f"缺少 qlib_{i:03d}"


def test_gtja191_all_present():
    """国泰君安 191 因子必须全部存在。"""
    pool = SeedPool()
    names = pool.list_names()
    for i in range(1, 192):
        assert f"gtja_{i:03d}" in names, f"缺少 gtja_{i:03d}"


# ─── 期货种子因子测试 ─────────────────────────────────────

_FUTURES_SEED_NAMES = {
    # 家族 1: 动量因子家族 (5)
    "fut_xsmom", "fut_tsmom", "fut_short_reversal",
    "fut_composite_momentum", "fut_basis_momentum",
    # 家族 2: 期限结构因子家族 (3)
    "fut_roll_yield_carry", "fut_stable_term_structure", "fut_basis_factor",
    # 家族 3: 持仓/资金流因子家族 (3)
    "fut_open_interest_full", "fut_warehouse_receipt", "fut_hedge_pressure",
    # 家族 4: 流动性因子家族 (3)
    "fut_turnover", "fut_bid_ask_spread", "fut_amihud_full",
    # 家族 5: 偏度/峰度/高阶矩因子家族 (3)
    "fut_skewness_full", "fut_upside_skewness", "fut_kurtosis",
    # 家族 6: 波动率因子家族 (2)
    "fut_cv", "fut_downside_volatility",
    # 家族 7: 基本面因子家族 (4)
    "fut_volume_price_corr_full", "fut_trend_strength", "fut_amplitude",
    "fut_mobile_big_data",
    # 家族 8: 拥挤度因子家族 (6)
    "fut_crowd_volume", "fut_crowd_volatility", "fut_crowd_turnover",
    "fut_crowd_bias_volume", "fut_crowd_bias_amount", "fut_crowd_composite",
    # 家族 9: Alpha/量价行为因子家族 (4)
    "fut_time_series_regression", "fut_bias", "fut_gp_alpha1", "fut_ht_alpha",
    # 家族 10: 高频因子家族 (6)
    "fut_hf_quote_imbalance", "fut_hf_trade_imbalance",
    "fut_hf_historical_return", "fut_hf_turnover", "fut_hf_spread",
    "fut_hf_down_vol",
    # 家族 11: 期权隐含信息因子家族 (3)
    "fut_option_vol_term", "fut_option_skew", "fut_option_pcr",
    # 家族 12: 市场环境因子家族 (8)
    "fut_macro_cpi", "fut_macro_interest_rate", "fut_macro_export",
    "fut_macro_us_bond", "fut_mkt_trend", "fut_mkt_speculation",
    "fut_mkt_rotation", "fut_mkt_concentration",
}


def test_futures_seed_pool_loads_all_seeds():
    """期货模式加载 50 个期货专用种子因子（12大因子家族）。"""
    pool = SeedPool(market="futures")
    seeds = pool.load_all_seeds()
    assert len(seeds) == 50


def test_futures_seed_pool_count():
    """期货模式 count() 返回 50。"""
    pool = SeedPool(market="futures")
    assert pool.count() == 50


def test_futures_seed_pool_list_names():
    """期货模式必须包含所有 50 个期货专用因子名称。"""
    pool = SeedPool(market="futures")
    names = pool.list_names()
    assert _FUTURES_SEED_NAMES.issubset(set(names))
    assert len(names) == 50


def test_futures_seed_pool_no_stock_seeds():
    """期货模式不应包含任何股票种子因子。"""
    pool = SeedPool(market="futures")
    names = pool.list_names()
    stock_names = {"momentum", "volatility_reversion", "volume_flow",
                   "macro_regime", "rate_proxy", "pmi_proxy",
                   "value_factor", "quality_factor", "size_factor"}
    assert stock_names.isdisjoint(set(names)), "期货模式不应包含股票种子"
    # 不应包含外部量价因子
    assert "alpha_001" not in names
    assert "qlib_001" not in names
    assert "gtja_001" not in names
    assert "fund_val_pe" not in names


def test_futures_seed_get_by_name():
    """期货种子可通过名称查询。"""
    pool = SeedPool(market="futures")
    seed = pool.get_seed("fut_roll_yield_carry")
    assert seed is not None
    assert seed["name"] == "fut_roll_yield_carry"
    assert "def factor_program" in seed["code"]


def test_futures_seed_has_valid_structure():
    """每个期货种子因子必须满足 FactorProgram 契约。"""
    pool = SeedPool(market="futures")
    for seed in pool.load_all_seeds():
        assert "factor_id" in seed
        assert seed["factor_id"].startswith("fct_")
        assert "name" in seed
        assert "code" in seed
        assert "params" in seed
        assert "signature" in seed
        assert "economic_logic" in seed
        assert seed["source"] == "seed"
        assert seed["generation"] == 0


def test_futures_seed_code_is_compilable():
    """每个期货种子因子的代码必须能通过安全沙箱验证。"""
    from fts.factor_engine.factor_program import validate_factor_code
    pool = SeedPool(market="futures")
    for seed in pool.load_all_seeds():
        ok, reasons = validate_factor_code(seed["code"])
        assert ok, f"期货种子 {seed['name']} 编译失败: {reasons}"


def test_futures_seed_has_economic_logic_narrative():
    """每个期货种子因子的经济逻辑 narrative 不能为空。"""
    pool = SeedPool(market="futures")
    for seed in pool.load_all_seeds():
        el = seed["economic_logic"]
        assert el["narrative"], f"期货种子 {seed['name']} 缺少经济逻辑 narrative"


def test_futures_seed_has_four_economic_dimensions():
    """每个期货种子因子必须包含四维经济逻辑评分。"""
    pool = SeedPool(market="futures")
    for seed in pool.load_all_seeds():
        el = seed["economic_logic"]
        for dim in ["theory", "behavioral", "microstructure", "institutional"]:
            assert dim in el
            assert 0 <= el[dim] <= 5


def test_futures_seed_inject_from_l1():
    """L1 注入在期货模式下仍正常工作。"""
    pool = SeedPool(market="futures")
    # 先加载种子，再注入 L1
    pool.load_all_seeds()
    assert pool.count() == 50
    candidate = {
        "name": "fut_test_candidate",
        "code": "def factor_program(data, params):\n    import numpy as np\n    return np.clip(data['close'], -1, 1)",
        "params": {},
        "signature": {"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 5},
        "economic_logic": {"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "测试"},
        "candidate_id": "test_001",
    }
    injected = pool.inject_from_l1(candidate)
    assert injected is not None
    assert injected["source"] == "bootstrapping"
    # 注入后种子数不变（L1 注入不计入 base seeds）
    assert pool.count() == 50
    injected_list = pool.list_injected_l1()
    assert len(injected_list) == 1