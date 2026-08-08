"""临时脚本：扫描 futures_elite 活跃因子防护状态（任务后删除）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
elite = ROOT / "memory/knowledge/factors/futures_elite"

rows = []
for fp in sorted(elite.glob("fct_*.json")):
    try:
        f = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        continue
    code = f.get("code", "")
    name = f.get("name", "")
    fid = f["factor_id"]
    entry_guard = "np.isnan(close).any()" in code
    output_guard = "nan_to_num" in code
    risk = []
    if "np.bincount" in code:
        risk.append("bincount")
    if "astype(int)" in code:
        risk.append("astype(int)")
    if "np.histogram" in code:
        risk.append("histogram")
    if "np.digitize" in code:
        risk.append("digitize")
    rows.append({
        "fid": fid, "name": name,
        "entry_guard": entry_guard,
        "output_guard": output_guard,
        "risk": risk,
        "uses_fields": [k for k in ("close", "volume", "high", "low", "open") if f"['{k}']" in code or f'["{k}"]' in code],
    })

no_guard = [r for r in rows if not r["entry_guard"]]
print(f"活跃因子总数: {len(rows)}")
print(f"已含入口 NaN 防护: {len(rows) - len(no_guard)}")
print(f"缺入口 NaN 防护: {len(no_guard)}\n")

print("=== 缺入口 NaN 防护 + 含风险模式（优先） ===")
for r in no_guard:
    if r["risk"]:
        print(f"  {r['fid']} | {r['name']} | risk={r['risk']} | fields={r['uses_fields']}")

print("\n=== 缺入口 NaN 防护（其余） ===")
for r in no_guard:
    if not r["risk"]:
        print(f"  {r['fid']} | {r['name']} | output_guard={r['output_guard']} | fields={r['uses_fields']}")
