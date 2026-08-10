"""tests/test_package_init.py — fts 包初始化测试。

覆盖:
    1. __version__ 动态读取（与 pyproject.toml 一致）
    2. 包元数据常量
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import fts

_FTS_ROOT = Path(__file__).resolve().parent.parent


class TestPackageInit:
    def test_version_matches_pyproject(self):
        pyproject = _FTS_ROOT / "pyproject.toml"
        assert pyproject.exists()
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        assert fts.__version__ == data["project"]["version"]

    def test_version_format(self):
        parts = fts.__version__.split(".")
        assert len(parts) >= 3
        for p in parts:
            assert p.isdigit()

    def test_package_importable(self):
        assert hasattr(fts, "__version__")
        assert isinstance(fts.__version__, str)
