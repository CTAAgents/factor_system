#!/usr/bin/env python3
"""
FTS 环境变量管理修复脚本

自动检查并修复以下问题：
1. python-dotenv 是否在 pyproject.toml 依赖中声明
2. .env.example 是否包含所有必需变量且未被注释

用法:
    python scripts/fix_env_management.py          # 检查并报告
    python scripts/fix_env_management.py --fix    # 自动修复
    python scripts/fix_env_management.py --check  # 仅检查（返回状态码）
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 检查项 ────────────────────────────────────────────────────

REQUIRED_ENV_VARS: dict[str, str] = {
    "OPENAI_API_KEY": "sk-your-deepseek-api-key-here",
    "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
    "OPENAI_MODEL": "deepseek-chat",
}

RECOMMENDED_ENV_VARS: dict[str, str] = {
    # LLM 后端选择器
    "FTS_LLM_BACKEND": "openai",
    # Anthropic 配置
    "ANTHROPIC_MODEL": "claude-sonnet-4-20250514",
    # FTS 路径配置
    "FTS_MEMORY_DIR": "memory",
    "FTS_ELITE_DIR": "memory/knowledge/factors/stocks_elite",
    "FTS_FUTURES_ELITE_DIR": "memory/knowledge/factors/futures_elite",
    "FTS_DEFAULT_MARKET": "futures",
    "FTS_MAX_WORKERS": "4",
    "FTS_LOG_LEVEL": "INFO",
    "FTS_LOG_FILE": "",
    "FTS_CONFIG_FILE": "config/settings.yaml",
    "FTS_EVOLUTION_MODE": "hybrid",
}

DOTENV_DEP_SPEC = "python-dotenv>=1.0"


def check_pyproject_dotenv() -> tuple[bool, str]:
    """检查 pyproject.toml 中是否声明了 python-dotenv 依赖。"""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return False, f"❌ 未找到 {pyproject}"

    content = pyproject.read_text(encoding="utf-8")

    # 检查 dependencies 部分
    dep_pattern = re.compile(r'^\s*"python-dotenv', re.MULTILINE)
    if dep_pattern.search(content):
        return True, "✅ python-dotenv 已在 pyproject.toml 依赖中声明"

    # 检查 optional-dependencies 部分
    opt_dep_pattern = re.compile(r'^\s*"python-dotenv', re.MULTILINE)
    if opt_dep_pattern.search(content):
        return True, "✅ python-dotenv 已在 pyproject.toml optional-dependencies 中声明"

    return False, f"❌ python-dotenv 未在 pyproject.toml 中声明（需要添加 {DOTENV_DEP_SPEC} 到 dependencies）"


def fix_pyproject_dotenv() -> bool:
    """自动在 pyproject.toml 的 dependencies 中添加 python-dotenv。"""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")

    # 找到 dependencies 列表的末尾
    dep_end = content.rfind('"]', 0, content.find("]", content.find("[project.optional-dependencies]")))
    if dep_end == -1:
        # fallback: 找第一个 ] 结束的 dependencies
        dep_start = content.find("dependencies = [")
        if dep_start == -1:
            print("❌ 无法定位 dependencies 起始位置，pyproject.toml 格式异常")
            return False
        dep_end = content.find("]", dep_start)
        if dep_end == -1:
            return False

    # 在最后一个 " 前插入
    insert_pos = content.rfind('"', 0, dep_end)
    if insert_pos == -1:
        return False

    new_content = content[: insert_pos + 1] + f',\n    "{DOTENV_DEP_SPEC}"' + content[insert_pos + 1 :]
    pyproject.write_text(new_content, encoding="utf-8")
    print(f"✅ 已添加 {DOTENV_DEP_SPEC} 到 pyproject.toml dependencies")
    return True


def check_env_example() -> list[dict]:
    """检查 .env.example 是否包含所有必需变量。"""
    env_example = PROJECT_ROOT / ".env.example"
    issues: list[dict] = []

    if not env_example.exists():
        issues.append({"type": "missing", "detail": "❌ .env.example 文件不存在"})
        return issues

    content = env_example.read_text(encoding="utf-8")

    # 检查必需变量是否存在且未被注释
    for var, default_value in REQUIRED_ENV_VARS.items():
        # 查找非注释行中的变量
        uncommented = re.search(rf"^{var}=", content, re.MULTILINE)
        commented = re.search(rf"^#\s*{var}=", content, re.MULTILINE)

        if not uncommented and not commented:
            issues.append({"type": "missing", "var": var, "detail": f"❌ {var} 缺失"})
        elif not uncommented and commented:
            issues.append({"type": "commented", "var": var, "detail": f"⚠️ {var} 被注释（# {var}=...）"})

    # 检查推荐变量是否存在（注释或未注释均可）
    for var, default_value in RECOMMENDED_ENV_VARS.items():
        uncommented = re.search(rf"^{var}=", content, re.MULTILINE)
        commented = re.search(rf"^#\s*{var}=", content, re.MULTILINE)
        if not uncommented and not commented:
            issues.append({"type": "missing_optional", "var": var, "detail": f"ℹ️ {var} 缺失（可选，建议添加）"})
        elif not uncommented and commented:
            # 注释状态的可选变量是可接受的
            pass

    if not issues:
        issues.append({"type": "ok", "detail": "✅ .env.example 检查通过，包含所有必需变量"})

    return issues


def fix_env_example() -> bool:
    """自动修复 .env.example（取消注释缺失的必需变量，添加缺失的变量）。"""
    env_example = PROJECT_ROOT / ".env.example"
    content = env_example.read_text(encoding="utf-8")
    lines = content.split("\n")
    modified = False

    # 1. 取消注释必需变量
    for var in REQUIRED_ENV_VARS:
        for i, line in enumerate(lines):
            if re.match(rf"^#\s*{var}=", line):
                lines[i] = line.lstrip("# ").strip()
                modified = True
                print(f"✅ 已取消注释 {var}")

    # 2. 添加缺失的推荐变量（在 FTS 配置段中）
    for i, line in enumerate(lines):
        if "# ── FTS 配置（可选，以下为默认值） ──" in line:
            existing_vars = set()
            # 收集 FTS 配置段已有的变量名
            for j in range(i + 1, len(lines)):
                if (
                    lines[j].startswith("# ──")
                    or lines[j].startswith("\n")
                    or (j < len(lines) - 1 and lines[j].strip() == "")
                ):
                    break
                m = re.match(r"^([A-Z_]+)=", lines[j])
                if m:
                    existing_vars.add(m.group(1))

            # 插入缺失的变量
            insert_pos = j
            added = []
            for var, default_value in RECOMMENDED_ENV_VARS.items():
                if var not in existing_vars:
                    lines.insert(insert_pos, f"{var}={default_value}")
                    insert_pos += 1
                    added.append(var)

            if added:
                modified = True
                print(f"✅ 已添加缺失变量: {', '.join(added)}")
            break

    if modified:
        env_example.write_text("\n".join(lines), encoding="utf-8")
        print("✅ .env.example 已更新")
    else:
        print("✅ .env.example 无需修改")

    return modified


def main() -> int:
    parser = argparse.ArgumentParser(description="FTS 环境变量管理修复脚本")
    parser.add_argument("--fix", action="store_true", help="自动修复检测到的问题")
    parser.add_argument("--check", action="store_true", help="仅检查（不输出详细信息），返回 0=全部通过")
    args = parser.parse_args()

    exit_code = 0

    # ── 检查项 1: python-dotenv 依赖 ──
    dep_ok, dep_msg = check_pyproject_dotenv()
    if args.check:
        if not dep_ok:
            exit_code = 1
    else:
        print(f"\n{'=' * 60}")
        print("【检查项 1】python-dotenv 依赖")
        print(f"{'=' * 60}")
        print(f"  {dep_msg}")

    if not dep_ok and args.fix:
        if fix_pyproject_dotenv():
            # 重新检查
            dep_ok, dep_msg = check_pyproject_dotenv()
            print(f"  → 修复后: {dep_msg}")
        else:
            print("  ❌ 自动修复失败，请手动编辑 pyproject.toml")

    # ── 检查项 2: .env.example ──
    env_issues = check_env_example()
    if args.check:
        if any(i["type"] in ("missing", "commented") for i in env_issues):
            exit_code = 1
    else:
        print(f"\n{'=' * 60}")
        print("【检查项 2】.env.example 完整性")
        print(f"{'=' * 60}")
        for issue in env_issues:
            print(f"  {issue['detail']}")

    if args.fix:
        has_actionable = any(i["type"] in ("commented", "missing_optional") for i in env_issues)
        if has_actionable:
            fix_env_example()
        else:
            print("\n  ✅ .env.example 无需修复")

    # ── 汇总 ──
    if args.check:
        return exit_code

    all_ok = dep_ok and all(i["type"] == "ok" for i in env_issues)
    print(f"\n{'=' * 60}")
    if all_ok:
        print("🎉 环境变量管理全部通过检查")
    else:
        print("⚠️ 存在未修复的问题，请使用 --fix 参数自动修复或手动处理")
    print(f"{'=' * 60}\n")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
