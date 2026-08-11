"""
scripts/validate_sector_clusters.py — 产业链分类数据驱动校验（聚类对照）

用收益相关性层次聚类对照专家产业链分类（FUTURES_SECTOR_MAP），量化两者一致性，
检测"分类漂移"或漏分。仅作校验/监控，不替代专家分类（专家分类保证可解释性、
稳定性与代际可比）。

方法:
    1. 从 DuckDB kline_cache 加载收盘价（可指定回看天数）
    2. 计算日频/5 日收益相关矩阵（品种对重叠观测不足视为 0 信息）
    3. Ward 层次聚类（相关距离 1-|corr|），切分簇数 = 专家产业链数
    4. 对比专家分类 vs 聚类:
       - Adjusted Rand Index (ARI): 两划分整体一致性 (0=随机, 1=完全一致)
       - 每产业链主导簇纯度: 链内品种最集中簇的占比
       - 板块内 vs 板块外平均相关性: 分类"内部凝聚度"质量
    5. 输出控制台汇总 + Markdown 报告 (reports/futures/{date}/) + 可选 --json

数据不足提示: 品种在窗口内有效观测 < min_obs（默认 60）时不参与聚类/对比，
报告列出被排除品种与原因（当前 57/82 品种仅约 120 天数据时结果仅供参考）。

用法:
    python scripts/validate_sector_clusters.py [--days 250] [--horizon 5]
        [--n-clusters 0] [--min-obs 60] [--json out.json]

版本: v1.0.0 (GAP-S05)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data_futures import FUTURES_SECTOR_MAP, FUTURES_SUBSET

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("validate_sector_clusters")

DUCKDB_PATH = PROJECT_ROOT / "data" / "fts_history.duckdb"
REPORTS_ROOT = PROJECT_ROOT / "reports"


def load_close_matrix(symbols: list[str], days: int) -> pd.DataFrame:
    """从 DuckDB kline_cache 加载收盘价矩阵（只读）；失败返回空 DataFrame。"""
    try:
        import duckdb
    except ImportError:
        logger.warning("duckdb 不可用，无法加载行情")
        return pd.DataFrame()
    try:
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        df = con.execute(
            "select symbol, date, close from kline_cache "
            "where symbol in (select unnest(?)) order by symbol, date",
            [symbols],
        ).df()
        con.close()
    except Exception as e:  # noqa: BLE001 — 校验脚本降级不阻断
        logger.warning("DuckDB 读取失败: %s", e)
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    close = df.pivot(index="date", columns="symbol", values="close").tail(days)
    return close


def return_corr(close: pd.DataFrame, horizon: int, min_obs: int) -> pd.DataFrame:
    """计算 horizon 期收益相关矩阵；重叠观测不足的配对填 0（无信息）。"""
    if close.empty or len(close) < horizon + 2:
        return pd.DataFrame()
    rets = close.pct_change(horizon, fill_method=None).dropna(how="all")
    if len(rets) < min_obs:
        logger.warning("有效收益期数 %d < min_obs=%d，相关性不可靠", len(rets), min_obs)
    corr = rets.corr(min_periods=min_obs)
    return corr.fillna(0.0)


def hierarchical_cluster(corr: pd.DataFrame, n_clusters: int) -> dict[str, int]:
    """Ward 层次聚类（相关距离 1-|corr|），返回 {symbol: cluster_id}。"""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    n = len(corr)
    if n == 0 or n_clusters <= 0:
        return {}
    dist = np.clip(1.0 - corr.values, 0.0, 2.0)
    np.fill_diagonal(dist, 0.0)
    n_clusters = min(n_clusters, n)
    condensed = squareform(dist)
    Z = linkage(condensed, method="ward")
    labels = fcluster(Z, t=n_clusters, criterion="maxclust")
    return {sym: int(lab) for sym, lab in zip(corr.columns, labels)}


def adjusted_rand_index(labels_a: dict[str, int], labels_b: dict[str, int]) -> float:
    """调整兰德指数（ARI）：两划分一致性。0=随机一致，1=完全一致。"""
    try:
        from sklearn.metrics import adjusted_rand_score
    except ImportError:
        return float("nan")
    common = [s for s in labels_a if s in labels_b]
    if len(common) < 2:
        return float("nan")
    a = [labels_a[s] for s in common]
    b = [labels_b[s] for s in common]
    return float(adjusted_rand_score(a, b))


def per_chain_purity(
    sector_map: dict[str, list[str]],
    cluster_labels: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """每产业链: 主导簇、纯度（链内最集中簇占比）、链内品种数；单品种链跳过。"""
    result: dict[str, dict[str, Any]] = {}
    for chain, syms in sector_map.items():
        members = [s for s in syms if s in cluster_labels]
        if len(members) < 2:
            continue
        counts: dict[int, int] = {}
        for s in members:
            counts[cluster_labels[s]] = counts.get(cluster_labels[s], 0) + 1
        dominant, max_n = max(counts.items(), key=lambda kv: kv[1])
        result[chain] = {
            "n_members": len(members),
            "dominant_cluster": int(dominant),
            "purity": round(max_n / len(members), 4),
        }
    return result


def within_vs_cross_corr(
    corr: pd.DataFrame,
    sector_map: dict[str, list[str]],
) -> tuple[list[float], list[float], dict[str, float]]:
    """板块内/板块外配对相关 + 每板块内部平均相关。"""
    sym2sector = {s: sec for sec, syms in sector_map.items() for s in syms}
    syms = [s for s in corr.columns if s in sym2sector]
    within, across = [], []
    per_chain: dict[str, list[float]] = {}
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            a, b = syms[i], syms[j]
            c = corr.loc[a, b]
            if not np.isfinite(c):
                continue
            if sym2sector[a] == sym2sector[b]:
                within.append(float(c))
                per_chain.setdefault(sym2sector[a], []).append(float(c))
            else:
                across.append(float(c))
    chain_avg = {k: round(float(np.mean(v)), 4) for k, v in per_chain.items() if v}
    return within, across, chain_avg


def build_report(
    close: pd.DataFrame,
    corr: pd.DataFrame,
    cluster_labels: dict[str, int],
    ari: float,
    purity: dict[str, dict[str, Any]],
    within: list[float],
    across: list[float],
    chain_avg: dict[str, float],
    excluded: list[str],
    horizon: int,
    days: int,
) -> list[str]:
    """生成 Markdown 报告行。"""
    lines: list[str] = []
    today = date.today().isoformat()

    def w(s: str = "") -> None:
        lines.append(s)

    w(f"# 产业链分类聚类校验报告 — {today}")
    w()
    w(f"生成时间: {today} | 回看 {days} 日 | {horizon} 日收益相关")
    w(f"覆盖品种: {len(corr.columns)} 个（排除 {len(excluded)} 个数据不足: {', '.join(excluded) or '无'}）")
    w()
    w("## 1. 专家分类 vs 聚类整体一致性")
    w()
    w(f"- **Adjusted Rand Index (ARI)**: {ari:.4f}（1=完全一致，0=随机一致，NaN=样本不足）")
    w(f"- **板块内平均相关**: {np.mean(within):.4f} ({len(within)} 对)  |  "
      f"**板块外平均相关**: {np.mean(across):.4f} ({len(across)} 对)")
    w()
    w("> ARI ≥ 0.6 视为专家分类与数据驱动聚类高度一致；ARI < 0.3 提示分类与当前行情联动结构"
      "偏离较大，需结合产业链逻辑人工复核（不自动改分类）。")
    w()
    w("## 2. 各产业链主导簇纯度")
    w()
    w("| 产业链 | 品种数 | 主导簇 | 纯度 |")
    w("|--------|--------|--------|------|")
    for chain, info in sorted(purity.items(), key=lambda kv: -kv[1]["purity"]):
        w(f"| {chain} | {info['n_members']} | {info['dominant_cluster']} | {info['purity']:.1%} |")
    w()
    w("## 3. 各产业链内部平均相关（凝聚度）")
    w()
    w("| 产业链 | 品种数 | 内部均相关 |")
    w("|--------|--------|-----------|")
    for chain, avg in sorted(chain_avg.items(), key=lambda kv: -kv[1]):
        n = len(FUTURES_SECTOR_MAP.get(chain, []))
        w(f"| {chain} | {n} | {avg:.4f} |")
    w()
    w("> 内部均相关为负或接近 0 的链，其品种价格联动弱，合成板块 OHLCV / 板块中性化时"
      "信号互抵风险较高，建议重点人工复核。")
    return lines


def main(
    days: int = 250,
    horizon: int = 5,
    n_clusters: int = 0,
    min_obs: int = 60,
    json_out: str | None = None,
) -> int:
    close = load_close_matrix(FUTURES_SUBSET, days)
    if close.empty:
        logger.error("无行情数据，退出")
        return 1

    # 数据完整性: 有效观测不足的品种排除出聚类/对比
    valid = close.notna().sum()
    excluded = sorted(valid[valid < min_obs].index.tolist())
    keep = valid[valid >= min_obs].index.tolist()
    logger.info("品种 %d 个，数据充足参与校验 %d 个，排除 %d 个", len(close.columns), len(keep), len(excluded))
    if len(keep) < 2:
        logger.warning("数据充足品种不足 2 个，无法聚类对比（数据深度不足，仅输出相关性质量）")

    corr = return_corr(close[keep] if keep else close, horizon, min_obs)
    if corr.empty:
        logger.error("相关矩阵为空，退出")
        return 1

    n = n_clusters or len(FUTURES_SECTOR_MAP)
    cluster_labels = hierarchical_cluster(corr, n)
    logger.info("层次聚类: %d 品种 -> %d 簇", len(cluster_labels), n)

    # 专家分类仅保留有数据品种的成员参与对比
    sector_map_active: dict[str, list[str]] = {
        sec: [s for s in syms if s in corr.columns] for sec, syms in FUTURES_SECTOR_MAP.items()
    }
    sector_map_active = {k: v for k, v in sector_map_active.items() if len(v) >= 2}
    expert_labels = {s: sec for sec, syms in sector_map_active.items() for s in syms}

    ari = adjusted_rand_index(expert_labels, cluster_labels)
    purity = per_chain_purity(sector_map_active, cluster_labels)
    within, across, chain_avg = within_vs_cross_corr(corr, sector_map_active)

    # 控制台汇总
    logger.info("ARI=%s | 板块内均相关 %.4f (%d 对) vs 板块外 %.4f (%d 对)",
                f"{ari:.4f}" if np.isfinite(ari) else "NaN", np.mean(within), len(within),
                np.mean(across), len(across))
    for chain, info in sorted(purity.items(), key=lambda kv: -kv[1]["purity"]):
        logger.info("  链 %-8s 纯度 %.0f%%", chain, info["purity"] * 100)

    # 报告落盘
    report_dir = REPORTS_ROOT / "futures" / date.today().isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    lines = build_report(
        close, corr, cluster_labels, ari, purity, within, across, chain_avg, excluded, horizon, days
    )
    out = report_dir / f"sector_cluster_validation_{date.today().isoformat()}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("报告已保存: %s", out)

    if json_out:
        payload = {
            "date": date.today().isoformat(),
            "days": days,
            "horizon": horizon,
            "n_clusters": n,
            "n_symbols_total": len(close.columns),
            "n_symbols_valid": len(corr.columns),
            "excluded": excluded,
            "ari": None if not np.isfinite(ari) else round(ari, 4),
            "within_avg_corr": round(float(np.mean(within)), 4),
            "across_avg_corr": round(float(np.mean(across)), 4),
            "purity": purity,
            "chain_internal_corr": chain_avg,
        }
        Path(json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("JSON 已保存: %s", json_out)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="产业链分类数据驱动校验（聚类对照）")
    parser.add_argument("--days", type=int, default=250, help="回看天数")
    parser.add_argument("--horizon", type=int, default=5, choices=[1, 5], help="收益计算周期（日/5日）")
    parser.add_argument("--n-clusters", type=int, default=0, help="聚类簇数（0=专家产业链数）")
    parser.add_argument("--min-obs", type=int, default=60, help="参与校验的最低有效观测数")
    parser.add_argument("--json", type=str, default=None, help="机器可读输出路径")
    args = parser.parse_args()
    sys.exit(main(days=args.days, horizon=args.horizon, n_clusters=args.n_clusters,
                  min_obs=args.min_obs, json_out=args.json))
