"""Bridgewater 增长×通胀四象限宏观制度报告 CLI（GAP-092）。

用法:
    python scripts/macro_regime_report.py [--json] [--trace-id <id>] \
        [--growth-threshold 50] [--inflation-target 2.0] [--inflation-band 2.0]

流程:
    1. 拉取真实宏观时序（基于已闭环基础设施）:
         - 通胀维度: CPI 当月同比 —— EastmoneyMacroSource（东财 RPT_ECONOMY_CPI，
           GAP-088 闭环，edb_cache 缓存优先，零网络二次请求）
         - 增长维度: 制造业 PMI —— akshare macro_china_pmi「制造业-指数」（GAP-087 同款）
    2. MacroRegimeDetector 水平阈值判定 → 当前四象限 + 置信度 + 联合软概率；
    3. 输出象限画像（板块偏好）与四象限概率分布。

数据缺失处理（如实降级不伪造）: cpi/pmi 任一拉取失败 → 报告明确标注缺失维度，
quadrant 标记 unavailable（不编造象限）。

版本: v0.1.0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

# 动态解析项目根（禁止硬编码绝对路径）
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.macro_regime import (  # noqa: E402
    DEFAULT_MACRO_REGIME_CONFIG,
    MacroRegimeConfig,
    MacroRegimeDetector,
)


def _fetch_growth_series(trace_id: str) -> pd.Series | None:
    """拉取制造业 PMI 月度时序（akshare macro_china_pmi「制造业-指数」）。

    Returns:
        DatetimeIndex 月度序列；失败返回 None。
    """
    try:
        import akshare as ak  # type: ignore[import-untyped]

        df = ak.macro_china_pmi()
        if df is None or df.empty or "制造业-指数" not in df.columns:
            return None
        idx = pd.to_datetime(df["月份"].astype(str).str.replace("年", "-").str.replace("月份", "-01", regex=False))
        values = pd.to_numeric(df["制造业-指数"], errors="coerce")
        series = pd.Series(values.to_numpy(), index=idx, name="pmi").sort_index()
        return series.dropna()
    except Exception as e:  # noqa: BLE001
        print(f"[macro-regime] PMI 获取失败: {e}")
        return None


def _fetch_inflation_series(trace_id: str) -> pd.Series | None:
    """拉取 CPI 当月同比月度时序（EastmoneyMacroSource，已闭环 edb_cache）。"""
    try:
        from fts.data_sources.macro_eastmoney_source import EastmoneyMacroSource

        series = EastmoneyMacroSource().get_macro_series("中国CPI当月同比", trace_id=trace_id)
        if series is None or series.empty:
            return None
        return pd.to_numeric(series, errors="coerce").dropna()
    except Exception as e:  # noqa: BLE001
        print(f"[macro-regime] CPI 获取失败: {e}")
        return None


def _json_safe(obj: Any) -> Any:
    """递归将 NaN/Inf 转换为 None（保证输出合法 JSON）。"""
    import math

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _build_config(args: argparse.Namespace) -> MacroRegimeConfig:
    cfg = dict(DEFAULT_MACRO_REGIME_CONFIG)
    overrides = {
        "growth_threshold": args.growth_threshold,
        "inflation_target": args.inflation_target,
        "inflation_band": args.inflation_band,
    }
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return MacroRegimeConfig(**cfg)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridgewater 四象限宏观制度报告（GAP-092）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    parser.add_argument("--trace-id", default="", help="HARNESS trace_id")
    parser.add_argument("--growth-threshold", type=float, default=None, help="增长荣枯线（默认 50）")
    parser.add_argument("--inflation-target", type=float, default=None, help="通胀目标中枢（默认 2.0）")
    parser.add_argument("--inflation-band", type=float, default=None, help="通胀带宽（默认 2.0）")
    args = parser.parse_args()

    trace_id = args.trace_id or f"macro-regime-{pd.Timestamp.now():%Y%m%d%H%M%S}"
    growth = _fetch_growth_series(trace_id)
    inflation = _fetch_inflation_series(trace_id)

    detector = MacroRegimeDetector(_build_config(args))
    result = detector.detect(growth, inflation) if growth is not None and inflation is not None else None

    latest_g = growth.iloc[-1] if growth is not None and not growth.empty else None
    latest_i = inflation.iloc[-1] if inflation is not None and not inflation.empty else None

    report: dict[str, Any] = {
        "trace_id": trace_id,
        "data": {
            "growth_pmi": _json_safe(float(latest_g)) if latest_g is not None else None,
            "inflation_cpi": _json_safe(float(latest_i)) if latest_i is not None else None,
        },
        "quadrant": result["quadrant"] if result else "unavailable",
        "confidence": round(result["confidence"], 4) if result else None,
        "growth_score": round(result["growth_score"], 4) if result else None,
        "inflation_score": round(result["inflation_score"], 4) if result else None,
        "quadrant_probs": {k: round(v, 4) for k, v in result["quadrant_probs"].items()} if result else None,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("\n=== Bridgewater 增长×通胀四象限宏观制度报告 ===")
    print(f"trace_id: {trace_id}")
    if latest_g is None or latest_i is None:
        print(f"数据缺失: PMI={'缺失' if latest_g is None else 'OK'} | CPI={'缺失' if latest_i is None else 'OK'}")
        print("无法判定象限（如实标注，不伪造）")
        return 0
    print(f"数据: PMI(最新月)={latest_g:.1f} | CPI(最新月)={latest_i:.1f}%")
    if result is None:
        print("无法判定象限（数据为空/无效）")
        return 0

    profile = detector.quadrant_profile(result["quadrant"])
    print(f"当前象限: {result['quadrant']}（{profile['label']}，{profile['description']}）")
    print(f"置信度: {result['confidence']:.3f}")
    print(
        f"增长得分: {result['growth_score']:.3f}（{'高' if result['growth_score'] >= 0 else '低'}增长）"
        f" | 通胀得分: {result['inflation_score']:.3f}（{'高' if result['inflation_score'] >= 0 else '低'}通胀）"
    )
    probs = " / ".join(f"{k}={v:.3f}" for k, v in result["quadrant_probs"].items())
    print(f"四象限概率: {probs}")
    print(f"画像: {profile['narrative']}")
    print(f"偏好: {', '.join(profile['favored'])} | 规避: {', '.join(profile['hedged'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
