"""
tests/factor_engine/test_loader.py — seed_data loader 测试。

覆盖范围:
    - get_external_seed_count() 返回正确的各源计数

版本: v0.1.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 确保能导入 fts 模块
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))


class TestGetExternalSeedCount:
    """测试 get_external_seed_count 函数。"""

    def test_get_external_seed_count(self):
        """返回正确的各源种子计数（lines 304-310）。"""
        from fts.factor_engine.seed_data.loader import get_external_seed_count

        wq, ql, gj, total = get_external_seed_count()

        # 验证各源计数为正整数
        assert isinstance(wq, int)
        assert isinstance(ql, int)
        assert isinstance(gj, int)
        assert isinstance(total, int)

        # 验证总数 = 各源之和
        assert total == wq + ql + gj

        # 验证各源都有数据
        assert wq > 0, "WQ101 应有种子因子"
        assert ql > 0, "QLIB158 应有种子因子"
        assert gj > 0, "GTJA191 应有种子因子"
        assert total > 0, "总种子数应 > 0"

    def test_get_external_seed_count_types(self):
        """返回的四个值均为整数。"""
        from fts.factor_engine.seed_data.loader import get_external_seed_count

        result = get_external_seed_count()
        assert all(isinstance(v, int) for v in result)
        assert len(result) == 4