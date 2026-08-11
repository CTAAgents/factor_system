"""P2 运行状态入库：分散状态 JSON → data/state.duckdb + 过程痕迹 gz 归档（plans/29 §4 Phase 2）

背景:
    FTS 运行时状态（evolution/portfolio/meta_loop/extractors/loop 的 state.json、
    combo_history、drift_history、权重快照、动态池等）散落于 memory/ 下多个 JSON
    文件，属「可重建的运行时状态」。P2 将其收敛至 data/state.duckdb（当前状态表
    + 历史追加表），使「无 state.json 可从 DuckDB 冷启动」；演化过程痕迹
    （traces/tracking/test_elite/failure/agent_proposals）为可重建日志，打包 gz
    归档至 data/archive/（复制语义，不删除源——删除留 P4 冻结期）。

模式:
    --dry-run       只预估（状态条目数 + 可归档文件数），不写入
    --verify-only   从 DuckDB 读回与源 JSON 逐字段比对（对账复核），不写入
    --archive       将过程痕迹目录打包 gz 至 data/archive/（复制语义）
    默认             权威状态入库 + 对账复核

用法:
    python scripts/migrate_state_to_duckdb.py --dry-run
    python scripts/migrate_state_to_duckdb.py
    python scripts/migrate_state_to_duckdb.py --verify-only --json
    python scripts/migrate_state_to_duckdb.py --archive

HARNESS: trace_id 全链路（fts.migrate_state.{ts}）；幂等可重入；失败透明。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tarfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

# 脚本独立运行时的导入引导（项目惯用法，ruff E402 豁免）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.store.state_db import DEFAULT_STATE_DB, StateKVStore  # noqa: E402

logger = logging.getLogger("migrate_state_to_duckdb")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEMORY_ROOT = PROJECT_ROOT / "memory"
ARCHIVE_ROOT = PROJECT_ROOT / "data" / "archive"

# ─── 权威状态源规则（glob → namespace/key，相对 MEMORY_ROOT）───
# key 模板: {stem}=文件名去扩展名, {parent}=父目录名
STATEFUL_GLOBS: list[tuple[str, str, str, str]] = [
    ("evolution/state.json", "evolution", "state", "file"),
    ("evolution/*/state.json", "evolution", "{parent}/state", "file"),
    ("meta_loop/state.json", "meta_loop", "state", "file"),
    ("meta_loop/*/state.json", "meta_loop", "{parent}/state", "file"),
    ("extractors/state.json", "extractors", "state", "file"),
    ("loop/*.json", "loop", "{stem}", "file"),
    ("portfolio/state.json", "portfolio", "state", "file"),
    ("portfolio/combo_history/*.json", "portfolio", "combo_history/{stem}", "file"),
    ("portfolio/drift_history/*.json", "portfolio", "drift_history/{stem}", "file"),
    ("portfolio/live_feedback.jsonl", "portfolio", "live_feedback", "jsonl"),
    ("portfolio/*.json", "portfolio", "{stem}", "file"),
    ("knowledge/factors/factor_pool.json", "knowledge", "factor_pool", "file"),
]

# ─── 过程痕迹目录（可重建，归档不删除）───
ARCHIVE_DIRS: list[str] = [
    "evolution/traces",
    "evolution/tracking",
    "evolution/test_elite",
    "evolution/failure",
    "evolution/futures/traces",
    "evolution/futures/failure",
    "portfolio/agent_proposals",
]


def _render_key(template: str, stem: str, parent: str) -> str:
    return template.replace("{stem}", stem).replace("{parent}", parent)


def discover_stateful_sources(memory_root: Path) -> list[tuple[str, str, Path, str]]:
    """按规则发现权威状态源，返回 [(namespace, key, 文件路径, kind)]（按路径去重）。"""
    sources: list[tuple[str, str, Path, str]] = []
    seen: set[str] = set()
    for pattern, namespace, key_tpl, kind in STATEFUL_GLOBS:
        for fp in sorted(memory_root.glob(pattern)):
            if not fp.exists() or not fp.is_file():
                continue
            abs_p = str(fp.resolve())
            if abs_p in seen:
                continue
            seen.add(abs_p)
            stem = fp.stem
            parent = fp.parent.name
            key = _render_key(key_tpl, stem, parent)
            sources.append((namespace, key, fp, kind))
    return sources


def discover_archivable(memory_root: Path) -> list[Path]:
    """收集过程痕迹文件（JSON 全部递归）。"""
    files: list[Path] = []
    for rel_dir in ARCHIVE_DIRS:
        d = memory_root / rel_dir
        if d.exists():
            files.extend(sorted(p for p in d.rglob("*.json")))
    return files


def _load_value(fp: Path, kind: str) -> Any:
    if kind == "jsonl":
        rows: list[Any] = []
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"raw": line})
        return rows
    return json.loads(fp.read_text(encoding="utf-8"))


def migrate_state(
    db_path: str | Path | None = None,
    memory_root: str | Path | None = None,
    dry_run: bool = False,
    verify_only: bool = False,
    trace_id: str = "",
) -> dict[str, Any]:
    """执行权威状态入库（+ 对账复核）。

    Returns:
        统计字典: total/migrated/verified/mismatched/failed/mismatch_samples
    """
    root = Path(memory_root) if memory_root else MEMORY_ROOT
    sources = discover_stateful_sources(root)
    stats: dict[str, Any] = {
        "db_path": str(Path(db_path) if db_path else DEFAULT_STATE_DB),
        "trace_id": trace_id,
        "total": len(sources),
        "migrated": 0,
        "verified": 0,
        "mismatched": 0,
        "failed": 0,
        "mismatch_samples": [],
        "errors": [],
    }

    if dry_run:
        logger.info("[%s] dry-run：预估权威状态条目 %d（不写入）", trace_id, len(sources))
        stats["migrated"] = len(sources)
        return stats

    store = StateKVStore(db_path)
    try:
        for namespace, key, fp, kind in sources:
            try:
                value = _load_value(fp, kind)
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append(f"{fp.name}: 解析失败 — {e}")
                continue
            if not verify_only:
                store.upsert(namespace, key, value, run_id=trace_id)
                stats["migrated"] += 1
            # 对账：从 DuckDB 读回与源逐字段一致
            back = store.get(namespace, key)
            if back == value:
                stats["verified"] += 1
            else:
                stats["mismatched"] += 1
                stats["mismatch_samples"].append({"ns": namespace, "key": key})
                logger.warning("[%s] 对账不一致: %s/%s", trace_id, namespace, key)
    finally:
        store.close()
    return stats


def archive_process_traces(
    memory_root: str | Path | None = None,
    archive_root: str | Path | None = None,
    dry_run: bool = False,
    trace_id: str = "",
) -> dict[str, Any]:
    """将过程痕迹目录打包 gz 至 data/archive/（复制语义，不删除源）。

    Returns:
        统计字典: total_files/archived_bytes/tar_path
    """
    root = Path(memory_root) if memory_root else MEMORY_ROOT
    ar = Path(archive_root) if archive_root else ARCHIVE_ROOT
    files = discover_archivable(root)
    stats: dict[str, Any] = {"total_files": len(files), "archived_bytes": 0, "tar_path": ""}
    if not files:
        logger.info("[%s] 无可归档过程痕迹", trace_id)
        return stats
    if dry_run:
        logger.info("[%s] dry-run：可归档 %d 个过程痕迹文件", trace_id, len(files))
        stats["archived_bytes"] = sum(p.stat().st_size for p in files)
        return stats
    ar.mkdir(parents=True, exist_ok=True)
    tar_path = ar / f"state_traces_{datetime.now().strftime('%Y%m%d%H%M%S')}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for fp in files:
            tar.add(fp, arcname=str(fp.relative_to(root)))
            stats["archived_bytes"] += fp.stat().st_size
    stats["tar_path"] = str(tar_path)
    logger.info("[%s] 已归档 %d 个过程痕迹 → %s", trace_id, len(files), tar_path)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="P2 运行状态入库：状态 JSON → state.duckdb + 过程痕迹归档")
    parser.add_argument("--dry-run", action="store_true", help="只预估不写入")
    parser.add_argument("--verify-only", action="store_true", help="仅从 DuckDB 读回对账，不写入")
    parser.add_argument(
        "--archive", action="store_true", help="将过程痕迹（traces/agent_proposals 等）打包 gz 至 data/archive/"
    )
    parser.add_argument("--db-path", default="", help="覆盖 state.duckdb 路径（默认 data/state.duckdb）")
    parser.add_argument("--state-dir", default="", help="覆盖 memory 根目录（测试隔离用）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    trace_id = f"fts.migrate_state.{ts}"
    result: dict[str, Any] = {"trace_id": trace_id}
    rc = 0
    try:
        result["state"] = migrate_state(
            db_path=args.db_path or None,
            memory_root=args.state_dir or None,
            dry_run=args.dry_run,
            verify_only=args.verify_only,
            trace_id=trace_id,
        )
        s = result["state"]
        logger.info(
            "[%s] 状态入库: total=%d migrated=%d verified=%d mismatched=%d failed=%d",
            trace_id,
            s["total"],
            s["migrated"],
            s["verified"],
            s["mismatched"],
            s["failed"],
        )
        if s["mismatched"]:
            rc = 2
        if args.archive:
            result["archive"] = archive_process_traces(
                memory_root=args.state_dir or None,
                dry_run=args.dry_run,
                trace_id=trace_id,
            )
    except Exception as e:
        logger.error("[%s] 迁移失败: %s", trace_id, e)
        traceback.print_exc()
        result["error"] = str(e)
        rc = 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
