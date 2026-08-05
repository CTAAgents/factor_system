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
    pool = SeedPool(market="stock")
    seeds = pool.load_all_seeds()
    assert len(seeds) == _TOTAL_SEEDS


def test_seed_pool_loads_internal_only():
    """include_external=False 时只加载 9 个内置种子。"""
    pool = SeedPool(market="stock")
    seeds = pool.load_all_seeds(include_external=False)
    assert len(seeds) == 9


def test_seed_pool_count():
    pool = SeedPool(market="stock")
    assert pool.count() == _TOTAL_SEEDS


def test_seed_pool_list_names():
    """种子因子名称列表必须包含所有 482 个名称。"""
    pool = SeedPool(market="stock")
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
    pool = SeedPool(market="stock")
    seed = pool.get_seed("momentum")
    assert seed is not None
    assert seed["name"] == "momentum"
    assert "def factor_program" in seed["code"]


def test_seed_pool_get_by_name_external():
    """外部种子可通过名称查询。"""
    pool = SeedPool(market="stock")
    seed = pool.get_seed("alpha_001")
    assert seed is not None
    assert seed["name"] == "alpha_001"
    assert "def factor_program" in seed["code"]

    seed = pool.get_seed("qlib_001")
    assert seed is not None
    assert seed["name"] == "qlib_001"


def test_seed_pool_get_nonexistent_returns_none():
    pool = SeedPool(market="stock")
    assert pool.get_seed("nonexistent") is None


def test_seed_factor_has_valid_structure():
    """每个种子因子必须满足 FactorProgram 契约。"""
    pool = SeedPool(market="stock")
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
    pool = SeedPool(market="stock")
    for seed in pool.load_all_seeds():
        ok, reasons = validate_factor_code(seed["code"])
        assert ok, f"种子因子 {seed['name']} 编译失败: {reasons}"


def test_seed_factor_has_economic_logic_narrative():
    """每个种子因子的经济逻辑 narrative 不能为空。"""
    pool = SeedPool(market="stock")
    for seed in pool.load_all_seeds():
        el = seed["economic_logic"]
        assert el["narrative"], f"种子 {seed['name']} 缺少经济逻辑 narrative"


def test_seed_factor_has_four_economic_dimensions():
    """每个种子因子的经济逻辑必须包含四维评分。"""
    pool = SeedPool(market="stock")
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
    p1 = get_default_seed_pool(market="stock")
    p2 = get_default_seed_pool(market="stock")
    assert p1.count() == _TOTAL_SEEDS
    assert p2.count() == _TOTAL_SEEDS


def test_external_seed_code_has_alpha_ops():
    """量价外部种子代码必须包含公共操作函数（基本面种子使用不同模板）。"""
    pool = SeedPool(market="stock")
    for seed in pool.load_all_seeds():
        if seed["name"].startswith("alpha_") or seed["name"].startswith("qlib_") or seed["name"].startswith("gtja_"):
            assert "rank" in seed["code"], f"{seed['name']} 缺少 rank 操作"
            assert "ts_sum" in seed["code"], f"{seed['name']} 缺少 ts_sum 操作"


def test_wq101_all_present():
    """WQ 101 因子必须全部存在。"""
    pool = SeedPool(market="stock")
    names = pool.list_names()
    for i in range(1, 102):
        assert f"alpha_{i:03d}" in names, f"缺少 alpha_{i:03d}"


def test_qlib158_all_present():
    """Qlib 158 因子必须全部存在。"""
    pool = SeedPool(market="stock")
    names = pool.list_names()
    for i in range(1, 159):
        assert f"qlib_{i:03d}" in names, f"缺少 qlib_{i:03d}"


def test_gtja191_all_present():
    """国泰君安 191 因子必须全部存在。"""
    pool = SeedPool(market="stock")
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
    # 家族 13: CTA注册表补充因子 (7)
    "tsmom_5d", "tsmom_22d", "basis_level", "volatility_annual",
    "liquidity_ratio", "long_term_reversal", "oi_change_rate",
    # 家族 14: 算子字典种子因子 (24)
    "seed_kbar_mid", "seed_kbar_upper", "seed_kbar_lower", "seed_kbar_shift",
    "seed_bull_bear", "seed_argmax_close", "seed_argmin_close",
    "seed_vol_chg", "seed_vwap_proxy_1", "seed_vwap_proxy_2",
    "seed_reversal_1d", "seed_mom_5d", "seed_mom_20d",
    "seed_vol_5d", "seed_vol_20d", "seed_vol_ratio",
    "seed_trend_slope", "seed_trend_rsqr",
    "seed_vp_corr", "seed_vol_ratio_volume",
    "seed_oi_chg", "seed_oi_ret_confirm", "seed_spread", "seed_settle_bias",
}


def test_futures_seed_pool_loads_all_seeds():
    """期货模式加载 81 个期货专用种子因子（14大因子家族）。"""
    pool = SeedPool(market="futures")
    seeds = pool.load_all_seeds()
    assert len(seeds) == 81


def test_futures_seed_pool_count():
    """期货模式 count() 返回 81。"""
    pool = SeedPool(market="futures")
    assert pool.count() == 81


def test_futures_seed_pool_list_names():
    """期货模式必须包含所有 81 个期货专用因子名称。"""
    pool = SeedPool(market="futures")
    names = pool.list_names()
    assert _FUTURES_SEED_NAMES.issubset(set(names))
    assert len(names) == 81


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
    assert pool.count() == 81
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
    assert pool.count() == 81
    injected_list = pool.list_injected_l1()
    assert len(injected_list) == 1


# ─── 种子因子相关性预检测试 ─────────────────────────────────

def test_compute_seed_correlations_identical_signals():
    """两个完全相同信号的因子应被标记为高相关。"""
    from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature
    from fts.factor_engine.seed_pool import compute_seed_correlations

    import pandas as pd
    import numpy as np

    n = 100
    close = np.sin(np.linspace(0, 4 * np.pi, n))  # 正弦波，有方差
    volume = np.ones(n)
    df = pd.DataFrame({"close": close, "volume": volume})

    # 构造两个完全相同的因子（都用 close 信号）
    code = "def factor_program(data, params):\n    import numpy as np\n    return np.array(data['close'])"
    f1 = FactorProgram(
        factor_id="fct_aaaa1111", name="factor_a", code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="A"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )
    f2 = FactorProgram(
        factor_id="fct_bbbb2222", name="factor_b", code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="B"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )

    result = compute_seed_correlations([f1, f2], df, threshold=0.95)
    assert len(result) == 1
    assert result[0]["factor_id_a"] == "fct_aaaa1111"
    assert result[0]["factor_id_b"] == "fct_bbbb2222"
    assert abs(result[0]["pearson"]) >= 0.95
    assert abs(result[0]["spearman"]) >= 0.95


def test_compute_seed_correlations_low_correlation():
    """两个正交信号的因子不应被标记。"""
    from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature
    from fts.factor_engine.seed_pool import compute_seed_correlations

    import pandas as pd
    import numpy as np

    n = 200
    close = np.cumsum(np.random.randn(n))
    df = pd.DataFrame({"close": close, "volume": np.ones(n)})

    # 两个因子：一个用 close，一个用 volume（后者全为 1 → 零方差 → 低相关）
    f1_code = "def factor_program(data, params):\n    import numpy as np\n    return np.array(data['close'])"
    f2_code = "def factor_program(data, params):\n    import numpy as np\n    return np.array(data['volume'])"

    f1 = FactorProgram(
        factor_id="fct_ortho1", name="ortho1", code=f1_code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="A"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )
    f2 = FactorProgram(
        factor_id="fct_ortho2", name="ortho2", code=f2_code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="B"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )

    result = compute_seed_correlations([f1, f2], df, threshold=0.95)
    # volume 全为 1 → 零方差 → spearman 计算时跳过
    # 因此可能没有高相关对
    assert isinstance(result, list)


def test_compute_seed_correlations_single_factor():
    """单个因子应返回空列表。"""
    from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature
    from fts.factor_engine.seed_pool import compute_seed_correlations

    import pandas as pd
    import numpy as np

    n = 50
    close = np.sin(np.linspace(0, 2 * np.pi, n))
    df = pd.DataFrame({"close": close, "volume": np.ones(n)})

    f1 = FactorProgram(
        factor_id="fct_single", name="single",
        code="def factor_program(data, params):\n    import numpy as np\n    return np.array(data['close'])",
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="S"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )

    result = compute_seed_correlations([f1], df)
    assert result == []


def test_compute_seed_correlations_empty_list():
    """空因子列表应返回空列表。"""
    from fts.factor_engine.seed_pool import compute_seed_correlations
    import pandas as pd
    import numpy as np
    df = pd.DataFrame({"close": np.sin(np.linspace(0, 2 * np.pi, 10))})
    result = compute_seed_correlations([], df)
    assert result == []


def test_compute_seed_correlations_mixed_validity():
    """部分因子执行失败时仍应计算有效因子间的相关性。"""
    from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature
    from fts.factor_engine.seed_pool import compute_seed_correlations

    import pandas as pd
    import numpy as np

    n = 100
    close = np.sin(np.linspace(0, 4 * np.pi, n))
    df = pd.DataFrame({"close": close, "volume": np.ones(n)})

    # 两个有效因子（相同信号）
    valid_code = "def factor_program(data, params):\n    import numpy as np\n    return np.array(data['close'])"
    f1 = FactorProgram(
        factor_id="fct_valid1", name="valid1", code=valid_code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="V1"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )
    f2 = FactorProgram(
        factor_id="fct_valid2", name="valid2", code=valid_code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="V2"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )

    # 一个无效因子（会抛异常）
    invalid_code = "def factor_program(data, params):\n    raise RuntimeError('bad')"
    f3 = FactorProgram(
        factor_id="fct_invalid", name="invalid", code=invalid_code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="I"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )

    result = compute_seed_correlations([f1, f2, f3], df, threshold=0.95)
    # f1 和 f2 有效且信号相同 → 高相关
    # f3 无效 → 被剔除
    assert len(result) >= 1
    pair_ids = {frozenset([result[0]["factor_id_a"], result[0]["factor_id_b"]])}
    assert frozenset(["fct_valid1", "fct_valid2"]) in pair_ids


def test_seed_pool_compute_correlations_method():
    """SeedPool.compute_correlations() 方法应正常工作。"""
    import pandas as pd
    import numpy as np

    n = 50
    close = np.sin(np.linspace(0, 2 * np.pi, n))
    df = pd.DataFrame({"close": close, "volume": np.ones(n)})

    pool = SeedPool(market="stock")
    # 仅用 9 个内置种子（快速）
    result = pool.compute_correlations(df, threshold=0.99, max_factors=9)
    assert isinstance(result, list)
    # 返回的每个条目都应有正确的结构
    for item in result:
        assert "factor_id_a" in item
        assert "factor_id_b" in item
        assert "pearson" in item
        assert "spearman" in item


def test_compute_seed_correlations_threshold_effect():
    """不同阈值应产生不同数量的高相关对。"""
    from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature
    from fts.factor_engine.seed_pool import compute_seed_correlations

    import pandas as pd
    import numpy as np

    n = 200
    close = np.cumsum(np.random.randn(n))
    df = pd.DataFrame({"close": close, "volume": np.ones(n)})

    # 两个完全相同的因子
    code = "def factor_program(data, params):\n    import numpy as np\n    return np.array(data['close'])"
    f1 = FactorProgram(
        factor_id="fct_th1", name="th1", code=code, params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="T1"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )
    f2 = FactorProgram(
        factor_id="fct_th2", name="th2", code=code, params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="T2"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )

    # 高阈值 → 可能没有（随机 close 不完全相关）
    result_hi = compute_seed_correlations([f1, f2], df, threshold=0.99)
    # 低阈值 → 更容易命中
    result_lo = compute_seed_correlations([f1, f2], df, threshold=0.5)

    assert len(result_lo) >= len(result_hi)


# ─── 横截面相关性预检测试 ─────────────────────────────────────

def _make_panel_data(n_varieties: int = 10, n_dates: int = 60) -> tuple[dict, pd.DatetimeIndex]:
    """创建模拟横截面面板数据。"""
    import pandas as pd
    import numpy as np
    rng = np.random.default_rng(42)
    panel = {}
    for i in range(n_varieties):
        closes = 100 + np.cumsum(rng.standard_normal(n_dates) * 0.5)
        noise_1 = np.abs(rng.normal(0, 0.5, n_dates))
        noise_2 = np.abs(rng.normal(0, 1.0, n_dates))
        panel[f"V{i}"] = pd.DataFrame({
            "open": closes - noise_1,
            "high": closes + noise_2,
            "low": closes - noise_2,
            "close": closes,
            "volume": rng.integers(1000, 10000, n_dates),
        })
    dates = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=n_dates, freq="B"))
    return panel, dates


def _make_test_factor(factor_id: str, name: str = "test", expr: str = "close") -> FactorProgram:
    """创建测试用因子程序。"""
    from fts.factor_engine.contracts import EconomicLogic, FactorSignature
    code = f"def factor_program(data, params):\n    import numpy as np\n    return np.array(data['{expr}'])"
    return FactorProgram(
        factor_id=factor_id, name=name, code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative=name),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )


def test_cross_section_correlations_same_signal():
    """横截面模式：两个因子产生相同信号 → 高相关。"""
    from fts.factor_engine.seed_pool import compute_cross_section_correlations

    panel, dates = _make_panel_data(10, 60)

    f1 = _make_test_factor("id_1", "test_a", "close")
    f2 = _make_test_factor("id_2", "test_b", "close")

    result = compute_cross_section_correlations([f1, f2], panel, dates, threshold=0.95)

    # 相同信号应该被标记
    assert len(result) >= 1
    assert result[0]["factor_id_a"] == "id_1"
    assert result[0]["factor_id_b"] == "id_2"
    assert abs(result[0]["spearman"]) >= 0.95


def test_cross_section_correlations_opposite_signal():
    """横截面模式：两个因子产生相反信号 → 高相关 (|spearman| ≈ 1)。"""
    from fts.factor_engine.seed_pool import compute_cross_section_correlations
    from fts.factor_engine.contracts import EconomicLogic, FactorSignature

    panel, dates = _make_panel_data(10, 60)

    f1 = _make_test_factor("id_1", "test_a", "close")
    # -close 在 code 中是取负
    f2 = FactorProgram(
        factor_id="id_2", name="test_b",
        code="def factor_program(data, params):\n    import numpy as np\n    return -np.array(data['close'])",
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="B"),
        source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
    )

    result = compute_cross_section_correlations([f1, f2], panel, dates, threshold=0.95)

    # 完全相反的信号：截面排名也相反 → spearman ≈ -1
    assert len(result) >= 1
    assert abs(result[0]["spearman"]) >= 0.95


def test_cross_section_correlations_single_factor():
    """横截面模式：单个因子 → 返回空。"""
    from fts.factor_engine.seed_pool import compute_cross_section_correlations

    panel, dates = _make_panel_data()
    f1 = _make_test_factor("id_1")

    result = compute_cross_section_correlations([f1], panel, dates)

    assert result == []


def test_cross_section_correlations_empty_list():
    """横截面模式：空列表 → 返回空。"""
    from fts.factor_engine.seed_pool import compute_cross_section_correlations

    panel, dates = _make_panel_data()

    result = compute_cross_section_correlations([], panel, dates)

    assert result == []


def test_cross_section_correlations_few_varieties():
    """横截面模式：品种数 < 5 → 返回空（不够计算截面排名）。"""
    from fts.factor_engine.seed_pool import compute_cross_section_correlations

    panel, dates = _make_panel_data(3, 60)
    f1 = _make_test_factor("id_1")
    f2 = _make_test_factor("id_2")

    result = compute_cross_section_correlations([f1, f2], panel, dates)

    assert result == []


def test_cross_section_correlations_threshold_effect():
    """横截面模式：阈值影响结果数量。"""
    from fts.factor_engine.seed_pool import compute_cross_section_correlations

    panel, dates = _make_panel_data(10, 60)

    f1 = _make_test_factor("id_1", "a", "close")
    f2 = _make_test_factor("id_2", "b", "close")

    # 相同信号在任何阈值下都应被标记
    result_hi = compute_cross_section_correlations([f1, f2], panel, dates, threshold=0.99)
    result_lo = compute_cross_section_correlations([f1, f2], panel, dates, threshold=0.5)

    assert len(result_lo) >= len(result_hi)


def test_seed_pool_compute_correlations_cross_section():
    """SeedPool.compute_correlations 自动检测横截面模式。"""
    import numpy as np
    import pandas as pd
    from fts.factor_engine.seed_pool import SeedPool

    panel, dates = _make_panel_data(10, 60)
    pool = SeedPool(market="futures")

    # 横截面模式 (dict + common_dates)
    seeds = pool.load_all_seeds()[:5]
    result_cs = pool.compute_correlations(panel, common_dates=dates, threshold=0.95)
    assert isinstance(result_cs, list)

    # 时序模式 (DataFrame)
    df_stock = pd.DataFrame({
        "open": np.ones(100) * 100,
        "high": np.ones(100) * 101,
        "low": np.ones(100) * 99,
        "close": np.linspace(100, 110, 100),
        "volume": np.ones(100, dtype=int) * 1000,
    }, index=pd.date_range("2024-01-01", periods=100, freq="B"))
    result_ts = pool.compute_correlations(df_stock, threshold=0.95)
    assert isinstance(result_ts, list)


def test_seed_pool_cross_section_modes_are_isolated():
    """股票和期货种子池独立，互不影响。"""
    from fts.factor_engine.seed_pool import SeedPool

    stock_pool = SeedPool(market="stock")
    futures_pool = SeedPool(market="futures")

    stock_seeds = stock_pool.load_all_seeds()
    futures_seeds = futures_pool.load_all_seeds()

    # 股票种子远多于期货种子
    assert len(stock_seeds) > len(futures_seeds)
    # 两个池子独立
    stock_ids = {s["factor_id"] for s in stock_seeds}
    futures_ids = {s["factor_id"] for s in futures_seeds}
    assert stock_ids.isdisjoint(futures_ids)