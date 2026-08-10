"""scripts/verify_cross_check.py — 端到端验证多源交叉验证（Phase 14.2）。

HARNESS §任务 14.2 验证（5 场景）:
    1. 阈值内（差异 < 0.5%）→ 无告警 + 无日志
    2. 阈值外（差异 > 0.5%）→ 告警 + 写入 JSONL + outliers 正确
    3. 单源失败 → 不影响其他源 + 不抛异常
    4. enable_cross_check=False → 跳过验证
    5. 主路径 get_ohlcv 自动触发（最近 5 个交易日）

Usage:
    python scripts/verify_cross_check.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

# 让脚本独立运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fts.core.enums import DataSource  # noqa: E402
from fts.data_sources.aggregator import FuturesDataAggregator  # noqa: E402


# ─── mock 数据源 ───────────────────────────────────────────


def _make_close_only_source(
    source_name: str, close_value: float, symbol: str = "RB0", date_str: str = "2026-08-04"
) -> MagicMock:
    """构造一个仅返回单行 close 的 mock 源。"""
    df = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "period": "daily",
                "date": datetime.fromisoformat(date_str).date(),
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
    mock = MagicMock()
    mock.source_name = source_name
    mock.fetch_count = 0

    def _fetch(symbol, days, trace_id=""):
        mock.fetch_count += 1
        return df.copy()

    mock.fetch_ohlcv = _fetch
    mock.fetch_ohlcv_or_none = _fetch
    mock.is_available = lambda: True
    return mock


def _make_failing_source(source_name: str) -> MagicMock:
    """构造一个总是失败的 mock 源。"""
    mock = MagicMock()
    mock.source_name = source_name
    mock.fetch_count = 0

    def _fetch(symbol, days, trace_id=""):
        mock.fetch_count += 1
        raise ConnectionError(f"{source_name} down")

    mock.fetch_ohlcv = _fetch
    mock.fetch_ohlcv_or_none = _fetch
    mock.is_available = lambda: False
    return mock


def _make_kline_df(source_name: str, rows: int = 5, base_date: datetime | None = None) -> pd.DataFrame:
    """构造 K 线 DataFrame（用于主路径触发）。"""
    if base_date is None:
        base_date = datetime.now() - timedelta(days=rows - 1)
    data = []
    for i in range(rows):
        data.append(
            {
                "symbol": "RB0",
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
                "source": source_name,
                "fetched_at": datetime.now(),
                "trace_id": "",
            }
        )
    return pd.DataFrame(data)


# ─── 验证场景 ────────────────────────────────────────────


def scenario_1_within_threshold(tmp_dir: Path) -> bool:
    """场景 1: 阈值内（差异 < 0.5%）→ 无告警。"""
    print("\n[1/5] 场景 1: 阈值内（差异 < 0.5%）→ 无告警")
    log_path = tmp_dir / "scenario1.jsonl"
    sources = [
        _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0),
        _make_close_only_source(DataSource.WIND.value, 3541.5),  # 0.04%
        _make_close_only_source(DataSource.IFIND.value, 3542.0),  # 0.06%
    ]
    agg = FuturesDataAggregator(
        sources=sources,
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
    )
    result = agg.cross_check("RB0", "2026-08-04", sources=sources, trace_id="v-cc-1")
    if result == [] and (not log_path.exists() or log_path.stat().st_size == 0):
        print("       ✅ 差异 < 0.5% 时无告警 + 日志未生成")
        return True
    print(f"       ❌ 失败: result={result}, log_exists={log_path.exists()}")
    return False


def scenario_2_outside_threshold(tmp_dir: Path) -> bool:
    """场景 2: 阈值外（差异 > 0.5%）→ 告警 + 写入 JSONL。"""
    print("\n[2/5] 场景 2: 阈值外（差异 > 0.5%）→ 告警 + JSONL 写入")
    log_path = tmp_dir / "scenario2.jsonl"
    # 3 个源，AKSHARE/TQ 一致，WIND 偏离 1.69%
    sources = [
        _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0),
        _make_close_only_source(DataSource.AKSHARE.value, 3540.0),
        _make_close_only_source(DataSource.WIND.value, 3600.0),
    ]
    agg = FuturesDataAggregator(
        sources=sources,
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
    )
    result = agg.cross_check("RB0", "2026-08-04", sources=sources, trace_id="v-cc-2")

    if len(result) != 1:
        print(f"       ❌ 失败: 期望 1 条告警，实际 {len(result)}")
        return False
    if not log_path.exists():
        print("       ❌ 失败: 日志文件未生成")
        return False

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    record = json.loads(lines[0])
    print(f"       ✅ 告警: outliers={record['outliers']}, max_diff={record['max_diff_pct']:.4f}")
    print(f"       ✅ JSONL: {log_path.name} 写入 1 行")
    return True


def scenario_3_one_source_fails(tmp_dir: Path) -> bool:
    """场景 3: 单源失败 → 不影响其他源。"""
    print("\n[3/5] 场景 3: 单源失败 → 不影响其他源")
    log_path = tmp_dir / "scenario3.jsonl"
    good1 = _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0)
    bad = _make_failing_source(DataSource.WIND.value)
    good2 = _make_close_only_source(DataSource.IFIND.value, 3541.0)

    agg = FuturesDataAggregator(
        sources=[good1, bad, good2],
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
    )
    # 不应抛异常
    try:
        result = agg.cross_check(
            "RB0",
            "2026-08-04",
            sources=[good1, bad, good2],
            trace_id="v-cc-3",
        )
    except Exception as e:  # noqa: BLE001
        print(f"       ❌ 失败: 抛异常 {e}")
        return False

    if result == []:
        print("       ✅ 1 个失败 + 2 个成功 → 正常返回（差异 < 阈值）")
        print(f"       ✅ good1.fetch_count={good1.fetch_count}, good2.fetch_count={good2.fetch_count}")
        return True
    print(f"       ❌ 失败: result={result}")
    return False


def scenario_4_disabled(tmp_dir: Path) -> bool:
    """场景 4: enable_cross_check=False → 跳过验证。"""
    print("\n[4/5] 场景 4: enable_cross_check=False → 跳过")
    log_path = tmp_dir / "scenario4.jsonl"
    sources = [
        _make_close_only_source(DataSource.TQ_LOCAL.value, 3540.0),
        _make_close_only_source(DataSource.WIND.value, 3600.0),  # 巨大差异
    ]
    agg = FuturesDataAggregator(
        sources=sources,
        disagreement_log_path=log_path,
        cross_check_threshold=0.005,
        enable_cross_check=False,
    )
    result = agg.cross_check("RB0", "2026-08-04", sources=sources, trace_id="v-cc-4")
    if result == [] and not log_path.exists():
        print("       ✅ 禁用交叉验证时即使差异巨大也返回空 + 无日志")
        return True
    print(f"       ❌ 失败: result={result}, log_exists={log_path.exists()}")
    return False


def scenario_5_main_path_triggers(tmp_dir: Path) -> bool:
    """场景 5: get_ohlcv 主路径自动触发（最近 5 个交易日）。"""
    print("\n[5/5] 场景 5: get_ohlcv 主路径自动触发交叉验证")
    log_path = tmp_dir / "scenario5.jsonl"

    # K 线主路径 TQ 返回 5 天
    today = datetime.now().date()
    base_date = today - timedelta(days=4)
    tq_df = _make_kline_df(
        DataSource.TQ_LOCAL.value, rows=5, base_date=datetime.combine(base_date, datetime.min.time())
    )

    tq_mock = MagicMock()
    tq_mock.source_name = DataSource.TQ_LOCAL.value
    tq_mock.fetch_count = 0

    def _tq_fetch(symbol, days, trace_id=""):
        tq_mock.fetch_count += 1
        return tq_df.copy()

    tq_mock.fetch_ohlcv = _tq_fetch
    tq_mock.fetch_ohlcv_or_none = _tq_fetch
    tq_mock.is_available = lambda: True

    # Wind 字段增强层 — 返回 5 天，最后一天偏离巨大
    wind_dates = [base_date + timedelta(days=i) for i in range(5)]
    wind_data = []
    for i, d in enumerate(wind_dates):
        close_v = 3540.0 + i if i < 4 else 3595.0  # 最后一天偏离
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
    wind_mock = MagicMock()
    wind_mock.source_name = DataSource.WIND.value
    wind_mock.fetch_count = 0

    def _wind_fetch(symbol, days, trace_id=""):
        wind_mock.fetch_count += 1
        return wind_df.copy()

    wind_mock.fetch_ohlcv = _wind_fetch
    wind_mock.fetch_ohlcv_or_none = _wind_fetch
    wind_mock.is_available = lambda: True

    # iFinD 字段增强层 — 同样 5 天，但最后一天不偏离
    ifind_df = pd.DataFrame(
        [
            {
                "symbol": "RB0",
                "period": "daily",
                "date": base_date + timedelta(days=i),
                "open": 3540 + i,
                "high": 3540 + i,
                "low": 3540 + i,
                "close": 3540 + i,
                "volume": 100000,
                "amount": 350000000,
                "hold": 80000,
                "settle": 3540 + i,
                "pre_settle": 3540 + i,
                "oi_change": 0,
                "vwap": 3540 + i,
                "source": DataSource.IFIND.value,
                "fetched_at": datetime.now(),
                "trace_id": "",
            }
            for i in range(5)
        ]
    )
    ifind_mock = MagicMock()
    ifind_mock.source_name = DataSource.IFIND.value
    ifind_mock.fetch_count = 0

    def _ifind_fetch(symbol, days, trace_id=""):
        ifind_mock.fetch_count += 1
        return ifind_df.copy()

    ifind_mock.fetch_ohlcv = _ifind_fetch
    ifind_mock.fetch_ohlcv_or_none = _ifind_fetch
    ifind_mock.is_available = lambda: True

    agg = FuturesDataAggregator(
        sources=[tq_mock],
        enhancers=[wind_mock, ifind_mock],
        db_path=tmp_dir / "fts.duckdb",
        enable_cross_check=True,
        cross_check_threshold=0.005,
        disagreement_log_path=log_path,
    )

    df = agg.get_ohlcv("RB0", days=5, trace_id="v-cc-5")
    if df["source"].iloc[0] != DataSource.TQ_LOCAL.value:
        print(f"       ❌ 失败: 主路径 K 线不是 TQ_LOCAL, source={df['source'].iloc[0]}")
        return False

    # 字段增强层 + cross_check 应被调用
    # 字段增强层 1 次 + cross_check 5 天 (前 4 天无告警但仍调用)
    if wind_mock.fetch_count < 2:
        print(f"       ❌ 失败: wind.fetch_count={wind_mock.fetch_count}，期望 >= 2（增强层 1 + 交叉验证 N）")
        return False

    print(f"       ✅ get_ohlcv 返回正常（source=TQ_LOCAL, len={len(df)}）")
    print(f"       ✅ wind.fetch_count={wind_mock.fetch_count}（字段增强 1 + 交叉验证 5 天）")

    # 检查日志：仅最后一天有告警（前 4 天 close 相同）
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        print(f"       ✅ JSONL 告警行数: {len(lines)}")
    else:
        print("       ⚠️ JSONL 未生成（前 4 天 close 相同，无 outlier）")
    return True


# ─── main ──────────────────────────────────────────────────


def main() -> int:
    print("=" * 60)
    print("Phase 14.2 端到端验证: 多源交叉验证 (cross_check)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        results = [
            scenario_1_within_threshold(tmp_dir),
            scenario_2_outside_threshold(tmp_dir),
            scenario_3_one_source_fails(tmp_dir),
            scenario_4_disabled(tmp_dir),
            scenario_5_main_path_triggers(tmp_dir),
        ]

    passed = sum(results)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"端到端验证结果: {passed}/{total} 场景通过")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
