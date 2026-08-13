"""scripts/audit_mhf_minute_data.py 阶段0 审计逻辑单元测试。

覆盖：合格判断边界、日线 date 列归一化、合约乘数修正成交额。
纯逻辑测试（不依赖 TDX 实时服务 / DuckDB）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_mhf_minute_data import (  # noqa: E402
    _fetch_daily,
    _is_qualified,
)


class _FakeDailySrc:
    """伪造 TdxLocalSource：fetch_ohlcv 返回日线 DataFrame（date 列 %Y%m%d）。"""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def fetch_ohlcv(self, *args: object, **kwargs: object) -> pd.DataFrame:
        return self._df


class TestIsQualified:
    """合格品种判断边界。"""

    def test_ok_and_enough(self) -> None:
        rec = {"ok": True, "days": 200, "day_avg_amount": 6e8}
        assert _is_qualified(rec, min_days=120, min_amount=5e8)

    def test_missing_data(self) -> None:
        rec = {"ok": False, "days": 0, "reason": "5m 无数据"}
        assert not _is_qualified(rec, min_days=120, min_amount=5e8)

    def test_days_below(self) -> None:
        rec = {"ok": True, "days": 100, "day_avg_amount": 9e8}
        assert not _is_qualified(rec, min_days=120, min_amount=5e8)

    def test_amount_below(self) -> None:
        rec = {"ok": True, "days": 200, "day_avg_amount": 1e8}
        assert not _is_qualified(rec, min_days=120, min_amount=5e8)

    def test_fallback_to_minute_amount(self) -> None:
        rec = {"ok": True, "days": 200, "avg_daily_amount": 7e8}
        assert _is_qualified(rec, min_days=120, min_amount=5e8)


class TestFetchDaily:
    """日线 date 列（%Y%m%d）归一化为 datetime。"""

    def test_date_column_parsed(self) -> None:
        df = pd.DataFrame(
            {
                "date": ["20260811", "20260812", "20260813"],
                "close": [1.0, 2.0, 3.0],
                "volume": [10.0, 20.0, 30.0],
            }
        )
        src = _FakeDailySrc(df)
        out = _fetch_daily("RB0", src)
        assert not out.empty
        assert len(out) == 3
        assert out["datetime"].iloc[0] == pd.Timestamp("2026-08-11")

    def test_empty_returns_empty(self) -> None:
        src = _FakeDailySrc(pd.DataFrame())
        assert _fetch_daily("RB0", src).empty

    def test_malformed_date_dropped(self) -> None:
        df = pd.DataFrame(
            {
                "date": ["20260811", "bad-date", "20260813"],
                "close": [1.0, 2.0, 3.0],
                "volume": [10.0, 20.0, 30.0],
            }
        )
        out = _fetch_daily("RB0", _FakeDailySrc(df))
        assert len(out) == 2  # 非法日期被剔除


class TestMultiplierAmount:
    """合约乘数修正的可比成交额（日线 close×volume×mult 60 日均值）。"""

    def test_multiplier_applied(self) -> None:
        # RB0 乘数 10：close=3000, vol=10 万手 → 单日 30 亿元
        dates = pd.date_range("2026-06-01", periods=60, freq="B")
        day = pd.DataFrame(
            {
                "date": [d.strftime("%Y%m%d") for d in dates],
                "close": [3000.0] * 60,
                "volume": [100000.0] * 60,
            }
        )
        src = _FakeDailySrc(day)
        out = _fetch_daily("RB0", src)
        # 复用脚本内乘数修正逻辑（与 _audit_symbol 一致）
        from scripts.audit_mhf_minute_data import contract_multiplier

        mult = contract_multiplier("RB0")
        assert mult == 10.0
        assert not out.empty
        amount = float(
            (pd.to_numeric(out["close"]) * pd.to_numeric(out["volume"]) * mult)
            .tail(60)
            .mean()
        )
        assert amount == pytest.approx(3000.0 * 100000.0 * 10.0)
        assert np.isfinite(amount)
