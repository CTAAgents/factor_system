"""品种池/产业链配置 SSOT 加载测试（GAP-121 配置化，v2.104.0+38）。

覆盖:
1. YAML 有效时应用（与内置默认等价）；
2. YAML 缺失 → 回退内置默认；
3. YAML 损坏（非法 YAML）→ 回退内置默认；
4. YAML 校验失败（池子越界/重复/泛化子链缺失）→ 回退内置默认；
5. 修改训练池后盲测池自动重算（核心诉求：只改 YAML 即可换池）；
6. 炼化聚酯链分组由训练池自动生成并置首位。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import fts.data_futures as df

_YAML_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "futures_universe.yaml"


def _write_tmp_yaml(tmp_path: Path, cfg: dict) -> Path:
    p = tmp_path / "futures_universe.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _restore_yaml_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离 loader 的 YAML 路径，避免污染真实 config/futures_universe.yaml。"""
    monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", _YAML_PATH)
    yield
    df._FUTURES_UNIVERSE_YAML = _YAML_PATH


def _base_cfg() -> dict:
    """基于真实 YAML 的最小合法配置（改动可控）。"""
    with open(_YAML_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


class TestLoaderApply:
    def test_valid_yaml_applied_and_equivalent_to_builtin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write_tmp_yaml(tmp_path, _base_cfg())
        monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", p)
        assert df._load_futures_universe_config() is True
        # 与真实 YAML 等价（plans/57 全期货覆盖：82→84 增 T0/TL0；
        # 2026-08-19 橡胶子链 RU0/BR0 入训练池 12→14，盲测池 8→9）
        assert len(df.FUTURES_SUBSET) == 84
        assert len(df.ENERGY_CHAIN_SYMBOLS) == 14
        assert len(df.ENERGY_CHAIN_HOLDOUT) == 9

    def test_missing_yaml_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", tmp_path / "not_exist.yaml")
        assert df._load_futures_universe_config() is False
        # 内置默认仍可用（不为空即可，不依赖具体值避免脆弱）
        assert len(df.FUTURES_SUBSET) > 0

    def test_corrupt_yaml_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = tmp_path / "futures_universe.yaml"
        p.write_text("{ unclosed: [1, 2", encoding="utf-8")
        monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", p)
        assert df._load_futures_universe_config() is False


class TestLoaderValidation:
    def test_pool_outside_universe_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _base_cfg()
        cfg["holdout"] = ["XXXX"]  # 不在 universe
        p = _write_tmp_yaml(tmp_path, cfg)
        monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", p)
        assert df._load_futures_universe_config() is False

    def test_duplicate_universe_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _base_cfg()
        cfg["universe"]["dce"] = ["V0", "V0"]
        p = _write_tmp_yaml(tmp_path, cfg)
        monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", p)
        assert df._load_futures_universe_config() is False

    def test_holdout_overlap_stratified_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _base_cfg()
        cfg["holdout"] = [cfg["stratified_subset"][0]]  # 与分层训练集重叠
        p = _write_tmp_yaml(tmp_path, cfg)
        monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", p)
        assert df._load_futures_universe_config() is False

    def test_missing_chemical_sector_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _base_cfg()
        cfg["workflows"]["energy"]["chemical_sectors"] = ["不存在的链"]
        p = _write_tmp_yaml(tmp_path, cfg)
        monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", p)
        assert df._load_futures_universe_config() is False


class TestDynamicRecompute:
    def test_change_train_pool_recomputes_holdout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """核心诉求验证：只改 YAML 训练池，盲测池自动重算。"""
        cfg = _base_cfg()
        # 把 TA0 从训练池挪到盲测池（模拟一次换池；基线链已含 EG0、PX0 已在盲测池）
        cfg["workflows"]["energy"]["chain_symbols"] = [
            s for s in cfg["workflows"]["energy"]["chain_symbols"] if s != "TA0"
        ]
        p = _write_tmp_yaml(tmp_path, cfg)
        monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", p)
        assert df._load_futures_universe_config() is True
        # 基线训练池 14（含 RU0/BR0，2026-08-19）→ 移除 TA0 后 13
        assert len(df.ENERGY_CHAIN_SYMBOLS) == 13
        assert "TA0" not in df.ENERGY_CHAIN_SYMBOLS
        assert "TA0" in df.ENERGY_CHAIN_HOLDOUT  # TA0 回到盲测池
        assert not (set(df.ENERGY_CHAIN_SYMBOLS) & set(df.ENERGY_CHAIN_HOLDOUT))

    def test_lianhua_chain_group_derived_and_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _base_cfg()
        p = _write_tmp_yaml(tmp_path, cfg)
        monkeypatch.setattr(df, "_FUTURES_UNIVERSE_YAML", p)
        assert df._load_futures_universe_config() is True
        keys = list(df.FUTURES_SECTOR_MAP.keys())
        assert keys[0] == "炼化聚酯链"
        assert set(df.FUTURES_SECTOR_MAP["炼化聚酯链"]) == set(df.ENERGY_CHAIN_SYMBOLS)
