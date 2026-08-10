"""将最新 9 个 L1 注入候选因子的状态从 injected 改为 pending。"""

import json
from pathlib import Path

pool_path = Path("memory/knowledge/factors/factor_pool.json")
target_ids = {
    "cand_4b4dbd15",
    "cand_6e6052ab",
    "cand_397a955a",
    "cand_21901c83",
    "cand_65a24474",
    "cand_27cf8203",
    "cand_94f05aba",
    "cand_c73c4296",
    "cand_476bd29a",
}
target_names = {
    "IntradayRecoveryFactor",
    "VolumePriceDivergence",
    "RangeBreakoutMomentum",
    "AverageTrueRangePulse",
    "BiasSpotter",
    "HighLowAsymmetry",
    "VolumeWeightedPriceZScore",
    "ShockAbsorptionRatio",
    "MomentumQualityScore",
}

pool = json.loads(pool_path.read_text(encoding="utf-8"))
changed = 0
for entry in pool.get("factors", []):
    fid = entry.get("factor_id")
    if fid in target_ids:
        old = entry["status"]
        if old != "pending":
            entry["status"] = "pending"
            entry["updated_at"] = "2026-08-07T18:00:00.000000"
            changed += 1
            print(f"  {fid} ({entry['name']}): {old} → pending")
        else:
            print(f"  {fid} ({entry['name']}): 已经是 pending")

pool["updated_at"] = "2026-08-07T18:00:00.000000"
pool_path.write_text(json.dumps(pool, ensure_ascii=False, indent=2))
print(f"\n共修改 {changed} 个候选因子状态")
