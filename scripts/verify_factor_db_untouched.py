"""真实因子库零污染护栏（GAP-129，plans/43 §4.3/§5 步骤 6）。

背景: 根 tests/conftest.py autouse fixture 将 `schema.get_db_path` 重定向至
每测试独立 tmp DuckDB，常规测试零写真实库。本脚本作为 CI/本地回归护栏，
验证"运行测试前后真实 data/factor_catalog_{futures,energy}.duckdb 三表
（factor_catalog / factor_quality_scores / factor_audit_reports）COUNT 与
内容指纹完全一致"，防止未来新测试绕过隔离写真实库（回归即 CI 失败）。

用法:
    # 测试前（记录基线）
    python scripts/verify_factor_db_untouched.py --mode snapshot
    pytest tests/ ...
    # 测试后（对比基线）
    python scripts/verify_factor_db_untouched.py --mode check

护栏语义:
- 库文件不存在（如 CI runner 无 data/）→ 快照记录 absent；check 时仍 absent 则通过，
  但"基线 absent → 当前 present"（测试创建了真实库）判为差异 → CI 失败。
- 每表对比 count + 行级 md5 指纹（动态列，不依赖表结构）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import duckdb

_TABLES = ("factor_catalog", "factor_quality_scores", "factor_audit_reports")
_DEFAULT_MARKETS = ("futures", "energy")
_DEFAULT_SNAPSHOT = Path("data") / ".factor_db_untouched_snapshot.json"


# ────────────────────────────── 核心逻辑 ──────────────────────────────


def _table_fingerprint(conn: duckdb.DuckDBPyConnection, table: str) -> str:
    """计算单表内容指纹：每行 md5(全列值) → 排序聚合 md5。空表固定 'EMPTY'。"""
    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    if count == 0:
        return "EMPTY"
    cols = [row[0] for row in conn.execute(f'DESCRIBE "{table}"').fetchall()]
    if not cols:
        return "EMPTY"
    row_expr = " || '|' || ".join(f'COALESCE(CAST("{c}" AS VARCHAR), \'∅\')' for c in cols)
    return conn.execute(
        f"SELECT md5(string_agg(f, '\n' ORDER BY f)) "
        f"FROM (SELECT md5({row_expr}) AS f FROM \"{table}\") t"
    ).fetchone()[0]


def snapshot_database(db_path: Path) -> dict[str, Any]:
    """对单个库生成快照。库不存在 → {'absent': True}。"""
    entry: dict[str, Any] = {"absent": True, "tables": {}}
    if not Path(db_path).exists():
        return entry
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        entry["absent"] = False
        for table in _TABLES:
            exists = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = ? AND table_schema = 'main'",
                [table],
            ).fetchone()[0]
            if not exists:
                entry["tables"][table] = {"missing": True}
                continue
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            entry["tables"][table] = {
                "missing": False,
                "count": count,
                "fingerprint": _table_fingerprint(conn, table),
            }
    finally:
        conn.close()
    return entry


def snapshot_all(db_paths: dict[str, Path], trace_id: str) -> dict[str, Any]:
    """生成全市场基线快照。"""
    return {
        "schema_version": 1,
        "trace_id": trace_id,
        "snapshot_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "databases": {m: snapshot_database(p) for m, p in db_paths.items()},
    }


def _diff_entry(market: str, table: str, baseline: dict, current: dict) -> list[str]:
    """对比单表快照，返回差异描述列表。"""
    diffs: list[str] = []
    if baseline.get("absent") and current.get("absent"):
        return diffs
    if baseline.get("absent") and not current.get("absent"):
        diffs.append(f"[{market}] 真实库被创建（基线不存在 → 当前存在）：测试写库！")
        return diffs
    if not baseline.get("absent") and current.get("absent"):
        diffs.append(f"[{market}] 真实库被删除（基线存在 → 当前不存在）")
        return diffs
    b = baseline["tables"].get(table, {})
    c = current["tables"].get(table, {})
    if b.get("missing") and c.get("missing"):
        return diffs
    if b.get("missing") and not c.get("missing"):
        diffs.append(f"[{market}] 表 {table} 被创建（基线缺失 → 当前存在）")
        return diffs
    if not b.get("missing") and c.get("missing"):
        diffs.append(f"[{market}] 表 {table} 被删除（基线存在 → 当前缺失）")
        return diffs
    if b.get("count") != c.get("count"):
        diffs.append(f"[{market}] 表 {table} COUNT 变化：{b.get('count')} → {c.get('count')}")
    elif b.get("fingerprint") != c.get("fingerprint"):
        diffs.append(f"[{market}] 表 {table} 内容指纹变化（同 COUNT 下行数据被修改）")
    return diffs


def compare_snapshots(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """对比全量快照，返回全部差异描述（空列表 = 零污染）。"""
    diffs: list[str] = []
    markets = baseline.get("databases", {})
    for market, b_db in markets.items():
        c_db = current.get("databases", {}).get(market, {})
        for table in _TABLES:
            diffs.extend(_diff_entry(market, table, b_db, c_db))
    return diffs


# ────────────────────────────── CLI ──────────────────────────────


def _resolve_db_paths(markets: list[str]) -> dict[str, Path]:
    """解析真实库文件路径。

    注意：必须直接读 schema 模块级常量 DATABASE_PATH_*（而非 get_db_path）——
    get_db_path 是测试隔离的单挂载点（tests/conftest.py autouse fixture 会将其
    重定向至 tmp），护栏若走 get_db_path 会被隔离 fixture 架空，无法检测
    "显式传真实路径/直接用常量绕过隔离" 的回归场景。
    """
    from fts.factor_engine.factor_db import schema  # 延迟导入（脚本可独立于包运行前解析）

    return {m: (schema.DATABASE_PATH_ENERGY if m == "energy" else schema.DATABASE_PATH_FUTURES) for m in markets}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="真实因子库零污染护栏（GAP-129）：测试前后三表 COUNT 与内容指纹一致性"
    )
    ap.add_argument("--mode", choices=["snapshot", "check"], required=True, help="snapshot=记录基线 / check=对比基线")
    ap.add_argument(
        "--snapshot-file",
        type=Path,
        default=_DEFAULT_SNAPSHOT,
        help=f"快照文件路径（默认 {_DEFAULT_SNAPSHOT}）",
    )
    ap.add_argument(
        "--markets",
        default=",".join(_DEFAULT_MARKETS),
        help=f"市场列表，逗号分隔（默认 {','.join(_DEFAULT_MARKETS)}）",
    )
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ap.add_argument("--trace-id", default=None, help="trace_id（默认自动生成）")
    args = ap.parse_args(argv)

    trace_id = args.trace_id or f"verify_db_{uuid.uuid4().hex[:8]}"
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    db_paths = _resolve_db_paths(markets)
    snapshot_file = Path(args.snapshot_file)

    if args.mode == "snapshot":
        snapshot = snapshot_all(db_paths, trace_id)
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        snapshot_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.json:
            print(json.dumps({"mode": "snapshot", "trace_id": trace_id, "snapshot_file": str(snapshot_file), "ok": True}))
        else:
            print(f"✅ 快照已写入 {snapshot_file}（trace_id={trace_id}）")
        return 0

    if not snapshot_file.exists():
        if args.json:
            print(json.dumps({"mode": "check", "ok": False, "error": f"快照文件不存在: {snapshot_file}"}))
        else:
            print(f"❌ 快照文件不存在: {snapshot_file}（请先 --mode snapshot）")
        return 1

    baseline = json.loads(snapshot_file.read_text(encoding="utf-8"))
    current = snapshot_all(db_paths, trace_id)
    diffs = compare_snapshots(baseline, current)

    if args.json:
        print(json.dumps({"mode": "check", "trace_id": trace_id, "ok": not diffs, "diffs": diffs}))
    elif diffs:
        print(f"❌ 真实因子库被测试污染（trace_id={trace_id}）：")
        for d in diffs:
            print(f"   - {d}")
    else:
        print(f"✅ 真实因子库零污染（trace_id={trace_id}）")

    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
