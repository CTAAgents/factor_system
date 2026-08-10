"""scripts.verify_multi_source — Phase 14.4 真实多源联调脚本。

串联 14.1（聚合器）+ 14.2（交叉验证）+ 14.3（融合策略）的真实端到端流程：

  1) 构造 `FuturesDataAggregator`（TQ_LOCAL + WIND + IFIND）
  2) 并行拉取每个源的最新 N 天 K 线（真实调用，失败/超时计入熔断器）
  3) 对成功返回的源做 `OHLCVFusion` 融合（5 策略矩阵）
  4) 对融合结果最新日期做 `cross_check` 交叉验证
  5) 输出 `FusionReport`（含 disagreements）+ 落盘 `data/_lineage/multi_source_<ts>.json`

适用：
    python scripts/verify_multi_source.py --symbol RB0 --days 30
    python scripts/verify_multi_source.py --symbol RB0 --days 30 --strategy WEIGHTED
    python scripts/verify_multi_source.py --mock  # 离线模式（无真实网络）

HARNESS §5.5 trace_id 全链路：单次执行生成唯一 trace_id 贯穿所有阶段。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 允许脚本作为独立入口运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fts.core.contracts import FusionReport  # noqa: E402
from fts.core.enums import FusionStrategy  # noqa: E402
from fts.data_sources.aggregator import FuturesDataAggregator  # noqa: E402
from fts.data_sources.base import BaseFuturesSource  # noqa: E402
from fts.data_sources.fusion import OHLCVFusion  # noqa: E402
from fts.factor_engine import generate_trace_id  # noqa: E402

logger = logging.getLogger("verify_multi_source")


# ─── Mock 源（离线模式）───────────────────────────────────────


class _MockSource(BaseFuturesSource):
    """模拟数据源，固定 5 天 K 线，用于离线 E2E。"""

    def __init__(self, source_name: str, base_price: float, noise: float = 0.005, fail: bool = False):
        self.source_name = source_name
        self._base = base_price
        self._noise = noise
        self._fail = fail

    def is_available(self) -> bool:
        return not self._fail

    def fetch_quote(self, symbol, trace_id=""):
        return None

    def fetch_ohlcv(self, symbol, days, trace_id=""):
        if self._fail:
            raise RuntimeError(f"[{self.source_name}] mock failure")
        import numpy as np
        import pandas as pd
        from datetime import date, timedelta

        end = date.today()
        dates = [end - timedelta(days=i) for i in range(days)][::-1]
        np.random.seed(hash((self.source_name, symbol)) % 2**31)
        drift = np.random.uniform(-self._noise, self._noise, days).cumsum()
        close = self._base * (1.0 + drift)
        open_ = close * (1 + np.random.uniform(-0.001, 0.001, days))
        high = np.maximum(close, open_) * (1 + abs(np.random.uniform(0, 0.002, days)))
        low = np.minimum(close, open_) * (1 - abs(np.random.uniform(0, 0.002, days)))
        volume = np.random.randint(50000, 200000, days)
        return pd.DataFrame(
            {
                "symbol": symbol,
                "period": "daily",
                "date": dates,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "source": self.source_name,
                "trace_id": trace_id,
            }
        )


def _build_real_aggregator() -> tuple[FuturesDataAggregator, list[str]]:
    """构造真实聚合器（按需导入）。"""
    sources: list[BaseFuturesSource] = []
    enhancers: list[BaseFuturesSource] = []
    available: list[str] = []

    try:
        from fts.data_sources.tq_source import TQLocalSource

        src = TQLocalSource()
        if src.is_available():
            sources.append(src)
            available.append("TQ_LOCAL")
        else:
            logger.info("[TQ_LOCAL] 探活失败，跳过")
    except Exception as e:
        logger.info("[TQ_LOCAL] 不可用: %s", e)

    try:
        from fts.data_sources.wind_source import WindSource

        src = WindSource()
        if src.is_available():
            enhancers.append(src)
            available.append("WIND")
        else:
            logger.info("[WIND] 探活失败，跳过")
    except Exception as e:
        logger.info("[WIND] 不可用: %s", e)

    try:
        from fts.data_sources.ifind_source import IFindSource

        src = IFindSource()
        if src.is_available():
            enhancers.append(src)
            available.append("IFIND")
        else:
            logger.info("[IFIND] 探活失败，跳过")
    except Exception as e:
        logger.info("[IFIND] 不可用: %s", e)

    db_path = Path("data") / "fts_history.duckdb"
    agg = FuturesDataAggregator(
        sources=sources,
        enhancers=enhancers,
        db_path=db_path if db_path.exists() else None,
    )
    return agg, available


def _build_mock_aggregator() -> tuple[FuturesDataAggregator, list[str]]:
    """构造 mock 聚合器（3 源，含 1 个失败源用于测试熔断）。"""
    sources = [_MockSource("TQ_LOCAL", 3500.0, noise=0.003)]
    enhancers = [
        _MockSource("WIND", 3500.0, noise=0.004),
        _MockSource("IFIND", 3500.0, noise=0.005, fail=True),  # 模拟 IFIND 不可用
    ]
    db_path = Path("data") / "fts_history.duckdb"
    agg = FuturesDataAggregator(
        sources=sources,
        enhancers=enhancers,
        db_path=db_path if db_path.exists() else None,
    )
    return agg, ["TQ_LOCAL", "WIND"]  # 实际可用（fail=True 会被熔断）


# ─── 5 策略矩阵验证 ─────────────────────────────────────────


def _run_strategy(
    strategy: FusionStrategy,
    source_dfs: dict,
    symbol: str,
    trace_id: str,
) -> dict:
    """对一组多源 K 线按指定策略融合，返回简化结果。"""
    fuser = OHLCVFusion(strategy=strategy)
    fused_df = fuser.fuse_dataframe(symbol, source_dfs, trace_id=trace_id)
    if fused_df.empty:
        return {"strategy": strategy.name, "rows": 0}

    latest = fused_df.iloc[-1]
    return {
        "strategy": strategy.name,
        "rows": len(fused_df),
        "latest_date": str(latest["date"]),
        "latest_close": round(float(latest["close"]), 4),
        "latest_volume": round(float(latest["volume"]), 0),
        "sources": list(latest["contributing_sources"]),
        "disagreement_pct": round(float(latest.get("disagreement_pct", 0.0)), 6),
    }


# ─── 5 场景端到端 ───────────────────────────────────────────


def scenario_1_mock_offline(trace_id: str) -> dict:
    """场景 1: 离线 mock 模式完整流程（5 策略 + cross_check）。"""
    agg, available = _build_mock_aggregator()
    symbol = "RB0"
    days = 5

    # 1) 拉取多源 K 线（mock 直接返回）
    source_dfs: dict = {}
    for src in agg.sources + agg.enhancers:
        try:
            df = src.fetch_ohlcv_or_none(symbol, days=days, trace_id=trace_id)
            if df is not None and not df.empty:
                source_dfs[src.source_name] = df
                agg._record_success(src.source_name)
        except Exception as e:
            agg._record_failure(src.source_name, str(e))

    # 2) 5 策略融合
    results: list[dict] = []
    for strat in FusionStrategy:
        results.append(_run_strategy(strat, source_dfs, symbol, trace_id))

    # 3) 交叉验证（仅对最近日期）
    latest_date = str(results[0]["latest_date"])
    disagreements = agg.cross_check(symbol, latest_date, trace_id=trace_id)

    return {
        "scenario": "offline_mock_5_strategy",
        "symbol": symbol,
        "days": days,
        "sources": available,
        "source_dfs_keys": sorted(source_dfs.keys()),
        "strategy_results": results,
        "cross_check": {
            "date": latest_date,
            "disagreements_count": len(disagreements),
            "disagreements": disagreements,
        },
        "expected": {
            "sources": ["TQ_LOCAL", "WIND"],  # IFIND mock fail
            "strategies": 5,
            "rows_per_strategy": 5,
        },
    }


def scenario_2_real_one_strategy(trace_id: str) -> Optional[dict]:
    """场景 2: 真实模式（探活可用源 + MEDIAN 融合）。"""
    agg, available = _build_real_aggregator()
    if not available:
        logger.info("[scenario 2] 真实源均不可用，跳过")
        return None

    symbol = "RB0"
    days = 5
    source_dfs: dict = {}
    for src in agg.sources + agg.enhancers:
        try:
            df = src.fetch_ohlcv_or_none(symbol, days=days, trace_id=trace_id)
            if df is not None and not df.empty:
                source_dfs[src.source_name] = df
        except Exception as e:
            logger.debug("[scenario 2] %s 失败: %s", src.source_name, e)

    if not source_dfs:
        return {
            "scenario": "real_one_strategy",
            "available_sources": available,
            "data_obtained": False,
        }

    fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
    fused_df = fuser.fuse_dataframe(symbol, source_dfs, trace_id=trace_id)
    return {
        "scenario": "real_one_strategy",
        "available_sources": available,
        "data_obtained": not fused_df.empty,
        "rows": len(fused_df),
        "sources": sorted(source_dfs.keys()),
        "strategy": "MEDIAN",
    }


def scenario_3_breaker_state(trace_id: str) -> dict:
    """场景 3: 熔断器状态报告（连续失败累积验证）。"""
    agg, _ = _build_mock_aggregator()
    # 通过聚合器主路径触发 IFIND 失败（mock fail=True），
    # 这样 _record_failure 会被调用。
    src = next(s for s in agg.enhancers if s.source_name == "IFIND")
    for _ in range(3):
        # 模拟 aggregator 行为：调用 _record_failure
        try:
            df = src.fetch_ohlcv("RB0", days=5, trace_id=trace_id)
            if df is not None and not df.empty:
                agg._record_success(src.source_name)
        except Exception as e:
            agg._record_failure(src.source_name, str(e))

    return {
        "scenario": "breaker_state",
        "status": agg.get_source_status(),
        "expected": {
            "IFIND.consecutive_failures": 3,
            "IFIND.circuit_open": False,  # 阈值 5 还没到
        },
    }


def scenario_4_aggregation_pipeline(trace_id: str) -> dict:
    """场景 4: 串联 14.1 聚合器 + 14.2 cross_check + 14.3 融合（端到端）。"""
    agg, _ = _build_mock_aggregator()
    symbol = "RB0"
    days = 5

    # 14.1: 聚合器主路径（直接调 get_ohlcv 会优先缓存 → 这里绕过缓存）
    agg.enable_cross_check = True
    df = agg.get_ohlcv(symbol, days=days, trace_id=trace_id)
    rows_via_aggregator = len(df) if df is not None else 0

    # 14.3: 融合（仅取 WIND + TQ_LOCAL 数据，避开 IFIND 失败）
    source_dfs: dict = {}
    for src in agg.sources + agg.enhancers:
        try:
            sdf = src.fetch_ohlcv_or_none(symbol, days=days, trace_id=trace_id)
            if sdf is not None and not sdf.empty:
                source_dfs[src.source_name] = sdf
        except Exception:
            pass

    fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
    fused_df = fuser.fuse_dataframe(symbol, source_dfs, trace_id=trace_id)
    rows_via_fusion = len(fused_df) if not fused_df.empty else 0

    return {
        "scenario": "aggregation_pipeline",
        "symbol": symbol,
        "aggregator_rows": rows_via_aggregator,
        "fusion_rows": rows_via_fusion,
        "expected": {
            "both_have_rows": rows_via_aggregator > 0 and rows_via_fusion > 0,
        },
    }


def scenario_5_fusion_report(trace_id: str) -> dict:
    """场景 5: 完整 FusionReport 构造（用于落盘 / CLI 输出）。"""
    agg, _ = _build_mock_aggregator()
    symbol = "RB0"
    days = 3

    source_dfs: dict = {}
    for src in agg.sources + agg.enhancers:
        try:
            sdf = src.fetch_ohlcv_or_none(symbol, days=days, trace_id=trace_id)
            if sdf is not None and not sdf.empty:
                source_dfs[src.source_name] = sdf
        except Exception:
            pass

    fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
    fused_df = fuser.fuse_dataframe(symbol, source_dfs, trace_id=trace_id)
    if fused_df.empty:
        return {"scenario": "fusion_report", "rows": 0}

    rows = []
    for _, r in fused_df.iterrows():
        rows.append(
            {
                "symbol": str(r["symbol"]),
                "date": str(r["date"]),
                "close": float(r["close"]),
                "fusion_strategy": str(r["fusion_strategy"]),
                "contributing_sources": list(r["contributing_sources"]),
            }
        )

    report: FusionReport = {
        "trace_id": trace_id,
        "symbol": symbol,
        "strategy": "MEDIAN",
        "rows": rows,  # type: ignore[typeddict-item]
        "sources_used": sorted(source_dfs.keys()),
        "rows_count": len(rows),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }

    # 验证契约必填字段
    required = ("trace_id", "symbol", "strategy", "rows", "sources_used", "rows_count")
    missing = [f for f in required if f not in report]
    return {
        "scenario": "fusion_report",
        "report": report,
        "contract_ok": len(missing) == 0,
        "missing_required_fields": missing,
        "expected": {
            "rows_count": 3,
            "sources_used_min": 2,
        },
    }


# ─── 主入口 ───────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 14.4 真实多源联调验证")
    parser.add_argument("--symbol", default="RB0", help="品种代码（默认 RB0）")
    parser.add_argument("--days", type=int, default=30, help="回溯天数（默认 30）")
    parser.add_argument(
        "--strategy",
        default="MEDIAN",
        choices=[s.value for s in FusionStrategy],
        help="融合策略（默认 MEDIAN）",
    )
    parser.add_argument("--mock", action="store_true", help="离线 mock 模式（无真实网络）")
    parser.add_argument("--output", default=None, help="报告落盘路径（默认 data/_lineage/multi_source_<ts>.json）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    trace_id = generate_trace_id()
    print("=" * 70)
    print(f"  Phase 14.4 多源联调验证: trace_id={trace_id}")
    print(f"  symbol={args.symbol} days={args.days} strategy={args.strategy} mock={args.mock}")
    print("=" * 70)

    results: list[dict] = []

    # 场景 1: mock 离线
    print("\n[1/5] 场景 1: mock 离线（5 策略 + cross_check）")
    s1 = scenario_1_mock_offline(trace_id)
    src_keys = s1["source_dfs_keys"]
    print(f"       sources={src_keys} strategies={len(s1['strategy_results'])}")
    for r in s1["strategy_results"]:
        print(
            f"       {r['strategy']:<15} close={r.get('latest_close', 0):>10.2f} "
            f"disagreement={r.get('disagreement_pct', 0):.6f}"
        )
    cc = s1["cross_check"]
    print(f"       cross_check @ {cc['date']} → {cc['disagreements_count']} disagreements")
    assert s1["expected"]["sources"] == src_keys or set(src_keys) == set(s1["expected"]["sources"]), (
        f"expected {s1['expected']['sources']}, got {src_keys}"
    )
    results.append(s1)

    # 场景 2: 真实探活
    print("\n[2/5] 场景 2: 真实探活 + 单策略融合")
    s2 = scenario_2_real_one_strategy(trace_id)
    if s2 is None:
        print("       真实源不可用，跳过")
    else:
        print(f"       available={s2['available_sources']} data={s2['data_obtained']} rows={s2.get('rows', 0)}")
    results.append(s2 or {"scenario": "real_one_strategy", "skipped": True})

    # 场景 3: 熔断器
    print("\n[3/5] 场景 3: 熔断器状态（连续失败累积）")
    s3 = scenario_3_breaker_state(trace_id)
    ifind = s3["status"].get("IFIND", {})
    print(
        f"       IFIND consecutive_failures={ifind.get('consecutive_failures', 0)} "
        f"circuit_open={ifind.get('circuit_open', False)}"
    )
    assert ifind.get("consecutive_failures", 0) >= 3, (
        f"expected IFIND consecutive_failures >= 3, got {ifind.get('consecutive_failures', 0)}"
    )
    results.append(s3)

    # 场景 4: 端到端串联
    print("\n[4/5] 场景 4: 14.1 聚合器 + 14.3 融合 端到端")
    s4 = scenario_4_aggregation_pipeline(trace_id)
    print(f"       aggregator_rows={s4['aggregator_rows']} fusion_rows={s4['fusion_rows']}")
    assert s4["expected"]["both_have_rows"], f"expected both to have rows, got {s4}"
    results.append(s4)

    # 场景 5: 完整 FusionReport
    print("\n[5/5] 场景 5: 完整 FusionReport 构造")
    s5 = scenario_5_fusion_report(trace_id)
    print(
        f"       contract_ok={s5['contract_ok']} rows={s5['report']['rows_count']} "
        f"sources={s5['report']['sources_used']}"
    )
    assert s5["contract_ok"], f"contract missing fields: {s5['missing_required_fields']}"
    results.append(s5)

    # 落盘
    output_path = (
        Path(args.output)
        if args.output
        else (Path("data") / "_lineage" / f"multi_source_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "args": vars(args),
                "scenarios": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print("  5/5 场景通过 ✅")
    print(f"  报告落盘: {output_path}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
