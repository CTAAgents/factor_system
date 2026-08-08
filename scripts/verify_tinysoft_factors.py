"""
scripts/verify_tinysoft_factors.py — 验证天软因子种子文件

检查:
  1. YAML 格式正确可加载
  2. 所有因子包含必填字段
  3. 因子名称唯一
  4. code 字符串可编译为 Python 语法
  5. 与现有种子因子无名称冲突
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import yaml

REQUIRED_FIELDS = [
    "name", "description", "market", "code", "params",
    "input_fields", "lookback", "output_type", "frequency",
    "economic_logic",
]

REQUIRED_LOGIC_FIELDS = [
    "theory", "behavioral", "microstructure", "institutional", "narrative",
]


def validate_tinysoft_yaml(path: str) -> list[str]:
    errors = []

    # 1. 加载 YAML
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        errors.append("YAML 根节点不是字典")
        return errors

    if data.get("family") != "tinysoft":
        errors.append(f"family 应为 'tinysoft'，实际为 {data.get('family')}")
    if data.get("version") != "1.0":
        errors.append(f"version 应为 '1.0'，实际为 {data.get('version')}")
    if data.get("market") != "futures":
        errors.append(f"market 应为 'futures'，实际为 {data.get('market')}")

    factors = data.get("factors", [])
    if not isinstance(factors, list):
        errors.append("factors 不是列表")
        return errors

    if len(factors) != 27:
        errors.append(f"因子数量应为 27，实际为 {len(factors)}")

    names = set()
    for i, f in enumerate(factors):
        # 检查必填字段
        for field in REQUIRED_FIELDS:
            if field not in f:
                errors.append(f"因子 #{i}: 缺少必填字段 '{field}'")

        # 检查名称唯一性
        name = f.get("name", f"<missing #{i}>")
        if name in names:
            errors.append(f"因子名称重复: {name}")
        names.add(name)

        # 检查字段顺序（部分关键字段必须在 economic_logic 之前）
        keys = list(f.keys())
        if "economic_logic" in keys:
            el_idx = keys.index("economic_logic")
            for field in ["name", "market", "code", "params", "input_fields", "lookback"]:
                if field in keys and keys.index(field) > el_idx:
                    errors.append(f"因子 {name}: '{field}' 在 economic_logic 之后，顺序异常")

        # 检查 economic_logic 子字段
        logic = f.get("economic_logic", {})
        if isinstance(logic, dict):
            for lf in REQUIRED_LOGIC_FIELDS:
                if lf not in logic:
                    errors.append(f"因子 {name}: economic_logic 缺少 '{lf}'")

        # 检查 code 可编译
        # code 格式为 \n    def factor_program(...):\n        body...
        # 在 exec 上下文中有效，但需要包裹为完整模块来验证
        code = f.get("code", "")
        if code:
            # 去除首尾空白，包裹在函数中检查语法
            stripped = code.strip()
            try:
                ast.parse(stripped)
            except SyntaxError:
                # 尝试包裹在 exec 上下文
                try:
                    ast.parse(f"def _wrapper():\n{stripped}")
                except SyntaxError as e:
                    errors.append(f"因子 {name}: code 存在语法错误: {e}")

        # 检查 output_type
        otype = f.get("output_type", "")
        if otype not in ("signal", "rank", "raw"):
            errors.append(f"因子 {name}: output_type 应为 signal/rank/raw，实际为 {otype}")

        # 检查 frequency
        freq = f.get("frequency", "")
        if freq not in ("daily", "weekly", "monthly", "intraday"):
            errors.append(f"因子 {name}: frequency 应为 daily/weekly/monthly/intraday，实际为 {freq}")

    # 检查与现有种子因子名称冲突
    existing_seeds_dir = Path(path).parent
    existing_names = set()
    for yf in existing_seeds_dir.glob("*.yaml"):
        if yf.name == "tinysoft.yaml":
            continue
        with open(yf, "r", encoding="utf-8") as f:
            existing_data = yaml.safe_load(f)
        for ef in existing_data.get("factors", []):
            existing_names.add(ef.get("name"))

    conflicts = names & existing_names
    if conflicts:
        errors.append(f"与现有种子因子名称冲突: {conflicts}")

    return errors


def main():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds", "futures", "tinysoft.yaml")

    if not os.path.exists(path):
        print(f"❌ 文件不存在: {path}")
        sys.exit(1)

    errors = validate_tinysoft_yaml(path)

    if errors:
        print(f"❌ 验证失败: 发现 {len(errors)} 个问题")
        for e in errors:
            print(f"   - {e}")
        sys.exit(1)
    else:
        print(f"✅ 验证通过: {path}")
        print(f"   27 个因子全部通过必填字段检查")
        print(f"   所有因子名称唯一")
        print(f"   所有 code 语法正确")
        print(f"   与现有种子因子无名称冲突")
        print(f"   economic_logic 字段完整")


if __name__ == "__main__":
    main()