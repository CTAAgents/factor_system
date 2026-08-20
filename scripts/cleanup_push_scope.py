"""FTS 推送范围治理工具 — 清理一次性探索脚本 + 验证推送安全。

用法:
  python scripts/cleanup_push_scope.py prune              # dry-run：列出将被移出 git 跟踪的 _ 脚本
  python scripts/cleanup_push_scope.py prune --apply      # 实际执行 git rm --cached（保留本地文件，不删除工作区）
  python scripts/cleanup_push_scope.py verify             # 验证：扫描 git 跟踪中的禁止推送项

参考: .gitignore 的 "Push safety" 段 + docs/harness/plans/58-push-governance-plan.md
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 禁止推送的路径前缀（与 .gitignore "Push safety" 段对齐；seeds/ 为 L1 配置层应推送，不在禁止列）
FORBIDDEN_PREFIXES: tuple = (
    "data/", "memory/", "logs/", ".rhi/",
    "reports/", "output/", "signals/",
)
# 禁止推送的扩展名
FORBIDDEN_SUFFIXES: tuple = (
    ".duckdb", ".db", ".db-wal", ".db-shm",
    ".log", ".key", ".pem", ".p12", ".jks", ".crt", ".pyc",
)
# 禁止推送的关键词
FORBIDDEN_KEYWORDS: tuple = ("debug_llm_response_", "credentials")
# 允许的例外（占位文件 / 配置模板）
WHITELIST: set = {
    "memory/meta_loop/.gitkeep",
    "memory/portfolio/.gitkeep",
    ".env.example",
}


def _git(*args: str) -> List[str]:
    """执行 git 命令并返回按行拆分输出。"""
    result = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        sys.exit(f"[git error] git {' '.join(args)} 失败: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _is_forbidden(path: str) -> bool:
    """判断 git 跟踪路径是否属于禁止推送范围。"""
    if path in WHITELIST:
        return False
    if path == ".env" or path.startswith(".env."):
        return True
    if path.startswith(FORBIDDEN_PREFIXES):
        return True
    if path.endswith(FORBIDDEN_SUFFIXES):
        return True
    if any(k in path for k in FORBIDDEN_KEYWORDS):
        return True
    return False


def _tracked_scripts() -> List[str]:
    """返回 scripts/ 下 '_' 开头且已被 git 跟踪的脚本（相对路径，正斜杠统一）。"""
    tracked = {Path(p).as_posix() for p in _git("ls-files", "scripts/")}
    return sorted(
        p.relative_to(PROJECT_ROOT).as_posix()
        for p in (PROJECT_ROOT / "scripts").glob("_*.py")
        if p.relative_to(PROJECT_ROOT).as_posix() in tracked
    )


def cmd_prune(args: argparse.Namespace) -> int:
    """清理 scripts/ 下 '_' 开头的一次性探索脚本（仅移出跟踪，保留本地文件）。"""
    strays = _tracked_scripts()
    if not strays:
        print("ℹ️  没有需要清理的 '_' 开头脚本")
        return 0
    print(f"📦 发现 {len(strays)} 个 '_' 开头探索脚本被 git 跟踪：")
    for f in strays:
        print(f"  · {f}")
    if not args.apply:
        print("\n[DRY-RUN] 未实际执行。加 --apply 执行 git rm --cached（保留本地文件）。")
        return 0
    _git("rm", "--cached", *strays)
    print(f"\n✅ 已移出 git 跟踪 {len(strays)} 个脚本（本地文件保留）")
    print("   ⚠️  记得 git commit 使删除生效")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """验证推送范围：扫描 git 跟踪中的禁止推送项。"""
    files = _git("ls-files")
    violations = [f for f in files if _is_forbidden(f)]
    strays = _tracked_scripts()
    print(f"🔍 扫描 git 跟踪 {len(files)} 个文件")
    if violations:
        print(f"❌ 发现 {len(violations)} 个禁止推送项：")
        for v in violations:
            print(f"  · {v}")
        return 1
    print("✅ 禁止推送项: 0（范围安全）")
    if strays:
        print(f"⚠️  仍有 {len(strays)} 个 '_' 探索脚本被跟踪（建议 prune 清理）：")
        for s in strays:
            print(f"  · {s}")
    else:
        print("✅ '_' 探索脚本: 无残留")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FTS 推送范围治理工具")
    sub = parser.add_subparsers(dest="command", required=True)
    p_prune = sub.add_parser("prune", help="清理 '_' 开头探索脚本（dry-run 默认）")
    p_prune.add_argument("--apply", action="store_true", help="实际执行 git rm --cached")
    p_prune.set_defaults(func=cmd_prune)
    p_verify = sub.add_parser("verify", help="验证推送范围安全")
    p_verify.set_defaults(func=cmd_verify)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
