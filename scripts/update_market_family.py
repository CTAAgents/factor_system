"""update_market_family.py — 批量更新因子的 market 和 family 字段

分类规则:
- fut_* 前缀 → market='futures'
  - fut_hf_*   → high_frequency
  - fut_crowd_* → crowding
  - fut_bias_*  → sentiment
  - fut_basis_* → basis
  - fut_mkt_*   → market
  - fut_macro_* → macro
  - fut_option_* → volatility
  - fut_gp_*    → cross_section
  - fut_mobile_* → momentum
  - fut_roll_*  → technical
  - fut_turnover_* → liquidity

- 其余前缀 → market='stock'
  - qlib_*    → cross_section
  - alpha_*   → cross_section
  - gtja_*    → cross_section
  - pmi_*     → macro
  - volatility_* → volatility
  - quality_* → quality
  - momentum_* → momentum
  - fund_*    → fundamental
  - seed_*    → technical
  - rate_*    → macro
  - liquidity_* → liquidity
  - value_*   → value
  - size_*    → size
  - basis_*   → basis
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "factor_catalog.duckdb"


# ─── 分类规则 ────────────────────────────────────────────────

# 期货因子 family 映射: (前缀模式, family)
FUTURES_FAMILY_RULES = [
    ("fut_hf_", "high_frequency"),
    ("fut_crowd_", "crowding"),
    ("fut_bias_", "sentiment"),
    ("fut_basis_", "basis"),
    ("fut_mkt_", "market"),
    ("fut_macro_", "macro"),
    ("fut_option_", "volatility"),
    ("fut_gp_", "cross_section"),
    ("fut_mobile_", "momentum"),
    ("fut_roll_", "technical"),
    ("fut_turnover_", "liquidity"),
]

# 股票因子 family 映射: (前缀, family)
STOCK_FAMILY_RULES = [
    ("qlib_", "cross_section"),
    ("alpha_", "cross_section"),
    ("gtja_", "cross_section"),
    ("pmi_", "macro"),
    ("volatility_", "volatility"),
    ("quality_", "quality"),
    ("momentum_", "momentum"),
    ("fund_", "fundamental"),
    ("seed_", "technical"),
    ("rate_", "macro"),
    ("liquidity_", "liquidity"),
    ("value_", "value"),
    ("size_", "size"),
    ("basis_", "basis"),
]


def classify_factor(name: str) -> tuple[str, str]:
    """根据因子名称分类，返回 (market, family)。"""
    if name.startswith("fut_"):
        market = "futures"
        for prefix, family in FUTURES_FAMILY_RULES:
            if name.startswith(prefix):
                return market, family
        # fut_ 开头但未匹配到具体规则
        return market, "futures_other"
    else:
        market = "stock"
        for prefix, family in STOCK_FAMILY_RULES:
            if name.startswith(prefix):
                return market, family
        return market, "other"


def main():
    import duckdb

    if not DB_PATH.exists():
        logger.error("数据库不存在: %s", DB_PATH)
        sys.exit(1)

    conn = duckdb.connect(str(DB_PATH))

    # 获取所有因子
    factors = conn.execute("SELECT factor_id, name, market, family FROM factor_catalog").fetchall()

    logger.info("共 %d 个因子待更新\n", len(factors))

    # 统计
    stats = {
        "market_changed": 0,
        "family_set": 0,
        "family_changed": 0,
        "futures": 0,
        "stock": 0,
        "family_counts": {},
    }

    updates = []
    for factor_id, name, old_market, old_family in factors:
        new_market, new_family = classify_factor(name)

        if new_market == "futures":
            stats["futures"] += 1
        else:
            stats["stock"] += 1

        if new_market != old_market:
            stats["market_changed"] += 1

        if not old_family:
            stats["family_set"] += 1
        elif new_family != old_family:
            stats["family_changed"] += 1

        # family 统计
        stats["family_counts"][new_family] = stats["family_counts"].get(new_family, 0) + 1

        updates.append((new_market, new_family, factor_id))

    # 批量更新
    logger.info("执行批量更新...")
    conn.executemany(
        "UPDATE factor_catalog SET market = ?, family = ? WHERE factor_id = ?",
        updates,
    )
    conn.commit()

    # 验证
    logger.info("\n" + "=" * 60)
    logger.info("更新完成，验证结果:")
    logger.info("=" * 60)

    # 市场分布
    market_dist = conn.execute("""
        SELECT market, COUNT(*) as cnt
        FROM factor_catalog
        GROUP BY market
        ORDER BY cnt DESC
    """).fetchall()
    for m, cnt in market_dist:
        logger.info("  market=%s: %d", m, cnt)

    # family 分布
    family_dist = conn.execute("""
        SELECT market, family, COUNT(*) as cnt
        FROM factor_catalog
        GROUP BY market, family
        ORDER BY market, cnt DESC
    """).fetchall()
    current_market = None
    for m, f, cnt in family_dist:
        if m != current_market:
            logger.info("\n  [%s]", m)
            current_market = m
        logger.info("    %s: %d", f or "(null)", cnt)

    # 打印统计
    logger.info("\n" + "-" * 60)
    logger.info("变更统计:")
    logger.info("  market 字段变更: %d", stats["market_changed"])
    logger.info("  family 字段新增: %d", stats["family_set"])
    logger.info("  family 字段修改: %d", stats["family_changed"])
    logger.info("  期货因子总数: %d", stats["futures"])
    logger.info("  股票因子总数: %d", stats["stock"])

    conn.close()
    logger.info("\n✅ 数据库更新完成")


if __name__ == "__main__":
    main()
