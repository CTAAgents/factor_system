"""tests.data_sources.test_quantdata_provider — QuantData 权威源单测（v2.105.0+32）。

覆盖：
  - 品种映射 symbol_to_quantdata（RB0→RB）
  - 字段权威矩阵 validate_field_availability（L0/L1/L2 分层）
  - QuantDataProvider.fetch_ohlcv（假库：连续序列列对齐/hold 映射/非权威字段 NaN）
  - get_term_structure（continuous_map + kline_daily 近远月构建）
  - 熔断（连续失败 + 冷却）
  - aggregator 链路（source=QUANTDATA + pre_settle 派生兼容）

全部用例使用 tmp_path 假库，不依赖本机 D:\\QuantData。
"""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np
import pytest

from fts.data_sources.aggregator import FuturesDataAggregator
from fts.data_sources.base import SourceUnavailable
from fts.data_sources.quantdata_provider import (
    L2_MISSING_FIELDS,
    QuantDataProvider,
    symbol_to_quantdata,
    validate_field_availability,
)

# ─── fixtures ─────────────────────────────────────────────


@pytest.fixture
def fake_quantdata_home(tmp_path):
    """构造假 QuantData 库（continuous_daily / continuous_map / kline_daily）。"""
    db = tmp_path / "market_data" / "kline_history.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    con.execute(
        """
        CREATE TABLE continuous_daily (
            symbol VARCHAR, series_type VARCHAR, trade_date DATE,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, open_interest DOUBLE, adj_factor DOUBLE
        )
        """
    )
    # RB main：30 个交易日，close 逐日 +1
    start = date(2026, 7, 1)
    rows = []
    for i in range(30):
        d = start + timedelta(days=i)
        c = 3000.0 + i
        rows.append(
            ("RB", "main", d, c - 1, c + 1, c - 0.5, c, 1000 + i, 50000 + i * 100, 1.05)
        )
    con.executemany(
        "INSERT INTO continuous_daily VALUES (?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.execute(
        """
        CREATE TABLE continuous_map (
            symbol VARCHAR, trade_date DATE, main_contract VARCHAR, sub_contract VARCHAR
        )
        """
    )
    con.executemany(
        "INSERT INTO continuous_map VALUES (?,?,?,?)",
        [
            ("RB", start + timedelta(days=i), "SHFE.rb2601", "SHFE.rb2605")
            for i in range(30)
        ],
    )
    con.execute(
        """
        CREATE TABLE kline_daily (
            symbol VARCHAR, trade_date DATE,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, open_interest DOUBLE
        )
        """
    )
    # 近远月 close：近月 3000+，远月 3030+（近月+30，构造正向期限结构）
    krows = []
    for i in range(30):
        d = start + timedelta(days=i)
        krows.append(("SHFE.rb2601", d, 3000 + i, 3001 + i, 2999 + i, 3000 + i, 1000, 50000))
        krows.append(("SHFE.rb2605", d, 3030 + i, 3031 + i, 3029 + i, 3030 + i, 500, 20000))
    con.executemany("INSERT INTO kline_daily VALUES (?,?,?,?,?,?,?,?)", krows)
    con.close()
    return tmp_path


# ─── 品种映射 ─────────────────────────────────────────────


class TestSymbolMapping:
    def test_continuous_symbol_strip_zero(self) -> None:
        assert symbol_to_quantdata("RB0") == "RB"
        assert symbol_to_quantdata("SC0") == "SC"

    def test_plain_symbol_passthrough(self) -> None:
        assert symbol_to_quantdata("RB") == "RB"

    def test_contract_symbol_not_stripped(self) -> None:
        # 具体合约（含数字尾）不按连续合约处理，原样返回
        assert symbol_to_quantdata("RB2601") == "RB2601"


# ─── 字段权威矩阵 ─────────────────────────────────────────


class TestFieldAuthority:
    def test_l0_authoritative(self) -> None:
        res = validate_field_availability(["close", "hold", "volume"])
        assert res["authoritative"] == ["close", "hold", "volume"]
        assert res["missing"] == []

    def test_l1_fallback(self) -> None:
        res = validate_field_availability(["settle", "vwap"])
        assert res["fallback"] == ["settle", "vwap"]

    def test_l2_missing(self) -> None:
        res = validate_field_availability(["fut_inventory"])
        assert "fut_inventory" in res["missing"]
        assert "fut_inventory" in L2_MISSING_FIELDS

    def test_unknown_field(self) -> None:
        res = validate_field_availability(["nonexistent_field"])
        assert "nonexistent_field" in res["unknown"]


# ─── Provider fetch_ohlcv ─────────────────────────────────


class TestFetchOhlcv:
    def test_fetch_ohlcv_alignment(self, fake_quantdata_home) -> None:
        p = QuantDataProvider(home=str(fake_quantdata_home))
        df = p.get_ohlcv("RB0", days=300)
        assert len(df) == 30
        # 17 列契约
        for col in (
            "date", "open", "high", "low", "close", "volume",
            "hold", "amount", "settle", "pre_settle", "oi_change", "vwap",
            "symbol", "period", "source", "fetched_at", "trace_id",
        ):
            assert col in df.columns, f"缺列 {col}"
        # hold = open_interest 映射
        assert float(df["hold"].iloc[-1]) == pytest.approx(50000 + 29 * 100)
        # 复权 close（假库 close 原值，未再复权）
        assert float(df["close"].iloc[-1]) == pytest.approx(3000 + 29)
        # 非权威字段 NaN（float，np.isnan 兼容）
        assert df["settle"].isna().all()
        assert df["settle"].dtype == np.float64
        # 元数据
        assert (df["source"] == "QUANTDATA").all()
        assert (df["symbol"] == "RB0").all()

    def test_fetch_ohlcv_unknown_symbol_returns_none(self, fake_quantdata_home) -> None:
        p = QuantDataProvider(home=str(fake_quantdata_home))
        assert p.fetch_ohlcv("NOPE0", days=100) is None

    def test_is_available_true(self, fake_quantdata_home) -> None:
        p = QuantDataProvider(home=str(fake_quantdata_home))
        assert p.is_available() is True

    def test_is_available_false_missing_db(self, tmp_path) -> None:
        p = QuantDataProvider(home=str(tmp_path / "no_such_dir"))
        assert p.is_available() is False


# ─── 期限结构构建 ─────────────────────────────────────────


class TestTermStructure:
    def test_build_term_structure(self, fake_quantdata_home) -> None:
        p = QuantDataProvider(home=str(fake_quantdata_home))
        ts = p.get_term_structure("RB0", days=100)
        assert len(ts) == 30
        assert {"date", "term_spread", "roll_yield", "near_contract", "far_contract"} <= set(ts.columns)
        # 近月 3000+，远月 3030+ → 正向期限结构 ≈ 0.01
        spread = float(ts["term_spread"].iloc[-1])
        assert spread == pytest.approx(30.0 / (3000 + 29), rel=1e-6)

    def test_term_structure_empty_without_map(self, tmp_path) -> None:
        db = tmp_path / "market_data" / "kline_history.duckdb"
        db.parent.mkdir(parents=True)
        con = duckdb.connect(str(db))
        con.execute(
            "CREATE TABLE continuous_map (symbol VARCHAR, trade_date DATE, main_contract VARCHAR, sub_contract VARCHAR)"
        )
        con.close()
        p = QuantDataProvider(home=str(tmp_path))
        assert p.get_term_structure("RB0").empty


# ─── 熔断 ─────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_breaker_opens_after_threshold(self, tmp_path) -> None:
        # 不存在的库 → 连续失败触发熔断 → is_available 返回 False（熔断期不重试探活）
        p = QuantDataProvider(
            home=str(tmp_path / "missing"),
            circuit_breaker_threshold=2,
            circuit_breaker_cooldown_seconds=600,
        )
        assert p.is_available() is False  # 第 1 次失败
        assert p.is_available() is False  # 第 2 次失败（达阈值）
        assert p.is_available() is False  # 熔断 OPEN：不重试，仍 False

    def test_breaker_raises_source_unavailable(self, fake_quantdata_home) -> None:
        p = QuantDataProvider(
            home=str(fake_quantdata_home),
            circuit_breaker_threshold=1,
            circuit_breaker_cooldown_seconds=600,
        )
        # 触发熔断后 fetch 抛 SourceUnavailable（聚合器据此判定跳过该源）
        p._record_failure("test")
        with pytest.raises(SourceUnavailable):
            p.fetch_ohlcv("RB0", days=100)


# ─── aggregator 链路兼容 ──────────────────────────────────


class TestAggregatorIntegration:
    def test_aggregator_uses_quantdata(self, fake_quantdata_home) -> None:
        agg = FuturesDataAggregator(
            sources=[QuantDataProvider(home=str(fake_quantdata_home))],
            db_path=None,
            enhancers=[],
        )
        df = agg.get_ohlcv("RB0", days=200)
        assert (df["source"] == "QUANTDATA").all()
        assert len(df) == 30
        # pre_settle 派生兼容（np.isnan 不抛错）
        assert df["pre_settle"].isna().sum() == 0  # 全部由 close.shift(1) 派生

    def test_aggregator_write_cache(self, fake_quantdata_home, tmp_path) -> None:
        cache = tmp_path / "cache.duckdb"
        agg = FuturesDataAggregator(
            sources=[QuantDataProvider(home=str(fake_quantdata_home))],
            db_path=cache,
            enhancers=[],
        )
        agg.get_ohlcv("RB0", days=100)
        con = duckdb.connect(str(cache), read_only=True)
        try:
            n = con.execute("SELECT count(*) FROM kline_cache").fetchone()[0]
            sym_src = con.execute("SELECT DISTINCT symbol, source FROM kline_cache").fetchall()
        finally:
            con.close()
        assert n == 30
        assert ("RB0", "QUANTDATA") in sym_src
