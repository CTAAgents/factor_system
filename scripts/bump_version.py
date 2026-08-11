#!/usr/bin/env python3
"""
FTS 统一版本号 bump 工具（v2.101.0+ 发布里程碑制）

用法:
    python scripts/bump_version.py --type patch --message "..."         # 执行发布 bump
    python scripts/bump_version.py --type minor --message "..." --force # 同日重复 bump（追加当日条目）
    python scripts/bump_version.py --check                              # 检查今日是否已 bump（exit 1 表示已 bump）
    python scripts/bump_version.py --peek                               # 打印当前版本与建议下一版本

规则（HARNESS §5.8 修订 v2.101.0）:
- 版本号仅代表"可交付发布里程碑"；日常开发（GAP 实现/测试/文档/数据修复）只在
  docs/harness/07-operations.md 追加变更记录，不 bump 版本号
- 同一天最多 bump 一次；同日重复 bump 默认拒绝，--force 跳过（追加到当日已有条目）
- bump 流程：pyproject.toml version → 07-operations.md 版本历史 → 全部文档版本头
"""

import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
OPERATIONS = PROJECT_ROOT / "docs" / "harness" / "07-operations.md"
UPDATE_DOC_VERSIONS = PROJECT_ROOT / "scripts" / "update_doc_versions.py"

VERSION_LINE_RE = re.compile(r'^version\s*=\s*"([\d.]+)"')
HISTORY_ENTRY_RE = re.compile(r"^\|\s*\*\*v[\d.]+\*\*\s*\|\s*\*\*(\d{4}-\d{2}-\d{2})\*\*")


def _read_lines(path: Path) -> list[str]:
    """读取文件并保留原始换行符（CRLF/LF）。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.readlines()


def _write_lines(path: Path, lines: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)


def get_current_version() -> str:
    for line in _read_lines(PYPROJECT):
        m = VERSION_LINE_RE.match(line.strip())
        if m:
            return m.group(1)
    sys.exit(f"❌ 无法从 pyproject.toml 解析版本号: {PYPROJECT}")


def bump_version(current: str, bump_type: str) -> str:
    major, minor, patch = (int(x) for x in current.split("."))
    if bump_type == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump_type == "minor":
        minor, patch = minor + 1, 0
    else:  # patch
        patch += 1
    return f"{major}.{minor}.{patch}"


def today() -> str:
    return _dt.date.today().strftime("%Y-%m-%d")


def latest_history_date() -> str | None:
    """返回 07-operations.md 版本历史最新一条的日期（表头下第一行）。"""
    for line in _read_lines(OPERATIONS):
        m = HISTORY_ENTRY_RE.match(line.strip())
        if m:
            return m.group(1)
    return None


def _escaped(msg: str) -> str:
    return msg.replace("|", "\\|")


def update_pyproject(new_version: str) -> None:
    lines = _read_lines(PYPROJECT)
    for i, line in enumerate(lines):
        if VERSION_LINE_RE.match(line.strip()):
            eol = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = f'version = "{new_version}"{eol}'
            break
    _write_lines(PYPROJECT, lines)


def append_history(new_version: str, msg: str) -> None:
    lines = _read_lines(OPERATIONS)
    eol = "\r\n" if any(ln.endswith("\r\n") for ln in lines) else "\n"
    idx = -1
    for i, line in enumerate(lines):
        if line.lstrip().startswith("|:"):
            idx = i + 1  # 表头分隔行之后为第一条版本记录，新条目插到最前
            break
    if idx < 0:
        sys.exit("❌ 无法定位 07-operations.md 版本历史表头，请人工检查格式。")
    entry = f"| **v{new_version}** | **{today()}** | **{_escaped(msg)}** |** |{eol}"
    lines.insert(idx, entry)
    _write_lines(OPERATIONS, lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="FTS 统一版本号 bump 工具（发布里程碑制）")
    parser.add_argument("--type", choices=["patch", "minor", "major"], default="patch")
    parser.add_argument("--message", default="", help="本次发布的变更说明（写入 07-operations.md）")
    parser.add_argument("--force", action="store_true", help="同日重复 bump 时跳过单日护栏")
    parser.add_argument("--check", action="store_true", help="检查今日是否已 bump（exit 1 表示已 bump）")
    parser.add_argument("--peek", action="store_true", help="打印当前版本与建议下一版本")
    args = parser.parse_args()

    current = get_current_version()

    if args.peek:
        print(f"当前版本: v{current}")
        print(f"下次 patch: v{bump_version(current, 'patch')}")
        print(f"下次 minor: v{bump_version(current, 'minor')}")
        print(f"下次 major: v{bump_version(current, 'major')}")
        return

    last_date = latest_history_date()
    if args.check:
        bumped = last_date == today()
        print(f"最新版本条目日期: {last_date or '无'} | 今日: {today()} | 今日已 bump: {bumped}")
        sys.exit(1 if bumped else 0)

    if last_date == today() and not args.force:
        print(f"❌ 今日（{today()}）已存在版本条目，同一天最多 bump 一次。")
        print("   - 若为并发会话补充，请将变更追加到当日已有条目，不产生新版本号")
        print("   - 若确需新版本，请显式使用 --force（不推荐）")
        sys.exit(1)

    if not args.message:
        parser.error("--message 必填：发布 bump 必须提供变更说明")

    new_version = bump_version(current, args.type)
    if last_date == today() and args.force:
        print(f"⚠️ 强制同日 bump（当日已有 {last_date} 条目，追加新版本 v{new_version}）")

    update_pyproject(new_version)
    append_history(new_version, args.message)
    print(f"✅ pyproject.toml: v{current} → v{new_version}")
    print(f"✅ 07-operations.md 已追加 v{new_version} 条目")

    subprocess.run(
        [sys.executable, str(UPDATE_DOC_VERSIONS), "--apply"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    print(f"\n🎉 发布 bump 完成: v{current} → v{new_version}")


if __name__ == "__main__":
    main()
