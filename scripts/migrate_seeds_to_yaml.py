"""migrate_seeds_to_yaml.py — 种子因子 YAML 迁移脚本

将旧格式（Python 硬编码）种子因子转换为 seeds/ 目录下的 YAML 文件。

数据源:
  1. seed_pool.py::_SEED_DEFINITIONS              → seeds/stock/builtin.yaml (9)
  2. seed_data_futures_full.py::_FUTURES_FULL_DEFINITIONS → seeds/futures/*.yaml (81)
  3. seed_data/wq101.py::WQ101_DEFINITIONS        → seeds/stock/wq101.yaml (101)
  4. seed_data/qlib158.py::QLIB158_DEFINITIONS    → seeds/stock/qlib158.yaml (158)
  5. seed_data/gtja191.py::GTJA191_DEFINITIONS    → seeds/stock/gtja191.yaml (191)
  6. seed_data/fundamental_seeds.py::FUNDAMENTAL_DEFINITIONS → seeds/stock/fundamental.yaml (23)

用法:
    python scripts/migrate_seeds_to_yaml.py              # 全量迁移（不覆盖）
    python scripts/migrate_seeds_to_yaml.py --force       # 全量迁移（覆盖现有 YAML）
    python scripts/migrate_seeds_to_yaml.py --verify      # 仅验证现有 YAML 完整性
    python scripts/migrate_seeds_to_yaml.py --source wq101  # 仅迁移指定来源

输出:
    seeds/
    ├── stock/
    │   ├── builtin.yaml
    │   ├── wq101.yaml
    │   ├── qlib158.yaml
    │   ├── gtja191.yaml
    │   └── fundamental.yaml
    └── futures/
        ├── momentum.yaml
        ├── term_structure.yaml
        ├── ... (14 个家族文件)
        └── operator_dict.yaml
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEEDS_DIR = PROJECT_ROOT / "seeds"

sys.path.insert(0, str(PROJECT_ROOT))


# ─── 期货家族映射 ────────────────────────────────────────────

FUTURES_FAMILY_MAP = {
    # ── 家族 1: 动量 (5) ──
    "fut_xsmom": "momentum",
    "fut_tsmom": "momentum",
    "fut_short_reversal": "momentum",
    "fut_composite_momentum": "momentum",
    "fut_basis_momentum": "momentum",
    # ── 家族 2: 期限结构 (3) ──
    "fut_roll_yield_carry": "term_structure",
    "fut_stable_term_structure": "term_structure",
    "fut_basis_factor": "term_structure",
    # ── 家族 3: 持仓/资金流 (3) ──
    "fut_open_interest_full": "position_flow",
    "fut_warehouse_receipt": "position_flow",
    "fut_hedge_pressure": "position_flow",
    # ── 家族 4: 流动性 (3) ──
    "fut_turnover": "liquidity",
    "fut_bid_ask_spread": "liquidity",
    "fut_amihud_full": "liquidity",
    # ── 家族 5: 高阶矩 (3) ──
    "fut_skewness_full": "higher_moments",
    "fut_upside_skewness": "higher_moments",
    "fut_kurtosis": "higher_moments",
    # ── 家族 6: 波动率 (2) ──
    "fut_cv": "volatility",
    "fut_downside_volatility": "volatility",
    # ── 家族 7: 基本面 (4) ──
    "fut_volume_price_corr_full": "fundamental",
    "fut_trend_strength": "fundamental",
    "fut_amplitude": "fundamental",
    "fut_mobile_big_data": "fundamental",
    # ── 家族 8: 拥挤度 (6) ──
    "fut_crowd_volume": "crowding",
    "fut_crowd_volatility": "crowding",
    "fut_crowd_turnover": "crowding",
    "fut_crowd_bias_volume": "crowding",
    "fut_crowd_bias_amount": "crowding",
    "fut_crowd_composite": "crowding",
    # ── 家族 9: Alpha 行为 (4) ──
    "fut_time_series_regression": "alpha_behavior",
    "fut_bias": "alpha_behavior",
    "fut_gp_alpha1": "alpha_behavior",
    "fut_ht_alpha": "alpha_behavior",
    # ── 家族 10: 高频 (6) ──
    "fut_hf_quote_imbalance": "high_frequency",
    "fut_hf_trade_imbalance": "high_frequency",
    "fut_hf_historical_return": "high_frequency",
    "fut_hf_turnover": "high_frequency",
    "fut_hf_spread": "high_frequency",
    "fut_hf_down_vol": "high_frequency",
    # ── 家族 11: 期权 (3) ──
    "fut_option_vol_term": "options",
    "fut_option_skew": "options",
    "fut_option_pcr": "options",
    # ── 家族 12: 市场状态 (8) ──
    "fut_macro_cpi": "market_regime",
    "fut_macro_interest_rate": "market_regime",
    "fut_macro_export": "market_regime",
    "fut_macro_us_bond": "market_regime",
    "fut_mkt_trend": "market_regime",
    "fut_mkt_speculation": "market_regime",
    "fut_mkt_rotation": "market_regime",
    "fut_mkt_concentration": "market_regime",
    # ── 家族 13: CTA 注册表 (7) ──
    "tsmom_5d": "cta_registry",
    "tsmom_22d": "cta_registry",
    "basis_level": "cta_registry",
    "volatility_annual": "cta_registry",
    "liquidity_ratio": "cta_registry",
    "long_term_reversal": "cta_registry",
    "oi_change_rate": "cta_registry",
    # ── 家族 14: 算子字典 (24) ──
    "seed_kbar_mid": "operator_dict",
    "seed_kbar_upper": "operator_dict",
    "seed_kbar_lower": "operator_dict",
    "seed_kbar_shift": "operator_dict",
    "seed_bull_bear": "operator_dict",
    "seed_argmax_close": "operator_dict",
    "seed_argmin_close": "operator_dict",
    "seed_vol_chg": "operator_dict",
    "seed_vwap_proxy_1": "operator_dict",
    "seed_vwap_proxy_2": "operator_dict",
    "seed_reversal_1d": "operator_dict",
    "seed_mom_5d": "operator_dict",
    "seed_mom_20d": "operator_dict",
    "seed_vol_5d": "operator_dict",
    "seed_vol_20d": "operator_dict",
    "seed_vol_ratio": "operator_dict",
    "seed_trend_slope": "operator_dict",
    "seed_trend_rsqr": "operator_dict",
    "seed_vp_corr": "operator_dict",
    "seed_vol_ratio_volume": "operator_dict",
    "seed_oi_chg": "operator_dict",
    "seed_oi_ret_confirm": "operator_dict",
    "seed_spread": "operator_dict",
    "seed_settle_bias": "operator_dict",
}

FUTURES_FAMILY_NAMES = {
    "momentum": "动量因子",
    "term_structure": "期限结构",
    "position_flow": "资金流向",
    "liquidity": "流动性",
    "higher_moments": "高阶矩",
    "volatility": "波动率",
    "fundamental": "基本面",
    "crowding": "拥挤度",
    "alpha_behavior": "Alpha行为",
    "high_frequency": "高频",
    "options": "期权",
    "market_regime": "市场状态",
    "cta_registry": "CTA注册表",
    "operator_dict": "算子字典",
}

# ─── 提取逻辑 ────────────────────────────────────────────────


def _family_for_futures(name: str) -> str:
    """根据因子名称推断期货家族。"""
    if name in FUTURES_FAMILY_MAP:
        return FUTURES_FAMILY_MAP[name]
    return "other"


def _classify_fundamental(name: str) -> str:
    """根据因子名称推断基本面子家族。"""
    if "val" in name:
        return "value"
    if "growth" in name:
        return "growth"
    if "quality" in name or "roe" in name or "roa" in name:
        return "quality"
    if "size" in name:
        return "size"
    if "macro" in name or "pmi" in name or "rate" in name:
        return "macro"
    return "fundamental"


def _to_dict(obj: Any) -> dict:
    """将对象或字典统一转为 dict。"""
    if isinstance(obj, dict):
        return obj
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    """从对象或字典获取属性。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def extract_builtin() -> list[dict[str, Any]]:
    """从 seed_pool.py 提取 9 个内置种子。"""
    from fts.factor_engine.seed_pool import _SEED_DEFINITIONS

    result = []
    for defn in _SEED_DEFINITIONS:
        sig = defn["signature"]
        eco = defn["economic_logic"]
        narrative = _get_attr(eco, "narrative", "")
        result.append(
            {
                "name": defn["name"],
                "description": narrative[:80] if narrative else "",
                "market": "stock",
                "params": defn.get("params", {}),
                "input_fields": _get_attr(sig, "input_fields", []),
                "lookback": _get_attr(sig, "lookback", 20),
                "output_type": _get_attr(sig, "output_type", "signal"),
                "frequency": _get_attr(sig, "frequency", "daily"),
                "economic_logic": {
                    "theory": _get_attr(eco, "theory", 4),
                    "behavioral": _get_attr(eco, "behavioral", 3),
                    "microstructure": _get_attr(eco, "microstructure", 3),
                    "institutional": _get_attr(eco, "institutional", 3),
                    "narrative": narrative,
                },
                "code": defn["code"],
            }
        )
    logger.info("  builtin: %d 个因子", len(result))
    return result


def extract_futures() -> dict[str, list[dict[str, Any]]]:
    """从 seed_data_futures_full.py 提取 81 个期货种子，按家族分组。"""
    from fts.factor_engine.seed_data_futures_full import _FUTURES_FULL_DEFINITIONS

    families: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for defn in _FUTURES_FULL_DEFINITIONS:
        sig = defn["signature"]
        eco = defn["economic_logic"]
        family = _family_for_futures(defn["name"])
        narrative = _get_attr(eco, "narrative", "")

        families[family].append(
            {
                "name": defn["name"],
                "description": narrative[:80] if narrative else "",
                "market": "futures",
                "params": defn.get("params", {}),
                "input_fields": _get_attr(sig, "input_fields", []),
                "lookback": _get_attr(sig, "lookback", 20),
                "output_type": _get_attr(sig, "output_type", "signal"),
                "frequency": _get_attr(sig, "frequency", "daily"),
                "economic_logic": {
                    "theory": _get_attr(eco, "theory", 4),
                    "behavioral": _get_attr(eco, "behavioral", 3),
                    "microstructure": _get_attr(eco, "microstructure", 3),
                    "institutional": _get_attr(eco, "institutional", 3),
                    "narrative": narrative,
                },
                "code": defn["code"],
            }
        )

    total = sum(len(v) for v in families.values())
    logger.info("  futures: %d 个因子, 分布在 %d 个家族", total, len(families))
    for fam, factors in sorted(families.items()):
        logger.info("    %s: %d", fam, len(factors))

    return dict(families)


def extract_expression_seeds(
    module_path: str,
    def_name: str,
    source_label: str,
) -> list[dict[str, Any]]:
    """提取表达式类因子（WQ101/Qlib158/GTJA191）。"""
    mod = importlib.import_module(module_path)
    definitions = getattr(mod, def_name)

    result = []
    for i, defn in enumerate(definitions):
        name = defn["name"]
        expression = defn["expression"]
        narrative = defn.get("narrative", f"{source_label} #{i + 1:03d}")

        from fts.factor_engine.seed_data.loader import _estimate_input_fields, _estimate_lookback

        input_fields = defn.get("input_fields") or _estimate_input_fields(expression)
        lookback = defn.get("lookback") or _estimate_lookback(expression)

        result.append(
            {
                "name": name,
                "description": narrative[:80],
                "market": "stock",
                "expression": expression,
                "input_fields": input_fields,
                "lookback": lookback,
                "output_type": "signal",
                "frequency": "daily",
                "economic_logic": {
                    "theory": defn.get("theory", 4),
                    "behavioral": defn.get("behavioral", 3),
                    "microstructure": defn.get("microstructure", 3),
                    "institutional": defn.get("institutional", 3),
                    "narrative": narrative,
                },
            }
        )

    logger.info("  %s: %d 个因子", source_label, len(result))
    return result


def extract_fundamental() -> list[dict[str, Any]]:
    """提取基本面因子。"""
    from fts.factor_engine.seed_data.fundamental_seeds import FUNDAMENTAL_DEFINITIONS

    result = []
    for defn in FUNDAMENTAL_DEFINITIONS:
        result.append(
            {
                "name": defn["name"],
                "description": defn.get("narrative", "")[:80],
                "market": "stock",
                "expression": defn["expression"],
                "field_defs": defn.get("field_defs", ""),
                "field_check": defn.get("field_check", ""),
                "input_fields": defn.get("input_fields", ["close"]),
                "lookback": defn.get("lookback", 1),
                "output_type": "signal",
                "frequency": "daily",
                "economic_logic": {
                    "theory": defn.get("theory", 4),
                    "behavioral": defn.get("behavioral", 3),
                    "microstructure": defn.get("microstructure", 3),
                    "institutional": defn.get("institutional", 3),
                    "narrative": defn.get("narrative", ""),
                },
            }
        )

    logger.info("  fundamental: %d 个因子", len(result))
    return result


# ─── YAML 生成 ────────────────────────────────────────────────


def generate_yaml_document(
    family: str,
    market: str,
    factors: list[dict[str, Any]],
    version: str = "1.0",
) -> dict[str, Any]:
    """生成 YAML 文档结构。"""
    yaml_factors = []
    for f in factors:
        yf = {
            "name": f["name"],
            "description": f.get("description", ""),
            "market": f.get("market", market),
        }

        if "code" in f:
            yf["code"] = f["code"]
        elif "expression" in f:
            yf["expression"] = f["expression"]

        if "field_defs" in f:
            yf["field_defs"] = f["field_defs"]
        if "field_check" in f:
            yf["field_check"] = f["field_check"]

        if f.get("params"):
            yf["params"] = f["params"]

        yf["input_fields"] = f.get("input_fields", ["close"])
        yf["lookback"] = f.get("lookback", 20)
        yf["output_type"] = f.get("output_type", "signal")
        yf["frequency"] = f.get("frequency", "daily")

        if f.get("economic_logic"):
            yf["economic_logic"] = f["economic_logic"]

        yaml_factors.append(yf)

    return {
        "family": family,
        "version": version,
        "market": market,
        "factors": yaml_factors,
    }


def write_yaml(path: Path, doc: dict[str, Any]) -> None:
    """写入 YAML 文件（保留多行字符串格式）。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    class _BlockStyleDumper(yaml.SafeDumper):
        pass

    def _str_representer(dumper, data):
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    _BlockStyleDumper.add_representer(str, _str_representer)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, Dumper=_BlockStyleDumper, allow_unicode=True, sort_keys=False, width=120)


# ─── 迁移主流程 ──────────────────────────────────────────────


def migrate(force: bool = False, sources: set[str] | None = None) -> dict[str, Any]:
    """执行全量迁移。"""
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    stock_dir = SEEDS_DIR / "stock"
    futures_dir = SEEDS_DIR / "futures"
    stock_dir.mkdir(exist_ok=True)
    futures_dir.mkdir(exist_ok=True)

    stats = {"files_written": 0, "factors_total": 0, "skipped": [], "errors": [], "cleaned": []}

    logger.info("=" * 60)
    logger.info("种子因子 YAML 迁移")
    logger.info("=" * 60)

    # 收集将生成的文件集合，用于清理 stale 文件
    planned_files: set[Path] = set()

    # ── Task 1: builtin (9) ──
    if not sources or "builtin" in sources:
        logger.info("\n📦 Task 1: 内置种子 (stock/builtin.yaml)")
        try:
            factors = extract_builtin()
            doc = generate_yaml_document("builtin", "stock", factors, "1.0")
            out_path = stock_dir / "builtin.yaml"
            planned_files.add(out_path)
            if force or not out_path.exists():
                write_yaml(out_path, doc)
                stats["files_written"] += 1
                stats["factors_total"] += len(factors)
                logger.info("  ✅ 写入 %s (%d 因子)", out_path.name, len(factors))
            else:
                stats["skipped"].append(str(out_path))
                logger.info("  ⏭️ 已存在，跳过 (--force 覆盖)")
        except Exception as e:
            stats["errors"].append(f"builtin: {e}")
            logger.error("  ❌ %s", e)

    # ── Task 2: futures (81, 分 14 家族) ──
    if not sources or "futures" in sources:
        logger.info("\n📦 Task 2: 期货种子 (futures/*.yaml)")
        try:
            families = extract_futures()
            for family, factors in sorted(families.items()):
                doc = generate_yaml_document(family, "futures", factors, "1.0")
                out_path = futures_dir / f"{family}.yaml"
                if force or not out_path.exists():
                    write_yaml(out_path, doc)
                    stats["files_written"] += 1
                    stats["factors_total"] += len(factors)
                    logger.info("  ✅ 写入 %s (%d 因子)", out_path.name, len(factors))
                else:
                    stats["skipped"].append(str(out_path))
                    logger.info("  ⏭️ %s 已存在，跳过", out_path.name)
        except Exception as e:
            stats["errors"].append(f"futures: {e}")
            logger.error("  ❌ %s", e)

    # ── Task 3: WQ101 (101) ──
    if not sources or "wq101" in sources:
        logger.info("\n📦 Task 3: WQ 101 Alpha (stock/wq101.yaml)")
        try:
            factors = extract_expression_seeds("fts.factor_engine.seed_data.wq101", "WQ101_DEFINITIONS", "WQ101")
            doc = generate_yaml_document("wq101", "stock", factors, "1.1")
            out_path = stock_dir / "wq101.yaml"
            if force or not out_path.exists():
                write_yaml(out_path, doc)
                stats["files_written"] += 1
                stats["factors_total"] += len(factors)
                logger.info("  ✅ 写入 %s (%d 因子)", out_path.name, len(factors))
            else:
                stats["skipped"].append(str(out_path))
                logger.info("  ⏭️ 已存在，跳过")
        except Exception as e:
            stats["errors"].append(f"wq101: {e}")
            logger.error("  ❌ %s", e)

    # ── Task 4: Qlib158 (158) ──
    if not sources or "qlib158" in sources:
        logger.info("\n📦 Task 4: Qlib 158 (stock/qlib158.yaml)")
        try:
            factors = extract_expression_seeds("fts.factor_engine.seed_data.qlib158", "QLIB158_DEFINITIONS", "Qlib158")
            doc = generate_yaml_document("qlib158", "stock", factors, "1.1")
            out_path = stock_dir / "qlib158.yaml"
            if force or not out_path.exists():
                write_yaml(out_path, doc)
                stats["files_written"] += 1
                stats["factors_total"] += len(factors)
                logger.info("  ✅ 写入 %s (%d 因子)", out_path.name, len(factors))
            else:
                stats["skipped"].append(str(out_path))
                logger.info("  ⏭️ 已存在，跳过")
        except Exception as e:
            stats["errors"].append(f"qlib158: {e}")
            logger.error("  ❌ %s", e)

    # ── Task 5: GTJA191 (191) ──
    if not sources or "gtja191" in sources:
        logger.info("\n📦 Task 5: 国泰君安 191 (stock/gtja191.yaml)")
        try:
            factors = extract_expression_seeds("fts.factor_engine.seed_data.gtja191", "GTJA191_DEFINITIONS", "GTJA191")
            doc = generate_yaml_document("gtja191", "stock", factors, "1.0")
            out_path = stock_dir / "gtja191.yaml"
            if force or not out_path.exists():
                write_yaml(out_path, doc)
                stats["files_written"] += 1
                stats["factors_total"] += len(factors)
                logger.info("  ✅ 写入 %s (%d 因子)", out_path.name, len(factors))
            else:
                stats["skipped"].append(str(out_path))
                logger.info("  ⏭️ 已存在，跳过")
        except Exception as e:
            stats["errors"].append(f"gtja191: {e}")
            logger.error("  ❌ %s", e)

    # ── Task 6: Fundamental (23) ──
    if not sources or "fundamental" in sources:
        logger.info("\n📦 Task 6: 基本面因子 (stock/fundamental.yaml)")
        try:
            factors = extract_fundamental()
            doc = generate_yaml_document("fundamental", "stock", factors, "1.0")
            out_path = stock_dir / "fundamental.yaml"
            if force or not out_path.exists():
                write_yaml(out_path, doc)
                stats["files_written"] += 1
                stats["factors_total"] += len(factors)
                logger.info("  ✅ 写入 %s (%d 因子)", out_path.name, len(factors))
            else:
                stats["skipped"].append(str(out_path))
                logger.info("  ⏭️ 已存在，跳过")
        except Exception as e:
            stats["errors"].append(f"fundamental: {e}")
            logger.error("  ❌ %s", e)

    # ── 清理 stale 文件 ──
    if force:
        logger.info("\n🧹 清理 stale 文件...")
        for check_dir in [stock_dir, futures_dir]:
            for existing in check_dir.glob("*.yaml"):
                if existing not in planned_files:
                    existing.unlink()
                    stats["cleaned"].append(str(existing))
                    logger.info("  🗑️  删除 %s", existing.name)

    # ── 汇总 ──
    logger.info("\n" + "=" * 60)
    logger.info("迁移完成")
    logger.info("=" * 60)
    logger.info("  写入文件: %d", stats["files_written"])
    logger.info("  因子总数: %d", stats["factors_total"])
    logger.info("  跳过文件: %d", len(stats["skipped"]))
    logger.info("  错误:     %d", len(stats["errors"]))

    if stats["skipped"]:
        logger.info("\n  跳过的文件:")
        for s in stats["skipped"]:
            logger.info("    - %s", s)

    if stats["errors"]:
        logger.info("\n  错误详情:")
        for e in stats["errors"]:
            logger.info("    - %s", e)

    return stats


# ─── 验证 ────────────────────────────────────────────────────


def verify() -> dict[str, Any]:
    """验证现有 YAML 文件的完整性。"""
    logger.info("=" * 60)
    logger.info("YAML 完整性验证")
    logger.info("=" * 60)

    report = {"total_files": 0, "total_factors": 0, "issues": []}

    yaml_files = sorted(SEEDS_DIR.rglob("*.yaml"))
    report["total_files"] = len(yaml_files)

    for yf in yaml_files:
        with open(yf, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            report["issues"].append(f"{yf.relative_to(SEEDS_DIR)}: 空文件")
            continue

        family = data.get("family", "?")
        version = data.get("version", "?")
        market = data.get("market", "?")
        factors = data.get("factors", [])

        file_issues = []
        for i, fac in enumerate(factors):
            if "name" not in fac:
                file_issues.append(f"  因子[{i}]: 缺少 name")
            if "code" not in fac and "expression" not in fac:
                file_issues.append(f"  因子[{fac.get('name', i)}]: 缺少 code 或 expression")

        if file_issues:
            report["issues"].append(f"{yf.relative_to(SEEDS_DIR)}: {len(file_issues)} 个问题")
            for fi in file_issues:
                report["issues"].append(fi)

        report["total_factors"] += len(factors)
        logger.info(
            "  %-40s  family=%-20s  v=%-4s  market=%-8s  factors=%d  %s",
            yf.relative_to(SEEDS_DIR).name,
            family,
            version,
            market,
            len(factors),
            "⚠️" if file_issues else "✅",
        )

    logger.info("\n" + "-" * 60)
    logger.info("  文件总数: %d", report["total_files"])
    logger.info("  因子总数: %d", report["total_factors"])
    logger.info("  问题数:   %d", len(report["issues"]))

    if report["issues"]:
        logger.info("\n  问题详情:")
        for issue in report["issues"]:
            logger.info("    %s", issue)

    return report


# ─── CLI ────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="将旧格式种子因子迁移为 YAML 文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/migrate_seeds_to_yaml.py              # 迁移（不覆盖已有）
  python scripts/migrate_seeds_to_yaml.py --force       # 迁移并覆盖已有
  python scripts/migrate_seeds_to_yaml.py --verify      # 仅验证现有 YAML
  python scripts/migrate_seeds_to_yaml.py --source wq101 qlib158  # 仅迁移指定来源
        """,
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="强制覆盖已存在的 YAML 文件",
    )
    parser.add_argument(
        "--verify",
        "-v",
        action="store_true",
        help="仅验证现有 YAML 文件完整性",
    )
    parser.add_argument(
        "--source",
        "-s",
        nargs="+",
        choices=["builtin", "futures", "wq101", "qlib158", "gtja191", "fundamental"],
        help="仅迁移指定来源（可多选）",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=None,
        help="输出目录（默认 seeds/）",
    )

    args = parser.parse_args()

    if args.output_dir:
        global SEEDS_DIR
        SEEDS_DIR = Path(args.output_dir)

    if args.verify:
        verify()
        return

    sources = set(args.source) if args.source else None
    migrate(force=args.force, sources=sources)


if __name__ == "__main__":
    main()
