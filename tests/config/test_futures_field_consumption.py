"""
tests.config.test_futures_field_consumption — 期货字段消费字典（SSOT）单元测试。

覆盖: 三组字段完整性、唯一性校验、分组/通道映射、契约（新增消费字段必须登记）。
纯内存校验，无网络依赖。
"""

from __future__ import annotations

import pytest

from fts.config.futures_field_consumption import (
    FUTURES_FIELD_CONSUMPTION,
    FuturesFieldConsumptionConfig,
)


# ─── 三组字段完整性 ────────────────────────────────────────


class TestFieldGroups:
    def test_kline_group_17_fields(self) -> None:
        """行情组 17 字段（与 kline_cache schema 对齐）。"""
        assert set(FUTURES_FIELD_CONSUMPTION.field_names("kline")) == {
            "symbol", "period", "date",
            "open", "high", "low", "close",
            "volume", "amount", "hold", "settle", "pre_settle", "oi_change", "vwap",
            "source", "fetched_at", "trace_id",
        }

    def test_fundamental_group_9_fields(self) -> None:
        """基本面组 9 字段（与 enrich_futures_fundamental 对齐）。"""
        assert set(FUTURES_FIELD_CONSUMPTION.field_names("fundamental")) == {
            "fut_inventory", "fut_inventory_chg",
            "fut_warehouse_receipt", "fut_warehouse_receipt_chg",
            "fut_spot_price", "fut_near_basis", "fut_dom_basis",
            "fut_near_basis_rate", "fut_dom_basis_rate",
        }

    def test_term_structure_group_4_fields(self) -> None:
        """期限结构组 4 字段。"""
        assert set(FUTURES_FIELD_CONSUMPTION.field_names("term_structure")) == {
            "term_spread", "roll_yield", "near_contract", "far_contract",
        }

    def test_total_registered_fields(self) -> None:
        assert len(FUTURES_FIELD_CONSUMPTION.fields) == 30  # 17 + 9 + 4

    def test_every_field_has_required_metadata(self) -> None:
        """每条登记必须含 channel/source/coverage/consumers 元数据。"""
        for f in FUTURES_FIELD_CONSUMPTION.fields:
            assert f.channel, f"字段 {f.field} 缺 channel"
            assert f.source, f"字段 {f.field} 缺 source"
            assert f.coverage, f"字段 {f.field} 缺 coverage"
            assert isinstance(f.consumers, list), f"字段 {f.field} 的 consumers 必须为 list"


# ─── 唯一性与契约 ─────────────────────────────────────────


class TestUniquenessAndContract:
    def test_no_duplicate_fields(self) -> None:
        """重复登记必须报错（防止字典污染）。"""
        names = [f.field for f in FUTURES_FIELD_CONSUMPTION.fields]
        assert len(names) == len(set(names))

    def test_duplicate_raises(self) -> None:
        cfg = FuturesFieldConsumptionConfig(fields=[*FUTURES_FIELD_CONSUMPTION.fields])
        # 人为注入重复
        dup = cfg.fields[0].model_copy()
        with pytest.raises(ValueError, match="重复登记"):
            FuturesFieldConsumptionConfig(fields=[*cfg.fields, dup]).validate_unique()

    def test_groups_mapping(self) -> None:
        groups = FUTURES_FIELD_CONSUMPTION.groups()
        assert set(groups.keys()) == {"kline", "fundamental", "term_structure"}
        for g, fields in groups.items():
            assert len(fields) > 0, f"组 {g} 为空"

    def test_channels_mapping(self) -> None:
        channels = FUTURES_FIELD_CONSUMPTION.channels()
        assert set(channels.keys()) == {"kline_cache", "futures_fundamental", "futures_term_structure"}
        # 每个通道字段数与组一致
        assert len(channels["kline_cache"]) == 17
        assert len(channels["futures_fundamental"]) == 9
        assert len(channels["futures_term_structure"]) == 4

    def test_term_spread_consumers(self) -> None:
        """期限结构字段必须被期货期限结构因子家族消费（契约）。"""
        term = {f.field: f for f in FUTURES_FIELD_CONSUMPTION.fields if f.group == "term_structure"}
        assert "fut_roll_yield_carry" in term["term_spread"].consumers


# ─── 契约: 新增消费字段必须登记 ──────────────────────────


class TestRegistryContract:
    def test_newly_consumed_field_must_be_registered(self) -> None:
        """新增被消费字段必须先登记本字典（SSOT 契约）。

        以现有消费方（enrich_futures_fundamental 注入列 + 期限结构输出列）为准，
        校验字典覆盖全部输出列。
        """
        from fts.data_futures_fundamental_sync import FUNDAMENTAL_COLUMNS
        from fts.data_futures_term_structure import TERM_STRUCTURE_COLUMNS

        registered = set(FUTURES_FIELD_CONSUMPTION.field_names())
        produced = set(FUNDAMENTAL_COLUMNS) | set(TERM_STRUCTURE_COLUMNS)
        missing = produced - registered
        assert not missing, f"以下输出列未在字段消费字典登记: {missing}"
