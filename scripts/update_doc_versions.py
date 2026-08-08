#!/usr/bin/env python3
"""
FTS 文档版本号统一更新脚本 — 自动同步所有 Harness 文档版本头

用法:
    python scripts/update_doc_versions.py                          # 扫描并显示差异
    python scripts/update_doc_versions.py --apply                  # 执行更新
    python scripts/update_doc_versions.py --apply --file 01-arch   # 更新单个文件
    python scripts/update_doc_versions.py --check                  # CI 检查模式（不一致则 exit 1）
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = PROJECT_ROOT / "docs" / "harness"


def get_version_from_pyproject() -> str:
    """从 pyproject.toml 读取当前版本号（兼容 Python 3.10 / 3.11+）。"""
    try:
        import tomllib as _toml
    except ImportError:
        import tomli as _toml

    pyproject = PROJECT_ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = _toml.load(f)
    return data["project"]["version"]


def collect_docs() -> list[Path]:
    """收集所有需要同步版本号的文档（排除历史验收文档）。"""
    docs: list[Path] = []
    glob_patterns = [
        "*.md",                          # 核心文档
        "plans/*.md",                    # 计划文档
        "design/*.md",                   # 设计文档
    ]
    for pattern in glob_patterns:
        docs.extend(HARNESS_DIR.glob(pattern))
    return sorted(set(docs))


VERSION_HEADER_RE = re.compile(r"> 版本: v[\d.]+")
VERSION_HEADER_TEMPLATE = "> 版本: v{version}"


def scan_docs(docs: list[Path], target_version: str) -> list[tuple[Path, str, str]]:
    """扫描文档，返回 (path, old_version, new_version) 列表。"""
    changes: list[tuple[Path, str, str]] = []
    for doc in docs:
        content = doc.read_text(encoding="utf-8")
        match = VERSION_HEADER_RE.search(content)
        if match:
            old = match.group(0)
            new = VERSION_HEADER_TEMPLATE.format(version=target_version)
            if old != new:
                changes.append((doc, old, new))
        else:
            changes.append((doc, "(无版本头)", VERSION_HEADER_TEMPLATE.format(version=target_version)))
    return changes


def apply_changes(changes: list[tuple[Path, str, str]]):
    """执行版本号替换。"""
    for doc, old, new in changes:
        content = doc.read_text(encoding="utf-8")
        if old == "(无版本头)":
            # 文件无版本头，在标题后插入
            # 查找第一个 # 标题行，在其后插入版本头
            lines = content.split("\n")
            insert_at = 0
            for i, line in enumerate(lines):
                if line.startswith("# ") and i < 3:  # 文件开头的标题
                    insert_at = i + 1
                    break
            # 确保插入后有空行
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            lines.insert(insert_at, "")
            lines.insert(insert_at + 1, new)
            lines.insert(insert_at + 2, "")
            content = "\n".join(lines)
        else:
            content = content.replace(old, new, 1)
        doc.write_text(content, encoding="utf-8")
        print(f"  ✅ {doc.name}: {old} → {new}")


def main():
    parser = argparse.ArgumentParser(description="FTS 文档版本号统一更新脚本")
    parser.add_argument("--apply", action="store_true", help="执行更新（默认仅扫描）")
    parser.add_argument("--file", type=str, help="指定单个文件（支持 glob 片段）")
    parser.add_argument("--check", action="store_true", help="CI 检查模式，不一致时 exit 1")
    args = parser.parse_args()

    target_version = get_version_from_pyproject()
    print(f"\n📌 pyproject.toml 版本: v{target_version}\n")

    docs = collect_docs()
    if args.file:
        docs = [d for d in docs if args.file in d.name]
        if not docs:
            print(f"❌ 未匹配到文件: {args.file}")
            sys.exit(1)

    changes = scan_docs(docs, target_version)

    if not changes:
        print("✅ 所有文档版本号均已一致，无需更新。")
        return

    print(f"📋 发现 {len(changes)} 个文档版本号不一致：\n")
    for doc, old, new in changes:
        print(f"  ⚠️  {doc.name}: {old} → {new}")

    if args.check:
        print(f"\n❌ {len(changes)} 个文档版本号不一致，请运行 --apply 修复。")
        sys.exit(1)

    if args.apply:
        print(f"\n🔄 执行更新...\n")
        apply_changes(changes)
        print(f"\n✅ 更新完成。")
    else:
        print(f"\n💡 使用 --apply 参数执行更新。")

    # 始终更新 pyproject.toml 和 fts/__init__.py 的一致性
    init_file = PROJECT_ROOT / "fts" / "__init__.py"
    fts_version = f"v{target_version}"
    print(f"\n🔍 fts/__init__.py 动态读取版本: {fts_version}")


if __name__ == "__main__":
    main()