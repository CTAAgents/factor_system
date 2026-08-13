"""scripts/p0_golden_baseline.py — P0 基线冻结：导出 golden output + 环境指纹。

用途: 32-stock-extraction-plan.md Phase 0 产物。冻结期货演化基线，
     供迁移阶段（P1/P2/P3）golden diff 对比使用。

输出:
  memory/baseline/golden_output_futures.json   — factor_id → 评估指标 + elite JSON hash
  memory/baseline/env_fingerprint.json         — Python/依赖版本 + 随机种子指纹

用法:
  python scripts/p0_golden_baseline.py
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ELITE_DIR = PROJECT_ROOT / "memory" / "knowledge" / "factors" / "futures_elite"
OUT_DIR = PROJECT_ROOT / "memory" / "baseline"


def file_hash(path: Path) -> str:
    """SHA-256 文件摘要。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_metrics(factor: dict) -> dict:
    """提取可对比的评估指标子集（golden diff 锚点）。

    实际 evaluation 结构（调研确认）:
      level_1_backtest / level_2_economic / level_3_multiple / passed /
      failure_reasons / walk_forward / backtest_pipeline / robustness_check /
      causal_validation / shap_analysis / ablation_check
    """
    ev = factor.get("evaluation", {})
    l1 = ev.get("level_1_backtest", {})
    wf = ev.get("walk_forward", {})
    robustness = ev.get("robustness_check", {})
    return {
        "factor_id": factor.get("factor_id"),
        "name": factor.get("name"),
        "market": factor.get("market"),
        "family": factor.get("family"),
        "passed": ev.get("passed"),
        "level_1": {
            "ic": l1.get("ic"),
            "icir": l1.get("icir"),
            "sharpe": l1.get("sharpe"),
            "max_drawdown": l1.get("max_drawdown"),
            "win_rate": l1.get("win_rate"),
            "monotonicity": l1.get("monotonicity"),
            "oos_ratio": l1.get("oos_ratio"),
        },
        "walk_forward": {
            "ic_consistency": wf.get("ic_consistency"),
            "windows": wf.get("windows"),
            "passed": wf.get("passed"),
        },
        "robustness": {
            "passed": robustness.get("passed"),
            "pass_rate": robustness.get("pass_rate"),
            "failed": robustness.get("failed"),
        },
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. golden output: elite JSON + hash + 评估指标 ──
    elites = sorted(ELITE_DIR.glob("*.json"))
    golden: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elite_dir": str(ELITE_DIR),
        "n_elites": len(elites),
        "factors": {},
    }
    for p in elites:
        try:
            factor = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[p0] 读取失败 {p.name}: {e}")
            continue
        golden["factors"][p.stem] = {
            "json_hash": file_hash(p),
            "metrics": extract_metrics(factor),
        }
    golden_path = OUT_DIR / "golden_output_futures.json"
    golden_path.write_text(json.dumps(golden, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[p0] golden output 写入: {golden_path} ({len(golden['factors'])} 个 elite 因子)")

    # ── 2. 环境指纹 ──
    fingerprint: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "python": sys.version,
        "random_seed": "未显式固定（依赖 FTS 内部固定种子，需在计划中记录）",
    }
    for mod_name in ("numpy", "pandas", "scipy", "duckdb"):
        try:
            mod = __import__(mod_name)
            fingerprint[mod_name] = getattr(mod, "__version__", "unknown")
        except Exception:  # noqa: BLE001
            fingerprint[mod_name] = "not installed"
    env_path = OUT_DIR / "env_fingerprint.json"
    env_path.write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[p0] 环境指纹写入: {env_path}")

    # ── 3. 摘要 ──
    print("\n[p0] ═══ P0 基线冻结摘要 ═══")
    print(f"[p0] elite 因子数: {len(golden['factors'])}")
    for fid, info in golden["factors"].items():
        m = info["metrics"]
        print(f"[p0]   {fid} | name={m['name']} | ic={m['level_1'].get('ic')} | "
              f"sharpe={m['level_1'].get('sharpe')} | passed={m.get('passed')} | "
              f"wf_consistency={m['walk_forward'].get('ic_consistency')} | "
              f"hash={info['json_hash'][:12]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
