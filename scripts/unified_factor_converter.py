"""
scripts/unified_factor_converter.py — 统一因子转换器 + 验证器

整合 P0-P2 提取的所有因子，执行：
  1. 去重（跨所有 YAML 种子文件）
  2. 语法验证（code 可编译、必填字段完整）
  3. 格式一致性检查（与现有种子文件对齐）
  4. 生成验证报告

用法:
    python scripts/unified_factor_converter.py --check-all    # 检查所有种子文件
    python scripts/unified_factor_converter.py --verify <file>  # 验证单个文件
    python scripts/unified_factor_converter.py --report       # 生成种子因子统计报告
    python scripts/unified_factor_converter.py --dedup        # 检查跨文件重复
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


def _seeds_dir(market: str = "futures") -> Path:
    """获取指定市场的种子因子目录。"""
    return Path(__file__).resolve().parent.parent / "seeds" / market


REQUIRED_FIELDS = [
    "name",
    "description",
    "market",
    "code",
    "params",
    "input_fields",
    "lookback",
    "output_type",
    "frequency",
    "economic_logic",
]

REQUIRED_LOGIC_FIELDS = [
    "theory",
    "behavioral",
    "microstructure",
    "institutional",
    "narrative",
]

VALID_OUTPUT_TYPES = {"signal", "rank", "raw"}
VALID_FREQUENCIES = {"daily", "weekly", "monthly", "intraday"}
VALID_MARKETS = {"futures", "stock"}


def load_all_factors(market: str = "futures") -> dict[str, list[dict[str, Any]]]:
    """加载所有种子文件，返回 {filename: [factors]}。"""
    seeds_path = _seeds_dir(market)
    result: dict[str, list[dict[str, Any]]] = {}
    for yf in sorted(seeds_path.glob("*.yaml")):
        with open(yf, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        factors = data.get("factors", [])
        if factors:
            result[yf.name] = factors
    return result


def check_duplicates(all_factors: dict[str, list[dict[str, Any]]]) -> list[str]:
    """检查跨文件因子名称重复。"""
    errors: list[str] = []
    name_to_files: dict[str, list[str]] = {}

    for fname, factors in all_factors.items():
        for f in factors:
            name = f.get("name", "")
            if name not in name_to_files:
                name_to_files[name] = []
            name_to_files[name].append(fname)

    for name, files in sorted(name_to_files.items()):
        if len(files) > 1:
            errors.append(f"⚠️  因子 '{name}' 出现在多个文件中: {', '.join(files)}")

    return errors


def validate_factor(f: dict[str, Any], source: str) -> list[str]:
    """验证单个因子的完整性。"""
    errors: list[str] = []
    name = f.get("name", "<unknown>")

    # 1. 必填字段
    for field in REQUIRED_FIELDS:
        if field not in f:
            errors.append(f"[{source}/{name}] 缺少必填字段: {field}")

    # 2. 字段有效性
    if "market" in f and f["market"] not in VALID_MARKETS:
        errors.append(f"[{source}/{name}] market 无效: {f['market']}")

    if "output_type" in f and f["output_type"] not in VALID_OUTPUT_TYPES:
        errors.append(f"[{source}/{name}] output_type 无效: {f['output_type']}")

    if "frequency" in f and f["frequency"] not in VALID_FREQUENCIES:
        errors.append(f"[{source}/{name}] frequency 无效: {f['frequency']}")

    # 3. economic_logic 子字段
    logic = f.get("economic_logic", {})
    if isinstance(logic, dict):
        for lf in REQUIRED_LOGIC_FIELDS:
            if lf not in logic:
                errors.append(f"[{source}/{name}] economic_logic 缺少 '{lf}'")

    # 4. code 语法检查
    code = f.get("code", "")
    if code:
        stripped = code.strip()
        valid = False
        try:
            ast.parse(stripped)
            valid = True
        except SyntaxError:
            try:
                ast.parse(f"def _w():\n{stripped}")
                valid = True
            except SyntaxError:
                pass
        if not valid:
            errors.append(f"[{source}/{name}] code 语法错误")

    # 5. 字段顺序检查（name 必须在 economic_logic 之前）
    keys = list(f.keys())
    if "economic_logic" in keys:
        el_idx = keys.index("economic_logic")
        for field in ["name", "market", "code", "params", "input_fields", "lookback"]:
            if field in keys and keys.index(field) > el_idx:
                errors.append(f"[{source}/{name}] '{field}' 在 economic_logic 之后，顺序异常")

    # 6. lookback 合理性
    lb = f.get("lookback", 0)
    if isinstance(lb, (int, float)) and lb < 1:
        errors.append(f"[{source}/{name}] lookback 应为正数，实际为 {lb}")

    # 7. params 合理性
    params = f.get("params", {})
    if not isinstance(params, dict):
        errors.append(f"[{source}/{name}] params 应为字典")

    # 8. input_fields 合理性
    input_fields = f.get("input_fields", [])
    if not isinstance(input_fields, list) or len(input_fields) == 0:
        errors.append(f"[{source}/{name}] input_fields 应为非空列表")

    return errors


def validate_all(all_factors: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    """验证所有文件中的所有因子。"""
    results: dict[str, list[str]] = {}
    for fname, factors in all_factors.items():
        for f in factors:
            name = f.get("name", "<unknown>")
            key = f"{fname}/{name}"
            errors = validate_factor(f, fname)
            if errors:
                results[key] = errors
    return results


def generate_report(all_factors: dict[str, list[dict[str, Any]]], market: str = "futures") -> str:
    """生成种子因子统计报告。"""
    market_label = "期货" if market == "futures" else "股票"
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append(f"  FTS {market_label}种子因子统计报告")
    lines.append("=" * 60)
    lines.append("")

    total = 0
    family_counts: Counter = Counter()
    source_counts: Counter = Counter()

    for fname, factors in all_factors.items():
        source_counts[fname] = len(factors)
        total += len(factors)
        for f in factors:
            family = f.get("name", "").split("_")[1] if "_" in f.get("name", "") else "other"
            family_counts[family] += 1

    lines.append(f"📊 总因子数: {total}")
    lines.append(f"📂 种子文件数: {len(all_factors)}")
    lines.append("")

    lines.append("按文件分布:")
    lines.append("-" * 40)
    for fname, count in sorted(source_counts.items()):
        lines.append(f"  {fname:35s} {count:3d} 个因子")
    lines.append("")

    lines.append("按家族分布(Top 15):")
    lines.append("-" * 40)
    for family, count in family_counts.most_common(15):
        pct = count / total * 100
        bar = "█" * int(pct / 2)
        lines.append(f"  {family:20s} {count:3d} ({pct:5.1f}%) {bar}")

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="统一因子转换器 + 验证器")
    parser.add_argument(
        "--market", type=str, default="futures", choices=["futures", "stock"], help="市场类型（默认: futures）"
    )
    parser.add_argument("--check-all", action="store_true", help="检查所有种子文件")
    parser.add_argument("--verify", type=str, default=None, help="验证单个种子文件")
    parser.add_argument("--report", action="store_true", help="生成种子因子统计报告")
    parser.add_argument("--dedup", action="store_true", help="检查跨文件重复")
    args = parser.parse_args()

    if not any([args.check_all, args.verify, args.report, args.dedup]):
        parser.print_help()
        return

    if args.dedup:
        all_factors = load_all_factors(args.market)
        errors = check_duplicates(all_factors)
        if errors:
            print("❌ 发现重复:")
            for e in errors:
                print(f"  {e}")
        else:
            print("✅ 无跨文件重复")

    if args.verify:
        path = Path(args.verify)
        if not path.exists():
            print(f"❌ 文件不存在: {path}")
            return
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        factors = data.get("factors", [])
        print(f"📂 验证文件: {path.name}")
        print(f"   因子数: {len(factors)}")
        print()
        all_errors = [validate_factor(f, path.name) for f in factors]
        flat_errors = [e for errors in all_errors for e in errors]
        if flat_errors:
            print(f"❌ 发现 {len(flat_errors)} 个问题:")
            for e in flat_errors:
                print(f"  {e}")
        else:
            print("✅ 全部通过")

    if args.check_all:
        all_factors = load_all_factors(args.market)
        print(f"📂 共加载 {len(all_factors)} 个种子文件")
        print()

        errors = validate_all(all_factors)
        if errors:
            print(f"❌ 发现 {len(errors)} 个因子存在问题:")
            for key, errs in sorted(errors.items()):
                print(f"  {key}:")
                for e in errs:
                    print(f"    - {e}")
        else:
            print("✅ 所有因子验证通过")

        print()
        dup_errors = check_duplicates(all_factors)
        if dup_errors:
            for e in dup_errors:
                print(f"  {e}")
        else:
            print("✅ 无跨文件重复")

    if args.report:
        all_factors = load_all_factors(args.market)
        report = generate_report(all_factors, args.market)
        print(report)


if __name__ == "__main__":
    main()
