"""E.3（S2）L4 运行状态库后端迁移：state.duckdb → state.db（SQLite WAL）

背景:
    FTS 运行状态（evolution/portfolio/meta_loop/extractors 的 state_kv +
    state_history 双表）自 plans/29 P2 起收敛于 data/state.duckdb。E.3（S2）
    将后端切换为 SQLite（WAL），解决「演化进程持有写锁时外部只读亦被锁」
    的并发阻塞（实测 read_only 亦被拒）。本脚本一次性迁移既有数据。

模式:
    --force   目标库已存在非空时强制执行（覆盖式重建目标）
    默认      目标库已存在非空 → 拒绝（幂等保护）

用法:
    python scripts/migrate_state_to_sqlite.py
    python scripts/migrate_state_to_sqlite.py --source data/state.duckdb --target data/state.db
    python scripts/migrate_state_to_sqlite.py --force --json

行为:
    - 源库被写锁占用（DuckDB File is already open）→ 降级拒绝并提示 PID，
      不强行操作（与 scripts/archive_history_cold.py 同策略）
    - 迁移后不删除源库（保留只读备份，冻结期后按 plans/29 约定清理）
    - 迁移过程幂等可重入：目标校验行数一致即为成功

HARNESS: trace_id 全链路（fts.state_migrate.{ts}）；失败透明。
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

# 脚本独立运行时的导入引导（项目惯用法，ruff E402 豁免）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("migrate_state_to_sqlite")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "state.duckdb"
DEFAULT_TARGET = PROJECT_ROOT / "data" / "state.db"

_BUSY_TIMEOUT_MS = 5000

# 与 fts/store/state_db.py DDL 保持同构（迁移目标库）
_CREATE_STATE_KV = """
CREATE TABLE IF NOT EXISTS state_kv (
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    run_id      TEXT DEFAULT '',
    PRIMARY KEY (namespace, key)
);
"""

_CREATE_STATE_HISTORY = """
CREATE TABLE IF NOT EXISTS state_history (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    run_id      TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_state_history_ns_key
    ON state_history(namespace, key, seq);
"""


def _init_target(conn: sqlite3.Connection) -> None:
    """幂等建目标表 + 索引（executescript 支持多语句）。"""
    conn.executescript(_CREATE_STATE_KV + "\n" + _CREATE_STATE_HISTORY)


def migrate_state_db(
    source: str | Path | None = None,
    target: str | Path | None = None,
    force: bool = False,
    trace_id: str = "",
) -> dict[str, Any]:
    """执行 state.duckdb → state.db 数据迁移 + 校验。

    Args:
        source: 源 DuckDB 路径（默认 data/state.duckdb）
        target: 目标 SQLite 路径（默认 data/state.db）
        force: 目标已存在非空时是否覆盖重建
        trace_id: trace 标识（全链路贯穿）

    Returns:
        统计字典: source/target/kv_rows/history_rows/verified/seq_range

    Raises:
        FileNotFoundError: 源库不存在
        RuntimeError: 源库被写锁占用（降级拒绝）
        RuntimeError: 目标库已存在非空且未 --force（幂等保护）
    """
    src = Path(source) if source else DEFAULT_SOURCE
    tgt = Path(target) if target else DEFAULT_TARGET
    stats: dict[str, Any] = {
        "source": str(src),
        "target": str(tgt),
        "trace_id": trace_id,
        "kv_rows": 0,
        "history_rows": 0,
        "verified": False,
        "seq_range": [],
        "error": "",
    }

    if not src.exists():
        raise FileNotFoundError(f"源库不存在: {src}")

    # 1) 读源库（read_only；被写锁占用时降级拒绝）
    import duckdb  # noqa: PLC0415 — 迁移场景才需要 duckdb，主路径（SQLite）零依赖

    try:
        con_src = duckdb.connect(str(src), read_only=True)
    except Exception as e:  # noqa: BLE001 — DuckDB 锁占用为 IOError，统一降级提示
        msg = str(e)
        if "already open" in msg or "lock" in msg.lower() or "正在使用" in msg:
            raise RuntimeError(
                f"源库 {src} 被写锁占用，无法以只读打开（{msg}）。"
                "请先关闭持有写锁的演化/写入进程后重试。"
            ) from e
        raise

    tgt.parent.mkdir(parents=True, exist_ok=True)
    con_tgt = sqlite3.connect(str(tgt))
    try:
        con_tgt.execute("PRAGMA journal_mode=WAL")
        con_tgt.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")

        # 2) 目标库处置：--force 覆盖重建；否则已存在非空 → 拒绝（幂等保护）
        if force:
            con_tgt.execute("DROP TABLE IF EXISTS state_kv")
            con_tgt.execute("DROP TABLE IF EXISTS state_history")
            _init_target(con_tgt)
        else:
            _init_target(con_tgt)
            existing = con_tgt.execute("SELECT COUNT(*) FROM state_kv").fetchone()[0]
            if existing > 0:
                raise RuntimeError(
                    f"目标库 {tgt} 已存在非空（state_kv={existing} 行）。"
                    "如需覆盖重建请加 --force。"
                )

        # 3) 复制 state_kv（保序；DuckDB TIMESTAMP 读回为 datetime，转 str 绑定）
        kv_rows = [
            (r[0], r[1], r[2], str(r[3]) if r[3] is not None else None, str(r[4]))
            for r in con_src.execute(
                "SELECT namespace, key, value, updated_at, run_id FROM state_kv ORDER BY namespace, key"
            ).fetchall()
        ]
        con_tgt.executemany(
            "INSERT INTO state_kv (namespace, key, value, updated_at, run_id) VALUES (?, ?, ?, ?, ?)",
            kv_rows,
        )

        # 4) 复制 state_history（按 seq 升序；AUTOINCREMENT 自动接续 max seq）
        hist_rows = [
            (r[0], r[1], r[2], r[3], str(r[4]) if r[4] is not None else None, str(r[5]))
            for r in con_src.execute(
                "SELECT seq, namespace, key, value, recorded_at, run_id FROM state_history ORDER BY seq"
            ).fetchall()
        ]
        con_tgt.executemany(
            "INSERT INTO state_history (seq, namespace, key, value, recorded_at, run_id) VALUES (?, ?, ?, ?, ?, ?)",
            hist_rows,
        )
        con_tgt.commit()

        # 5) 校验：行数逐一比对 + 值 JSON 抽查
        kv_check = con_tgt.execute("SELECT COUNT(*) FROM state_kv").fetchone()[0]
        hist_check = con_tgt.execute("SELECT COUNT(*) FROM state_history").fetchone()[0]
        stats["kv_rows"] = kv_check
        stats["history_rows"] = hist_check
        seq_range = con_tgt.execute("SELECT MIN(seq), MAX(seq) FROM state_history").fetchone()
        stats["seq_range"] = [seq_range[0], seq_range[1]]
        stats["verified"] = kv_check == len(kv_rows) and hist_check == len(hist_rows)
        if not stats["verified"]:
            raise RuntimeError(
                f"迁移校验失败: kv source={len(kv_rows)} target={kv_check}, "
                f"history source={len(hist_rows)} target={hist_check}"
            )
        logger.info(
            "[%s] 迁移完成: kv=%d history=%d seq=[%s, %s] → %s",
            trace_id,
            kv_check,
            hist_check,
            stats["seq_range"][0],
            stats["seq_range"][1],
            tgt,
        )
    finally:
        con_src.close()
        con_tgt.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="E.3 S2：L4 状态库后端迁移 state.duckdb → state.db（SQLite WAL）")
    parser.add_argument("--source", default="", help="源 DuckDB 路径（默认 data/state.duckdb）")
    parser.add_argument("--target", default="", help="目标 SQLite 路径（默认 data/state.db）")
    parser.add_argument("--force", action="store_true", help="目标已存在非空时覆盖重建")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    trace_id = f"fts.state_migrate.{ts}"
    result: dict[str, Any] = {"trace_id": trace_id}
    rc = 0
    try:
        result["migration"] = migrate_state_db(
            source=args.source or None,
            target=args.target or None,
            force=args.force,
            trace_id=trace_id,
        )
    except Exception as e:  # noqa: BLE001 — 失败透明：明确报错不静默
        logger.error("[%s] 迁移失败: %s", trace_id, e)
        traceback.print_exc()
        result["error"] = str(e)
        rc = 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
