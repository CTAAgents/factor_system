#!/usr/bin/env python3
"""FTS 补丁工具 — 原子写 + 回读断言（解决编辑工具对部分文件写入不可靠问题）。

读文件 → 逐条替换 → 原子写（tmp + os.replace）→ 回读断言（new 已存在 / old 不再残留）。

用法:
    python scripts/apply_patch.py --file <path> --old <文本> --new <文本> [--count 1]
    python scripts/apply_patch.py --patch-file <json>   # [{"file","old","new","count"}...]

行为:
    - 保留原文件换行风格（CRLF / LF 检测）
    - 每处替换默认 1 次；--count N 替换前 N 次
    - 写后回读断言：new 必须存在；old 残留次数 = 原次数 - count（0 表示全清）
    - 任一断言失败退出码 1（此前已写入条目保留，逐条原子）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def apply_one(path: str | Path, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    raw = p.open("r", encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    # 统一换行再替换，避免 CRLF 下 \n 匹配失败
    content = raw.replace(nl, "\n")
    old_n = content.count(old)
    if old_n == 0:
        raise AssertionError(f"[{p}] 未找到目标文本: {old[:60]!r}")
    if old == new:
        raise AssertionError(f"[{p}] old 与 new 相同，拒绝空操作")
    content = content.replace(old, new, count)
    content = content.replace("\n", nl)
    # 原子写：tmp + os.replace
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.open("w", encoding="utf-8", newline="").write(content)
    os.replace(str(tmp), str(p))
    # 回读断言（统一换行后比对，兼容 CRLF；期望残留 = 原次数 - count + new 内 old 次数）
    verify = p.open("r", encoding="utf-8", newline="").read().replace(nl, "\n")
    if new not in verify:
        raise AssertionError(f"[{p}] 回读失败：new 未写入 -> {new[:60]!r}")
    remain = verify.count(old)
    expect_remain = old_n - count + new.count(old) * count
    if remain != expect_remain:
        raise AssertionError(
            f"[{p}] 回读断言失败：old 残留 {remain} 处（期望 {expect_remain}）"
        )
    print(f"OK [{p.name}] {old_n}→{remain} 处替换: {old[:40]!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="FTS 原子补丁工具（写后回读断言）")
    parser.add_argument("--file", type=str, help="目标文件（配合 --old/--new）")
    parser.add_argument("--old", type=str, help="被替换文本")
    parser.add_argument("--new", type=str, help="替换文本")
    parser.add_argument("--count", type=int, default=1, help="替换次数（默认 1）")
    parser.add_argument("--patch-file", type=str, help="JSON 补丁清单 [{file,old,new,count}]")
    args = parser.parse_args()

    patches: list[dict[str, Any]] = []
    if args.patch_file:
        patches = json.loads(Path(args.patch_file).read_text(encoding="utf-8"))
    elif args.file and args.old is not None and args.new is not None:
        patches = [{"file": args.file, "old": args.old, "new": args.new, "count": args.count}]
    else:
        parser.print_help()
        return 2

    failed = 0
    for pt in patches:
        try:
            apply_one(pt["file"], pt["old"], pt["new"], int(pt.get("count", 1)))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL [{pt.get('file')}]: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
