"""Regime 制度预测力验证 CLI（plans/53 §D，决策门先行）。

用法:
    python scripts/regime_predictive_power_check.py [--days N] [--lookback N] [--step N] \
        [--horizon N] [--no-hmm] [--json]

流程:
    1. 加载能源化工面板（ENERGY_CHAIN_SYMBOLS ∪ ENERGY_CHAIN_HOLDOUT，SSOT）；
    2. 构建市场合成 OHLCV（SectorRegimeSelector._build_sector_ohlcv）；
    3. RegimeSeriesBuilder 滚动检测 → 历史制度序列；
    4. validate_regime_predictive_power：Kruskal-Wallis 检验制度对前向收益/波动的区分力；
    5. 决策门：K-W p < 0.05 → ✅ 制度标签可用（进入 A 模块）；
                不显著 → ❌ 停止（不推进条件化，防空中楼阁）。

报告落盘: reports/{market}/{date}/regime_predictive_power.md

版本: v0.1.0（plans/53 §D）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 动态解析项目根（禁止硬编码绝对路径）
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.data import FTSDataProvider
from fts.data_futures import ENERGY_CHAIN_HOLDOUT, ENERGY_CHAIN_SYMBOLS
from fts.factor_engine.regime_profile import RegimeSeriesBuilder
from fts.factor_engine.regime_validation import validate_regime_predictive_power

DECISION_ALPHA: float = 0.05  # K-W 显著性水平（制度标签区分前向收益的决策门）


def _forward_series(ohlcv: pd.DataFrame, horizon: int) -> tuple[pd.Series, pd.Series]:
    """计算前向收益与前向波动（t 时点之后 horizon 期）。"""
    close = ohlcv["close"].astype(float)
    rets = close.pct_change()
    fwd_ret = close.shift(-horizon) / close - 1.0  # t → t+horizon 累计收益
    fwd_vol = rets.rolling(horizon).std().shift(-horizon)  # 未来 horizon 期日收益波动
    return fwd_ret, fwd_vol


def _json_safe(obj: Any) -> Any:
    """递归将 NaN/Inf 转换为 None（保证输出合法 JSON）。"""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    return obj


def _markdown_report(
    market: str,
    symbols: list[str],
    params: dict[str, Any],
    result: dict[str, Any],
    passed: bool,
    regime_series: pd.Series,
) -> str:
    """生成 markdown 验证报告。"""
    lines: list[str] = [
        "# Regime 制度预测力验证报告（plans/53 §D）",
        "",
        f"- **市场**: {market}",
        f"- **品种池**: {len(symbols)} 个（{', '.join(symbols)}）",
        f"- **参数**: 回看窗口={params['lookback']} 步长={params['step']} 前向周期={params['horizon']} 天数={params['days']}",
        f"- **生成时间**: {datetime.now().isoformat()}",
        "",
        "## 决策门结论",
        "",
        f"**{'✅ 通过 — 制度标签可区分前向收益/波动，可进入 A 模块画像资产化' if passed else '❌ 未通过 — 制度标签对前向收益/波动无显著区分力，停止条件化推进'}**",
        "",
    ]
    if "error" in result:
        lines.append(f"**错误**: {result['error']}")
        return "\n".join(lines) + "\n"

    lines.append(f"**样本数**: {result.get('n', 0)}  **制度种类**: {sorted(regime_series.unique())}")
    if "kruskal_stat" in result:
        lines.append(
            f"\n**Kruskal-Wallis**: stat={result['kruskal_stat']:.4f}  p={result['kruskal_p']:.4g}"
            f"（组间前向收益分布差异，决策门 p<{DECISION_ALPHA}）\n"
        )
    lines.append("\n| 制度 | 样本数 | 条件前向收益均值 | 条件前向波动均值 | 收益标准差 |")
    lines.append("|:-----|:-------|:-----------------|:-----------------|:-----------|")
    for regime in sorted(regime_series.unique()):
        st = result.get(str(regime), {})
        lines.append(
            f"| {regime} | {st.get('count', 0)} | "
            f"{st.get('mean_fwd_return', float('nan')):+.5f} | "
            f"{st.get('mean_fwd_vol', float('nan')):.5f} | "
            f"{st.get('fwd_return_std', float('nan')):.5f} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Regime 制度预测力验证（plans/53 §D）")
    parser.add_argument("--days", type=int, default=500, help="面板回溯天数（默认 500，对齐 MIN_EVAL_DAYS）")
    parser.add_argument("--lookback", type=int, default=120, help="滚动检测窗口（默认 120）")
    parser.add_argument("--step", type=int, default=5, help="滚动检测步长（默认 5）")
    parser.add_argument("--horizon", type=int, default=5, help="前向收益周期（默认 5）")
    parser.add_argument("--no-hmm", action="store_true", help="禁用 HMM 检测（仅规则检测，速度快）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = parser.parse_args()

    if args.horizon < 1 or args.lookback < 20 or args.step < 1:
        raise SystemExit("参数非法: --horizon>=1, --lookback>=20, --step>=1")

    market = "energy"
    symbols = sorted(set(ENERGY_CHAIN_SYMBOLS) | set(ENERGY_CHAIN_HOLDOUT))
    provider = FTSDataProvider()
    panel, _cdates = provider.get_futures_panel(symbols=symbols, days=args.days)
    if not panel:
        print(f"[D] 面板加载失败（{market}），退出")
        return 1

    # 构建制度序列
    builder = RegimeSeriesBuilder(
        lookback_days=args.lookback,
        step_days=args.step,
        use_hmm=not args.no_hmm,
    )
    regime_series = builder.build_from_panel(panel, symbols)
    if regime_series.empty:
        print(f"[D] 制度序列构建失败（合成 OHLCV 不足 {args.lookback} 行），退出")
        return 1

    # 预测力检验（前向收益/波动按制度序列时点对齐）
    from fts.factor_engine.regime import SectorRegimeSelector

    ohlcv = SectorRegimeSelector._build_sector_ohlcv(panel, symbols)
    fwd_ret, fwd_vol = _forward_series(ohlcv, args.horizon)
    aligned = pd.DataFrame({"fwd": fwd_ret, "fwd_vol": fwd_vol}).reindex(regime_series.index)
    result = validate_regime_predictive_power(regime_series, aligned["fwd"], aligned["fwd_vol"])

    # 决策门
    kruskal_p = result.get("kruskal_p")
    passed = bool(
        kruskal_p is not None
        and np.isfinite(kruskal_p)
        and float(kruskal_p) < DECISION_ALPHA
    )

    report = {
        "source": market,
        "symbols": symbols,
        "params": {
            "days": args.days,
            "lookback": args.lookback,
            "step": args.step,
            "horizon": args.horizon,
            "use_hmm": not args.no_hmm,
        },
        "decision_passed": passed,
        "decision_alpha": DECISION_ALPHA,
        "regime_distinct": sorted(set(regime_series)),
        "validation": result,
    }

    if args.json:
        print(json.dumps(_json_safe(report), ensure_ascii=False, indent=2))
    else:
        md = _markdown_report(market, symbols, report["params"], result, passed, regime_series)
        ts = date.today().isoformat()
        out_dir = Path("reports") / market / ts
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "regime_predictive_power.md"
        out_file.write_text(md, encoding="utf-8")
        print(md)
        print(f"[D] 报告已落盘: {out_file}")
        print(f"[D] 决策门: {'✅ 通过' if passed else '❌ 未通过'}")

    return 0 if passed else 0  # 决策门结果由报告承载，CLI 退出码仅反映执行成功


if __name__ == "__main__":
    raise SystemExit(main())
