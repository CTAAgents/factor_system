# -*- coding: utf-8 -*-
"""B4/B5: 评估指标回归对比 + 样本外一致性抽检。

B4: 用 QuantData 复权面板重算每因子截面 IC，与 factor_catalog 记录 IC 对比（漂移报告）。
B5: 抽样 10 因子，新链路（QuantData）IC 与 catalog IC 相关性。
"""
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("b4b5")

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.data import FTSDataProvider  # noqa: E402
from fts.data_futures import FUTURES_HOLDOUT, FUTURES_STRATIFIED_SUBSET  # noqa: E402
from fts.factor_engine.l3_signal_service import build_signal_matrix  # noqa: E402

DB = "data/factor_catalog_futures.duckdb"

# 1) 加载因子 + catalog IC
con = duckdb.connect(DB, read_only=True)
rows = con.execute(
    "SELECT factor_id, code, params, ic, icir, status FROM factor_catalog WHERE market='futures'"
).fetchall()
con.close()
factors, catalog_ic = [], {}
for fid, code, params, ic, icir, status in rows:
    if not code:
        continue
    try:
        p = json.loads(params) if params else {}
    except Exception:  # noqa: BLE001
        p = {}
    factors.append({"factor_id": fid, "code": code, "params": p, "status": status})
    catalog_ic[fid] = (ic, icir, status)
logger.info("加载因子 %d", len(factors))

# 2) 面板（与 B3 同口径）
train_symbols = [s for s in FUTURES_STRATIFIED_SUBSET if s not in FUTURES_HOLDOUT]
provider = FTSDataProvider()
panel, common_dates = provider.get_futures_panel(
    symbols=train_symbols, days=2000, trace_id="b4"
)
if not panel:
    sys.exit(1)

# 3) 重算信号矩阵
t0 = time.time()
bundle = build_signal_matrix(
    panel, factors, {f["factor_id"]: f for f in factors}, common_dates, forward_days=5,
)
logger.info("信号矩阵重算完成 (%.1fs)", time.time() - t0)

sig = bundle.signal_matrix          # (T, N, K)
fwd = bundle.forward_returns        # (T, N)
dates_arr = [pd.Timestamp(d) for d in bundle.dates]
n_dates, n_stocks, n_factors = sig.shape

# 4) 逐因子截面 Spearman IC
from scipy import stats as sp_stats

ic_now, icir_now = {}, {}
for j, fid in enumerate(bundle.factor_ids):
    ic_list = []
    for t in range(n_dates):
        s = sig[t, :, j]
        r = fwd[t, :]
        m = ~(np.isnan(s) | np.isnan(r))
        if m.sum() >= 5:
            ic_val, _ = sp_stats.spearmanr(s[m], r[m])
            if np.isfinite(ic_val):
                ic_list.append(ic_val)
    if ic_list:
        arr = np.array(ic_list)
        ic_now[fid] = float(np.mean(arr))
        icir_now[fid] = float(np.mean(arr) / (np.std(arr) + 1e-12))
    else:
        ic_now[fid], icir_now[fid] = np.nan, np.nan

# 5) B4 漂移报告
print("\n=== B4 评估指标回归对比（QuantData 复权口径）===")
n_active = n_archived = n_retired = 0
drift_big = []
rows_out = []
for fid in bundle.factor_ids:
    old_ic, old_icir, status = catalog_ic.get(fid, (None, None, "unknown"))
    new_ic = ic_now.get(fid)
    if new_ic is None or np.isnan(new_ic) or old_ic is None:
        rows_out.append((fid, status, "NA", "NA", "NA"))
        continue
    drift = new_ic - old_ic
    rows_out.append((fid, status, f"{old_ic:.4f}", f"{new_ic:.4f}", f"{drift:+.4f}"))
    if status == "active":
        n_active += 1
    elif status == "archived":
        n_archived += 1
    elif status == "retired":
        n_retired += 1
    if abs(drift) > 0.05:
        drift_big.append((fid, status, old_ic, new_ic, drift))

print(f"可对比因子: {len([r for r in rows_out if r[2] != 'NA'])} (active={n_active} archived={n_archived} retired={n_retired})")
print(f"IC 漂移 >0.05 的因子数: {len(drift_big)}")
for r in drift_big[:15]:
    print(f"  {r[0]} [{r[1]}] catalog_ic={r[2]:.4f} → new_ic={r[3]:.4f} (Δ{r[4]:+.4f})")

# 6) B5 样本外一致性：active 因子新旧 IC 相关性
print("\n=== B5 样本外一致性（active 因子新旧 IC 相关性）===")
act_pairs = []
for fid, status, o, n, d in rows_out:
    if status == "active" and o != "NA" and n != "NA":
        act_pairs.append((float(o), float(n)))
if len(act_pairs) >= 5:
    o_arr = np.array([p[0] for p in act_pairs])
    n_arr = np.array([p[1] for p in act_pairs])
    corr, _ = sp_stats.spearmanr(o_arr, n_arr)
    print(f"active 因子 {len(act_pairs)} 个: 新旧 IC Spearman 相关 = {corr:.4f}")
    # 同向率
    same = np.mean(np.sign(o_arr) == np.sign(n_arr))
    print(f"IC 符号同向率: {same:.1%}")
else:
    print(f"active 可对比因子不足: {len(act_pairs)}")

# 7) 保存报告
rep = {
    "n_factors": len(bundle.factor_ids),
    "rows": rows_out,
    "drift_big_count": len(drift_big),
    "active_corr": float(corr) if len(act_pairs) >= 5 else None,
}
out = Path("memory/cache/recompute_b4_report.json")
out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
logger.info("报告已保存: %s", out)
