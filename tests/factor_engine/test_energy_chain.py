# -*- coding: utf-8 -*-
"""能源产业链专属工作流配置校验（GAP-Ixxx）。

校验：
1. 链配置：训练链 = 12 个化工品种（四大子链各 3，2026-08-15 由 9 扩至 12），盲测池 = 其余化工产业链品种，无重叠
2. 盲测池全部位于全量品种（FUTURES_SUBSET）内，覆盖聚酯链/油化工/煤化工
3. 存储路由：get_db_path("energy") → 独立 factor_catalog_energy.duckdb
4. 精英目录路由：get_elite_dir("energy") → energy_chain_elite（独立目录）
5. FUTURES_SECTOR_MAP 新增"炼化聚酯链"分组置于首位，
   通用工作流中性化反向映射 {sym: sector} 保持原板块归属不变
6. load_futures_elite_factors_from_db market 参数按 market 路由（隔离库）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fts.data_futures import (
    ENERGY_CHAIN_CHEMICAL_SECTORS,
    ENERGY_CHAIN_HOLDOUT,
    ENERGY_CHAIN_MARKET,
    ENERGY_CHAIN_MIN_TRAIN_ROWS,
    ENERGY_CHAIN_SYMBOLS,
    ENERGY_CHAIN_TRAIN,
    FUTURES_SECTOR_MAP,
    FUTURES_SUBSET,
    check_energy_chain_depth,
)
from fts.factor_engine.factor_db.schema import DATABASE_PATH_ENERGY, get_db_path

# scripts/ 不在 sys.path 中，需要手动添加（基于项目根目录动态解析）
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from futures_signal_pipeline import load_futures_elite_factors_from_db  # noqa: E402


class TestEnergyChainConfig:
    """能源链专属训练链与盲测池配置。"""

    def test_train_is_full_energy_chain(self) -> None:
        """训练链 = 12 个化工品种（四大化工子链；v2.104.0+106 GAP-133 换池 PX0→EG0）。"""
        assert len(ENERGY_CHAIN_SYMBOLS) == 12
        assert ENERGY_CHAIN_TRAIN == ENERGY_CHAIN_SYMBOLS
        assert set(ENERGY_CHAIN_SYMBOLS) == {
            # 能源
            "SC0",
            "FU0",
            "BU0",
            # 聚酯链
            "PF0",
            "TA0",
            "EG0",
            # 油化工
            "L0",
            "PP0",
            "PG0",
            # 煤化工
            "MA0",
            "UR0",
            "SA0",
        }

    def test_train_covers_four_subsectors(self) -> None:
        """训练池覆盖能源/聚酯链/油化工/煤化工四大子链（v2.104.0+106 聚酯链 PX0→EG0）。"""
        expected_per_sector = {
            "能源": {"SC0", "FU0", "BU0"},
            "聚酯链": {"PF0", "TA0", "EG0"},
            "油化工": {"L0", "PP0", "PG0"},
            "煤化工": {"MA0", "UR0", "SA0"},
        }
        for sec, expected in expected_per_sector.items():
            in_train = set(FUTURES_SECTOR_MAP.get(sec, [])) & set(ENERGY_CHAIN_SYMBOLS)
            assert in_train == expected, f"{sec} 训练品种异常: {in_train}"

    def test_holdout_are_remaining_chemicals(self) -> None:
        """盲测池 = 其余化工产业链品种，与训练链无重叠。"""
        assert ENERGY_CHAIN_HOLDOUT
        assert not (set(ENERGY_CHAIN_SYMBOLS) & set(ENERGY_CHAIN_HOLDOUT)), "训练链与盲测池重叠"
        # 盲测池品种均来自化工产业链分组
        chem_members = {
            s
            for sec in ENERGY_CHAIN_CHEMICAL_SECTORS
            for s in FUTURES_SECTOR_MAP.get(sec, [])
        }
        assert set(ENERGY_CHAIN_HOLDOUT) <= chem_members, "盲测池超出化工产业链分组"
        # 化工链成员减去训练链 = 盲测池
        assert set(ENERGY_CHAIN_HOLDOUT) == chem_members - set(ENERGY_CHAIN_SYMBOLS)

    def test_holdout_covers_three_chemical_chains(self) -> None:
        """盲测池覆盖聚酯链/油化工/煤化工三大化工产业链。"""
        for sec in ENERGY_CHAIN_CHEMICAL_SECTORS:
            members = set(FUTURES_SECTOR_MAP[sec]) - set(ENERGY_CHAIN_SYMBOLS)
            assert members, f"化工链 {sec} 无盲测品种"
            assert members & set(ENERGY_CHAIN_HOLDOUT)

    def test_all_within_full_universe(self) -> None:
        """训练链与盲测池全部位于全量品种列表。"""
        full = set(FUTURES_SUBSET)
        assert set(ENERGY_CHAIN_SYMBOLS) <= full, set(ENERGY_CHAIN_SYMBOLS) - full
        assert set(ENERGY_CHAIN_HOLDOUT) <= full, set(ENERGY_CHAIN_HOLDOUT) - full


class TestEnergyChainStorageRouting:
    """能源链独立因子库 / 精英目录路由。"""

    @pytest.mark.uses_real_factor_db  # GAP-129: 真实存储路由断言
    def test_db_path_isolated(self) -> None:
        assert get_db_path("energy") == DATABASE_PATH_ENERGY
        assert "factor_catalog_energy" in str(DATABASE_PATH_ENERGY)
        assert get_db_path("energy") != get_db_path("futures")
        # 通用路径保持默认行为
        assert get_db_path("futures").name == "factor_catalog_futures.duckdb"

    def test_elite_dir_isolated(self) -> None:
        from fts.config.settings import get_config

        cfg = get_config()
        assert "energy_chain_elite" in cfg.get_elite_dir(ENERGY_CHAIN_MARKET)
        assert cfg.get_elite_dir("energy") != cfg.get_elite_dir("futures")
        # 通用默认不受影响
        assert "futures_elite" in cfg.get_elite_dir("futures")


class TestSectorMapNeutralizationStability:
    """FUTURES_SECTOR_MAP 新增分组不改变通用工作流中性化归属。"""

    def test_new_group_placed_first(self) -> None:
        groups = list(FUTURES_SECTOR_MAP.keys())
        assert groups[0] == "炼化聚酯链", "炼化聚酯链分组须置于首位（反向映射后序覆盖前序）"
        assert set(FUTURES_SECTOR_MAP["炼化聚酯链"]) == set(ENERGY_CHAIN_SYMBOLS)

    def test_reverse_map_keeps_original_sectors(self) -> None:
        """反向映射（后序覆盖前序）下，能源链品种仍归属原板块。"""
        reverse = {
            sym: sector for sector, symbols in FUTURES_SECTOR_MAP.items() for sym in symbols
        }
        assert reverse["SC0"] == "能源"
        assert reverse["PG0"] == "油化工"
        assert reverse["TA0"] == "聚酯链"

    def test_original_sectors_intact(self) -> None:
        """原板块成员未因新增分组而缺失。"""
        assert "SC0" in FUTURES_SECTOR_MAP["能源"]
        assert "PG0" in FUTURES_SECTOR_MAP["油化工"]
        assert "TA0" in FUTURES_SECTOR_MAP["聚酯链"]


class TestFactorLoaderMarketRouting:
    """load_futures_elite_factors_from_db 按 market 路由到独立库。"""

    def test_energy_market_loads_from_isolated_db(self, tmp_path) -> None:
        db = tmp_path / "factor_catalog_energy_test.duckdb"
        # 独立空库 + market="energy" → 返回空列表（不落通用库、不抛异常）
        factors = load_futures_elite_factors_from_db(ic_threshold=0, db_path=db, market="energy")
        assert factors == []


class TestEnergyChainDepthThreshold:
    """训练品种深度阈值与审计（GAP-121 A/C，2026-08-15）。"""

    def test_min_train_rows_positive(self) -> None:
        """深度阈值必须为正且定义明确。"""
        assert ENERGY_CHAIN_MIN_TRAIN_ROWS > 0
        assert ENERGY_CHAIN_MIN_TRAIN_ROWS == 300

    def test_depth_audit_all_train_symbols(self) -> None:
        """审计覆盖全部训练链品种，结果字段契约完整。"""
        result = check_energy_chain_depth()
        assert set(result.keys()) == {"ok", "below", "below_symbols"}
        assert result["ok"] + result["below"] == len(ENERGY_CHAIN_TRAIN)
        assert isinstance(result["below_symbols"], list)

    def test_depth_audit_not_all_failed(self) -> None:
        """真实库中训练链品种应有真实行情（不应全部判为不达标）。"""
        result = check_energy_chain_depth(min_rows=1)
        assert result["ok"] >= 1


class TestEnergyChainL1Knowledge:
    """能源链 L1 知识输入（GAP-121 v2.104.0+35）：通用期货 + 能化专属两线混入。"""

    def test_seed_pool_energy_mixed(self) -> None:
        """SeedPool(market="energy") 混入加载通用期货 + 能化专属种子。"""
        from fts.factor_engine.seed_pool import SeedPool

        pool = SeedPool(market="energy")
        seeds = pool.load_all_seeds()
        names = {s["name"] for s in seeds}
        eng_names = {n for n in names if n.startswith(("eng_", "ec_"))}
        fut_names = {n for n in names if n.startswith("fut_")}
        assert len(eng_names) >= 8, f"能化专属种子缺失: {len(eng_names)}"
        assert len(fut_names) >= 100, f"通用期货种子缺失: {len(fut_names)}"
        assert pool._market == "energy"

    def test_meta_loop_energy_default_symbols(self) -> None:
        """energy 模式下感知层默认品种 = 能化链 12 训练品种（非通用 13 品种；v2.104.0+106 PX0→EG0）。"""
        from fts.factor_engine.meta_loop import MetaLoop

        loop = MetaLoop(market="energy", llm_client=None, web_collector=None)
        assert loop.market == "energy"
        assert loop.sample_symbols == ["sc", "fu", "bu", "pf", "ta", "eg", "l", "pp", "pg", "ma", "ur", "sa"]

    def test_meta_loop_energy_chain_knowledge_injected(self) -> None:
        """energy 模式下感知快照注入能化专属市场知识（chain_knowledge）。"""
        from fts.factor_engine.meta_loop import MetaLoop

        loop = MetaLoop(market="energy", llm_client=None, web_collector=None)
        snapshot = loop._perceive_market("test_l1_energy")
        knowledge = snapshot.get("chain_knowledge", "")
        assert knowledge, "energy 模式应注入 chain_knowledge"
        # 核心能化机制关键词
        for kw in ("训练链 12 品种", "裂解价差", "聚酯链加工差", "库存周期", "链内纵向传导", "子链间相对强弱"):
            assert kw in knowledge, f"chain_knowledge 缺少能化知识: {kw}"
        # 通用期货模式不注入
        from fts.factor_engine.meta_loop import MetaLoop as _ML

        fut_loop = _ML(market="futures", llm_client=None, web_collector=None)
        assert "chain_knowledge" not in fut_loop._perceive_market("test_l1_fut")

    def test_bootstrap_prompt_contains_energy_knowledge(self) -> None:
        """LLM bootstrap prompt 注入能化专属市场知识段（chain_knowledge 进入 prompt）。"""
        from fts.factor_engine.meta_loop import MetaLoop
        from fts.llm import OpenAIClient

        loop = MetaLoop(market="energy", llm_client=None, web_collector=None)
        snapshot = loop._perceive_market("test_l1_energy")
        prompt = OpenAIClient._build_bootstrap_prompt(snapshot, [], 3, "test")
        assert "【能源产业链专属市场知识】" in prompt
        assert "裂解价差" in prompt
