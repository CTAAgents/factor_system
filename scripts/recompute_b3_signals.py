# -*- coding: utf-8 -*-
"""B3: 全量重算 346 因子信号（QuantData 复权口径，主链路已验证）。

流程：
1. factor_catalog_futures 加载全部因子（code/params）
2. FTSDataProvider.get_futures_panel 构建 QuantData 复权面板（全期货分层子集）
3. build_signal_matrix 全量重算信号
4. 输出每因子有效信号比例 + 快照（供 B4 对比）
"""
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("b3")

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.data import FTSDataProvider  # noqa: E402
from fts.data_futures import FUTURES_HOLDOUT, FUTURES_STRATIFIED_SUBSET  # noqa: E402
from fts.factor_engine.l3_signal_service import build_signal_matrix  # noqa: E402

DB = "data/factor_catalog_futures.duckdb"

# 1) 加载因子
con = duckdb.connect(DB, read_only=True)
rows = con.execute(
    "SELECT factor_id, code, params, status FROM factor_catalog WHERE market='futures'"
).fetchall()
con.close()
factors = []
for fid, code, params, status in rows:
    if not code:
        continue
    try:
        p = json.loads(params) if params else {}
    except Exception:  # noqa: BLE001
        p = {}
    factors.append({"factor_id": fid, "code": code, "params": p, "status": status})
logger.info("加载因子 %d 个（active=%d）", len(factors),
            sum(1 for f in factors if f["status"] == "active"))
if not factors:
    sys.exit(1)

# 2) 构建面板（分层训练集，排除盲测池）
train_symbols = [s for s in FUTURES_STRATIFIED_SUBSET if s not in FUTURES_HOLDOUT]
provider = FTSDataProvider()
t0 = time.time()
panel, common_dates = provider.get_futures_panel(
    symbols=train_symbols, days=2000, trace_id="b3_recompute"
)
logger.info("面板构建完成: %d 品种 × %d 交易日 (%.1fs)",
            len(panel), len(common_dates), time.time() - t0)
if not panel:
    sys.exit(1)

# 3) 全量重算信号矩阵
factor_codes = {f["factor_id"]: f for f in factors}
t0 = time.time()
bundle = build_signal_matrix(panel, factors, factor_codes, common_dates, forward_days=5)
logger.info("信号矩阵重算完成: %s (%.1fs)", str(bundle.signal_matrix.shape), time.time() - t0)

sig = bundle.signal_matrix
n_dates, n_stocks, n_factors = sig.shape
non_nan = np.mean(~np.isnan(sig), axis=(0, 1))

print("\n=== B3 全量重算结果 ===")
print(f"矩阵: {n_dates} 日 × {n_stocks} 品种 × {n_factors} 因子")
print(f"每因子有效信号比例: min={non_nan.min():.2%} median={np.median(non_nan):.2%} max={non_nan.max():.2%}")
zero_factors = [factors[j]["factor_id"] for j in range(n_factors) if non_nan[j] < 0.05]
print(f"有效信号比例 <5%: {len(zero_factors)} 个")
for fid in zero_factors[:10]:
    print("  ", fid)

# 4) 快照（供 B4 对比）
snapshot = {
    "n_dates": n_dates, "n_stocks": n_stocks, "n_factors": n_factors,
    "symbols": bundle.symbols, "factor_ids": bundle.factor_ids,
    "dates": [str(d) for d in bundle.dates],
    "non_nan_by_factor": {bundle.factor_ids[j]: float(non_nan[j]) for j in range(n_factors)},
}
out = Path("memory/cache/recompute_b3_snapshot.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
logger.info("快照已保存: %s", out)
