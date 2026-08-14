"""G8 统一交易日历 + 断K/跳空清洗测试（plans/35 §5.1，v2.103.0+15）。

覆盖：
- TradingCalendar：节假日剔除 / is_trading_day / align（剔除休市、停牌 ffill）
- from_symbol_dates：多数日期推断（频率门槛）
- mark_panel_data_gaps：缺失占比 >5% 或连续缺失 >3 日 → data_gap
- mark_gap_anomalies：|隔夜跳空| > 5×ATR(20) 且无成交量 → gap_anomaly
- get_futures_panel：面板 df 附加 data_gap/gap_anomaly 列
- 配置默认：inject_overnight_gap_enabled / inject_data_gap_enabled 默认开启（D5）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.data_sources.trading_calendar import (
    TradingCalendar,
    mark_gap_anomalies,
    mark_hold_anomalies,
    mark_panel_data_gaps,
)


def _weekdays(start: str, periods: int = 30) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=periods, freq="B")


class TestTradingCalendar:
    def test_holidays_excluded(self):
        """bdate_range 生成含全部工作日；is_trading_day 拒绝周末。"""
        cal = TradingCalendar(_weekdays("2024-01-01"))
        assert cal.is_trading_day("2024-01-08") is True  # 周一
        assert cal.is_trading_day("2024-01-06") is False  # 周六

    def test_get_trading_days_range(self):
        cal = TradingCalendar(_weekdays("2024-01-01", 30))
        days = cal.get_trading_days("2024-01-15", "2024-01-19")
        assert all(cal.is_trading_day(d) for d in days)
        assert len(days) == 5

    def test_align_drops_weekend_and_ffills_missing(self):
        trading = _weekdays("2024-01-01", 10)  # 10 个工作日
        cal = TradingCalendar(trading)
        # 序列：trading[2]（周三）缺失 → 停牌前向填充；周六行被剔除
        series = pd.Series(
            [1.0, 2.0, 3.0, 5.0],
            index=[trading[0], trading[1], trading[3], trading[4]],
        )
        aligned = cal.align(series)
        assert aligned.index.isin(cal.get_trading_days(trading[0], trading[4])).all()
        # trading[2] 缺失 → ffill = trading[1] 的 2.0
        assert aligned.loc[trading[2]] == 2.0

    def test_from_symbol_dates_frequency(self):
        """日期出现频率 ≥80% 才进入日历。"""
        dates_a = _weekdays("2024-01-01", 10)
        dates_b = _weekdays("2024-01-01", 10)[:8]  # 少 2 天
        dates_c = ["2020-01-01", "2020-01-02"]  # 陈旧品种，1/3 < 80%
        cal = TradingCalendar.from_symbol_dates(
            {"A": dates_a, "B": dates_b, "C": dates_c},
            min_freq=0.8,
        )
        assert "2020-01-01" not in cal._days
        assert len(cal._days) >= 8

    def test_empty_symbols_empty_calendar(self):
        cal = TradingCalendar.from_symbol_dates({}, min_freq=0.8)
        assert len(cal._days) == 0


class TestMarkPanelDataGaps:
    def _panel(self, missing_days: list[int] | None = None, n: int = 40):
        trading = _weekdays("2024-01-01", n)
        idx = trading
        if missing_days:
            idx = trading.drop(trading[missing_days])
        close = np.arange(len(idx)) * 0.1 + 100.0
        return {
            f"S{i}": pd.DataFrame(
                {
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 1000.0,
                },
                index=idx,
            )
            for i in range(3)
        }

    def test_healthy_no_gap(self):
        panel = self._panel()
        marks = mark_panel_data_gaps(panel)
        assert all(not m["data_gap"] for m in marks.values())

    def test_missing_ratio_above_threshold(self):
        """缺失 4/40 = 10% > 5% → data_gap。"""
        panel = self._panel()
        # 缺 4 天 → 10% > 5%
        panel["S0"] = panel["S0"].drop(panel["S0"].index[[1, 2, 3, 4]])
        marks = mark_panel_data_gaps(panel)
        assert marks["S0"]["data_gap"] is True
        assert marks["S1"]["data_gap"] is False

    def test_consecutive_missing_above_three(self):
        """连续缺失 5 天（>3）→ data_gap。"""
        panel = self._panel()
        idx = panel["S0"].index
        drop_pos = list(range(5, 10))  # 连续 5 天
        panel["S0"] = panel["S0"].drop(idx[drop_pos])
        marks = mark_panel_data_gaps(panel)
        assert marks["S0"]["data_gap"] is True
        assert marks["S0"]["max_consecutive_missing"] > 3

    def test_empty_symbol_gap(self):
        marks = mark_panel_data_gaps({"S0": pd.DataFrame()})
        assert marks["S0"]["data_gap"] is True


class TestMarkGapAnomalies:
    def _df(self, n: int = 30, base: float = 100.0):
        idx = _weekdays("2024-01-01", n)
        close = np.linspace(base, base + 2, n)
        return pd.DataFrame(
            {
                "open": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": np.full(n, 1e5),
            },
            index=idx,
        )

    def test_no_anomaly_normal(self):
        df = self._df()
        anomaly = mark_gap_anomalies(df)
        assert not anomaly.any()

    def test_big_gap_without_volume_flagged(self):
        """10×ATR 跳空且无成交量 → 异常标记。"""
        df = self._df()
        # 制造隔夜跳空：第 25 日 open 大幅偏离前收（ATR(20) 已成形），volume=0
        i = 25
        df.loc[df.index[i], "open"] = df["close"].iloc[i - 1] + 10.0  # >> 5×ATR(~1)
        df.loc[df.index[i], "volume"] = 0.0
        anomaly = mark_gap_anomalies(df)
        assert bool(anomaly.iloc[i]) is True

    def test_big_gap_with_volume_not_flagged(self):
        """10×ATR 跳空但有成交量 → 不标异常（真实行情跳空）。"""
        df = self._df()
        i = 25
        df.loc[df.index[i], "open"] = df["close"].iloc[i - 1] + 10.0
        df.loc[df.index[i], "volume"] = 1e5  # 有量
        anomaly = mark_gap_anomalies(df)
        assert bool(anomaly.iloc[i]) is False

    def test_insufficient_data_no_anomaly(self):
        df = self._df(n=10)  # 不足 ATR 窗口
        assert not mark_gap_anomalies(df).any()


class TestMarkHoldAnomalies:
    """持仓量突变标记（CTA 手册阶段1，v2.104.0+20）。"""

    def _df(self, n: int = 30) -> pd.DataFrame:
        idx = _weekdays("2024-01-01", n)
        return pd.DataFrame(
            {
                "close": np.linspace(100, 102, n),
                "hold": np.full(n, 1e5),
            },
            index=idx,
        )

    def test_normal_hold_no_anomaly(self) -> None:
        """持仓量平稳 → 无异常标记。"""
        assert not mark_hold_anomalies(self._df()).any()

    def test_hold_spike_flagged(self) -> None:
        """单日持仓量 3 倍突变 → 异常标记。"""
        df = self._df()
        i = 15
        df.loc[df.index[i], "hold"] = 3e5  # 环比 +200% > 50%
        anomaly = mark_hold_anomalies(df)
        assert bool(anomaly.iloc[i]) is True
        assert bool(anomaly.iloc[i - 1]) is False

    def test_small_change_not_flagged(self) -> None:
        """10% 正常波动 → 不标记。"""
        df = self._df()
        i = 15
        df.loc[df.index[i], "hold"] = 1.1e5  # +10%
        assert bool(mark_hold_anomalies(df).iloc[i]) is False

    def test_hold_collapse_flagged(self) -> None:
        """持仓量骤降至 0 → 异常标记。"""
        df = self._df()
        i = 10
        df.loc[df.index[i], "hold"] = 0.0
        anomaly = mark_hold_anomalies(df)
        assert bool(anomaly.iloc[i]) is True

    def test_single_row_no_anomaly(self) -> None:
        """单行数据 → 全 False（不误标）。"""
        df = self._df(n=1)
        assert not mark_hold_anomalies(df).any()

    def test_missing_hold_column_no_anomaly(self) -> None:
        """无 hold 列 → 全 False（不崩溃）。"""
        df = self._df().drop(columns=["hold"])
        assert not mark_hold_anomalies(df).any()


class TestPanelIntegration:
    def test_get_futures_panel_adds_gap_columns(self, mocker, monkeypatch):
        """get_futures_panel 面板 df 附加 data_gap/gap_anomaly 列。"""
        from fts.data_futures import FuturesDataProvider

        trading = _weekdays("2024-01-01", 40)
        panel_input = {}
        for i in range(3):
            close = np.arange(len(trading)) * 0.1 + 100 * i
            panel_input[f"S{i}"] = pd.DataFrame(
                {
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 1000.0,
                },
                index=trading,
            )
        provider = FuturesDataProvider(use_akshare_fallback=False)
        mocker.patch.object(
            provider,
            "get_ohlcv",
            side_effect=lambda sym, days=500, trace_id="": panel_input[sym],
        )
        panel, common_dates = provider.get_futures_panel(list(panel_input.keys()), days=120)
        for df in panel.values():
            assert "data_gap" in df.columns
            assert "gap_anomaly" in df.columns
            assert "hold_anomaly" in df.columns  # CTA 手册阶段1（v2.104.0+20）
            assert not df["data_gap"].any()  # 健康面板不误标

    def test_config_defaults_on(self):
        """D5：跳空标记/断K清洗默认开启。"""
        from fts.config.settings import get_config

        cfg = get_config()
        assert cfg.inject_overnight_gap_enabled is True
        assert cfg.inject_data_gap_enabled is True
