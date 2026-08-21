"""tests/store/test_data_contract.py — 数据契约字段完整性校验（GAP-151，v3.1.0+6 单源）。

覆盖: 核心/增强字段分级判定、date 位于 index 的兼容形态、增强字段子集参数。
"""

from __future__ import annotations

import logging

import pandas as pd

from fts.store.data_contract import (
    KLINE_CORE_FIELDS,
    KLINE_EXTENDED_FIELDS,
    check_kline_field_integrity,
    classify_kline_field_integrity,
)

logger = logging.getLogger("fts.store.data_contract")


def _make_df(**cols) -> pd.DataFrame:
    return pd.DataFrame(cols)


def _df_date_index(**cols) -> pd.DataFrame:
    df = pd.DataFrame(cols)
    df.index = pd.to_datetime([f"2026-01-{i + 1:02d}" for i in range(len(df))])
    df.index.name = "date"
    return df


class TestClassify:
    def test_core_fields_constant(self) -> None:
        """核心/增强字段清单为契约权威定义（消费方 import 本模块）。"""
        assert KLINE_CORE_FIELDS == ("date", "open", "high", "low", "close", "volume")
        assert KLINE_EXTENDED_FIELDS == ("hold", "settle", "pre_settle")

    def test_complete_fields_no_missing(self) -> None:
        df = _make_df(
            date=["2026-01-01", "2026-01-02"], open=[1.0, 2.0], close=[2.0, 3.0],
            high=[3.0, 4.0], low=[0.5, 1.0], volume=[100, 200],
            hold=[10, 20], settle=[1.0, 2.0], pre_settle=[1.0, 2.0],
        )
        assert classify_kline_field_integrity(df) == ([], [])

    def test_core_missing_column(self) -> None:
        df = _make_df(close=[2.0])  # 缺 date/open/high/low/volume
        core, ext = classify_kline_field_integrity(df)
        assert "open" in core and "volume" in core
        # 增强字段同样缺失（df 无这些列），核心缺失优先阻断
        assert "hold" in ext and "settle" in ext

    def test_core_all_empty(self) -> None:
        df = _make_df(
            date=["2026-01-01"], open=[None], high=[None], low=[None],
            close=[None], volume=[None], hold=[10], settle=[1.0],
        )
        core, _ = classify_kline_field_integrity(df)
        assert core  # 核心字段全空视为缺失

    def test_extended_all_empty(self) -> None:
        df = _make_df(
            date=["2026-01-01", "2026-01-02"], open=[1.0, 2.0], close=[2.0, 3.0],
            high=[3.0, 4.0], low=[0.5, 1.0], volume=[100, 200],
            hold=[None, None], settle=[1.0, 2.0], pre_settle=[1.0, 2.0],
        )
        core, ext = classify_kline_field_integrity(df)
        assert core == []
        assert "hold" in ext

    def test_date_in_index_compatible(self) -> None:
        """data_futures 主加载路径形态：date 位于 index（set_index('date')）。"""
        df = _df_date_index(
            open=[1.0, 2.0], high=[3.0, 4.0], low=[0.5, 1.0], close=[2.0, 3.0],
            volume=[100, 200], hold=[None, None], settle=[1.0, 2.0],
        )
        assert classify_kline_field_integrity(df, ("hold", "settle")) == ([], ["hold"])

    def test_extended_subset_ignores_unlisted(self) -> None:
        """增强字段子集参数：data_futures 无 pre_settle 列时不产生噪音告警。"""
        df = _make_df(
            date=["2026-01-01"], open=[1.0], high=[2.0], low=[0.5], close=[1.5],
            volume=[100], hold=[10], settle=[1.0],
        )
        assert classify_kline_field_integrity(df, ("hold", "settle")) == ([], [])


class TestCheck:
    def test_core_missing_logs_error_and_false(self, caplog) -> None:
        df = _make_df(close=[2.0])
        with caplog.at_level("ERROR", logger="fts.store.data_contract"):
            ok = check_kline_field_integrity(df, "SC0", logger)
        assert ok is False
        assert any("核心字段缺失" in r.message and "open" in r.message for r in caplog.records)

    def test_extended_missing_logs_warning_and_true(self, caplog) -> None:
        df = _make_df(
            date=["2026-01-01", "2026-01-02"], open=[1.0, 2.0], close=[2.0, 3.0],
            high=[3.0, 4.0], low=[0.5, 1.0], volume=[100, 200],
            hold=[None, None], settle=[1.0, 2.0], pre_settle=[1.0, 2.0],
        )
        with caplog.at_level("WARNING", logger="fts.store.data_contract"):
            ok = check_kline_field_integrity(df, "RB0", logger)
        assert ok is True
        assert any("增强字段缺失" in r.message and "hold" in r.message for r in caplog.records)

    def test_complete_no_log(self, caplog) -> None:
        df = _make_df(
            date=["2026-01-01", "2026-01-02"], open=[1.0, 2.0], close=[2.0, 3.0],
            high=[3.0, 4.0], low=[0.5, 1.0], volume=[100, 200],
            hold=[10, 20], settle=[1.0, 2.0], pre_settle=[1.0, 2.0],
        )
        with caplog.at_level("WARNING", logger="fts.store.data_contract"):
            ok = check_kline_field_integrity(df, "RB0", logger)
        assert ok is True
        assert not any("字段缺失" in r.message for r in caplog.records)

    def test_date_index_shape_passes(self, caplog) -> None:
        """date 位于 index 且增强字段齐全 → True 无告警（data_futures 形态）。"""
        df = _df_date_index(
            open=[1.0, 2.0], high=[3.0, 4.0], low=[0.5, 1.0], close=[2.0, 3.0],
            volume=[100, 200], hold=[10, 20], settle=[1.0, 2.0],
        )
        with caplog.at_level("WARNING", logger="fts.store.data_contract"):
            ok = check_kline_field_integrity(df, "RB0", logger, extended_fields=("hold", "settle"))
        assert ok is True
        assert not any("字段缺失" in r.message for r in caplog.records)
