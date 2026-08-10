"""register_l2_elites.py — 将 L2 演化产生的 elite 因子注册到 factor_pool.json 并生成 _elite_index.json

Usage:
    python scripts/register_l2_elites.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    elite_dir = root / "memory" / "knowledge" / "factors" / "elite"
    pool_path = root / "memory" / "knowledge" / "factors" / "factor_pool.json"

    if not elite_dir.exists():
        print(f"[register] elite 目录不存在: {elite_dir}")
        return 1

    # ── 1. Load factor_pool.json ──
    if pool_path.exists():
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
    else:
        pool = {
            "version": "8.10.0",
            "updated_at": datetime.now().isoformat(),
            "factors": [],
            "total_count": 0,
            "pending_count": 0,
        }
    existing_ids = {f.get("factor_id", "") for f in pool.get("factors", [])}

    # ── 2. Register new L2 elites ──
    registered: list[str] = []
    for fp in sorted(elite_dir.glob("fct_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[register] 跳过损坏文件 {fp.name}: {e}")
            continue

        fid = data.get("factor_id", "")
        if not fid or fid in existing_ids:
            continue

        bt = data.get("evaluation", {}).get("level_1_backtest", {})
        sharpe = bt.get("sharpe", 0)
        if sharpe > 3:
            priority = "high"
        elif sharpe > 1.5:
            priority = "medium"
        else:
            priority = "low"

        entry = {
            "factor_id": fid,
            "name": data.get("name", ""),
            "source": "l2_evolution",
            "parent_topic": data.get("parent_id"),
            "debate_round_ref": None,
            "debate_gap": None,
            "economic_logic": data.get("economic_logic", {}),
            "priority": priority,
            "status": "elite",
            "trace_id": data.get("trace_id", ""),
            "created_at": data.get("created_at", datetime.now().isoformat()),
            "updated_at": datetime.now().isoformat(),
        }
        pool["factors"].append(entry)
        existing_ids.add(fid)
        registered.append(fid)

    # ── 3. Update pool metadata ──
    pool["updated_at"] = datetime.now().isoformat()
    pool["total_count"] = len(pool.get("factors", []))
    pool["pending_count"] = sum(1 for f in pool.get("factors", []) if f.get("status") == "pending")
    pool_path.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 4. Generate _elite_index.json ──
    index = {
        "version": pool.get("version", "8.10.0"),
        "updated_at": datetime.now().isoformat(),
        "total_elite": 0,
        "factors": [],
    }
    for fp in sorted(elite_dir.glob("fct_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if fp.name.startswith("_"):
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        bt = data.get("evaluation", {}).get("level_1_backtest", {})
        mtime = datetime.fromtimestamp(fp.stat().st_mtime).isoformat()
        index["factors"].append(
            {
                "factor_id": data.get("factor_id"),
                "name": data.get("name"),
                "source": data.get("source"),
                "generation": data.get("generation"),
                "ic": bt.get("ic"),
                "sharpe": bt.get("sharpe"),
                "max_drawdown": bt.get("max_drawdown"),
                "t_stat": bt.get("t_stat"),
                "parent_id": data.get("parent_id"),
                "trace_id": data.get("trace_id"),
                "file": fp.name,
                "modified_at": mtime,
            }
        )
    index["total_elite"] = len(index["factors"])
    index_path = elite_dir / "_elite_index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 5. Report ──
    print(f"[register] 注册 {len(registered)} 个 L2 精英因子 -> factor_pool.json")
    print(f"[register] factor_pool 总数: {pool['total_count']}")
    print(f"[register] 生成精英索引: {index['total_elite']} 因子 -> {index_path}")
    if registered:
        print("[register] 新增因子详情:")
        for fid in registered:
            rec = next((f for f in index["factors"] if f["factor_id"] == fid), None)
            if rec:
                s = rec.get("sharpe", 0) or 0
                ic = rec.get("ic", 0) or 0
                print(f"  + {rec['factor_id']} | {rec['name']} | Sharpe={s:.3f} | IC={ic:.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
