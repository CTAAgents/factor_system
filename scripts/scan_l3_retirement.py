"""scan_l3_retirement.py — 阶段 2 FTS L3 组合侧退役扫描（plans/57 §4.1 / §5.3 准备工具）。

在阶段 1 双轨对账通过前，先建立退役对象的调用图扫描，供退役时"按调用图逐层剥离"。
仅只读扫描，不修改任何代码。

退役对象（§4.1）:
  - futures_signal_pipeline.py: _compute_composite_scores / _compute_per_variety_weights /
    _apply_regime_weight_adjustment / _apply_regime_direction_bias / _generate_trading_advice*
    / _compute_holdout_validation / _load_l3_combo_*
  - portfolio_loop.py: synthesize_signals / _compute_elastic_net_weights /
    _compute_ml_ensemble_weights / _synthesize_bl_weights / regime_adaptive_weight_adjustment /
    build_combo / _cap_safety_valve / _validate_combo_sharpe / _run_sharpe_randomization_test /
    decay_test / apply_turnover_penalty / _apply_sticky_constraints / _compute_subchain_exposure /
    _merge_gate_scale_into_modulation / _greedy_select_by_correlation / _dedup_factors_by_chain*
    / _filter_*
  - weight_learning.py / capital_allocator.py / regime_crowding.py（平移后 FTS 侧删除或标记弃用）

用法:
  python scripts/scan_l3_retirement.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

logger = logging.getLogger(__name__)

RETIRE_MAP: dict[str, list[str]] = {
    "futures_signal_pipeline.py": [
        "_compute_composite_scores", "_compute_per_variety_weights",
        "_apply_regime_weight_adjustment", "_apply_regime_direction_bias",
        "_generate_trading_advice", "_compute_holdout_validation",
        "_load_l3_combo_weights", "_load_l3_subchain_meta",
        "_load_l3_combo_meta", "_load_l3_combo_factors",
    ],
    "portfolio_loop.py": [
        "synthesize_signals", "_compute_elastic_net_weights",
        "_compute_ml_ensemble_weights", "_synthesize_bl_weights",
        "regime_adaptive_weight_adjustment", "build_combo",
        "_cap_safety_valve", "_validate_combo_sharpe",
        "_run_sharpe_randomization_test", "decay_test",
        "apply_turnover_penalty", "_apply_sticky_constraints",
        "_compute_subchain_exposure", "_merge_gate_scale_into_modulation",
        "_greedy_select_by_correlation", "_dedup_factors_by_chain",
        "_dedup_factors_by_chain_cluster", "_dedup_within_chain",
        "_filter_by_quality_gate", "_filter_shadow_pending", "_filter_review_approved",
    ],
}

MODULE_RETIRE = ["weight_learning.py", "capital_allocator.py", "regime_crowding.py"]


def scan() -> dict[str, object]:
    """扫描退役对象在 FTS 源码/测试中的调用点。"""
    files = sorted(Path(_PROJ_ROOT, "fts").rglob("*.py")) + sorted(Path(_PROJ_ROOT, "scripts").rglob("*.py"))
    test_files = sorted(Path(_PROJ_ROOT, "tests").rglob("*.py"))
    results: dict[str, object] = {"functions": {}, "modules": {}, "test_files": {}}

    def _scan(fname_list, name, patterns):
        hits = {}
        for fp in fname_list:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            for p in patterns:
                if f"{p}" in text:
                    hits.setdefault(p, []).append(str(fp.relative_to(_PROJ_ROOT)))
        results[name] = hits

    for src, funcs in RETIRE_MAP.items():
        _scan(files, "functions", funcs)
    _scan(test_files, "test_files", [f for fs in RETIRE_MAP.values() for f in fs])
    results["modules"] = {
        m: [str(fp.relative_to(_PROJ_ROOT)) for fp in files if fp.name == m]
        for m in MODULE_RETIRE
    }
    results["summary"] = {
        "retire_functions": sum(len(v) for v in RETIRE_MAP.values()),
        "functions_with_callers": len(results["functions"]),
        "test_files_referencing": len(results["test_files"]),
    }
    return results


def render(r: dict[str, object]) -> str:
    lines = ["阶段 2 FTS L3 组合侧退役扫描（plans/57 §4.1，只读）", "=" * 78]
    for name, hits in r["functions"].items():
        n = len(hits)
        lines.append(f"  {name:<42} 调用点 {n} 处")
    lines.append("-" * 78)
    tf = r["test_files"]
    lines.append(f"测试引用退役对象: {len(tf)} 个函数名（涉及 {sum(len(v) for v in tf.values())} 处）")
    for name, hits in tf.items():
        lines.append(f"    {name}: {len(hits)} 处")
    lines.append(f"整体退役模块（§4.1）: {r['modules']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=str, default="")
    args = ap.parse_args()
    report = scan()
    print(render(report))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
