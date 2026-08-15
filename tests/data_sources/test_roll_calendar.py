"""
tests/data_sources/test_roll_calendar.py — 期货换月日历与复权测试 (v2.58.0, GAP-046)。

覆盖:
  1. RollCalendar.build_roll_calendar — 最大成交量主力判定 + 换月事件
  2. compute_adjust_factors — 比率法后复权累积因子
  3. apply_adjustment — 复权序列 + adj_factor 列
  4. contract_kline 缺失降级（无表/无数据 → 原始序列 + 因子 1.0）
  5. 切换日价格缺失 → 跳过该换月事件
  6. BacktestPipeline 展期成本扣除（持仓穿越换月日）
  7. settings 配置默认值（futures_adjusted / roll_cost_bps）
  8. FuturesDataProvider.get_ohlcv(adjusted) 复权应用
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from fts.data_sources.roll_calendar import RollCalendar, RollEvent


# ─── 测试数据构造 ────────────────────────────────────────


def _build_contract_db(db_path) -> None:
    """构造含 RB2509 / RB2601 两个合约的 contract_kline 临时库。

    前 60 日 RB2509 为最大成交量主力，后 40 日 RB2601 切换为主力。
    切换日（第 61 日）: old_close=3500, new_close=3600 → adj_ratio≈1.0286。
    """
    con = duckdb.connect(str(db_path))
    try:
        con.execute("""
            CREATE TABLE contract_kline (
                symbol VARCHAR, contract VARCHAR, period VARCHAR, date DATE,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                volume DOUBLE, amount DOUBLE, hold DOUBLE, settle DOUBLE,
                source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR
            )
        """)
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        rows = []
        for i, d in enumerate(dates):
            if i < 60:
                rows.append(
                    (
                        "RB",
                        "RB2509",
                        "daily",
                        d.date(),
                        3490,
                        3510,
                        3480,
                        3500 + i * 0.1,
                        100_000.0,
                        0.0,
                        50_000.0,
                        3500.0,
                        "TEST",
                        pd.Timestamp.now(),
                        "t",
                    )
                )
                rows.append(
                    (
                        "RB",
                        "RB2601",
                        "daily",
                        d.date(),
                        3590,
                        3610,
                        3580,
                        3600 + i * 0.1,
                        10_000.0,
                        0.0,
                        5_000.0,
                        3600.0,
                        "TEST",
                        pd.Timestamp.now(),
                        "t",
                    )
                )
            else:
                rows.append(
                    (
                        "RB",
                        "RB2509",
                        "daily",
                        d.date(),
                        3490,
                        3510,
                        3480,
                        3500 + i * 0.1,
                        10_000.0,
                        0.0,
                        5_000.0,
                        3500.0,
                        "TEST",
                        pd.Timestamp.now(),
                        "t",
                    )
                )
                rows.append(
                    (
                        "RB",
                        "RB2601",
                        "daily",
                        d.date(),
                        3590,
                        3610,
                        3580,
                        3600 + i * 0.1,
                        100_000.0,
                        0.0,
                        50_000.0,
                        3600.0,
                        "TEST",
                        pd.Timestamp.now(),
                        "t",
                    )
                )
        con.executemany(
            """
            INSERT INTO contract_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    finally:
        con.close()


# ─── RollCalendar ────────────────────────────────────────


class TestRollCalendarBuild:
    """换月日历构建。"""

    def test_detects_roll_event(self, tmp_path):
        """应检测到 RB2509 → RB2601 的换月事件。"""
        db = tmp_path / "fts.duckdb"
        _build_contract_db(db)
        rc = RollCalendar(str(db))
        rolls = rc.build_roll_calendar("RB0")
        assert len(rolls) == 1
        roll = rolls[0]
        assert roll.old_contract == "RB2509"
        assert roll.new_contract == "RB2601"
        # 切换日 = 第 61 日（2024-03-01），规范化后为 datetime.date
        assert roll.date == pd.Timestamp("2024-03-01").date()
        # 切换日收盘: old=3500+60×0.1=3506, new=3600+60×0.1=3606
        assert roll.old_close == pytest.approx(3506.0, rel=1e-6)
        assert roll.new_close == pytest.approx(3606.0, rel=1e-6)
        assert roll.adj_ratio == pytest.approx(3606.0 / 3506.0, rel=1e-6)

    def test_date_stored_as_varchar_matches_close(self, tmp_path):
        """回归（v2.104.0+39）: contract_kline.date 为 VARCHAR 时切换日价格应命中。

        生产库该列为 VARCHAR，fetchdf 返回 object(str)，历史实现 _close_on 用
        pd.Timestamp 比较永远失配 → 所有换月事件误判"价格缺失"。修复后仍须检出事件。
        """
        db = tmp_path / "fts.duckdb"
        con = duckdb.connect(str(db))
        try:
            con.execute("""
                CREATE TABLE contract_kline (
                    symbol VARCHAR, contract VARCHAR, period VARCHAR, date VARCHAR,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                    volume DOUBLE, amount DOUBLE, hold DOUBLE, settle DOUBLE,
                    source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR
                )
            """)
            dates = pd.date_range("2024-01-01", periods=100, freq="D")
            rows = []
            for i, d in enumerate(dates):
                if i < 60:
                    rows.append(("RB", "RB2509", "daily", str(d.date()), 3490, 3510, 3480, 3500 + i * 0.1, 100_000.0, 0.0, 50_000.0, 3500.0, "TEST", pd.Timestamp.now(), "t"))
                    rows.append(("RB", "RB2601", "daily", str(d.date()), 3590, 3610, 3580, 3600 + i * 0.1, 10_000.0, 0.0, 5_000.0, 3600.0, "TEST", pd.Timestamp.now(), "t"))
                else:
                    rows.append(("RB", "RB2509", "daily", str(d.date()), 3490, 3510, 3480, 3500 + i * 0.1, 10_000.0, 0.0, 5_000.0, 3500.0, "TEST", pd.Timestamp.now(), "t"))
                    rows.append(("RB", "RB2601", "daily", str(d.date()), 3590, 3610, 3580, 3600 + i * 0.1, 100_000.0, 0.0, 50_000.0, 3600.0, "TEST", pd.Timestamp.now(), "t"))
            con.executemany("INSERT INTO contract_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        finally:
            con.close()
        rc = RollCalendar(str(db))
        rolls = rc.build_roll_calendar("RB0")
        assert len(rolls) == 1
        assert rolls[0].old_contract == "RB2509"
        assert rolls[0].new_contract == "RB2601"
        assert rolls[0].old_close == pytest.approx(3506.0, rel=1e-6)
        assert rolls[0].new_close == pytest.approx(3606.0, rel=1e-6)

    def test_zero_volume_rows_excluded_from_dominant(self, tmp_path):
        """回归（v2.104.0+39）: volume 为 0 的行不参与主力判定，消除假换月来回切换。"""
        db = tmp_path / "fts.duckdb"
        con = duckdb.connect(str(db))
        try:
            con.execute("""
                CREATE TABLE contract_kline (
                    symbol VARCHAR, contract VARCHAR, period VARCHAR, date VARCHAR,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                    volume DOUBLE, amount DOUBLE, hold DOUBLE, settle DOUBLE,
                    source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR
                )
            """)
            # 5 日：前 3 日 A 主力（volume=100），第 4 日 A/B 均 volume=0，
            # 第 5 日 B 主力（volume=100）→ 应只有一次 A→B 换月（第 4 日不参与判定）
            dates = pd.date_range("2024-01-01", periods=5, freq="D")
            rows = []
            for i, d in enumerate(dates):
                if i < 3:
                    rows.append(("RB", "A", "daily", str(d.date()), 100, 101, 99, 100.0, 100.0, 0.0, 50.0, 100.0, "TEST", pd.Timestamp.now(), "t"))
                    rows.append(("RB", "B", "daily", str(d.date()), 200, 201, 199, 200.0, 10.0, 0.0, 5.0, 200.0, "TEST", pd.Timestamp.now(), "t"))
                elif i == 3:
                    rows.append(("RB", "A", "daily", str(d.date()), 100, 101, 99, 100.0, 0.0, 0.0, 50.0, 100.0, "TEST", pd.Timestamp.now(), "t"))
                    rows.append(("RB", "B", "daily", str(d.date()), 200, 201, 199, 200.0, 0.0, 0.0, 5.0, 200.0, "TEST", pd.Timestamp.now(), "t"))
                else:
                    rows.append(("RB", "A", "daily", str(d.date()), 100, 101, 99, 100.0, 10.0, 0.0, 50.0, 100.0, "TEST", pd.Timestamp.now(), "t"))
                    rows.append(("RB", "B", "daily", str(d.date()), 200, 201, 199, 200.0, 100.0, 0.0, 5.0, 200.0, "TEST", pd.Timestamp.now(), "t"))
            con.executemany("INSERT INTO contract_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        finally:
            con.close()
        rc = RollCalendar(str(db))
        rolls = rc.build_roll_calendar("RB0")
        assert len(rolls) == 1
        assert rolls[0].old_contract == "A"
        assert rolls[0].new_contract == "B"

    def test_no_roll_single_contract(self, tmp_path):
        """单一合约无换月 → 空事件列表。"""
        db = tmp_path / "fts.duckdb"
        con = duckdb.connect(str(db))
        try:
            con.execute("""
                CREATE TABLE contract_kline (
                    symbol VARCHAR, contract VARCHAR, period VARCHAR, date DATE,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                    volume DOUBLE, amount DOUBLE, hold DOUBLE, settle DOUBLE,
                    source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR
                )
            """)
            dates = pd.date_range("2024-01-01", periods=30, freq="D")
            rows = [
                (
                    "RB",
                    "RB2509",
                    "daily",
                    d.date(),
                    3500,
                    3510,
                    3490,
                    3500.0,
                    100.0,
                    0.0,
                    50.0,
                    3500.0,
                    "TEST",
                    pd.Timestamp.now(),
                    "t",
                )
                for d in dates
            ]
            con.executemany("INSERT INTO contract_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        finally:
            con.close()
        rc = RollCalendar(str(db))
        assert rc.build_roll_calendar("RB0") == []

    def test_table_missing_returns_empty(self, tmp_path):
        """contract_kline 表缺失 → 空事件列表（降级）。"""
        db = tmp_path / "fts.duckdb"
        duckdb.connect(str(db)).close()
        rc = RollCalendar(str(db))
        assert rc.build_roll_calendar("RB0") == []

    def test_missing_prices_skips_event(self, tmp_path):
        """切换日旧合约收盘缺失 → 跳过该换月事件。"""
        db = tmp_path / "fts.duckdb"
        con = duckdb.connect(str(db))
        try:
            con.execute("""
                CREATE TABLE contract_kline (
                    symbol VARCHAR, contract VARCHAR, period VARCHAR, date DATE,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                    volume DOUBLE, amount DOUBLE, hold DOUBLE, settle DOUBLE,
                    source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR
                )
            """)
            dates = pd.date_range("2024-01-01", periods=6, freq="D")
            rows = []
            for i, d in enumerate(dates):
                # 前 3 日 RB2509 主力；第 4 日起 RB2601 主力，但切换日缺 RB2509 收盘
                if i < 3:
                    rows.append(
                        (
                            "RB",
                            "RB2509",
                            "daily",
                            d.date(),
                            3500,
                            3510,
                            3490,
                            3500.0,
                            100.0,
                            0.0,
                            50.0,
                            3500.0,
                            "TEST",
                            pd.Timestamp.now(),
                            "t",
                        )
                    )
                    rows.append(
                        (
                            "RB",
                            "RB2601",
                            "daily",
                            d.date(),
                            3600,
                            3610,
                            3590,
                            3600.0,
                            10.0,
                            0.0,
                            5.0,
                            3600.0,
                            "TEST",
                            pd.Timestamp.now(),
                            "t",
                        )
                    )
                else:
                    # RB2509 第 4 日起无记录（缺失）
                    rows.append(
                        (
                            "RB",
                            "RB2601",
                            "daily",
                            d.date(),
                            3600,
                            3610,
                            3590,
                            3600.0,
                            100.0,
                            0.0,
                            50.0,
                            3600.0,
                            "TEST",
                            pd.Timestamp.now(),
                            "t",
                        )
                    )
            con.executemany("INSERT INTO contract_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        finally:
            con.close()
        rc = RollCalendar(str(db))
        assert rc.build_roll_calendar("RB0") == []


class TestAdjustFactors:
    """复权因子计算。"""

    def test_ratio_accumulation(self):
        """多换月事件 → 切换日之前数据乘所有后续 adj_ratio。"""
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        rolls = [
            RollEvent(
                date=dates[10].date(),
                old_contract="A",
                new_contract="B",
                old_close=100.0,
                new_close=110.0,
                adj_ratio=1.10,
            ),
            RollEvent(
                date=dates[20].date(),
                old_contract="B",
                new_contract="C",
                old_close=110.0,
                new_close=121.0,
                adj_ratio=1.10,
            ),
        ]
        factor = RollCalendar().compute_adjust_factors(dates, rolls)
        # 第 10 日之前: 1.10 × 1.10 = 1.21
        assert factor.iloc[0] == pytest.approx(1.21)
        assert factor.iloc[9] == pytest.approx(1.21)
        # 第 10~19 日: 仅第二个换月 → 1.10
        assert factor.iloc[10] == pytest.approx(1.10)
        assert factor.iloc[19] == pytest.approx(1.10)
        # 第 20 日之后: 1.0
        assert factor.iloc[20] == pytest.approx(1.0)
        assert factor.iloc[-1] == pytest.approx(1.0)

    def test_empty_rolls_identity(self):
        """无换月事件 → 因子全 1.0。"""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        factor = RollCalendar().compute_adjust_factors(dates, [])
        assert (factor == 1.0).all()


class TestApplyAdjustment:
    """复权序列应用。"""

    def test_applies_adjustment(self, tmp_path):
        """复权后 close 在切换日前被缩放，切换日后不变；新增 adj_factor 列。"""
        db = tmp_path / "fts.duckdb"
        _build_contract_db(db)
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        df = pd.DataFrame(
            {
                "open": np.linspace(3500, 3600, 100),
                "high": np.linspace(3505, 3605, 100),
                "low": np.linspace(3495, 3595, 100),
                "close": np.linspace(3500, 3600, 100),
                "volume": np.full(100, 1e5),
                "settle": np.linspace(3500, 3600, 100),
            },
            index=dates,
        )
        rc = RollCalendar(str(db))
        result, rolls = rc.apply_adjustment(df, "RB0")
        assert len(rolls) == 1
        assert "adj_factor" in result.columns
        # 切换日（第 60 日）之前 close 被放大
        assert result["close"].iloc[0] > df["close"].iloc[0]
        # 切换日之后 close 不变
        assert result["close"].iloc[-1] == pytest.approx(df["close"].iloc[-1])
        # 切换日当天 adj_factor == 1.0（新主力为基准）
        assert result["adj_factor"].iloc[60] == pytest.approx(1.0)
        # 切换日前 adj_factor == adj_ratio（3606/3506，切换日两合约收盘）
        assert result["adj_factor"].iloc[0] == pytest.approx(3606.0 / 3506.0, rel=1e-6)

    def test_no_roll_identity(self, tmp_path):
        """无换月日历 → 返回原始 df，adj_factor 全 1.0。"""
        db = tmp_path / "fts.duckdb"
        duckdb.connect(str(db)).close()  # 空库（无 contract_kline 表）
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame({"close": np.arange(10.0) + 100.0}, index=dates)
        rc = RollCalendar(str(db))
        result, rolls = rc.apply_adjustment(df, "RB0")
        assert rolls == []
        assert result["adj_factor"].iloc[0] == 1.0
        assert result["close"].iloc[0] == pytest.approx(df["close"].iloc[0])


# ─── BacktestPipeline 展期成本 ────────────────────────────


class TestRollCostInBacktest:
    """回测持仓穿越换月日扣除展期成本。"""

    def test_roll_cost_deducted(self):
        """持仓穿越换月日时，策略收益扣除 |position| × roll_cost_bps。"""
        from fts.factor_engine.backtest_pipeline import BacktestPipeline

        pipeline = BacktestPipeline()
        n = 30
        # 有波动的信号 → 滚动 z-score 在第 10 日已产生非零持仓
        rng = np.random.default_rng(42)
        factor_values = rng.normal(0.0, 1.0, n)
        forward_returns = np.zeros(n)
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        roll_dates = {str(dates[10].date())}

        returns_base, positions, _ = pipeline._compute_strategy_returns(
            factor_values,
            forward_returns,
            cost_rate=0.0003,
            slippage=0.0001,
            dates=dates,
            roll_dates=None,
            roll_cost_bps=10.0,
        )
        returns_roll, _, _ = pipeline._compute_strategy_returns(
            factor_values,
            forward_returns,
            cost_rate=0.0003,
            slippage=0.0001,
            dates=dates,
            roll_dates=roll_dates,
            roll_cost_bps=10.0,
        )
        # 第 10 日确已建仓
        assert abs(positions[10]) > 1e-8
        # 换月日扣除 |position[10]| × 10bps，其余收益不受影响
        assert returns_roll[10] == pytest.approx(returns_base[10] - abs(positions[10]) * (10.0 / 10000.0), abs=1e-12)
        assert returns_roll[5] == pytest.approx(returns_base[5], abs=1e-12)

    def test_no_roll_dates_no_cost(self):
        """无换月日期 → 不扣展期成本。"""
        from fts.factor_engine.backtest_pipeline import BacktestPipeline

        pipeline = BacktestPipeline()
        n = 20
        factor_values = np.ones(n) * 2.0
        forward_returns = np.zeros(n)
        dates = pd.date_range("2024-01-01", periods=n, freq="D")

        returns, _, _ = pipeline._compute_strategy_returns(
            factor_values,
            forward_returns,
            cost_rate=0.0003,
            slippage=0.0001,
            dates=dates,
            roll_dates=None,
            roll_cost_bps=10.0,
        )
        assert returns[10] == pytest.approx(0.0, abs=1e-9)

    def test_backtest_input_roll_fields(self):
        """BacktestInput 支持 roll_dates / roll_cost_bps 字段。"""
        from fts.factor_engine.backtest_pipeline import BacktestInput

        inp = BacktestInput(
            factor={"factor_id": "fct_x", "code": "x"},
            data=pd.DataFrame({"close": [1.0]}),
            roll_dates={"2024-01-10"},
            roll_cost_bps=5.0,
        )
        assert inp.roll_dates == {"2024-01-10"}
        assert inp.roll_cost_bps == 5.0


# ─── 配置默认值 ──────────────────────────────────────────


class TestConfigDefaults:
    """futures_adjusted / roll_cost_bps 配置默认值（v2.58.0）。"""

    def test_defaults(self):
        from fts.config.settings import FTSConfig

        cfg = FTSConfig()
        assert cfg.futures_adjusted is True
        assert cfg.roll_cost_bps == pytest.approx(2.0)

    def test_env_overrides(self, monkeypatch):
        from fts.config.settings import load_config

        monkeypatch.setenv("FTS_FUTURES_ADJUSTED", "false")
        monkeypatch.setenv("FTS_ROLL_COST_BPS", "5.5")
        cfg = load_config(config_path=None)
        assert cfg.futures_adjusted is False
        assert cfg.roll_cost_bps == pytest.approx(5.5)


# ─── get_ohlcv 复权应用 ──────────────────────────────────


class TestGetOhlcvAdjusted:
    """FuturesDataProvider.get_ohlcv(adjusted) 复权应用。"""

    def test_adjusted_applies_roll_calendar(self, tmp_path, monkeypatch):
        """adjusted=True 时应用复权；adjusted=False 返回原始序列。"""
        from fts.data_futures import FuturesDataProvider

        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        raw = pd.DataFrame(
            {
                "open": np.linspace(3500, 3600, 10),
                "high": np.linspace(3505, 3605, 10),
                "low": np.linspace(3495, 3595, 10),
                "close": np.linspace(3500, 3600, 10),
                "volume": np.full(10, 1e5),
            },
            index=dates,
        )

        provider = FuturesDataProvider(use_akshare_fallback=False)

        class _FakeRoll:
            def apply_adjustment(self, df, symbol):
                result = df.copy()
                result["close"] = df["close"] * 1.1
                result["adj_factor"] = 1.1
                return result, ["roll_event"]

        monkeypatch.setattr(
            "fts.data_sources.roll_calendar.RollCalendar",
            lambda *a, **k: _FakeRoll(),
        )

        # adjusted=True → 走复权
        provider._get_ohlcv_raw = lambda *a, **k: raw.copy()
        out_adj = provider.get_ohlcv("RB0", adjusted=True)
        assert out_adj["close"].iloc[0] == pytest.approx(raw["close"].iloc[0] * 1.1)
        assert "adj_factor" in out_adj.columns

        # adjusted=False → 原始序列
        out_raw = provider.get_ohlcv("RB0", adjusted=False)
        assert out_raw["close"].iloc[0] == pytest.approx(raw["close"].iloc[0])
