"""
tests.test_futures_term_structure — 期货期限结构每日同步（Stage 3）单元测试。

覆盖: 合约月份解析、多合约截面计算（term_spread / roll_yield）、Parquet upsert。
通过临时 DuckDB 库隔离真实数据，无网络依赖。
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

import fts.data_futures_term_structure as mod


# ─── 合约月份解析 ─────────────────────────────────────────


class TestParseContractMonth:
    def test_yy_mm(self) -> None:
        assert mod._parse_contract_month("RB2609") == (2026, 9)
        assert mod._parse_contract_month("CU2510") == (2025, 10)

    def test_continuous_contract_none(self) -> None:
        assert mod._parse_contract_month("RB0") is None

    def test_invalid_month_none(self) -> None:
        assert mod._parse_contract_month("RB2513") is None  # 13 月非法
        assert mod._parse_contract_month("RB25") is None


# ─── 截面计算 ─────────────────────────────────────────────


def _make_db(tmp_path: Path) -> Path:
    """构造含 contract_kline 表的临时 DuckDB 库并写入测试数据。"""
    db = tmp_path / "fts_test.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        """
        CREATE TABLE contract_kline (
            symbol VARCHAR, contract VARCHAR, period VARCHAR, date DATE,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, hold DOUBLE, settle DOUBLE,
            source VARCHAR, fetched_at VARCHAR, trace_id VARCHAR
        )
        """
    )
    rows = [
        # RB 品种：近月 2609 最新到 8-14，次近月 2610 最新到 8-13（日期不同步场景）
        ("RB", "RB2609", "daily", "2026-08-14", 3300, 3320, 3280, 3300, 100, 0, 0, 0, "TEST", "t", "t"),
        ("RB", "RB2610", "daily", "2026-08-13", 3270, 3290, 3250, 3260, 80, 0, 0, 0, "TEST", "t", "t"),
        ("RB", "RB2611", "daily", "2026-08-14", 3240, 3260, 3220, 3230, 60, 0, 0, 0, "TEST", "t", "t"),
        # 历史已交割旧合约（交割月份远低于基准，不得进入期限结构截面）
        ("RB", "RB1905", "daily", "2019-05-15", 3000, 3010, 2990, 3000, 10, 0, 0, 0, "TEST", "t", "t"),
        # 单合约品种 CU（截面不足 → None）
        ("CU", "CU2609", "daily", "2026-08-14", 78000, 78100, 77900, 78000, 50, 0, 0, 0, "TEST", "t", "t"),
    ]
    con.executemany(
        "INSERT INTO contract_kline VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.close()
    return db


class TestComputeLatestSection:
    def test_term_spread_and_roll_yield(self, tmp_path: Path, monkeypatch) -> None:
        db = _make_db(tmp_path)
        monkeypatch.setattr(mod, "_db_path", lambda: db)
        sec = mod._compute_latest_section("RB0")
        assert sec is not None
        row = sec.iloc[0]
        assert row["near_contract"] == "RB2609"
        assert row["far_contract"] == "RB2610"
        # term_spread = (3300-3260)/3300
        assert abs(row["term_spread"] - (3300 - 3260) / 3300) < 1e-9
        # roll_yield = term_spread / (1 个月 / 12)
        assert abs(row["roll_yield"] - ((3300 - 3260) / 3300) * 12) < 1e-6
        assert str(row["date"]) == "2026-08-14"

    def test_insufficient_section_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        db = _make_db(tmp_path)
        monkeypatch.setattr(mod, "_db_path", lambda: db)
        assert mod._compute_latest_section("CU0") is None  # 仅 1 个合约

    def test_missing_db_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(mod, "_db_path", lambda: tmp_path / "not_exist.duckdb")
        assert mod._compute_latest_section("RB0") is None


# ─── sync_term_structure_fields 集成 ──────────────────────


class TestSyncTermStructureFields:
    @pytest.fixture
    def env(self, tmp_path: Path, monkeypatch):
        db = _make_db(tmp_path)
        monkeypatch.setattr(mod, "_db_path", lambda: db)
        cache = tmp_path / "ts_cache"
        monkeypatch.setattr(mod, "TERM_STRUCTURE_CACHE_DIR", cache)
        return cache

    def test_success_flow_without_refresh(self, env: Path) -> None:
        result = mod.sync_term_structure_fields(
            ["RB0", "CU0"], days=120, trace_id="t_ts", refresh_contract_kline=False
        )
        assert result["success"] == 1  # RB0 产出，CU0 无截面
        assert result["failure"] == 0
        assert result["rows"] == 1
        assert result["no_section"] == ["CU0"]
        df = pd.read_parquet(env / "RB0.parquet")
        assert df["term_spread"].iloc[-1] > 0  # Back 结构
        assert not df["near_contract"].isna().all()

    def test_upsert_dedup(self, env: Path) -> None:
        mod.sync_term_structure_fields(["RB0"], days=120, trace_id="t1", refresh_contract_kline=False)
        mod.sync_term_structure_fields(["RB0"], days=120, trace_id="t2", refresh_contract_kline=False)
        df = pd.read_parquet(env / "RB0.parquet")
        assert df["date"].duplicated().sum() == 0
