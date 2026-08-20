"""tests/scripts/test_seeds_yaml.py — seeds/ L1 配置层 YAML 完整性校验（plans/58 入库后）。

覆盖: seeds/ 下全部 YAML 可被 yaml.safe_load 解析且顶层为 dict（配置结构契约），
防止新增种子文件破坏配置层装载。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_FTS_ROOT = Path(__file__).resolve().parents[2]
_SEEDS_DIR = _FTS_ROOT / "seeds"


@pytest.mark.parametrize("yaml_file", sorted((_SEEDS_DIR / "futures").glob("*.yaml")), ids=lambda p: p.name)
def test_futures_seed_yaml_parseable(yaml_file: Path) -> None:
    data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{yaml_file.name} 顶层结构应为 dict"


@pytest.mark.parametrize("yaml_file", sorted((_SEEDS_DIR / "energy").glob("*.yaml")), ids=lambda p: p.name)
def test_energy_seed_yaml_parseable(yaml_file: Path) -> None:
    data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{yaml_file.name} 顶层结构应为 dict"


def test_seeds_readme_exists() -> None:
    assert (_SEEDS_DIR / "README.md").exists()
