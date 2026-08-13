"""scripts/sync_core_to_stock.py — 共享内核单向同步（主系统 → fts-stock）。

32-stock-extraction-plan.md P6: 共享内核主体留在主系统，股票新项目
拷贝初始副本 + hash 单向同步。只允许主系统 → 新项目，禁止反向漂移。

同步范围（无市场形状的共享内核）:
  fts_core/ 概念映射到主系统 fts/ 下的共享模块:
    - fts/core/contracts.py（逻辑契约，含 OHLCVBase/FusionMeta）
    - fts/data_sources/ 薄封装（tdx_local/akshare 基础源、aggregator/fusion 骨架）
    - fts/factor_engine/expr_dsl/（DSL 与算子库，市场无关）
    - fts/store/（存储 registry/state_db）

用法:
    python scripts/sync_core_to_stock.py [--check]   # --check 仅对比 hash 不写入

输出:
    d:/Programs/fts-stock/fts_core/.core_hash  — 上游文件 hash 清单
    [--check] 差异清单（不同步）
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent
DST_ROOT = Path(r"d:\Programs\fts-stock")

# 共享内核模块（相对主系统 fts/ 的路径）
CORE_MODULES: tuple[str, ...] = (
    "core/contracts.py",
    "core/enums.py",
    "data_sources/tdx_local_source.py",
    "data_sources/akshare_minute_source.py",
    "data_sources/aggregator.py",
    "data_sources/fusion.py",
    "data_sources/base.py",
    "factor_engine/expr_dsl/",
    "store/registry.py",
    "store/state_db.py",
)

HASH_FILE = "fts_core/.core_hash"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(src_root: Path, rel_path: str) -> dict[str, str]:
    """收集模块内全部文件的相对路径 → hash。"""
    src = src_root / "fts" / rel_path
    out: dict[str, str] = {}
    if src.is_file():
        out[rel_path] = file_hash(src)
    elif src.is_dir():
        for p in sorted(src.rglob("*.py")):
            out[str(p.relative_to(src_root / "fts")).replace("\\", "/")] = file_hash(p)
    return out


def main() -> int:
    check_only = "--check" in sys.argv

    # 1. 汇总上游 hash
    upstream: dict[str, str] = {}
    for mod in CORE_MODULES:
        upstream.update(collect(SRC_ROOT, mod))

    # 2. 读取新项目侧现有 hash（若存在）
    dst_hash = DST_ROOT / HASH_FILE
    existing: dict[str, str] = {}
    if dst_hash.exists():
        existing = json.loads(dst_hash.read_text(encoding="utf-8"))

    # 3. 对比
    new_files = {k for k in upstream if k not in existing or existing[k] != upstream[k]}
    deleted = {k for k in existing if k not in upstream}

    print(f"[sync] 上游共享内核文件数: {len(upstream)}")
    print(f"[sync] 需新增/更新: {len(new_files)} | 需删除: {len(deleted)}")

    if check_only:
        for f in sorted(new_files)[:20]:
            print(f"  [DIFF] {f}")
        for f in sorted(deleted):
            print(f"  [DEL]  {f}")
        return 0 if not new_files and not deleted else 1

    # 4. 同步写入（纯 Python 文件操作）
    import shutil

    for rel, _h in upstream.items():
        src = SRC_ROOT / "fts" / rel
        dst = DST_ROOT / "fts_core" / rel
        if not dst.parent.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            continue
        shutil.copy2(src, dst)

    # 5. 写入 hash 清单
    dst_hash.parent.mkdir(parents=True, exist_ok=True)
    dst_hash.write_text(json.dumps(upstream, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[sync] 同步完成 → {DST_ROOT / 'fts_core'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
