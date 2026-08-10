"""
scripts/generate_jq_factors_yaml.py — 生成 jq_factors.yaml 种子因子文件

从 fts/factor_engine/seed_data/jq_factors.py 中的 JQ_DEFINITIONS，
生成 seeds/stock/jq_factors.yaml。

用法:
    python scripts/generate_jq_factors_yaml.py
"""

import sys
import os
import yaml
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入 JQ 因子定义
from fts.factor_engine.seed_data.jq_factors import JQ_DEFINITIONS


def _factor(**kwargs) -> dict[str, Any]:
    """按顺序构建因子字典，确保 YAML 字段顺序与现有种子文件一致。"""
    d = {}
    for k, v in kwargs.items():
        d[k] = v
    return d


def convert_to_yaml_format(defn: dict[str, Any]) -> dict[str, Any]:
    """将 JQ_DEFINITIONS 中的因子字典转换为 YAML 格式。"""
    kwargs = {
        "name": defn["name"],
        "description": defn["narrative"],
        "market": "stock",
        "expression": defn["expression"],
    }

    # 基本面因子有 field_defs 和 field_check（在 expression 和 input_fields 之间）
    if "field_defs" in defn:
        kwargs["field_defs"] = defn["field_defs"]
    if "field_check" in defn:
        kwargs["field_check"] = defn["field_check"]

    kwargs["input_fields"] = defn["input_fields"]
    kwargs["lookback"] = defn["lookback"]
    kwargs["output_type"] = "signal"
    kwargs["frequency"] = "daily"
    kwargs["economic_logic"] = {
        "theory": defn["theory"],
        "behavioral": defn["behavioral"],
        "microstructure": defn["microstructure"],
        "institutional": defn["institutional"],
        "narrative": defn["narrative"],
    }

    return _factor(**kwargs)


def main():
    yaml_data = _factor(
        family="jq",
        version="1.0",
        market="stock",
        factors=[convert_to_yaml_format(d) for d in JQ_DEFINITIONS],
    )

    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds", "stock", "jq_factors.yaml")

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"✅ 已生成 YAML 种子文件: {output_path}")
    print(f"   共 {len(JQ_DEFINITIONS)} 个因子")


if __name__ == "__main__":
    main()
