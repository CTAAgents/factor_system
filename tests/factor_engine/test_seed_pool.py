"""tests/factor_engine/test_seed_pool.py — 种子池测试。"""

from __future__ import annotations

import pytest

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.seed_pool import SeedPool, get_default_seed_pool

# 内置 9 个 + 外部 WQ101 101 个 + Qlib158 158 个 + GTJA191 191 个 = 459
_TOTAL_SEEDS = 9 + 101 + 158 + 191
_INTERNAL_NAMES = {
    "momentum", "volatility_reversion", "volume_flow",
    "macro_regime", "rate_proxy", "pmi_proxy",
    "value_factor", "quality_factor", "size_factor",
}


def test_seed_pool_loads_all_seeds():
    """种子池必须包含全部 459 个种子因子（内置 9 + 外部 450）。"""
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
    """种子因子名称列表必须包含所有 459 个名称。"""
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
    """外部种子代码必须包含公共操作函数。"""
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