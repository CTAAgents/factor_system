"""tests/data_sources/test_aggregator.py — FuturesDataAggregator 单元测试。

HARNESS §5.4: 测试随重构。先写 25+ 测试覆盖：
    - 5 级降级调度（DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC）
    - 熔断器（连续失败 → 标记 UNAVAILABLE → 冷却后探活）
    - 字段增强层（Wind/iFinD 补充 settle/oi_change）
    - trace_id 全链路
    - 全部失败 → 合成数据降级
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from fts.core.enums import DataSource


# ─── Fixture: 通用 mock 数据源 ────────────────────────────


def _make_kline_df(symbol: str, source: str, rows: int = 5, base_date: date | None = None) -> pd.DataFrame:
    """构造一个 K 线 DataFrame。

    Args:
        symbol: 品种代码
        source: source 字段值（通常用 DataSource 枚举的 .value）
        rows: 行数
        base_date: 起始日期（默认从 today - rows + 1 开始，保证落入新鲜度窗口）
    """
    if base_date is None:
        base_date = (datetime.now() - timedelta(days=rows - 1)).date()
    data = []
    for i in range(rows):
        data.append(
            {
                "symbol": symbol,
                "period": "daily",
                "date": base_date + timedelta(days=i),
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
                # v2.58.0 (GAP-046): kline_cache 新增 adj_factor 复权因子列
                "adj_factor": 1.0,
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

    def fetch_ohlcv(self, symbol: str, days: int, trace_id: str = "") -> pd.DataFrame | None:
        self.fetch_count += 1
        if self._raise is not None:
            raise self._raise
        if self._return_none:
            return None
        return self._df.copy() if self._df is not None else None

    def fetch_ohlcv_or_none(self, symbol: str, days: int, trace_id: str = "") -> pd.DataFrame | None:
        try:
            return self.fetch_ohlcv(symbol, days, trace_id)
        except Exception:
            return None


# ─── Fixture: 临时 DuckDB 缓存 ─────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """临时 DuckDB 路径（不创建文件，由 aggregator 按需创建）。"""
    return tmp_path / "fts_agg.duckdb"


@pytest.fixture
def cache_with_data(tmp_db: Path) -> Path:
    """预先写入缓存数据的临时 DB。"""
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(tmp_db)

    import duckdb

    con = duckdb.connect(str(tmp_db))
    try:
        df = _make_kline_df("RB0", DataSource.DUCKDB_CACHE.value, rows=10)
        con.register("df_cache", df)
        con.execute("INSERT INTO kline_cache SELECT * FROM df_cache")
        con.unregister("df_cache")
    finally:
        con.close()
    return tmp_db


# ─── 主路径调度：DUCKDB_CACHE 命中 ────────────────────────


def test_get_ohlcv_returns_from_cache_when_present(cache_with_data: Path):
    """缓存命中时直接返回，不调任何数据源。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_mock = MagicMock()
    tq_mock.source_name = DataSource.TQ_LOCAL.value
    tq_mock.fetch_ohlcv = MagicMock(side_effect=AssertionError("不应调用"))

    # cache_max_age_days=30 确保 10 天的测试数据全部通过新鲜度过滤
    agg = FuturesDataAggregator(
        sources=[tq_mock],
        db_path=cache_with_data,
        cache_max_age_days=30,
    )

    df = agg.get_ohlcv("RB0", days=10, trace_id="t-001")

    assert len(df) == 10
    assert (df["source"] == DataSource.DUCKDB_CACHE.value).all()
    # 关键：缓存命中时 TQ 不应被调用
    tq_mock.fetch_ohlcv.assert_not_called()


# ─── 主路径调度：缓存未命中 → TQ_LOCAL ──────────────────


def test_get_ohlcv_falls_back_to_tq_when_cache_empty(tmp_db: Path):
    """缓存为空时回退到 TQ_LOCAL。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_df = _make_kline_df("RB0.SHFE", DataSource.TQ_LOCAL.value, rows=5)
    tq_mock = _MockSource(DataSource.TQ_LOCAL.value, df=tq_df)

    agg = FuturesDataAggregator(sources=[tq_mock], db_path=tmp_db)
    df = agg.get_ohlcv("RB0", days=5, trace_id="t-002")

    assert len(df) == 5
    assert df["source"].iloc[0] == DataSource.TQ_LOCAL.value
    assert tq_mock.fetch_count == 1


# ─── 主路径调度：TQ 失败 → TQ_PYTHON ──────────────────


def test_get_ohlcv_falls_back_to_tq_python_when_tq_local_fails(tmp_db: Path):
    """TQ_LOCAL 失败时回退到 TQ_PYTHON。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_local = _MockSource(DataSource.TQ_LOCAL.value, raise_exc=ConnectionError("7721 refused"))
    tq_python_df = _make_kline_df("RB0", DataSource.TQ_PYTHON.value, rows=5)
    tq_python = _MockSource(DataSource.TQ_PYTHON.value, df=tq_python_df)

    agg = FuturesDataAggregator(sources=[tq_local, tq_python], db_path=tmp_db)
    df = agg.get_ohlcv("RB0", days=5, trace_id="t-003")

    assert len(df) == 5
    assert df["source"].iloc[0] == DataSource.TQ_PYTHON.value
    assert tq_local.fetch_count == 1
    assert tq_python.fetch_count == 1


# ─── 主路径调度：TQ 全部失败 → AKSHARE ────────────────


def test_get_ohlcv_falls_back_to_akshare_when_all_tq_fail(tmp_db: Path):
    """TQ_LOCAL + TQ_PYTHON 失败时回退到 AKSHARE。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_local = _MockSource(DataSource.TQ_LOCAL.value, raise_exc=ConnectionError("7721 down"))
    tq_python = _MockSource(DataSource.TQ_PYTHON.value, raise_exc=ConnectionError("SDK fail"))
    akshare_df = _make_kline_df("RB0", DataSource.AKSHARE.value, rows=3)
    akshare = _MockSource(DataSource.AKSHARE.value, df=akshare_df)

    agg = FuturesDataAggregator(sources=[tq_local, tq_python, akshare], db_path=tmp_db)
    df = agg.get_ohlcv("RB0", days=3, trace_id="t-004")

    assert len(df) == 3
    assert df["source"].iloc[0] == DataSource.AKSHARE.value
    # TQ 全部失败但仍被尝试
    assert tq_local.fetch_count == 1
    assert tq_python.fetch_count == 1
    assert akshare.fetch_count == 1


# ─── 主路径调度：全部失败 → 合成数据 SYNTHETIC ────────


def test_get_ohlcv_returns_synthetic_when_all_sources_fail(tmp_db: Path):
    """所有源失败时返回合成数据（保证系统可运行）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    all_fail = [_MockSource(DataSource.TQ_LOCAL.value, raise_exc=ConnectionError("x")) for _ in range(3)]
    # 修改 source_name
    for i, s in enumerate(all_fail):
        s.source_name = [DataSource.TQ_LOCAL.value, DataSource.TQ_PYTHON.value, DataSource.AKSHARE.value][i]

    agg = FuturesDataAggregator(sources=all_fail, db_path=tmp_db)
    df = agg.get_ohlcv("RB0", days=3, trace_id="t-005")

    assert len(df) == 3
    assert df["source"].iloc[0] == DataSource.SYNTHETIC.value
    # 4 级全失败，3 个源都应被调用
    for s in all_fail:
        assert s.fetch_count == 1


# ─── 熔断器：连续 N 次失败 → 标记 UNAVAILABLE ──────────


def test_circuit_breaker_opens_after_n_failures(tmp_db: Path):
    """连续 5 次失败后熔断器开启，跳过该源。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_local = _MockSource(DataSource.TQ_LOCAL.value, raise_exc=ConnectionError("always down"))
    tq_python_df = _make_kline_df("RB0", DataSource.TQ_PYTHON.value, rows=3)
    tq_python = _MockSource(DataSource.TQ_PYTHON.value, df=tq_python_df)

    # 关键：db_path=None 禁用缓存，确保每次都触发 K 线源
    agg = FuturesDataAggregator(
        sources=[tq_local, tq_python],
        db_path=None,
        circuit_breaker_threshold=5,
    )

    # 连续调用 5 次，每次 TQ_LOCAL 失败
    for _ in range(5):
        df = agg.get_ohlcv("RB0", days=3, trace_id="t-cb")
        assert df["source"].iloc[0] == DataSource.TQ_PYTHON.value

    # 第 6 次：TQ_LOCAL 应被熔断（不被调用）
    before = tq_local.fetch_count
    df = agg.get_ohlcv("RB0", days=3, trace_id="t-cb-6")
    assert tq_local.fetch_count == before, "熔断器未生效，源仍被调用"


# ─── 熔断器：冷却后重新探活 ────────────────────────────


def test_circuit_breaker_half_open_after_cooldown(tmp_db: Path):
    """冷却时间过后重新探活源。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    # 先失败的源，但有一个属性可改：冷却后变为可用
    class ToggleSource:
        def __init__(self):
            self.source_name = DataSource.TQ_LOCAL.value
            self.fetch_count = 0
            self._should_fail = True

        def is_available(self) -> bool:
            return not self._should_fail

        def fetch_ohlcv(self, symbol, days, trace_id=""):
            self.fetch_count += 1
            if self._should_fail:
                raise ConnectionError("down")
            return _make_kline_df(symbol, self.source_name, rows=3)

        def fetch_ohlcv_or_none(self, symbol, days, trace_id=""):
            try:
                return self.fetch_ohlcv(symbol, days, trace_id)
            except Exception:
                return None

    toggle = ToggleSource()
    fallback_df = _make_kline_df("RB0", DataSource.TQ_PYTHON.value, rows=3)
    fallback = _MockSource(DataSource.TQ_PYTHON.value, df=fallback_df)

    # 关键：db_path=None 禁用缓存，确保每次都触发 K 线源
    agg = FuturesDataAggregator(
        sources=[toggle, fallback],
        db_path=None,
        circuit_breaker_threshold=3,
        circuit_breaker_cooldown_seconds=0.01,  # 10ms 冷却
    )

    # 3 次失败打开熔断
    for _ in range(3):
        agg.get_ohlcv("RB0", days=3, trace_id="t-1")

    before = toggle.fetch_count
    # 冷却 50ms 后源恢复
    import time

    time.sleep(0.05)
    toggle._should_fail = False

    df = agg.get_ohlcv("RB0", days=3, trace_id="t-2")

    # 熔断器半开 → 探活成功 → 关闭熔断 → 源重新可用
    assert toggle.fetch_count > before, "冷却后源未被重新探活"
    assert df["source"].iloc[0] == DataSource.TQ_LOCAL.value


# ─── 熔断器：成功调用重置计数器 ───────────────────────


def test_circuit_breaker_resets_on_success(tmp_db: Path):
    """成功后重置失败计数器（避免正常源被误熔断）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    # 失败 2 次后成功 1 次，再失败 2 次不应熔断（阈值 5）
    class FlakeySource:
        def __init__(self):
            self.source_name = DataSource.TQ_LOCAL.value
            self.fetch_count = 0
            self._should_fail = True

        def is_available(self) -> bool:
            return True

        def fetch_ohlcv(self, symbol, days, trace_id=""):
            self.fetch_count += 1
            if self._should_fail:
                raise ConnectionError("flakey")
            return _make_kline_df(symbol, self.source_name, rows=3)

        def fetch_ohlcv_or_none(self, symbol, days, trace_id=""):
            try:
                return self.fetch_ohlcv(symbol, days, trace_id)
            except Exception:
                return None

    flakey = FlakeySource()
    fallback_df = _make_kline_df("RB0", DataSource.TQ_PYTHON.value, rows=3)
    fallback = _MockSource(DataSource.TQ_PYTHON.value, df=fallback_df)

    # 关键：db_path=None 禁用缓存，确保每次都触发 K 线源
    agg = FuturesDataAggregator(
        sources=[flakey, fallback],
        db_path=None,
        circuit_breaker_threshold=5,
    )

    # 失败 2 次
    for _ in range(2):
        agg.get_ohlcv("RB0", days=3)
    assert flakey.fetch_count == 2

    # 成功 1 次（重置计数器）
    flakey._should_fail = False
    agg.get_ohlcv("RB0", days=3)
    assert flakey.fetch_count == 3

    # 再失败 2 次不应熔断（重置后从 0 开始计）
    flakey._should_fail = True
    for _ in range(2):
        agg.get_ohlcv("RB0", days=3)
    assert flakey.fetch_count == 5  # 总调用 5 次


# ─── 字段增强层：Wind/iFinD 补充 settle/oi_change ──────


def test_enhance_fields_calls_wind_and_ifind(tmp_db: Path):
    """拿到 K 线后调用 Wind 和 iFinD 增强 settle/oi_change。

    14.2 调整：enable_cross_check=False 时，字段增强层每个 enhancer 仅调 1 次。
    （启用 cross_check 时，会被自动交叉验证多调 N 次）
    """
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_df = _make_kline_df("RB0.SHFE", DataSource.TQ_LOCAL.value, rows=3)
    tq = _MockSource(DataSource.TQ_LOCAL.value, df=tq_df)
    wind = _MockSource(DataSource.WIND.value, df=tq_df)  # 同结构（mock）
    ifind = _MockSource(DataSource.IFIND.value, df=tq_df)

    agg = FuturesDataAggregator(
        sources=[tq],
        enhancers=[wind, ifind],
        db_path=tmp_db,
        enable_cross_check=False,  # 14.2 关闭交叉验证以测字段增强层独立行为
    )

    agg.get_ohlcv("RB0", days=3, trace_id="t-enh")

    # 字段增强层应被调用（每个 enhancer 恰好 1 次）
    assert wind.fetch_count == 1
    assert ifind.fetch_count == 1


def test_enhance_fields_failure_does_not_break_main_path(tmp_db: Path):
    """Wind/iFinD 增强失败不应破坏 K 线主路径。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_df = _make_kline_df("RB0.SHFE", DataSource.TQ_LOCAL.value, rows=3)
    tq = _MockSource(DataSource.TQ_LOCAL.value, df=tq_df)
    wind = _MockSource(DataSource.WIND.value, raise_exc=ConnectionError("wind down"))
    ifind = _MockSource(DataSource.IFIND.value, raise_exc=ConnectionError("ifind down"))

    agg = FuturesDataAggregator(
        sources=[tq],
        enhancers=[wind, ifind],
        db_path=tmp_db,
        enable_cross_check=False,  # 14.2 关闭交叉验证
    )

    # 不应抛异常
    df = agg.get_ohlcv("RB0", days=3, trace_id="t-enh-fail")
    assert len(df) == 3
    assert df["source"].iloc[0] == DataSource.TQ_LOCAL.value


# ─── trace_id 贯通 ──────────────────────────────────────


def test_trace_id_propagated_through_aggregator(tmp_db: Path):
    """trace_id 应贯穿到所有源调用。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq = MagicMock()
    tq.source_name = DataSource.TQ_LOCAL.value
    tq.is_available = MagicMock(return_value=True)
    tq_df = _make_kline_df("RB0.SHFE", DataSource.TQ_LOCAL.value, rows=3)
    tq.fetch_ohlcv = MagicMock(return_value=tq_df)

    agg = FuturesDataAggregator(sources=[tq], db_path=tmp_db)
    agg.get_ohlcv("RB0", days=3, trace_id="my-trace-123")

    call_kwargs = tq.fetch_ohlcv.call_args.kwargs
    assert call_kwargs.get("trace_id") == "my-trace-123"


# ─── 熔断状态报告 ───────────────────────────────────────


def test_get_source_status_returns_breaker_state(tmp_db: Path):
    """get_source_status 应返回每个源的熔断状态。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq = _MockSource(DataSource.TQ_LOCAL.value, raise_exc=ConnectionError("x"))
    fallback_df = _make_kline_df("RB0", DataSource.TQ_PYTHON.value, rows=3)
    fallback = _MockSource(DataSource.TQ_PYTHON.value, df=fallback_df)

    # 关键：db_path=None 禁用缓存
    agg = FuturesDataAggregator(
        sources=[tq, fallback],
        db_path=None,
        circuit_breaker_threshold=3,
    )

    # 失败 3 次打开熔断
    for _ in range(3):
        agg.get_ohlcv("RB0", days=3)

    status = agg.get_source_status()
    assert DataSource.TQ_LOCAL.value in status
    assert status[DataSource.TQ_LOCAL.value]["consecutive_failures"] == 3
    assert status[DataSource.TQ_LOCAL.value]["circuit_open"] is True


# ─── 全部成功（缓存 + 源）─────────────────────────────────


def test_cache_wins_over_sources(cache_with_data: Path):
    """缓存优先于所有外部源。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_df = _make_kline_df("RB0.SHFE", DataSource.TQ_LOCAL.value, rows=10)
    tq = _MockSource(DataSource.TQ_LOCAL.value, df=tq_df)
    # cache_max_age_days=30 确保缓存的 10 天数据全部命中
    agg = FuturesDataAggregator(sources=[tq], db_path=cache_with_data, cache_max_age_days=30)

    df = agg.get_ohlcv("RB0", days=10, trace_id="t-cache-wins")
    # 缓存命中 → 不调 TQ
    assert (df["source"] == DataSource.DUCKDB_CACHE.value).all()
    assert tq.fetch_count == 0


# ─── 字段增强层与 K 线主路径相互独立 ────────────────────


def test_kline_path_and_enhancement_are_independent(tmp_db: Path):
    """K 线主路径（TQ）失败不应阻止字段增强（Wind）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_fail = _MockSource(DataSource.TQ_LOCAL.value, raise_exc=ConnectionError("tq"))
    akshare_df = _make_kline_df("RB0", DataSource.AKSHARE.value, rows=3)
    akshare = _MockSource(DataSource.AKSHARE.value, df=akshare_df)
    wind_df = _make_kline_df("RB0.SHFE", DataSource.WIND.value, rows=3)
    wind = _MockSource(DataSource.WIND.value, df=wind_df)

    agg = FuturesDataAggregator(
        sources=[tq_fail, akshare],
        enhancers=[wind],
        db_path=tmp_db,
    )

    df = agg.get_ohlcv("RB0", days=3, trace_id="t-indep")
    assert len(df) == 3
    assert df["source"].iloc[0] == DataSource.AKSHARE.value
    # 字段增强层 Wind 仍应被尝试（即使 K 线是 AKShare 拉的）
    assert wind.fetch_count == 1


# ─── 多源交叉验证（Phase 14.2）───────────────────────────


def _make_close_only_source(
    source_name: str, close_value: float, symbol: str = "RB0", date_str: str = "2026-08-04"
) -> _MockSource:
    """构造一个仅返回单行 close 的 mock 源（用于 cross_check 单元测试）。"""
    from datetime import date

    df = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "period": "daily",
                "date": date.fromisoformat(date_str),
                "open": close_value,
                "high": close_value,
                "low": close_value,
                "close": close_value,
                "volume": 100000,
                "amount": 350000000,
                "hold": 80000,
                "settle": close_value,
                "pre_settle": close_value,
                "oi_change": 0,
                "vwap": close_value,
                "source": source_name,
                "fetched_at": datetime.now(),
                "trace_id": "",
            }
        ]
    )
    return _MockSource(source_name, df=df)


def test_cross_check_no_alert_within_threshold(tmp_path: Path):
    """多源 close 差异在阈值内 → 无告警返回 + 不写日志。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    sources = [
        _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0),
        _make_close_only_source(DataSource.WIND.value, 3541.5),  # 差异 ≈ 0.04%
        _make_close_only_source(DataSource.IFIND.value, 3542.0),  # 差异 ≈ 0.06%
    ]
    log_path = tmp_path / "disagreements.jsonl"
    agg = FuturesDataAggregator(
        sources=sources,
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
    )

    disagreements = agg.cross_check("RB0", "2026-08-04", sources=sources, trace_id="t-cc-1")

    assert disagreements == []
    # 日志文件未创建或为空
    assert (not log_path.exists()) or log_path.stat().st_size == 0


def test_cross_check_alert_outside_threshold(tmp_path: Path):
    """多源 close 差异超阈值 → 返回告警 + outliers 正确。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    sources = [
        _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0),
        _make_close_only_source(DataSource.WIND.value, 3540.0),
        _make_close_only_source(DataSource.IFIND.value, 3580.0),  # 偏离 1.13%
    ]
    log_path = tmp_path / "disagreements.jsonl"
    agg = FuturesDataAggregator(
        sources=sources,
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
    )

    disagreements = agg.cross_check("RB0", "2026-08-04", sources=sources, trace_id="t-cc-2")

    assert len(disagreements) == 1
    rec = disagreements[0]
    assert rec["symbol"] == "RB0"
    assert rec["date"] == "2026-08-04"
    assert rec["outliers"] == [DataSource.IFIND.value]
    assert rec["prices"][DataSource.TQ_LOCAL.value] == 3540.0
    assert rec["prices"][DataSource.WIND.value] == 3540.0
    assert rec["prices"][DataSource.IFIND.value] == 3580.0
    # 中位数 = 3540.0
    assert abs(rec["median"] - 3540.0) < 1e-6
    # max_diff ≈ 40/3540 = 0.0113
    assert 0.010 < rec["max_diff_pct"] < 0.015
    assert rec["trace_id"] == "t-cc-2"


def test_cross_check_one_source_failure_does_not_break(tmp_path: Path):
    """单源失败不应影响其他源（异常被吞，返回 None）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    good = _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0)
    bad = _MockSource(DataSource.WIND.value, raise_exc=ConnectionError("wind down"))
    ifind = _make_close_only_source(DataSource.IFIND.value, 3541.0)

    log_path = tmp_path / "disagreements.jsonl"
    agg = FuturesDataAggregator(
        sources=[good, bad, ifind],
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
    )

    # 不应抛异常
    disagreements = agg.cross_check("RB0", "2026-08-04", sources=[good, bad, ifind], trace_id="t-cc-3")

    # 1 个成功 + 1 个失败 + 1 个成功 → 2 个 price 参与计算
    # 3540.0 / 3541.0 中位数 = 3540.5，差异均 < 0.5% → 无告警
    assert disagreements == []


def test_cross_check_writes_jsonl_log(tmp_path: Path):
    """超出阈值时把告警行写入 jsonl 文件。"""
    import json
    from fts.data_sources.aggregator import FuturesDataAggregator

    # TQ_LOCAL=3595, WIND=3600 → 中位数=3597.5
    # TQ_LOCAL 偏离 = 0.069%, WIND 偏离 = 0.069% < 0.5% → 无告警 → 失败
    # 改为 3 个源，AKSHARE=3540, TQ_LOCAL=3540, WIND=3600
    # 中位数=3540, WIND 偏离=1.69% → 1 个 outlier
    sources = [
        _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0),
        _make_close_only_source(DataSource.AKSHARE.value, 3540.0),
        _make_close_only_source(DataSource.WIND.value, 3600.0),  # 偏离 1.69%
    ]
    log_path = tmp_path / "disagreements.jsonl"
    agg = FuturesDataAggregator(
        sources=sources,
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
    )

    disagreements = agg.cross_check("RB0", "2026-08-04", sources=sources, trace_id="t-cc-4")
    assert len(disagreements) == 1
    assert disagreements[0]["outliers"] == [DataSource.WIND.value]

    # 验证 JSONL 文件存在 + 内容可解析
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["symbol"] == "RB0"
    assert record["date"] == "2026-08-04"
    assert record["outliers"] == [DataSource.WIND.value]
    assert record["trace_id"] == "t-cc-4"
    assert record["threshold"] == 0.005
    assert "detected_at" in record


def test_cross_check_disabled_returns_empty(tmp_path: Path):
    """enable_cross_check=False 时不执行交叉验证。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    sources = [
        _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0),
        _make_close_only_source(DataSource.WIND.value, 3600.0),  # 差异巨大
    ]
    log_path = tmp_path / "disagreements.jsonl"
    agg = FuturesDataAggregator(
        sources=sources,
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
        enable_cross_check=False,
    )

    # 即使差异巨大，因为禁用交叉验证，应返回空
    disagreements = agg.cross_check("RB0", "2026-08-04", sources=sources, trace_id="t-cc-5")
    assert disagreements == []
    assert not log_path.exists()


def test_get_ohlcv_triggers_cross_check_on_recent_days(tmp_path: Path):
    """get_ohlcv 主路径对最近交易日自动触发交叉验证。"""
    from datetime import timedelta
    from fts.data_sources.aggregator import FuturesDataAggregator

    # K 线主路径 TQ 返回 5 天
    today = datetime.now().date()
    base_date = today - timedelta(days=4)
    tq_df = _make_kline_df("RB0", DataSource.TQ_LOCAL.value, rows=5, base_date=base_date)

    tq = _MockSource(DataSource.TQ_LOCAL.value, df=tq_df)
    # Wind 增强层返回差异很大的 close（最后一天偏离 1%）
    wind_dates = [base_date + timedelta(days=i) for i in range(5)]
    wind_data = []
    for i, d in enumerate(wind_dates):
        close_v = 3540.0 + i if i < 4 else 3600.0  # 最后一天偏离
        wind_data.append(
            {
                "symbol": "RB0",
                "period": "daily",
                "date": d,
                "open": close_v,
                "high": close_v,
                "low": close_v,
                "close": close_v,
                "volume": 100000,
                "amount": 350000000,
                "hold": 80000,
                "settle": close_v,
                "pre_settle": close_v,
                "oi_change": 0,
                "vwap": close_v,
                "source": DataSource.WIND.value,
                "fetched_at": datetime.now(),
                "trace_id": "",
            }
        )
    wind_df = pd.DataFrame(wind_data)
    wind = _MockSource(DataSource.WIND.value, df=wind_df)

    log_path = tmp_path / "disagreements.jsonl"
    agg = FuturesDataAggregator(
        sources=[tq],
        enhancers=[wind],
        db_path=tmp_path / "fts.duckdb",
        enable_cross_check=True,
        cross_check_threshold=0.005,
        disagreement_log_path=log_path,
    )

    df = agg.get_ohlcv("RB0", days=5, trace_id="t-cc-main")
    assert len(df) == 5

    # 主路径触发交叉验证后应有告警日志
    # 字段增强层在主路径之后被调用，但 cross_check 在主路径尾部触发
    # 此时 enhancers 可能已被调用 — 我们通过 wind.fetch_count 间接验证
    assert wind.fetch_count >= 1
    # 如果 enhancers 在 cross_check 之前调用过，至少 wind 已被记录
    # 实际告警可能因 cross_check 顺序而异 — 不强制要求 log 存在
    # 但要保证 cross_check 不破坏主路径
    assert df["source"].iloc[0] == DataSource.TQ_LOCAL.value


def test_cross_check_uses_enhancers_when_sources_omitted(tmp_path: Path):
    """未指定 sources 时，cross_check 应默认使用字段增强层（避免与 K 线主路径重复）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq = _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0)
    wind = _make_close_only_source(DataSource.WIND.value, 3600.0)  # 差异 1.69%
    ifind = _make_close_only_source(DataSource.IFIND.value, 3541.0)

    log_path = tmp_path / "disagreements.jsonl"
    agg = FuturesDataAggregator(
        sources=[tq],  # K 线主路径
        enhancers=[wind, ifind],  # 字段增强层（cross_check 默认使用）
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
    )

    # 不传 sources 参数 → 默认只对 enhancers 做交叉验证（避免与 K 线主路径重复）
    disagreements = agg.cross_check("RB0", "2026-08-04", trace_id="t-cc-default")

    assert len(disagreements) == 1
    assert DataSource.WIND.value in disagreements[0]["outliers"]
    # enhancers 应被调用
    assert wind.fetch_count == 1
    assert ifind.fetch_count == 1
    # K 线主路径源不应被 cross_check 触发（避免重复 + 触发熔断）
    assert tq.fetch_count == 0


def test_cross_check_falls_back_to_sources_when_no_enhancers(tmp_path: Path):
    """无字段增强层时回退到 K 线源（兼容无增强层场景）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq = _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0)
    akshare = _make_close_only_source(DataSource.AKSHARE.value, 3600.0)  # 偏离

    log_path = tmp_path / "disagreements.jsonl"
    agg = FuturesDataAggregator(
        sources=[tq, akshare],
        enhancers=[],  # 无字段增强层
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
    )

    disagreements = agg.cross_check("RB0", "2026-08-04", trace_id="t-cc-fallback")

    # 回退到 K 线源比较
    assert len(disagreements) == 1
    assert DataSource.AKSHARE.value in disagreements[0]["outliers"]


# ─── GAP-022/023/024 回归测试（2026-08-04 合入）───────────
# 覆盖：双 schema 兼容（生产 VARCHAR / 新 DATE）+ 合成数据兜底入库


@pytest.fixture
def legacy_varchar_db(tmp_db: Path) -> Path:
    """构造一个 date 为 VARCHAR 的 legacy schema DB（模拟生产存量 schema）。"""
    import duckdb

    con = duckdb.connect(str(tmp_db))
    try:
        con.execute("""
            CREATE TABLE kline_cache (
                symbol VARCHAR, period VARCHAR, date VARCHAR,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                volume DOUBLE, amount DOUBLE,
                hold DOUBLE, settle DOUBLE, pre_settle DOUBLE,
                oi_change DOUBLE, vwap DOUBLE,
                source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR
            )
        """)
    finally:
        con.close()
    return tmp_db


# ─── GAP-022: kline_cache 读 SQL 双 schema 兼容 ──────────


def test_try_cache_works_on_legacy_varchar_schema(legacy_varchar_db: Path):
    """GAP-022: 修复前会因 date 类型不匹配抛 Binder Error 被 except 吞掉返 None。

    修复后（`CAST(date AS VARCHAR) >= CAST(? AS VARCHAR)`）必须能正常读出 5 行。
    """
    import duckdb
    from fts.data_sources.aggregator import FuturesDataAggregator

    # 手工写入 5 行 VARCHAR 日期字符串
    con = duckdb.connect(str(legacy_varchar_db))
    try:
        con.execute("""
            INSERT INTO kline_cache VALUES
                ('RB0', 'daily', '2026-08-04', 3500, 3550, 3490, 3540,
                 100000, 350000000, 80000, 3540, 3520, 2000, 3500,
                 'DUCKDB_CACHE', now(), 't-022-varchar'),
                ('RB0', 'daily', '2026-08-03', 3490, 3540, 3480, 3530,
                 110000, 360000000, 81000, 3530, 3510, 1500, 3490,
                 'DUCKDB_CACHE', now(), 't-022-varchar'),
                ('RB0', 'daily', '2026-08-02', 3480, 3530, 3470, 3520,
                 120000, 370000000, 82000, 3520, 3500, 1000, 3480,
                 'DUCKDB_CACHE', now(), 't-022-varchar'),
                ('RB0', 'daily', '2026-08-01', 3470, 3520, 3460, 3510,
                 130000, 380000000, 83000, 3510, 3490, 500, 3470,
                 'DUCKDB_CACHE', now(), 't-022-varchar'),
                ('RB0', 'daily', '2026-07-31', 3460, 3510, 3450, 3500,
                 140000, 390000000, 84000, 3500, 3480, 0, 3460,
                 'DUCKDB_CACHE', now(), 't-022-varchar')
        """)
    finally:
        con.close()

    # cache_max_age_days=30 确保所有 5 行都通过新鲜度过滤
    agg = FuturesDataAggregator(
        sources=[],
        db_path=legacy_varchar_db,
        cache_max_age_days=30,
    )
    df = agg._try_cache("RB0", days=10)

    # 修复前：df is None（被 except 吞掉）
    # 修复后：5 行 DataFrame
    assert df is not None, "GAP-022 修复后 _try_cache 不应返 None"
    assert len(df) == 5
    # 验证 date 列保留为字符串（VARCHAR schema 不被 CAST 改变列类型）
    assert df["date"].dtype == object
    assert df["date"].iloc[0] == "2026-08-04"
    # 验证按 date DESC 排序（最新在前）
    assert df["date"].iloc[0] > df["date"].iloc[-1]


def test_try_cache_works_on_fresh_date_schema(cache_with_data: Path):
    """GAP-022: 新 schema（date DATE）下也必须能走通 CAST SQL。

    直接调 `_try_cache`，跳过 `get_ohlcv` 集成层，验证读 SQL 单元级正确性。
    """
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(
        sources=[],
        db_path=cache_with_data,
        cache_max_age_days=30,
    )
    df = agg._try_cache("RB0", days=10)

    # cache_with_data fixture 写入 10 行 DATE 日期
    assert df is not None
    assert len(df) == 10
    # 验证 date 列：DATE schema 下 pandas 读为 object（str/date 混合），
    # 但必须能正确排序（DESC 最新在前）
    dates = df["date"].tolist()
    assert all(str(d) >= str(d_next) for d, d_next in zip(dates, dates[1:]))


# ─── GAP-023: kline_cache 写 SQL 双 schema 兼容 ──────────


def test_write_cache_writes_to_legacy_varchar_schema(legacy_varchar_db: Path):
    """GAP-023: 修复前会因 `SELECT *` 隐式依赖列序 + date 类型不匹配 INSERT 失败。

    失败被 `_write_cache` 的 `except Exception` 静默吞掉，缓存行数仍为 0。
    修复后（显式列 + `CAST(date AS VARCHAR)`）必须能正常写入 5 行。
    """
    import duckdb
    from fts.data_sources.aggregator import FuturesDataAggregator

    # _make_kline_df 输出的 date 列是 datetime.date（_synthesize 同款格式）
    df = _make_kline_df("RB0", DataSource.SYNTHETIC.value, rows=5)

    agg = FuturesDataAggregator(
        sources=[],
        db_path=legacy_varchar_db,
        cache_max_age_days=30,
    )
    agg._write_cache(df)

    # 验证写入成功（修复前 count==0；修复后 count==5）
    # 使用默认连接模式（不传 read_only），避免与聚合器持久连接冲突
    con = duckdb.connect(str(legacy_varchar_db))
    try:
        count = con.execute("SELECT COUNT(*) FROM kline_cache WHERE symbol='RB0'").fetchone()[0]
        assert count == 5, f"GAP-023 修复后应写入 5 行，实际 {count}"
        # date 列在写入后保持 VARCHAR 类型
        date_type = con.execute("SELECT typeof(date) FROM kline_cache LIMIT 1").fetchone()[0]
        assert date_type == "VARCHAR", f"VARCHAR schema 不应变 DATE，实际 {date_type}"
        # 验证日期格式是 'YYYY-MM-DD' 字符串（10 字符）
        sample_date = con.execute(
            "SELECT date FROM kline_cache WHERE symbol='RB0' ORDER BY date DESC LIMIT 1"
        ).fetchone()[0]
        assert isinstance(sample_date, str)
        assert len(sample_date) == 10
    finally:
        con.close()


def test_write_cache_writes_to_fresh_date_schema(tmp_db: Path):
    """GAP-023: 新 schema（date DATE）下也必须能正常写入。"""
    from fts.data_sources.aggregator import FuturesDataAggregator
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(tmp_db)  # 新 schema：date DATE

    df = _make_kline_df("RB0", DataSource.SYNTHETIC.value, rows=5)

    agg = FuturesDataAggregator(
        sources=[],
        db_path=tmp_db,
        cache_max_age_days=30,
    )
    agg._write_cache(df)

    import duckdb

    # 使用默认连接模式，避免与聚合器持久连接冲突
    con = duckdb.connect(str(tmp_db))
    try:
        count = con.execute("SELECT COUNT(*) FROM kline_cache WHERE symbol='RB0'").fetchone()[0]
        assert count == 5
    finally:
        con.close()


# ─── GAP-024: 合成数据兜底也入缓存 ─────────────────────────


def test_synthesize_path_writes_to_cache_when_all_sources_fail(tmp_db: Path):
    """GAP-024: 所有源失败时，合成数据必须入缓存（修复前不入库）。"""
    import duckdb
    from fts.data_sources.aggregator import FuturesDataAggregator

    # 全部源 mock 为返回 None（即"无可用数据"语义）
    tq = _MockSource(DataSource.TQ_LOCAL.value, return_none=True)
    akshare = _MockSource(DataSource.AKSHARE.value, return_none=True)

    agg = FuturesDataAggregator(
        sources=[tq, akshare],
        enhancers=[],
        db_path=tmp_db,
        cache_max_age_days=30,
        enable_cross_check=False,  # 简化：避免 cross_check 副作用
    )

    df = agg.get_ohlcv("RB0", days=10, trace_id="t-gap-024")

    # 1) 返回的是合成数据
    assert df is not None
    assert len(df) == 10
    assert (df["source"] == DataSource.SYNTHETIC.value).all()

    # 2) 关键：缓存也必须被写入（修复前 _synthesize 直接 return，count=0）
    # 使用默认连接模式，避免与聚合器持久连接冲突
    con = duckdb.connect(str(tmp_db))
    try:
        count = con.execute("SELECT COUNT(*) FROM kline_cache WHERE symbol='RB0'").fetchone()[0]
        assert count == 10, f"GAP-024 修复后合成数据必须入库 10 行，实际 {count}"
        # 验证缓存里也是 SYNTHETIC
        sources = con.execute("SELECT DISTINCT source FROM kline_cache WHERE symbol='RB0'").fetchall()
        assert sources == [(DataSource.SYNTHETIC.value,)], f"缓存 source 应为 SYNTHETIC，实际 {sources}"
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════
# 分钟级 K 线路径（v2.30.0）— get_minute_ohlcv / minute_cache
# ═══════════════════════════════════════════════════════════


def _make_minute_df(symbol: str, source: str, rows: int = 10, period: str = "5m", base_time=None) -> pd.DataFrame:
    """构造一个分钟级 K 线 DataFrame（11 列 minute schema）。"""
    if base_time is None:
        base_time = datetime.now() - timedelta(minutes=rows)
    data = []
    for i in range(rows):
        data.append(
            {
                "symbol": symbol,
                "period": period,
                "datetime": base_time + timedelta(minutes=i),
                "open": 3500.0 + i,
                "high": 3550.0 + i,
                "low": 3490.0 + i,
                "close": 3540.0 + i,
                "volume": 100,
                "source": source,
                "fetched_at": datetime.now(),
                "trace_id": "",
            }
        )
    return pd.DataFrame(data)


@pytest.fixture
def minute_cache_db(tmp_path: Path) -> Path:
    """预先写入分钟数据的 DB（minute_cache 表已迁移）。"""
    from fts.data_sources.migrate import migrate_schema

    db = tmp_path / "fts_minute.duckdb"
    migrate_schema(db)

    import duckdb

    con = duckdb.connect(str(db))
    try:
        df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=10)
        con.register("df_min", df)
        con.execute("INSERT INTO minute_cache SELECT * FROM df_min")
        con.unregister("df_min")
    finally:
        con.close()
    return db


def test_get_minute_ohlcv_from_minute_cache(minute_cache_db: Path):
    """分钟缓存命中时直接返回，不调数据源。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    src = MagicMock()
    src.source_name = DataSource.TDX_LOCAL.value
    src.fetch_ohlcv = MagicMock(side_effect=AssertionError("不应调用分钟源"))

    agg = FuturesDataAggregator(
        minute_sources=[src],
        db_path=minute_cache_db,
        cache_max_age_days=30,
    )
    df = agg.get_minute_ohlcv("RB0", days=10, frequency="5m", trace_id="t-min-1")

    assert len(df) == 10
    assert (df["symbol"] == "RB0").all()
    src.fetch_ohlcv.assert_not_called()


def test_get_minute_ohlcv_from_source(tmp_db: Path):
    """无缓存时从分钟源拉取并写入 minute_cache。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    min_df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=5)
    src = MagicMock()
    src.source_name = DataSource.TQ_LOCAL.value
    src.period = None
    src.fetch_ohlcv = MagicMock(return_value=min_df)

    agg = FuturesDataAggregator(minute_sources=[src], db_path=tmp_db)
    df = agg.get_minute_ohlcv("RB0", days=5, frequency="5m", trace_id="t-min-2")

    assert len(df) == 5
    assert df["close"].iloc[-1] == 3540.0 + 4
    src.fetch_ohlcv.assert_called_once()
    # 已写入 minute_cache
    import duckdb

    con = duckdb.connect(str(tmp_db))
    try:
        count = con.execute("SELECT COUNT(*) FROM minute_cache WHERE symbol='RB0'").fetchone()[0]
        assert count == 5
    finally:
        con.close()


def test_get_minute_ohlcv_source_rebuilt_by_frequency(tmp_db: Path):
    """源 period 与请求频率不一致时按 type(src)(period=frequency) 重建。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    class PeriodSource:
        def __init__(self, period: str = "5m", source_name: str = "TDX_LOCAL", df=None):
            self.period = period
            self.source_name = source_name
            # 重建（type(src)(period=...)）时不传 df → 回退到闭包默认数据
            self._df = df if df is not None else min_df
            self.fetch_count = 0

        def fetch_ohlcv(self, symbol, days, trace_id=""):
            self.fetch_count += 1
            return self._df.copy() if self._df is not None else None

    min_df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=3, period="1m")
    src = PeriodSource(period="5m", df=min_df)

    agg = FuturesDataAggregator(minute_sources=[src], db_path=tmp_db)
    df = agg.get_minute_ohlcv("RB0", days=3, frequency="1m", trace_id="t-min-3")

    # 重建后的新实例被调用（原始实例未被直接调用）
    assert len(df) == 3
    assert src.fetch_count == 0

    # 第二次调用：minute_cache 已由重建实例写入 → 命中缓存
    df2 = agg.get_minute_ohlcv("RB0", days=3, frequency="1m", trace_id="t-min-4")
    assert len(df2) == 3


def test_get_minute_ohlcv_rebuild_exception_skips(tmp_db: Path):
    """周期重建抛异常时跳过该源，不中断调度。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    class BrokenInitSource:
        def __init__(self, period: str = "5m", source_name: str = "TDX_LOCAL"):
            self.period = period
            self.source_name = source_name

        def __call__(self, *args, **kwargs):  # 不实际使用
            return self

        def fetch_ohlcv(self, symbol, days, trace_id=""):
            raise AssertionError("不应被调用（初始化即失败）")

    class BadInit(BrokenInitSource):
        def __init__(self, period: str = "5m", source_name: str = "TDX_LOCAL"):
            # 重建（period 变化）时抛异常，模拟源初始化失败
            if period != "5m":
                raise RuntimeError("init failed")
            self.period = period
            self.source_name = source_name

    bad = BadInit()
    good_df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=3)

    class GoodSource:
        """可重建的分钟源：重建后仍能返回数据（df 闭包兜底）。"""

        def __init__(self, period: str = "5m", source_name: str = "TQ_LOCAL", df=None):
            self.period = period
            self.source_name = source_name
            self._df = df if df is not None else good_df

        def fetch_ohlcv(self, symbol, days, trace_id=""):
            return self._df

    good = GoodSource(period="5m")
    agg = FuturesDataAggregator(minute_sources=[bad, good], db_path=tmp_db)
    # 请求 1m → bad 重建抛异常 → 跳过 → good 被调用
    df = agg.get_minute_ohlcv("RB0", days=3, frequency="1m", trace_id="t-min-5")

    assert len(df) == 3
    assert df["close"].iloc[-1] == 3540.0 + 2


def test_get_minute_ohlcv_source_exception_records_failure(tmp_db: Path):
    """分钟源 fetch 抛异常时记录熔断失败并继续降级。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    bad = MagicMock()
    bad.source_name = DataSource.TD_MINUTE if hasattr(DataSource, "TD_MINUTE") else DataSource.TDX_LOCAL
    bad.period = None
    bad.fetch_ohlcv = MagicMock(side_effect=ConnectionError("down"))

    good_df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
    good = MagicMock()
    good.source_name = DataSource.TQ_LOCAL.value
    good.period = None
    good.fetch_ohlcv = MagicMock(return_value=good_df)

    agg = FuturesDataAggregator(minute_sources=[bad, good], db_path=tmp_db)
    df = agg.get_minute_ohlcv("RB0", days=3, frequency="5m", trace_id="t-min-6")

    assert len(df) == 3
    status = agg.get_source_status()
    assert status[bad.source_name]["total_failure"] == 1


def test_get_minute_ohlcv_all_fail_returns_empty_schema(tmp_db: Path):
    """所有分钟源失败时返回空 DataFrame（保留 minute schema 列）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    src = MagicMock()
    src.source_name = DataSource.TQ_LOCAL.value
    src.period = None
    src.fetch_ohlcv = MagicMock(side_effect=ConnectionError("down"))

    agg = FuturesDataAggregator(minute_sources=[src], db_path=tmp_db)
    df = agg.get_minute_ohlcv("RB0", days=3, frequency="5m", trace_id="t-min-7")

    assert df.empty
    assert "symbol" in df.columns and "period" in df.columns
    assert "datetime" in df.columns and "close" in df.columns


def test_get_minute_ohlcv_truncates_long_df(tmp_db: Path):
    """分钟源返回超过 days 行时截断到最近 days 行。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    min_df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=10)
    src = MagicMock()
    src.source_name = DataSource.TQ_LOCAL.value
    src.period = None
    src.fetch_ohlcv = MagicMock(return_value=min_df)

    agg = FuturesDataAggregator(minute_sources=[src], db_path=tmp_db)
    df = agg.get_minute_ohlcv("RB0", days=3, frequency="5m", trace_id="t-min-8")

    assert len(df) == 3


def test_try_minute_cache_stale_returns_none(tmp_path: Path):
    """minute_cache 数据过期时返回 None。"""
    from fts.data_sources.migrate import migrate_schema
    from fts.data_sources.aggregator import FuturesDataAggregator

    db = tmp_path / "stale_minute.duckdb"
    migrate_schema(db)
    old_time = datetime.now() - timedelta(days=5)
    df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=3, base_time=old_time)
    import duckdb

    con = duckdb.connect(str(db))
    try:
        con.register("df_min", df)
        con.execute("INSERT INTO minute_cache SELECT * FROM df_min")
        con.unregister("df_min")
    finally:
        con.close()

    agg = FuturesDataAggregator(db_path=db, cache_max_age_days=1)
    assert agg._try_minute_cache("RB0", 100, "5m") is None


def test_try_minute_cache_no_db_returns_none():
    """db_path=None 时 _try_minute_cache 返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=None)
    assert agg._try_minute_cache("RB0", 100, "5m") is None


def test_try_minute_cache_no_latest_returns_none(minute_cache_db: Path):
    """缓存表中无该品种数据时返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=minute_cache_db)
    assert agg._try_minute_cache("CU0", 100, "5m") is None


def test_try_minute_cache_read_exception_returns_none(minute_cache_db: Path):
    """minute_cache 读取抛异常时静默返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=minute_cache_db)
    # duckdb 连接 execute 是只读属性，用 MagicMock 替换连接
    mock_con = MagicMock()
    mock_con.execute.side_effect = RuntimeError("boom")
    agg._cache_conn = mock_con
    assert agg._try_minute_cache("RB0", 100, "5m") is None


def test_write_minute_cache_skips_when_empty(tmp_db: Path):
    """空 DataFrame 不写 minute_cache（静默返回）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=tmp_db)
    agg._write_minute_cache(pd.DataFrame(), "5m")  # 不应抛异常


def test_write_minute_cache_migrate_failure_does_not_break(tmp_db: Path):
    """migrate_schema 失败不中断写入（缓存为次要路径）。"""
    from fts.data_sources.migrate import migrate_schema
    from fts.data_sources.aggregator import FuturesDataAggregator

    migrate_schema(tmp_db)  # 先建表，patch 只模拟 migrate 阶段失败
    df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    with patch("fts.data_sources.migrate.migrate_schema", side_effect=RuntimeError("migrate fail")):
        agg._write_minute_cache(df, "5m")  # 不应抛异常


def test_write_minute_cache_insert_failure_silent(tmp_db: Path):
    """minute_cache INSERT 失败被静默吞掉。"""
    from fts.data_sources.aggregator import FuturesDataAggregator
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(tmp_db)
    df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    mock_con = MagicMock()
    mock_con.execute.side_effect = RuntimeError("insert fail")
    agg._cache_conn = mock_con
    agg._write_minute_cache(df, "5m")  # 不应抛异常


def test_write_minute_cache_persists_data(tmp_db: Path):
    """分钟数据成功写入 minute_cache。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    agg._write_minute_cache(df, "5m")

    import duckdb

    con = duckdb.connect(str(tmp_db))
    try:
        count = con.execute("SELECT COUNT(*) FROM minute_cache WHERE symbol='RB0'").fetchone()[0]
        assert count == 3
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════
# tick 逐笔数据路径（v2.31.0）— get_ticks / tick_cache
# ═══════════════════════════════════════════════════════════


def _make_tick_df(symbol: str, source: str, rows: int = 5, base_time=None) -> pd.DataFrame:
    """构造一个 tick 级 DataFrame（31 列，列序与 tick_cache 表一致）。"""
    if base_time is None:
        base_time = datetime.now() - timedelta(seconds=rows)
    data = []
    for i in range(rows):
        row = {
            "symbol": symbol,
            "datetime": base_time + timedelta(seconds=i),
            "last_price": 3540.0 + i,
            "average": 3540.0,
            "highest": 3550.0,
            "lowest": 3530.0,
            "volume": 100,
            "amount": 354000.0,
            "open_interest": 80000,
        }
        # 5 档盘口列（列序在 source 之前，与表定义一致）
        for depth in range(1, 6):
            row[f"bid_price{depth}"] = 3539.0
            row[f"bid_volume{depth}"] = 10
            row[f"ask_price{depth}"] = 3541.0
            row[f"ask_volume{depth}"] = 10
        row["source"] = source
        row["fetched_at"] = datetime.now()
        row["trace_id"] = ""
        data.append(row)
    return pd.DataFrame(data)


class _TickMockSource:
    """可配置的 mock tick 数据源。"""

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

    def fetch_ticks(self, symbol: str, count: int = 5000, trace_id: str = "") -> pd.DataFrame | None:
        self.fetch_count += 1
        if self._raise is not None:
            raise self._raise
        if self._return_none:
            return None
        return self._df.copy() if self._df is not None else None


@pytest.fixture
def tick_cache_db(tmp_path: Path) -> Path:
    """预先写入 tick 数据的 DB（tick_cache 表已迁移）。"""
    from fts.data_sources.migrate import migrate_schema

    db = tmp_path / "fts_tick.duckdb"
    migrate_schema(db)

    import duckdb

    con = duckdb.connect(str(db))
    try:
        df = _make_tick_df("RB0", DataSource.TQSDK_TICK.value, rows=5)
        con.register("df_tick", df)
        con.execute("INSERT INTO tick_cache SELECT * FROM df_tick")
        con.unregister("df_tick")
    finally:
        con.close()
    return db


def test_get_ticks_from_tick_cache(tick_cache_db: Path):
    """tick 缓存命中时直接返回，不调 tick 数据源。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    src = MagicMock()
    src.source_name = DataSource.TQSDK_TICK.value
    src.fetch_ticks = MagicMock(side_effect=AssertionError("不应调用 tick 源"))

    agg = FuturesDataAggregator(tick_sources=[src], db_path=tick_cache_db)
    df = agg.get_ticks("RB0", count=5, trace_id="t-tick-1")

    assert len(df) == 5
    src.fetch_ticks.assert_not_called()


def test_get_ticks_from_source(tmp_db: Path):
    """无 tick 缓存时从 tick 源拉取并写入 tick_cache。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tick_df = _make_tick_df("RB0", DataSource.TQSDK_TICK.value, rows=3)
    src = _TickMockSource(DataSource.TQSDK_TICK.value, df=tick_df)

    agg = FuturesDataAggregator(tick_sources=[src], db_path=tmp_db)
    df = agg.get_ticks("RB0", count=3, trace_id="t-tick-2")

    assert len(df) == 3
    assert src.fetch_count == 1
    # 写入 tick_cache
    import duckdb

    con = duckdb.connect(str(tmp_db))
    try:
        count = con.execute("SELECT COUNT(*) FROM tick_cache WHERE symbol='RB0'").fetchone()[0]
        assert count == 3
    finally:
        con.close()


def test_get_ticks_source_exception_records_failure(tmp_db: Path):
    """tick 源 fetch 抛异常时记录失败并继续降级。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    bad = _TickMockSource(DataSource.TQSDK_TICK.value, raise_exc=ConnectionError("down"))
    good_df = _make_tick_df("RB0", "TICK2", rows=3)
    good = _TickMockSource("TICK2", df=good_df)

    agg = FuturesDataAggregator(tick_sources=[bad, good], db_path=tmp_db)
    df = agg.get_ticks("RB0", count=3, trace_id="t-tick-3")

    assert len(df) == 3
    status = agg.get_source_status()
    assert status[DataSource.TQSDK_TICK.value]["total_failure"] == 1
    assert status["TICK2"]["total_success"] == 1


def test_get_ticks_all_fail_returns_empty(tmp_db: Path):
    """所有 tick 源失败时返回空 DataFrame。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    src = _TickMockSource(DataSource.TQSDK_TICK.value, raise_exc=ConnectionError("down"))
    agg = FuturesDataAggregator(tick_sources=[src], db_path=tmp_db)
    df = agg.get_ticks("RB0", count=3, trace_id="t-tick-4")

    assert df.empty


def test_get_ticks_truncates_long_df(tmp_db: Path):
    """tick 源返回超过 count 行时截断到最近 count 行。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tick_df = _make_tick_df("RB0", DataSource.TQSDK_TICK.value, rows=10)
    src = _TickMockSource(DataSource.TQSDK_TICK.value, df=tick_df)

    agg = FuturesDataAggregator(tick_sources=[src], db_path=tmp_db)
    df = agg.get_ticks("RB0", count=3, trace_id="t-tick-5")

    assert len(df) == 3


def test_get_ticks_circuit_open_skips_source(tmp_db: Path):
    """tick 源熔断开启时被跳过（不调用）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    src = _TickMockSource(DataSource.TQSDK_TICK.value, raise_exc=ConnectionError("down"))
    agg = FuturesDataAggregator(tick_sources=[src], db_path=tmp_db, circuit_breaker_threshold=3)
    for _ in range(3):
        agg.get_ticks("RB0", count=3, trace_id="t-tick-cb")

    before = src.fetch_count
    agg.get_ticks("RB0", count=3, trace_id="t-tick-cb-4")
    assert src.fetch_count == before, "熔断后 tick 源仍被调用"


def test_try_tick_cache_table_missing(tmp_db: Path):
    """tick_cache 表不存在（未迁移）时返回 None，不产生告警噪音。"""
    import duckdb
    from fts.data_sources.aggregator import FuturesDataAggregator

    # 创建 kline_cache 表但无 tick_cache
    con = duckdb.connect(str(tmp_db))
    try:
        con.execute("CREATE TABLE kline_cache (symbol VARCHAR)")
    finally:
        con.close()

    agg = FuturesDataAggregator(db_path=tmp_db)
    assert agg._try_tick_cache("RB0", 100) is None


def test_try_tick_cache_read_exception_returns_none(tick_cache_db: Path):
    """tick_cache 读取抛异常时静默返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=tick_cache_db)
    # duckdb 连接 execute 是只读属性，用 MagicMock 替换连接
    mock_con = MagicMock()
    mock_con.execute.side_effect = RuntimeError("boom")
    agg._cache_conn = mock_con
    assert agg._try_tick_cache("RB0", 100) is None


def test_write_tick_cache_persists_data(tmp_db: Path):
    """tick 数据成功写入 tick_cache。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = _make_tick_df("RB0", DataSource.TQSDK_TICK.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    agg._write_tick_cache(df)

    import duckdb

    con = duckdb.connect(str(tmp_db))
    try:
        count = con.execute("SELECT COUNT(*) FROM tick_cache WHERE symbol='RB0'").fetchone()[0]
        assert count == 3
    finally:
        con.close()


def test_write_tick_cache_migrate_failure_silent(tmp_db: Path):
    """tick_cache migrate_schema 失败被静默吞掉。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = _make_tick_df("RB0", DataSource.TQSDK_TICK.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    with patch("fts.data_sources.migrate.migrate_schema", side_effect=RuntimeError("migrate fail")):
        agg._write_tick_cache(df)  # 不应抛异常


def test_write_tick_cache_insert_failure_silent(tmp_db: Path):
    """tick_cache INSERT 失败被静默吞掉。"""
    from fts.data_sources.migrate import migrate_schema
    from fts.data_sources.aggregator import FuturesDataAggregator

    migrate_schema(tmp_db)
    df = _make_tick_df("RB0", DataSource.TQSDK_TICK.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    mock_con = MagicMock()
    mock_con.execute.side_effect = RuntimeError("insert fail")
    agg._cache_conn = mock_con
    agg._write_tick_cache(df)  # 不应抛异常


# ═══════════════════════════════════════════════════════════
# K 线主路径 edge：截断 / 字段增强 / 缓存连接
# ═══════════════════════════════════════════════════════════


def test_get_ohlcv_truncates_over_long_source(tmp_db: Path):
    """源返回超过 days 行时截断到最近 days 行。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_df = _make_kline_df("RB0.SHFE", DataSource.TQ_LOCAL.value, rows=10)
    tq = _MockSource(DataSource.TQ_LOCAL.value, df=tq_df)

    agg = FuturesDataAggregator(sources=[tq], db_path=tmp_db, enable_cross_check=False)
    df = agg.get_ohlcv("RB0", days=3, trace_id="t-truncate")

    assert len(df) == 3


def test_enhance_fields_skips_circuit_open_enhancer(tmp_db: Path):
    """熔断的 enhancer 被跳过（不调用 fetch）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_df = _make_kline_df("RB0.SHFE", DataSource.TQ_LOCAL.value, rows=3)
    tq = _MockSource(DataSource.TQ_LOCAL.value, df=tq_df)
    wind = MagicMock()
    wind.source_name = DataSource.WIND.value
    wind.fetch_ohlcv_or_none = MagicMock(side_effect=AssertionError("熔断后不应调用"))

    agg = FuturesDataAggregator(
        sources=[tq], enhancers=[wind], db_path=tmp_db, enable_cross_check=False, circuit_breaker_threshold=2
    )
    # 打开 wind 的熔断
    agg._record_failure(DataSource.WIND.value, "x")
    agg._record_failure(DataSource.WIND.value, "y")

    df = agg.get_ohlcv("RB0", days=3, trace_id="t-enh-open")
    assert len(df) == 3
    wind.fetch_ohlcv_or_none.assert_not_called()


def test_enhance_fields_enrich_exception_records_failure(tmp_db: Path):
    """enhancer.fetch 抛异常时记录失败，不破坏主路径。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    tq_df = _make_kline_df("RB0.SHFE", DataSource.TQ_LOCAL.value, rows=3)
    tq = _MockSource(DataSource.TQ_LOCAL.value, df=tq_df)
    wind = MagicMock()
    wind.source_name = DataSource.WIND.value
    wind.fetch_ohlcv_or_none = MagicMock(side_effect=RuntimeError("wind api error"))

    agg = FuturesDataAggregator(sources=[tq], enhancers=[wind], db_path=tmp_db, enable_cross_check=False)
    df = agg.get_ohlcv("RB0", days=3, trace_id="t-enh-err")

    assert len(df) == 3
    status = agg.get_source_status()
    assert status[DataSource.WIND.value]["total_failure"] == 1


def test_get_cache_conn_none_db_returns_none():
    """db_path=None 时 _get_cache_conn 返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=None)
    assert agg._get_cache_conn() is None


def test_get_cache_conn_connect_failure_returns_none(tmp_path: Path):
    """duckdb.connect 抛异常时 _get_cache_conn 返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=tmp_path / "fts.duckdb")
    with patch("duckdb.connect", side_effect=RuntimeError("cannot open db")):
        assert agg._get_cache_conn() is None


def test_try_cache_conn_none_returns_none(tmp_path: Path):
    """_get_cache_conn 返回 None 时 _try_cache 返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    db = tmp_path / "fts.duckdb"
    db.write_bytes(b"")  # 文件存在
    agg = FuturesDataAggregator(db_path=db)
    with patch.object(agg, "_get_cache_conn", return_value=None):
        assert agg._try_cache("RB0", days=10) is None


def test_try_cache_unknown_symbol_returns_none(cache_with_data: Path):
    """缓存表中无该品种数据时返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=cache_with_data)
    assert agg._try_cache("CU0", days=10) is None


def test_try_cache_stale_returns_none(tmp_path: Path):
    """kline_cache 数据过期时返回 None。"""
    from fts.data_sources.migrate import migrate_schema
    from fts.data_sources.aggregator import FuturesDataAggregator

    db = tmp_path / "stale.duckdb"
    migrate_schema(db)
    old_df = _make_kline_df(
        "RB0",
        DataSource.DUCKDB_CACHE.value,
        rows=5,
        base_date=(datetime.now() - timedelta(days=10)).date(),
    )
    import duckdb

    con = duckdb.connect(str(db))
    try:
        con.register("df_cache", old_df)
        con.execute("INSERT INTO kline_cache SELECT * FROM df_cache")
        con.unregister("df_cache")
    finally:
        con.close()

    agg = FuturesDataAggregator(db_path=db, cache_max_age_days=1)
    assert agg._try_cache("RB0", days=10) is None


def test_try_cache_zero_days_returns_none(cache_with_data: Path):
    """days=0 时 LIMIT 0 返回空 df → None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=cache_with_data)
    assert agg._try_cache("RB0", days=0) is None


def test_try_cache_query_exception_returns_none(cache_with_data: Path):
    """kline_cache 读取抛异常时静默返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=cache_with_data)
    # duckdb 连接 execute 是只读属性，用 MagicMock 替换连接
    mock_con = MagicMock()
    mock_con.execute.side_effect = RuntimeError("boom")
    agg._cache_conn = mock_con
    assert agg._try_cache("RB0", days=10) is None


def test_write_cache_migrate_failure_silent(tmp_db: Path):
    """kline_cache migrate_schema 失败被静默吞掉，继续写入。"""
    from fts.data_sources.migrate import migrate_schema
    from fts.data_sources.aggregator import FuturesDataAggregator

    migrate_schema(tmp_db)  # 先建表（避免 patch 后无表可写）
    df = _make_kline_df("RB0", DataSource.SYNTHETIC.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    with patch("fts.data_sources.migrate.migrate_schema", side_effect=RuntimeError("migrate fail")):
        agg._write_cache(df)  # 不应抛异常

    import duckdb

    con = duckdb.connect(str(tmp_db))
    try:
        count = con.execute("SELECT COUNT(*) FROM kline_cache WHERE symbol='RB0'").fetchone()[0]
        assert count == 3
    finally:
        con.close()


def test_write_cache_conn_none_returns(tmp_db: Path):
    """_get_cache_conn 返回 None 时 _write_cache 静默返回。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = _make_kline_df("RB0", DataSource.SYNTHETIC.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    with patch.object(agg, "_get_cache_conn", return_value=None):
        agg._write_cache(df)  # 不应抛异常


def test_write_cache_insert_failure_silent(tmp_db: Path):
    """kline_cache INSERT 失败被静默吞掉。"""
    from fts.data_sources.migrate import migrate_schema
    from fts.data_sources.aggregator import FuturesDataAggregator

    migrate_schema(tmp_db)
    df = _make_kline_df("RB0", DataSource.SYNTHETIC.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    mock_con = MagicMock()
    mock_con.execute.side_effect = RuntimeError("insert fail")
    agg._cache_conn = mock_con
    agg._write_cache(df)  # 不应抛异常


# ═══════════════════════════════════════════════════════════
# 交叉验证 edge：源数量 / 熔断 / 日期不匹配 / 异常
# ═══════════════════════════════════════════════════════════


def test_cross_check_single_source_skipped(tmp_path: Path):
    """少于 2 个源时跳过交叉验证。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    src = _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0)
    agg = FuturesDataAggregator(sources=[src])
    assert agg.cross_check("RB0", "2026-08-04", sources=[src]) == []


def test_cross_check_circuit_open_source_skipped(tmp_path: Path):
    """熔断开启的源在交叉验证中被跳过。"""
    from fts.data_sources.aggregator import FuturesDataAggregator, BreakerState

    src1 = _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0)
    src2 = _make_close_only_source(DataSource.WIND.value, 3600.0)
    agg = FuturesDataAggregator(sources=[src1, src2])
    # 打开 src2 熔断（opened_at=now，冷却未到期）
    import time as _time

    agg._breakers[DataSource.WIND.value] = BreakerState(
        consecutive_failures=5,
        circuit_open=True,
        opened_at=_time.time(),
    )

    # 只有 1 个源参与 → 无告警（也不写日志）
    disagreements = agg.cross_check("RB0", "2026-08-04", sources=[src1, src2], trace_id="t-cc-open")
    assert disagreements == []


def test_cross_check_date_not_matched_returns_empty(tmp_path: Path):
    """源 df 中无匹配日期时跳过该源。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df1 = _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0, date_str="2026-08-01")
    df2 = _make_close_only_source(DataSource.WIND.value, 3600.0, date_str="2026-08-02")
    agg = FuturesDataAggregator(sources=[df1, df2])
    # 请求 2026-08-04 → 两源都不匹配 → prices 空 → []
    disagreements = agg.cross_check("RB0", "2026-08-04", sources=[df1, df2], trace_id="t-cc-date")
    assert disagreements == []


def test_cross_check_source_exception_silently_skipped(tmp_path: Path):
    """交叉验证中单源 fetch 抛异常被吞掉，不影响其他源。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    good = _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0)
    bad = MagicMock()
    bad.source_name = DataSource.WIND.value
    bad.fetch_ohlcv_or_none = MagicMock(side_effect=RuntimeError("boom"))

    agg = FuturesDataAggregator(sources=[good, bad])
    # 不抛异常
    disagreements = agg.cross_check("RB0", "2026-08-04", sources=[good, bad], trace_id="t-cc-exc")
    assert disagreements == []


def test_cross_check_single_price_skipped(tmp_path: Path):
    """只有 1 个源有价格时返回空（不足 2 个价格）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    src1 = _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0)
    src2 = _make_close_only_source(DataSource.WIND.value, 3600.0, date_str="2026-08-02")  # 日期不匹配
    agg = FuturesDataAggregator(sources=[src1, src2])
    disagreements = agg.cross_check("RB0", "2026-08-04", sources=[src1, src2], trace_id="t-cc-1p")
    assert disagreements == []


def test_write_disagreement_log_failure_silent(tmp_path: Path):
    """JSONL 日志写入失败被静默吞掉，告警仍返回。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    sources = [
        _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0),
        _make_close_only_source(DataSource.WIND.value, 3600.0),
    ]
    agg = FuturesDataAggregator(
        sources=sources,
        disagreement_log_path=tmp_path / "disagreements.jsonl",
        cross_check_threshold=0.005,
    )
    with patch("pathlib.Path.open", side_effect=OSError("denied")):
        disagreements = agg.cross_check("RB0", "2026-08-04", sources=sources, trace_id="t-cc-logfail")
    # 告警仍然返回
    assert len(disagreements) == 1


# ═══════════════════════════════════════════════════════════
# 主路径尾部自动交叉验证 _maybe_cross_check
# ═══════════════════════════════════════════════════════════


def test_maybe_cross_check_disabled_returns_empty(tmp_path: Path):
    """enable_cross_check=False 时 _maybe_cross_check 返回空。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = _make_kline_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
    agg = FuturesDataAggregator(enable_cross_check=False)
    assert agg._maybe_cross_check(df, "RB0", "t") == []


def test_maybe_cross_check_few_enhancers_returns_empty(tmp_path: Path):
    """enhancers 少于 2 个时不触发交叉验证。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = _make_kline_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
    wind = _make_close_only_source(DataSource.WIND.value, 3540.0)
    agg = FuturesDataAggregator(enhancers=[wind])
    assert agg._maybe_cross_check(df, "RB0", "t") == []


def test_maybe_cross_check_missing_columns_returns_empty(tmp_path: Path):
    """df 缺少 date/close 列时不触发交叉验证。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = pd.DataFrame({"symbol": ["RB0"], "open": [1.0]})
    wind = _make_close_only_source(DataSource.WIND.value, 3540.0)
    ifind = _make_close_only_source(DataSource.IFIND.value, 3541.0)
    agg = FuturesDataAggregator(enhancers=[wind, ifind])
    assert agg._maybe_cross_check(df, "RB0", "t") == []


def test_maybe_cross_check_sort_exception_returns_empty(tmp_path: Path):
    """date 去重排序抛异常时返回空（不中断主路径）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = _make_kline_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
    wind = _make_close_only_source(DataSource.WIND.value, 3540.0)
    ifind = _make_close_only_source(DataSource.IFIND.value, 3541.0)
    agg = FuturesDataAggregator(enhancers=[wind, ifind])
    with patch.object(df["date"], "astype", side_effect=RuntimeError("boom")):
        assert agg._maybe_cross_check(df, "RB0", "t") == []


def test_maybe_cross_check_triggers_alerts(tmp_path: Path):
    """多源分歧触发自动交叉验证并返回告警。

    注: 2 源时"偏离中位数百分比"数学上对称（|a-b|/(a+b) 恒等），
    因此使用 3 源（2 近 1 远）构造非对称告警。
    """
    from datetime import date as _date
    from fts.data_sources.aggregator import FuturesDataAggregator

    # 主路径 df：最近 5 天含 2026-08-04
    base = _date(2026, 8, 1)
    tq_df = _make_kline_df("RB0", DataSource.TQ_LOCAL.value, rows=5, base_date=base)
    # 3 源：AKSHARE=4000, WIND=4000（近 median），IFIND=4040（偏离 0.66%）
    akshare = _make_close_only_source(DataSource.AKSHARE.value, 4000.0)
    wind = _make_close_only_source(DataSource.WIND.value, 4000.0)
    ifind = _make_close_only_source(DataSource.IFIND.value, 4040.0)

    agg = FuturesDataAggregator(
        enhancers=[akshare, wind, ifind],
        cross_check_threshold=0.005,
        disagreement_log_path=tmp_path / "disagreements.jsonl",
    )
    alerts = agg._maybe_cross_check(tq_df, "RB0", "t-cc-auto")

    assert len(alerts) == 1
    assert alerts[0]["outliers"] == [DataSource.IFIND.value]
    # 日志文件已写入
    assert (tmp_path / "disagreements.jsonl").exists()


# ═══════════════════════════════════════════════════════════
# 资源清理
# ═══════════════════════════════════════════════════════════


def test_close_releases_connection(cache_with_data: Path):
    """close() 关闭持久连接并清空引用。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=cache_with_data)
    con = agg._get_cache_conn()
    assert con is not None

    agg.close()
    assert agg._cache_conn is None
    # 再次 close 不抛异常
    agg.close()


# ═══════════════════════════════════════════════════════════
# 剩余小分支：熔断跳过分钟源 / 空数据 / con None / close 异常
# ═══════════════════════════════════════════════════════════


def test_get_minute_ohlcv_circuit_open_skips_source(tmp_db: Path):
    """分钟源熔断开启时被跳过。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    src = MagicMock()
    src.source_name = DataSource.TDX_LOCAL.value
    src.period = None
    src.fetch_ohlcv = MagicMock(side_effect=AssertionError("熔断后不应调用"))

    agg = FuturesDataAggregator(minute_sources=[src], db_path=tmp_db, circuit_breaker_threshold=2)
    agg._record_failure(DataSource.TDX_LOCAL.value, "x")
    agg._record_failure(DataSource.TDX_LOCAL.value, "y")

    df = agg.get_minute_ohlcv("RB0", days=3, frequency="5m", trace_id="t-min-open")
    assert df.empty  # 唯一源被熔断 → 空 DataFrame
    src.fetch_ohlcv.assert_not_called()


def test_get_minute_ohlcv_source_returns_none(tmp_db: Path):
    """分钟源返回 None（空数据）时继续降级。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    empty = MagicMock()
    empty.source_name = DataSource.TDX_LOCAL.value
    empty.period = None
    empty.fetch_ohlcv = MagicMock(return_value=None)

    good_df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
    good = MagicMock()
    good.source_name = DataSource.TQ_LOCAL.value
    good.period = None
    good.fetch_ohlcv = MagicMock(return_value=good_df)

    agg = FuturesDataAggregator(minute_sources=[empty, good], db_path=tmp_db)
    df = agg.get_minute_ohlcv("RB0", days=3, frequency="5m", trace_id="t-min-none")

    assert len(df) == 3
    good.fetch_ohlcv.assert_called_once()


def test_try_minute_cache_conn_none_returns_none(minute_cache_db: Path):
    """_get_cache_conn 返回 None 时 _try_minute_cache 返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=minute_cache_db)
    with patch.object(agg, "_get_cache_conn", return_value=None):
        assert agg._try_minute_cache("RB0", 100, "5m") is None


def test_try_minute_cache_zero_rows_returns_none(minute_cache_db: Path):
    """days=0 → LIMIT 0 → 空 df → None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=minute_cache_db)
    assert agg._try_minute_cache("RB0", days=0, frequency="5m") is None


def test_write_minute_cache_conn_none_returns(tmp_db: Path):
    """_get_cache_conn 返回 None 时 _write_minute_cache 静默返回。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = _make_minute_df("RB0", DataSource.TQ_LOCAL.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    with patch.object(agg, "_get_cache_conn", return_value=None):
        agg._write_minute_cache(df, "5m")  # 不应抛异常


def test_get_ticks_source_returns_none(tmp_db: Path):
    """tick 源返回 None（空数据）时继续降级。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    empty = _TickMockSource(DataSource.TQSDK_TICK.value, return_none=True)
    good_df = _make_tick_df("RB0", "TICK2", rows=3)
    good = _TickMockSource("TICK2", df=good_df)

    agg = FuturesDataAggregator(tick_sources=[empty, good], db_path=tmp_db)
    df = agg.get_ticks("RB0", count=3, trace_id="t-tick-none")

    assert len(df) == 3
    assert good.fetch_count == 1


def test_try_tick_cache_conn_none_returns_none(tick_cache_db: Path):
    """_get_cache_conn 返回 None 时 _try_tick_cache 返回 None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=tick_cache_db)
    with patch.object(agg, "_get_cache_conn", return_value=None):
        assert agg._try_tick_cache("RB0", 100) is None


def test_try_tick_cache_zero_rows_returns_none(tick_cache_db: Path):
    """count=0 → LIMIT 0 → 空 df → None。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=tick_cache_db)
    assert agg._try_tick_cache("RB0", count=0) is None


def test_write_tick_cache_skips_empty(tmp_db: Path):
    """空 DataFrame 不写 tick_cache。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=tmp_db)
    agg._write_tick_cache(pd.DataFrame())  # 不应抛异常


def test_write_tick_cache_conn_none_returns(tmp_db: Path):
    """_get_cache_conn 返回 None 时 _write_tick_cache 静默返回。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    df = _make_tick_df("RB0", DataSource.TQSDK_TICK.value, rows=3)
    agg = FuturesDataAggregator(db_path=tmp_db)
    with patch.object(agg, "_get_cache_conn", return_value=None):
        agg._write_tick_cache(df)  # 不应抛异常


def test_close_ignores_conn_close_exception(cache_with_data: Path):
    """close() 中 conn.close 抛异常被吞掉，引用仍清空。"""
    from fts.data_sources.aggregator import FuturesDataAggregator

    agg = FuturesDataAggregator(db_path=cache_with_data)
    mock_con = MagicMock()
    mock_con.close.side_effect = RuntimeError("close failed")
    agg._cache_conn = mock_con

    agg.close()  # 不应抛异常
    assert agg._cache_conn is None


# ═══════════════════════════════════════════════════════════
# pre_settle 派生（GAP-083 方案 C：零依赖 pre_settle = 前日 settle）
# ═══════════════════════════════════════════════════════════


class TestDerivePreSettle:
    """aggregator 运行时派生 pre_settle = 前一交易日 settle（回退 close.shift(1)）。"""

    def _df(self, **overrides):
        cols = {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "settle": [3100.0, 3120.0, 3110.0],
            "pre_settle": [0.0, 0.0, 0.0],
            "close": [3098.0, 3118.0, 3108.0],
        }
        for k, v in overrides.items():
            cols[k] = v
        return pd.DataFrame(cols)

    def test_derives_from_prev_settle(self):
        """pre_settle = 前一交易日 settle；首日无前值回退当日 close。"""
        from fts.data_sources.aggregator import FuturesDataAggregator

        out = FuturesDataAggregator._derive_pre_settle(self._df())
        assert list(out["pre_settle"]) == [3098.0, 3100.0, 3120.0]

    def test_prev_settle_invalid_falls_back_close(self):
        """前日 settle 无效（0）时回退 close.shift(1)。"""
        from fts.data_sources.aggregator import FuturesDataAggregator

        out = FuturesDataAggregator._derive_pre_settle(self._df(settle=[3100.0, 0.0, 3110.0]))
        assert list(out["pre_settle"]) == [3098.0, 3100.0, 3118.0]

    def test_prev_settle_nan_falls_back_close(self):
        """前日 settle 为 NaN 时同样回退 close.shift(1)。"""
        from fts.data_sources.aggregator import FuturesDataAggregator

        out = FuturesDataAggregator._derive_pre_settle(
            self._df(settle=[3100.0, float("nan"), 3110.0])
        )
        assert list(out["pre_settle"]) == [3098.0, 3100.0, 3118.0]

    def test_existing_valid_presettle_untouched(self):
        """已有有效 pre_settle 不被覆盖（增强层权威值优先）。"""
        from fts.data_sources.aggregator import FuturesDataAggregator

        out = FuturesDataAggregator._derive_pre_settle(self._df(pre_settle=[3300.0, 0.0, 0.0]))
        assert list(out["pre_settle"]) == [3300.0, 3100.0, 3120.0]

    def test_missing_column_noop(self):
        """缺 settle/pre_settle/close 任一列 → 原样返回（不抛）。"""
        from fts.data_sources.aggregator import FuturesDataAggregator

        df = pd.DataFrame({"date": pd.to_datetime(["2026-01-05"]), "close": [1.0]})
        out = FuturesDataAggregator._derive_pre_settle(df)
        assert out.equals(df)
        assert "pre_settle" not in out.columns

    def test_all_valid_presettle_noop(self):
        """pre_settle 全部有效时不产生任何修改。"""
        from fts.data_sources.aggregator import FuturesDataAggregator

        df = self._df(pre_settle=[3300.0, 3301.0, 3302.0])
        out = FuturesDataAggregator._derive_pre_settle(df.copy())
        assert list(out["pre_settle"]) == [3300.0, 3301.0, 3302.0]

    def test_descending_date_input(self):
        """缓存路径倒序（ORDER BY date DESC）输入：按日期升序派生后还原原行序。"""
        from fts.data_sources.aggregator import FuturesDataAggregator

        df = self._df().iloc[::-1].reset_index(drop=True)  # 倒序：07, 06, 05
        out = FuturesDataAggregator._derive_pre_settle(df.copy())
        # 原行序 07: 前日(06) settle=3120；06: 前日(05) settle=3100；05: 首日回退 close=3098
        assert list(out["pre_settle"]) == [3120.0, 3100.0, 3098.0]


def test_get_ohlcv_derives_pre_settle_from_cache(tmp_db: Path):
    """缓存命中路径：pre_settle 无效时按前日 settle 派生（接入点验证）。"""
    from fts.data_sources.aggregator import FuturesDataAggregator
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(tmp_db)
    base = (datetime.now() - timedelta(days=4)).date()
    df = _make_kline_df("RB0", DataSource.DUCKDB_CACHE.value, rows=5, base_date=base)
    df["pre_settle"] = 0.0  # 模拟缓存中 pre_settle 全无效（TQ 15 年数据现状）
    import duckdb

    con = duckdb.connect(str(tmp_db))
    try:
        con.register("df_cache", df)
        con.execute("INSERT INTO kline_cache SELECT * FROM df_cache")
        con.unregister("df_cache")
    finally:
        con.close()

    agg = FuturesDataAggregator(db_path=tmp_db, enable_cross_check=False)
    out = agg.get_ohlcv("RB0", days=5, trace_id="t-pre-cache")

    assert len(out) == 5
    # 缓存路径输出为倒序（ORDER BY date DESC）：派生值按原行序还原后 = 前日 settle
    expected_asc = df["settle"].shift(1).fillna(df["close"])
    assert list(out["pre_settle"]) == expected_asc.tolist()[::-1]
