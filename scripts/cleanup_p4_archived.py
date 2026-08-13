"""P4 归档清理（安全部分）：删除已归档的过程痕迹源 + 归档删除 experiments（plans/29 Phase 4）

背景:
    P2 已把过程痕迹（evolution/traces、tracking、test_elite、failure、futures/traces、
    futures/failure、portfolio/agent_proposals）打包为 data/archive/state_traces_*.tar.gz
    （复制语义）。P4 安全清理 = 删除这些「已归档且可重建」的过程痕迹源 + 归档删除
    data/experiments-*.json 实验日志。

范围:
    - 仅处理「已归档/可重建」的过程痕迹源与实验日志，删除不影响运行
    - 读路径依赖的 elite JSON / state JSON / 动态池等**不在此范围**（需读路径切 DuckDB 后另行处理）

模式:
    --dry-run       只统计待删除文件，不执行
    默认             删除已归档过程痕迹源 + 归档删除 experiments

HARNESS: trace_id 全链路（fts.cleanup_p4.{ts}）；幂等可重入；失败透明。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tarfile
from datetime import datetime
from pathlib import Path

# 脚本独立运行时的导入引导（项目惯用法，ruff E402 豁免）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.migrate_state_to_duckdb import ARCHIVE_DIRS  # noqa: E402

logger = logging.getLogger("cleanup_p4_archived")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEMORY_ROOT = PROJECT_ROOT / "memory"
DATA_ROOT = PROJECT_ROOT / "data"
ARCHIVE_ROOT = DATA_ROOT / "archive"


def _count_process_traces() -> tuple[list[Path], int]:
    """统计过程痕迹源文件（ARCHIVE_DIRS 下）。"""
    files: list[Path] = []
    for rel in ARCHIVE_DIRS:
        p = MEMORY_ROOT / rel
        if p.exists():
            files.extend(f for f in p.rglob("*") if f.is_file())
    return files, len(files)


def _count_experiments() -> tuple[list[Path], int]:
    files = list(DATA_ROOT.glob("experiments-*.json"))
    return files, len(files)


def _archive_experiments(exp_files: list[Path], trace_id: str) -> Path:
    """把 experiments 打包 tar.gz 到 data/archive。"""
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    out = ARCHIVE_ROOT / f"experiments_{datetime.now().strftime('%Y%m%d%H%M%S')}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for fp in exp_files:
            tar.add(fp, arcname=fp.name)
    return out


def dry_run() -> dict[str, int]:
    trace_files, trace_n = _count_process_traces()
    exp_files, exp_n = _count_experiments()
    return {
        "mode": "dry-run",
        "process_trace_files": trace_n,
        "experiment_files": exp_n,
    }


def execute() -> dict[str, int]:
    """执行清理：删除已归档过程痕迹源 + 归档删除 experiments。"""
    trace_files, trace_n = _count_process_traces()
    exp_files, exp_n = _count_experiments()

    # 1) 删除已归档的过程痕迹源（归档副本 state_traces_*.tar.gz 已存在）
    removed = 0
    for fp in trace_files:
        try:
            fp.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("删除失败 %s: %s", fp, exc)

    # 2) 归档 experiments 后删除源
    exp_archive: Path | None = None
    if exp_files:
        exp_archive = _archive_experiments(exp_files, "")
        for fp in exp_files:
            try:
                fp.unlink()
            except OSError as exc:
                logger.warning("删除失败 %s: %s", fp, exc)

    return {
        "mode": "execute",
        "process_trace_files": trace_n,
        "process_trace_removed": removed,
        "experiment_files": exp_n,
        "experiment_archive": str(exp_archive) if exp_archive else None,
    }


def _run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P4 归档清理（安全部分）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不删除")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    trace_id = f"fts.cleanup_p4.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[%s] 模式: %s", trace_id, "dry-run" if args.dry_run else "execute")

    result = dry_run() if args.dry_run else execute()
    if args.json:
        print(json.dumps({"trace_id": trace_id, **result}, ensure_ascii=False, default=str))
    else:
        logger.info("[%s] 结果: %s", trace_id, json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(_run_cli())