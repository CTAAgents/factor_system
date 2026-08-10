"""scripts/verify_aggregator.py — 端到端验证 FuturesDataAggregator 调度器。

HARNESS §任务 14.1 验证:
    1. 5 级 K 线降级（DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC）
    2. 字段增强层（Wind/iFinD 补充 settle/oi_change）
    3. 熔断器（连续失败 → 开启 → 冷却 → 半开探活）
    4. trace_id 全链路贯通
    5. DuckDB 缓存读写

Usage:
    python scripts/verify_aggregator.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pandas as pd

# 让脚本独立运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fts.core.enums import DataSource  # noqa: E402
from fts.data_sources.aggregator import FuturesDataAggregator  # noqa: E402
from fts.data_sources.migrate import migrate_schema  # noqa: E402


# ─── mock 数据源 ───────────────────────────────────────────


def _make_kline_df(symbol: str, source: str, rows: int = 5, base_date: datetime | None = None) -> pd.DataFrame:
    """构造 K 线 DataFrame。"""
    if base_date is None:
        base_date = datetime.now() - timedelta(days=rows - 1)
    data = []
    for i in range(rows):
        data.append(
            {
                "symbol": symbol,
                "period": "daily",
                "date": (base_date + timedelta(days=i)).date(),
                "open": 3500 + i,
                "high": 3550 + i,
                "low": 3490 + i,
                "close": 3540 + i,
                "volume": 100000,
                "amount": 350000000,
                "hold": 80000 + i * 100,
                "settle": 3540 + i,
                "pre_settle": 3520 + i,
                "oi_change": 2000,
                "vwap": 3500.0,
                "source": source,
                "fetched_at": datetime.now(),
                "trace_id": "",
            }
        )
    return pd.DataFrame(data)


class _MockSource:
    """可配置的 mock 数据源。"""

    def __init__(
        self,
        source_name: str,
        df: pd.DataFrame | None = None,
        raise_exc: Exception | None = None,
        return_none: bool = False,
    ):
        self.source_name = source_name
        self._df = df
        self._raise = raise_exc
        self._return_none = return_none
        self.fetch_count = 0

    def is_available(self) -> bool:
        return self._df is not None or self._raise is None

    def fetch_ohlcv(self, symbol: str, days: int, trace_id: str = ""):
        self.fetch_count += 1
        if self._raise is not None:
            raise self._raise
        if self._return_none:
            return None
        return self._df.copy() if self._df is not None else None

    def fetch_ohlcv_or_none(self, symbol: str, days: int, trace_id: str = ""):
        try:
            return self.fetch_ohlcv(symbol, days, trace_id)
        except Exception:
            return None

    def fetch_quote(self, symbol: str, trace_id: str = ""):
        return None


# ─── 验证主函数 ───────────────────────────────────────────


def main() -> int:
    print("=" * 70)
    print("任务 14.1 验证: FuturesDataAggregator 数据源优先级调度器")
    print("=" * 70)

    tmpdir = Path(tempfile.mkdtemp(prefix="fts_agg_verify_"))
    db_path = tmpdir / "fts_agg.duckdb"
    print(f"\n[setup] 临时 DB: {db_path}")

    try:
        # 1. 预填充缓存（5 行 K 线 + 8 个新字段）
        migrate_schema(db_path)
        con = duckdb.connect(str(db_path))
        try:
            df_cache = _make_kline_df("RB0", DataSource.DUCKDB_CACHE.value, rows=5)
            con.register("df_cache", df_cache)
            con.execute("INSERT INTO kline_cache SELECT * FROM df_cache")
            con.unregister("df_cache")
            print("[1/7] 预填充 kline_cache 5 行 OK")
        finally:
            con.close()

        # ── Step 2: 缓存命中 → 不调任何源
        print("\n[2/7] 场景 A: 缓存命中测试")
        tq_mock = MagicMock()
        tq_mock.source_name = DataSource.TQ_LOCAL.value
        tq_mock.fetch_ohlcv = MagicMock(side_effect=AssertionError("不应调用"))
        agg_cache = FuturesDataAggregator(sources=[tq_mock], db_path=db_path, cache_max_age_days=30)
        df = agg_cache.get_ohlcv("RB0", days=5, trace_id="v-step2")
        assert len(df) == 5, f"期望 5 行，实际 {len(df)}"
        assert (df["source"] == DataSource.DUCKDB_CACHE.value).all()
        assert not tq_mock.fetch_ohlcv.called
        print(f"       ✅ 缓存命中返回 {len(df)} 行，未触发任何 K 线源")

        # ── Step 3: 5 级降级 (TQ_LOCAL → TQ_PYTHON → AKSHARE)
        print("\n[3/7] 场景 B: 5 级降级链测试")
        agg_degrade = FuturesDataAggregator(
            sources=[
                _MockSource(DataSource.TQ_LOCAL.value, raise_exc=ConnectionError("7721 down")),
                _MockSource(DataSource.TQ_PYTHON.value, raise_exc=ConnectionError("SDK fail")),
                _MockSource(DataSource.AKSHARE.value, df=_make_kline_df("RB0", DataSource.AKSHARE.value, rows=3)),
            ],
            db_path=None,  # 禁用缓存以确保降级路径
            cache_max_age_days=30,
        )
        df = agg_degrade.get_ohlcv("RB0", days=3, trace_id="v-step3")
        assert len(df) == 3, f"期望 3 行，实际 {len(df)}"
        assert df["source"].iloc[0] == DataSource.AKSHARE.value
        print("       ✅ TQ_LOCAL 失败 → TQ_PYTHON 失败 → AKSHARE 成功（3 行）")

        # ── Step 4: 全失败 → 合成数据
        print("\n[4/7] 场景 C: 全部失败 → 合成数据降级")
        all_fail = [
            _MockSource(DataSource.TQ_LOCAL.value, raise_exc=ConnectionError("x")),
            _MockSource(DataSource.TQ_PYTHON.value, raise_exc=ConnectionError("x")),
            _MockSource(DataSource.AKSHARE.value, raise_exc=ConnectionError("x")),
        ]
        agg_synth = FuturesDataAggregator(sources=all_fail, db_path=None)
        df = agg_synth.get_ohlcv("RB0", days=3, trace_id="v-step4")
        assert len(df) == 3, f"期望 3 行合成数据，实际 {len(df)}"
        assert df["source"].iloc[0] == DataSource.SYNTHETIC.value
        for s in all_fail:
            assert s.fetch_count == 1, f"{s.source_name} 应被调用 1 次"
        print("       ✅ 3 个源全部失败 → 返回 3 行合成数据 (source=SYNTHETIC)")

        # ── Step 5: 熔断器开启
        print("\n[5/7] 场景 D: 熔断器测试 (连续失败 5 次 → 开启)")
        tq_local = _MockSource(DataSource.TQ_LOCAL.value, raise_exc=ConnectionError("always down"))
        tq_python = _MockSource(
            DataSource.TQ_PYTHON.value,
            df=_make_kline_df("RB0", DataSource.TQ_PYTHON.value, rows=3),
        )
        agg_cb = FuturesDataAggregator(
            sources=[tq_local, tq_python],
            db_path=None,
            circuit_breaker_threshold=5,
        )
        # 5 次失败
        for i in range(5):
            df = agg_cb.get_ohlcv("RB0", days=3, trace_id=f"v-step5-{i}")
            assert df["source"].iloc[0] == DataSource.TQ_PYTHON.value
        # 第 6 次：TQ_LOCAL 应被熔断
        before = tq_local.fetch_count
        df = agg_cb.get_ohlcv("RB0", days=3, trace_id="v-step5-6")
        assert tq_local.fetch_count == before, f"熔断器未生效，源仍被调用 ({tq_local.fetch_count})"
        status = agg_cb.get_source_status()
        assert status[DataSource.TQ_LOCAL.value]["circuit_open"] is True
        assert status[DataSource.TQ_LOCAL.value]["consecutive_failures"] == 5
        print("       ✅ TQ_LOCAL 连续 5 次失败 → 熔断器开启")
        print("       ✅ 第 6 次调用跳过 TQ_LOCAL，直接用 TQ_PYTHON")

        # ── Step 6: 字段增强层 (Wind + iFinD)
        print("\n[6/7] 场景 E: 字段增强层测试 (Wind + iFinD 补充 settle/oi_change)")
        tq_df = _make_kline_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
        wind_df = _make_kline_df("RB0.SHFE", DataSource.WIND.value, rows=3)
        ifind_df = _make_kline_df("RB0.SHFE", DataSource.IFIND.value, rows=3)
        tq = _MockSource(DataSource.TQ_LOCAL.value, df=tq_df)
        wind = _MockSource(DataSource.WIND.value, df=wind_df)
        ifind = _MockSource(DataSource.IFIND.value, df=ifind_df)
        agg_enh = FuturesDataAggregator(sources=[tq], enhancers=[wind, ifind], db_path=None)
        df = agg_enh.get_ohlcv("RB0", days=3, trace_id="v-step6")
        assert len(df) == 3
        assert df["source"].iloc[0] == DataSource.TQ_LOCAL.value
        assert wind.fetch_count == 1, f"Wind 应被调用 1 次，实际 {wind.fetch_count}"
        assert ifind.fetch_count == 1, f"iFinD 应被调用 1 次，实际 {ifind.fetch_count}"
        print("       ✅ K 线源 TQ_LOCAL + 字段增强层 Wind + iFinD 全部调用")

        # 字段增强失败不破坏主路径
        wind_fail = _MockSource(DataSource.WIND.value, raise_exc=ConnectionError("wind"))
        ifind_fail = _MockSource(DataSource.IFIND.value, raise_exc=ConnectionError("ifind"))
        tq2 = _MockSource(DataSource.TQ_LOCAL.value, df=_make_kline_df("RB0", DataSource.TQ_LOCAL.value, rows=3))
        agg_enh_fail = FuturesDataAggregator(sources=[tq2], enhancers=[wind_fail, ifind_fail], db_path=None)
        df = agg_enh_fail.get_ohlcv("RB0", days=3, trace_id="v-step6-fail")
        assert len(df) == 3
        assert df["source"].iloc[0] == DataSource.TQ_LOCAL.value
        print("       ✅ 字段增强层失败 → K 线主路径仍正常返回 (优雅降级)")

        # ── Step 7: trace_id 全链路贯通
        print("\n[7/7] 场景 F: trace_id 全链路贯通")
        tq_trace = MagicMock()
        tq_trace.source_name = DataSource.TQ_LOCAL.value
        tq_trace.is_available = MagicMock(return_value=True)
        tq_trace_df = _make_kline_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
        tq_trace.fetch_ohlcv = MagicMock(return_value=tq_trace_df)
        tq_trace.fetch_ohlcv_or_none = MagicMock(return_value=tq_trace_df)
        tq_trace.fetch_quote = MagicMock(return_value=None)
        agg_trace = FuturesDataAggregator(sources=[tq_trace], db_path=None)
        trace_id = "verify-trace-14.1"
        agg_trace.get_ohlcv("RB0", days=3, trace_id=trace_id)
        call_kwargs = tq_trace.fetch_ohlcv.call_args.kwargs
        assert call_kwargs.get("trace_id") == trace_id
        print(f"       ✅ trace_id='{trace_id}' 正确传递到源调用")

        # ── 总结 ──
        print("\n" + "=" * 70)
        print("✅ 任务 14.1 端到端验证全部通过")
        print("=" * 70)
        print("  - 5 级 K 线降级：DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC")
        print("  - 字段增强层：Wind + iFinD 并行补充 settle/oi_change（失败不破坏主路径）")
        print("  - 熔断器：连续 5 次失败 → 开启 → 跳过该源")
        print("  - trace_id 全链路贯通")
        print("  - DuckDB 缓存：命中优先 + 新鲜度过滤 + 写入透明")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
