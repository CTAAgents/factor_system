#!/usr/bin/env python3
"""
FTS 统一版本号 bump 工具（v2.103.0+ SemVer build 段制）

用法:
    python scripts/bump_version.py --build --message "..."              # 日常修改：build 段 +1
    python scripts/bump_version.py --type patch --message "..."         # 里程碑发布：patch bump（build 清零）
    python scripts/bump_version.py --type minor --message "..." --force # 同日重复里程碑 bump（追加当日条目）
    python scripts/bump_version.py --check                              # 检查今日是否已里程碑 bump（exit 1 表示已 bump）
    python scripts/bump_version.py --peek                               # 打印当前版本与建议下一版本

规则（HARNESS §5.8 修订，SemVer build 段制）:
- 版本号 = 里程碑版本 + build 段：x.y.z[+N]（如 2.103.0+42）
- 日常开发（GAP 实现/测试/文档/数据修复）→ --build：build 段 +1，并在
  docs/harness/07-operations.md 追加版本历史记录
- 里程碑发布（可交付）→ --type patch/minor/major：正式版本 +1 且 build 段清零
- 单日护栏仅约束里程碑 bump（一天最多一次）；build bump 不限次
- bump 流程：pyproject.toml version → README 版本徽章 → 07-operations.md 版本历史 → 全部文档版本头
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
README = PROJECT_ROOT / "README.md"
UPDATE_DOC_VERSIONS = PROJECT_ROOT / "scripts" / "update_doc_versions.py"

VERSION_LINE_RE = re.compile(r'^version\s*=\s*"([\d.]+(?:\+\d+)?)"')
HISTORY_ENTRY_RE = re.compile(r"^\|\s*\*\*v[\d.]+\*\*\s*\|\s*\*\*(\d{4}-\d{2}-\d{2})\*\*")
README_VERSION_RE = re.compile(r"(badge/version-)[\d.]+(?:%2B\d+)?")


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


def _parse_version(current: str) -> tuple[int, int, int, int | None]:
    """解析 x.y.z[+N] → (major, minor, patch, build|None)。"""
    base, _, build_str = current.partition("+")
    major, minor, patch = (int(x) for x in base.split("."))
    build = int(build_str) if build_str else None
    return major, minor, patch, build


def bump_version(current: str, bump_type: str) -> str:
    """里程碑 bump：patch/minor/major +1，build 段清零（SemVer 标准）。"""
    major, minor, patch, _ = _parse_version(current)
    if bump_type == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump_type == "minor":
        minor, patch = minor + 1, 0
    else:  # patch
        patch += 1
    return f"{major}.{minor}.{patch}"


def bump_build(current: str) -> str:
    """日常 build bump：build 段 +1（无 build 则从 1 起），里程碑版本不变。"""
    major, minor, patch, build = _parse_version(current)
    return f"{major}.{minor}.{patch}+{(build or 0) + 1}"


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


def update_readme_version(new_version: str) -> None:
    """同步 README.md 版本徽章（shields.io 路径中 + 需编码为 %2B）。"""
    lines = _read_lines(README)
    encoded = new_version.replace("+", "%2B")
    for i, line in enumerate(lines):
        m = README_VERSION_RE.search(line)
        if m:
            lines[i] = line[: m.start()] + f"badge/version-{encoded}" + line[m.end():]
            _write_lines(README, lines)
            return
    sys.exit(f"❌ 无法定位 README.md 版本徽章: {README}")


def main() -> None:
    parser = argparse.ArgumentParser(description="FTS 统一版本号 bump 工具（SemVer build 段制）")
    parser.add_argument("--build", action="store_true", help="日常修改：build 段 +1（不受单日护栏约束）")
    parser.add_argument("--type", choices=["patch", "minor", "major"], default="patch")
    parser.add_argument("--message", default="", help="本次变更说明（写入 07-operations.md）")
    parser.add_argument("--force", action="store_true", help="同日重复里程碑 bump 时跳过单日护栏")
    parser.add_argument("--check", action="store_true", help="检查今日是否已里程碑 bump（exit 1 表示已 bump）")
    parser.add_argument("--peek", action="store_true", help="打印当前版本与建议下一版本")
    args = parser.parse_args()

    current = get_current_version()

    if args.peek:
        print(f"当前版本: v{current}")
        print(f"下次 build: v{bump_build(current)}")
        print(f"下次 patch: v{bump_version(current, 'patch')}")
        print(f"下次 minor: v{bump_version(current, 'minor')}")
        print(f"下次 major: v{bump_version(current, 'major')}")
        return

    last_date = latest_history_date()
    if args.check:
        bumped = last_date == today()
        print(f"最新里程碑版本条目日期: {last_date or '无'} | 今日: {today()} | 今日已里程碑 bump: {bumped}")
        sys.exit(1 if bumped else 0)

    if not args.message:
        parser.error("--message 必填：bump 必须提供变更说明")

    if args.build:
        # build bump：不受单日护栏约束，里程碑版本保持不变
        new_version = bump_build(current)
        update_pyproject(new_version)
        append_history(new_version, args.message)
        update_readme_version(new_version)
        print(f"✅ pyproject.toml: v{current} → v{new_version}")
        print("✅ README.md 版本徽章已同步")
        print(f"✅ 07-operations.md 已追加 v{new_version} 条目")
    else:
        if last_date == today() and not args.force:
            print(f"❌ 今日（{today()}）已存在里程碑版本条目，同一天最多 bump 一次。")
            print("   - 日常修改请使用 --build（build 段 +1，不受护栏约束）")
            print("   - 若确需新里程碑版本，请显式使用 --force（不推荐）")
            sys.exit(1)

        new_version = bump_version(current, args.type)
        if last_date == today() and args.force:
            print(f"⚠️ 强制同日里程碑 bump（当日已有 {last_date} 条目，追加新版本 v{new_version}）")

        update_pyproject(new_version)
        append_history(new_version, args.message)
        update_readme_version(new_version)
        print(f"✅ pyproject.toml: v{current} → v{new_version}")
        print("✅ README.md 版本徽章已同步")
        print(f"✅ 07-operations.md 已追加 v{new_version} 条目")

    subprocess.run(
        [sys.executable, str(UPDATE_DOC_VERSIONS), "--apply"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    print(f"\n🎉 版本 bump 完成: v{current} → v{new_version}")


if __name__ == "__main__":
    main()
