"""分析期货精英因子性能"""

import json
from pathlib import Path

elite_dir = Path("memory/knowledge/factors/futures_elite")
records = {}
for fp in sorted(elite_dir.glob("*.json")):
    d = json.loads(fp.read_text(encoding="utf-8"))
    name = d.get("name", fp.stem)
    bt = d.get("evaluation", {}).get("level_1_backtest", {})
    source = d.get("source", "seed")
    ic = bt.get("ic", 0)
    if name not in records or abs(ic) > abs(records[name]["ic"]):
        records[name] = {
            "name": name,
            "ic": ic,
            "sharpe": bt.get("sharpe", 0),
            "t_stat": bt.get("t_stat", 0),
            "max_dd": bt.get("max_drawdown", 0),
            "source": source,
        }

sorted_records = sorted(records.values(), key=lambda r: -abs(r["ic"]))
print(f"{'因子名称':<30s} {'IC':>8s} {'Sharpe':>8s} {'t_stat':>8s} {'MaxDD':>8s} {'来源':<12s}")
print("-" * 80)
for r in sorted_records:
    src = "演化" if r["source"] == "macro_evolution" else "种子"
    print(f"{r['name']:<30s} {r['ic']:>8.4f} {r['sharpe']:>8.2f} {r['t_stat']:>8.2f} {r['max_dd']:>8.4f} {src:<12s}")

print(f"\n总计: {len(sorted_records)} 个唯一因子")
evolved = [r for r in sorted_records if r["source"] == "macro_evolution"]
print(f"其中演化产生: {len(evolved)} 个")
for r in evolved:
    print(f"  - {r['name']}: IC={r['ic']:.4f}, Sharpe={r['sharpe']:.2f}")
