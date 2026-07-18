"""tests/factor_engine/test_seed_pool.py — 种子池测试。"""

from __future__ import annotations

import pytest

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.seed_pool import SeedPool, get_default_seed_pool


def test_seed_pool_loads_12_seeds():
    """种子池必须包含 12 个种子因子（来自 multi_factor_strategy.py）。"""
    pool = SeedPool()
    seeds = pool.load_all_seeds()
    assert len(seeds) == 12


def test_seed_pool_count():
    pool = SeedPool()
    assert pool.count() == 12


def test_seed_pool_list_names():
    """种子因子名称必须与 multi_factor_strategy.py FACTOR_WEIGHTS 对应。"""
    pool = SeedPool()
    names = pool.list_names()
    expected = {
        "momentum", "volatility_reversion", "volume_flow", "oi_change",
        "basis", "inventory_pct", "capacity", "macro_regime",
        "rate_proxy", "pmi_proxy", "position_rank", "warrant_change",
    }
    assert set(names) == expected


def test_seed_pool_get_by_name():
    pool = SeedPool()
    seed = pool.get_seed("momentum")
    assert seed is not None
    assert seed["name"] == "momentum"
    assert "def factor_program" in seed["code"]


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
    # 默认实现是新建实例
    assert p1.count() == 12
    assert p2.count() == 12
