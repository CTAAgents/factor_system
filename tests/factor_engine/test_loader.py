"""
tests/factor_engine/test_loader.py — seed_data loader 测试。

覆盖范围:
    - get_external_seed_count() 返回正确的各源计数

版本: v0.1.0
"""

from __future__ import annotations

import sys
from pathlib import Path


# 确保能导入 fts 模块
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))


class TestGetExternalSeedCount:
    """测试 get_external_seed_count 函数。"""

    def test_get_external_seed_count(self):
        """返回正确的各源种子计数（lines 297-421）。"""
        from fts.factor_engine.seed_data.loader import get_external_seed_count

        wq, ql, gj, fd, jq, total = get_external_seed_count()

        # 验证各源计数为正整数
        assert isinstance(wq, int)
        assert isinstance(ql, int)
        assert isinstance(gj, int)
        assert isinstance(fd, int)
        assert isinstance(jq, int)
        assert isinstance(total, int)

        # 验证总数 = 各源之和
        assert total == wq + ql + gj + fd + jq

        # 验证各源都有数据
        assert wq > 0, "WQ101 应有种子因子"
        assert ql > 0, "QLIB158 应有种子因子"
        assert gj > 0, "GTJA191 应有种子因子"
        assert fd > 0, "基本面应有种子因子"
        assert jq > 0, "JQ应有种子因子"
        assert total > 0, "总种子数应 > 0"

    def test_get_external_seed_count_types(self):
        """返回的六个值均为整数。"""
        from fts.factor_engine.seed_data.loader import get_external_seed_count

        result = get_external_seed_count()
        assert all(isinstance(v, int) for v in result)
        assert len(result) == 6


class TestMakeFundamentalProgram:
    """测试 make_fundamental_program 函数。"""

    def test_make_fundamental_program_defaults(self):
        """使用默认 lookback/input_fields 创建基本面因子程序（覆盖 lines 334-337）。"""
        from fts.factor_engine.seed_data.loader import make_fundamental_program

        fp = make_fundamental_program(
            name="test_fund",
            field_defs="close = data['close'].values",
            field_check="close is not None",
            expression="np.tanh(close / 15.0)",
            narrative="测试基本面因子",
        )

        assert fp["name"] == "test_fund"
        assert fp["source"] == "seed"
        assert fp["signature"]["lookback"] == 1
        assert fp["signature"]["input_fields"] == ["close"]
        assert "def factor_program" in fp["code"]
        assert "close" in fp["code"]

    def test_make_fundamental_program_custom(self):
        """使用自定义参数创建基本面因子程序。"""
        from fts.factor_engine.seed_data.loader import make_fundamental_program

        fp = make_fundamental_program(
            name="test_fund_custom",
            field_defs="pe = data['pe_ttm'].values",
            field_check="pe is not None",
            expression="np.tanh(1.0 / pe)",
            narrative="自定义测试",
            lookback=10,
            input_fields=["pe_ttm"],
        )

        assert fp["name"] == "test_fund_custom"
        assert fp["signature"]["lookback"] == 10
        assert fp["signature"]["input_fields"] == ["pe_ttm"]


class TestGetFundamentalSeedCount:
    """测试 get_fundamental_seed_count 函数。"""

    def test_get_fundamental_seed_count(self):
        """返回基本面种子因子数（覆盖 line 407）。"""
        from fts.factor_engine.seed_data.fundamental_seeds import get_fundamental_seed_count

        count = get_fundamental_seed_count()
        assert count == 23
        assert isinstance(count, int)
