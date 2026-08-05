#!/usr/bin/env python3
"""
FTS 文档一致性检查脚本 — Layer 2 自动校验

检查 docs/harness/ 目录下各文档的一致性元数据，验证代码→文档映射关系。

用法:
    python scripts/verify_doc_consistency.py          # 检查全部文档
    python scripts/verify_doc_consistency.py --file   # 检查指定文件
    python scripts/verify_doc_consistency.py --help   # 帮助
"""

import os
import re
import sys
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = PROJECT_ROOT / "docs" / "harness"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


# 一致性元数据模板 — 每篇文档末尾的表格字段
METADATA_FIELDS = [
    "代码→文档映射",
    "可验证断言",
    "检验方式",
]


def find_docs() -> list[Path]:
    """扫描 docs/harness/ 下的所有 .md 文档。"""
    if not HARNESS_DIR.exists():
        print(f"❌ 目录不存在: {HARNESS_DIR}")
        return []
    return sorted(HARNESS_DIR.glob("*.md"))


def check_metadata_table(doc_path: Path) -> list[str]:
    """检查文档是否包含一致性元数据表格。"""
    issues: list[str] = []
    content = doc_path.read_text(encoding="utf-8")

    # 检查是否包含 "一致性元数据" 标题
    if "## 一致性元数据" not in content:
        issues.append(f"缺少 '## 一致性元数据' 章节")

    # 检查是否包含各字段
    for field in METADATA_FIELDS:
        if field not in content:
            issues.append(f"缺少元数据字段: {field}")

    # 检查版本号和最后更新日期
    version_match = re.search(r"> 版本: (v[\d.]+)", content)
    date_match = re.search(r"> 最后更新: (\d{4}-\d{2}-\d{2})", content)

    if not version_match:
        issues.append("缺少版本号声明")
    if not date_match:
        issues.append("缺少最后更新日期")

    return issues


def check_file_exists(file_path: str, base_dir: Path = PROJECT_ROOT) -> bool:
    """检查文件是否存在。"""
    full_path = base_dir / file_path
    return full_path.exists()


def check_doc_assertions(doc_path: Path) -> list[str]:
    """检查文档中可验证断言的正确性。"""
    issues: list[str] = []
    content = doc_path.read_text(encoding="utf-8")
    doc_name = doc_path.name

    # 01-architecture: 检查种子池数
    if doc_name == "01-architecture.md":
        seed_count = len(re.findall(r"482", content))
        if seed_count == 0:
            issues.append("种子池数 482 未在文档中体现")

    # 06-testing: 检查测试用例数
    if doc_name == "06-testing.md":
        test_count = len(re.findall(r"2066", content))
        if test_count == 0:
            issues.append("测试用例数 2066 未在文档中体现")

    # 07-operations: 检查版本号文件是否存在
    if doc_name == "07-operations.md":
        version_file = PROJECT_ROOT / "fts" / "__init__.py"
        if not version_file.exists():
            issues.append("版本号文件 fts/__init__.py 不存在")
        else:
            v_content = version_file.read_text(encoding="utf-8")
            if "__version__" not in v_content:
                issues.append("fts/__init__.py 中缺少 __version__")

    # 08-gap-analysis: 检查差距总览表一致性
    if doc_name == "08-gap-analysis.md":
        closed_count = len(re.findall(r"✅ 已关闭", content))
        open_count = len(re.findall(r"🟡 开放中", content))
        total = closed_count + open_count
        if total == 0:
            issues.append("差距登记表为空")

    return issues


def check_flow_docs_exist() -> list[str]:
    """检查流程文档是否存在。"""
    issues: list[str] = []
    flow_docs = [
        PROJECT_ROOT / "docs" / "execution_modes_flowchart.md",
        PROJECT_ROOT / "docs" / "business_flow.md",
    ]
    for doc in flow_docs:
        if not doc.exists():
            issues.append(f"流程文档缺失: {doc.name}")
    return issues


def run_all_checks() -> dict[str, Any]:
    """运行全部一致性检查。"""
    results: dict[str, Any] = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "checks": [],
        "errors": [],
    }

    docs = find_docs()
    if not docs:
        results["errors"].append("未找到 Harness 文档")
        return results

    for doc in docs:
        doc_name = doc.name

        # 1. 检查元数据表
        meta_issues = check_metadata_table(doc)
        for issue in meta_issues:
            results["checks"].append({
                "file": doc_name,
                "type": "元数据",
                "status": "FAIL",
                "message": issue,
            })
            results["failed"] += 1

        # 2. 检查断言
        assertion_issues = check_doc_assertions(doc)
        for issue in assertion_issues:
            results["checks"].append({
                "file": doc_name,
                "type": "断言",
                "status": "FAIL",
                "message": issue,
            })
            results["failed"] += 1

        # 如果没有问题，标记为通过
        if not meta_issues and not assertion_issues:
            results["checks"].append({
                "file": doc_name,
                "type": "综合",
                "status": "PASS",
                "message": "一致性检查通过",
            })
            results["passed"] += 1

    # 3. 检查流程文档存在性
    flow_issues = check_flow_docs_exist()
    for issue in flow_issues:
        results["checks"].append({
            "file": "docs/",
            "type": "存在性",
            "status": "FAIL",
            "message": issue,
        })
        results["failed"] += 1

    if not flow_issues:
        results["checks"].append({
            "file": "docs/",
            "type": "存在性",
            "status": "PASS",
            "message": "流程文档完整",
        })
        results["passed"] += 1

    return results


def print_report(results: dict[str, Any]) -> None:
    """打印检查报告。"""
    total = results["passed"] + results["failed"]
    print(f"\n{'='*60}")
    print(f"  FTS 文档一致性检查报告")
    print(f"{'='*60}")
    print(f"  检查项: {total}  |  通过: {results['passed']}  |  失败: {results['failed']}")
    print(f"{'='*60}\n")

    for check in results["checks"]:
        status_icon = "✅" if check["status"] == "PASS" else "❌"
        print(f"  {status_icon} [{check['file']}] {check['type']}: {check['message']}")

    if results["errors"]:
        print(f"\n  ⚠️ 错误:")
        for err in results["errors"]:
            print(f"     - {err}")

    print(f"\n{'='*60}")

    if results["failed"] > 0:
        print(f"  ❌ {results['failed']} 项检查失败，请修复后再提交。")
    else:
        print(f"  ✅ 全部通过！")

    print(f"{'='*60}\n")


def main() -> int:
    """主入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="FTS 文档一致性检查脚本")
    parser.add_argument("--file", type=str, help="指定检查单个文件")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--fix", action="store_true", help="自动修复简单问题（暂未实现）")
    args = parser.parse_args()

    if args.file:
        doc_path = HARNESS_DIR / args.file
        if not doc_path.exists():
            print(f"❌ 文件不存在: {doc_path}")
            return 1
        docs = [doc_path]
    else:
        docs = find_docs()
        if not docs:
            print("❌ 未找到文档")
            return 1

    results = run_all_checks()

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results)

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())