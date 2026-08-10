"""
提前淘汰高衰减风险期货精英因子。

操作:
1. DuckDB 直接更新 factor_catalog.status → 'retired' + 插入状态变迁记录
2. JSON 快照移至 futures_elite/_retired/ 目录（原子操作，已幂等）
"""

import json
import shutil
from pathlib import Path
from datetime import datetime, timezone
import duckdb

# ─── 配置 ───────────────────────────────────────────────────
ELITE_DIR = Path("memory/knowledge/factors/futures_elite")
RETIRED_DIR = ELITE_DIR / "_retired"
DUCKDB_PATH = "data/factor_catalog.duckdb"

# 12 个待淘汰因子
RETIRE_LIST = [
    # ── 立即淘汰（极端异常, MDD>50%）──
    ("fct_e7c086cb", "fut_mkt_trend", "极端异常: 最大回撤 122.68%"),
    ("fct_e69955ae", "fut_crowd_volatility", "极端异常: 最大回撤 91.18%"),
    # ── 下轮重点淘汰（高风险）──
    ("fct_0dc7eea2", "fut_mkt_speculation", "高风险: IC=0.0345, ICIR=0.13, MDD=30.99%"),
    ("fct_586ecd54", "fut_mobile_big_data", "高风险: IC=0.0376, ICIR=0.14, MDD=32.53%"),
    ("fct_a84d8412", "fut_crowd_composite", "高风险: IC=0.0357, ICIR=0.15, MDD=32.61%"),
    ("fct_fdc54826", "fut_turnover", "高风险: IC=0.0345, ICIR=0.13, MDD=30.99%"),
    ("fct_04be6601", "fut_crowd_volume", "高风险: IC=0.0861, ICIR=0.35, MDD=14.42%"),
    ("fct_2f361bf7", "fut_mkt_concentration", "高风险: IC=0.1030, ICIR=0.39, MDD=22.78%"),
    ("fct_71ab1898", "fut_crowd_bias_amount", "高风险: IC=0.0737, ICIR=0.25, MDD=34.63%"),
    ("fct_b6a6fc04", "fut_turnover_g3", "高风险: IC=0.1712, ICIR=0.55, MDD=34.74%"),
    ("fct_d3c6c33d", "fut_crowd_turnover", "高风险: IC=0.0794, ICIR=0.31, MDD=18.44%"),
    ("fct_0958dd32", "fut_basis_momentum_g19", "高风险: IC=0.3091, ICIR=2.36, MDD=28.39%"),
]


def main():
    print("=" * 60)
    print("期货精英因子提前淘汰")
    print(f"批次: {len(RETIRE_LIST)} 个因子")
    print("  立即淘汰 (极端异常): 2 个")
    print(f"  下轮重点淘汰 (高风险): {len(RETIRE_LIST) - 2} 个")
    print("=" * 60)

    # 1. 准备废弃目录
    RETIRED_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n[✓] 废弃目录: {RETIRED_DIR}")

    # 2. 连接 DuckDB
    conn = duckdb.connect(str(DUCKDB_PATH))
    now = datetime.now(timezone.utc).isoformat()
    print(f"[✓] DuckDB 已连接: {DUCKDB_PATH}")

    retired_count = 0
    failed_count = 0

    for factor_id, name, reason in RETIRE_LIST:
        print(f"\n  [{retired_count + 1}/{len(RETIRE_LIST)}] {name} ({factor_id})")
        print(f"     原因: {reason}")

        # 2a. 移动 JSON 文件（已在 _retired 中的跳过）
        json_path = ELITE_DIR / f"{factor_id}.json"
        retired_path = RETIRED_DIR / json_path.name
        if json_path.exists():
            shutil.move(str(json_path), str(retired_path))
            print("     [✓] JSON 已移至 _retired/")
        elif retired_path.exists():
            print("     [✓] JSON 已在 _retired/（跳过移动）")
        else:
            print(f"     [⚠] JSON 文件不存在: {json_path}")

        # 2b. 更新 DuckDB 状态（幂等，绕过 ART 索引 bug）
        try:
            conn.execute("DROP INDEX IF EXISTS idx_factor_catalog_status")
            conn.execute(
                "UPDATE factor_catalog SET status = 'retired' WHERE factor_id = ?",
                [factor_id],
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_catalog_status ON factor_catalog(status)")
            conn.execute("CHECKPOINT")
            print("     [✓] DuckDB status → retired")
        except Exception as e:
            print(f"     [✗] DuckDB 更新失败: {e}")
            failed_count += 1
            continue

        # 2c. 插入状态变迁记录
        try:
            history_id = f"fsh_{factor_id[:8]}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            snapshot = json.dumps(
                {
                    "retired_at": now,
                    "retired_by": "proactive_retirement_script",
                    "batch": "high_risk_decay_20260806",
                }
            )
            conn.execute(
                """
                INSERT INTO factor_status_history (
                    history_id, factor_id, from_status, to_status,
                    reason, changed_at, snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                [history_id, factor_id, "active", "retired", reason, now, snapshot],
            )
            conn.execute("CHECKPOINT")
            print("     [✓] 状态变迁已记录")
        except Exception as e:
            print(f"     [⚠] 状态变迁记录失败: {e}")

        retired_count += 1

    # 3. 统计活跃因子数
    try:
        result = conn.execute(
            "SELECT COUNT(*) FROM factor_catalog WHERE status = 'active' AND market = 'futures'"
        ).fetchone()
        active_count = result[0] if result else 0
        print(f"\n{'=' * 60}")
        print(f"✅ 淘汰完成: {retired_count} 个因子 (失败: {failed_count})")
        print(f"   DuckDB 活跃期货因子: {active_count}")
        print(f"   JSON 已移至: {RETIRED_DIR}/")
        print(f"{'=' * 60}")
    except Exception as e:
        print(f"\n[⚠] 统计活跃因子数失败: {e}")

    conn.close()


if __name__ == "__main__":
    main()
