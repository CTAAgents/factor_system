"""
scripts/liquidity_snapshot.py — 期货品种流动性快照评估（GAP-054 前置工具）

数据源优先级（TQ 优先）:
    1. TQ-Local（通达信本地客户端，端口 17709）——探活成功即优先
    2. TqSdk（天勤量化，.env 配置 TQSDK_USERNAME/PASSWORD）——当前主路径
    用**主连合约**（KQ.m@ 主力序列）真实 volume / open_oi（持仓量）/ close，
    成交额 = 量 × 价 × 合约乘数（交易所公开固定规格，见 CONTRACT_MULTIPLIERS）。

评估 FUTURES_CORE_SUBSET（25 核心品种）与 FUTURES_HOLDOUT（盲测池）
是否"够格"（机构 L1 可交易性硬门槛：流动性 + 数据完整度）。

判定口径（机构标准，可配置）:
    - 数据完整度: 窗口内有效交易日数 >= --min-days（默认 50）
    - 相对流动性: 日均成交额全市场排名 <= --top-n（默认 60，即前 73%）

用法:
    python scripts/liquidity_snapshot.py [--days 60] [--min-days 50] [--top-n 60] [--json out.json]

输出:
    - 控制台全市场排名表（★ = 25 核心品种，△ = 盲测池）
    - 可选 --json 落盘快照（供数据驱动动态池消费）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data_futures import FUTURES_CORE_SUBSET, FUTURES_HOLDOUT, FUTURES_SUBSET

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("liquidity_snapshot")

DB_PATH = PROJECT_ROOT / "data" / "fts_history.duckdb"

# 合约乘数（交易所公开固定规格，跨品种成交额换算）
# contract_kline.amount = volume × close（每手价，未乘乘数）→ 真实成交额 = amount × mult
CONTRACT_MULTIPLIERS: dict[str, float] = {
    # 上期所
    "CU": 5.0,
    "AL": 5.0,
    "ZN": 5.0,
    "PB": 5.0,
    "NI": 1.0,
    "SN": 1.0,
    "AU": 1000.0,
    "AG": 15.0,
    "RB": 10.0,
    "HC": 10.0,
    "SS": 5.0,
    "RU": 10.0,
    "FU": 10.0,
    "BU": 10.0,
    "SP": 10.0,
    "AO": 20.0,
    "BR": 5.0,
    # 能源中心
    "SC": 1000.0,
    "NR": 10.0,
    "LU": 10.0,
    "BC": 5.0,
    "EC": 50.0,
    # 大商所
    "M": 10.0,
    "Y": 10.0,
    "A": 10.0,
    "B": 10.0,
    "P": 10.0,
    "C": 10.0,
    "CS": 10.0,
    "JD": 5.0,
    "L": 5.0,
    "PP": 5.0,
    "V": 5.0,
    "EB": 5.0,
    "EG": 10.0,
    "PG": 20.0,
    "LH": 16.0,
    "RR": 10.0,
    "J": 100.0,
    "JM": 60.0,
    "I": 100.0,
    "FB": 500.0,
    "BB": 500.0,
    # 郑商所
    "TA": 5.0,
    "MA": 10.0,
    "FG": 20.0,
    "SA": 20.0,
    "SF": 5.0,
    "SM": 5.0,
    "CF": 5.0,
    "SR": 10.0,
    "OI": 10.0,
    "RM": 10.0,
    "RS": 10.0,
    "WH": 20.0,
    "JR": 20.0,
    "LR": 20.0,
    "RI": 20.0,
    "CY": 5.0,
    "AP": 10.0,
    "CJ": 5.0,
    "UR": 20.0,
    "PK": 5.0,
    "PF": 5.0,
    "PX": 5.0,
    "SH": 30.0,
    # 中金所（元/点）
    "IF": 300.0,
    "IH": 300.0,
    "IC": 200.0,
    "IM": 200.0,
    # 广期所
    "SI": 5.0,
    "LC": 1.0,
}

# 乘数未知的新上市品种（mult=1 告警）
_UNKNOWN_MULT = {
    "AD",
    "BZ",
    "LG",
    "OP",
    "PD",
    "PL",
    "PR",
    "PS",
    "PT",
    "TS",
    "TF",
}


def _multiplier(sym: str) -> float:
    """返回品种合约乘数；未知品种 1.0。支持主连后缀（AU0 -> AU）。"""
    import re

    return CONTRACT_MULTIPLIERS.get(re.sub(r"\d+$", "", sym.upper()), 1.0)


def _probe_tq_local(timeout: float = 2.0) -> bool:
    """TQ-Local（通达信本地 7721）探活。"""
    try:
        import socket

        with socket.create_connection(("127.0.0.1", 17709), timeout=timeout):
            return True
    except OSError:
        return False


def load_tqlocal_liquidity(days: int) -> tuple[pd.DataFrame, str]:
    """TQ-Local（通达信本地 HTTP 17709）批量主路径。

    用 get_stock_list(market=92) 取全市场主力合约，get_market_data 批量
    取日线（真实 Volume + VolInStock 持仓量），成交额 = 量×价×合约乘数。
    本地 HTTP，一次批量请求全市场，远快于 TqSdk。

    Returns:
        (DataFrame[symbol0, contract, n_days, avg_volume, avg_open_oi, avg_close],
         数据截止日期)
    """
    import json
    import re
    import urllib.request

    URL = "http://127.0.0.1:17709/"

    def rpc(method: str, params: dict) -> dict:
        payload = {"id": 1, "method": method, "params": params}
        req = urllib.request.Request(
            URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # 1. 全市场主力合约（market=92: 国内期货主力）
    r = rpc("get_stock_list", {"market": "92", "list_type": 0})
    contracts = r.get("result", {}).get("Value") or []
    if not contracts:
        raise SystemExit("[liquidity_snapshot] TQ-Local 主力合约列表为空（请先在通达信下载期货数据）")

    # 2. 主力合约前缀匹配 FTS 品种（RB2610.SHF -> RB -> RB0）
    base_map = {s[:-1] if s.endswith("0") else s: s for s in FUTURES_SUBSET}
    by_base: dict[str, str] = {}
    for code in contracts:
        head = re.match(r"^([A-Za-z]+)", str(code))
        if head and head.group(1).upper() in base_map and "-" not in str(code):
            # 排除月均价/指标合约（如 L-F2610.DCE），只匹配真实主力合约（字母+数字）
            by_base.setdefault(head.group(1).upper(), str(code))
    if not by_base:
        raise SystemExit("[liquidity_snapshot] 主力合约与 FTS 品种代码匹配失败")

    # 3. 批量取日线（一次最多 100）。
    #    TQ-Local 对"当前主力合约"返回该合约完整历史，其中混有非主力期低成交数据
    #    （换月污染，如 AU2610 前10日均量2万手 vs 主力期28万手），
    #    故只取最近 LOOKBACK 个交易日（主力期窗口）评估当前可交易性。
    LOOKBACK = 5
    rows: list[dict] = []
    failed: list[str] = []
    asof = ""
    codes = list(by_base.values())
    for i in range(0, len(codes), 80):
        batch = codes[i : i + 80]
        r2 = rpc("get_market_data", {"stock_list": batch, "period": "1d", "count": 10})
        val = r2.get("result", {}).get("Value") or {}
        for base, code in by_base.items():
            if code not in batch:
                continue
            d = val.get(code)
            if not d or not d.get("Close"):
                failed.append(base_map[base])
                continue
            closes = [float(x) for x in d["Close"] if x]
            vols = [float(x) for x in d.get("Volume", []) if x]
            ois = [float(x) for x in d.get("VolInStock", []) if x]
            dates = [x for x in d.get("Date", []) if x]
            if not closes or not vols:
                failed.append(base_map[base])
                continue
            # 主力期窗口：最近 LOOKBACK 个有效交易日
            closes = closes[-LOOKBACK:]
            vols = vols[-LOOKBACK:]
            ois = ois[-LOOKBACK:]
            rows.append(
                {
                    "symbol": base_map[base],
                    "contract": code,
                    "n_days": len(closes),
                    "avg_volume": sum(vols) / len(vols),
                    "avg_open_oi": sum(ois) / len(ois) if ois else 0.0,
                    "avg_close": sum(closes) / len(closes),
                }
            )
            if dates:
                asof = max(asof, str(dates[-1]))

    if failed:
        logger.warning("[liquidity_snapshot] TQ-Local 失败品种 %d 个: %s", len(failed), ", ".join(failed[:12]))
    if not rows:
        raise SystemExit("[liquidity_snapshot] TQ-Local 全市场拉取失败（通达信行情服务异常）")
    snap = pd.DataFrame(rows)
    snap["mult"] = snap["symbol"].map(_multiplier)
    snap["avg_turnover_yi"] = (snap["avg_volume"] * snap["avg_close"] * snap["mult"]) / 1e8
    return snap, asof


def load_tqsdk_liquidity(days: int) -> tuple[pd.DataFrame, str]:
    """TqSdk 批量主路径：全市场主连合约日线（真实 volume / open_oi / close）。

    批量订阅（get_kline_serial 传 list）避免逐品种 60×len 秒阻塞。

    Returns:
        (DataFrame[symbol0, contract, n_days, avg_volume, avg_open_oi, avg_close],
         数据截止日期)
    """
    import os
    import time

    from fts.data_sources.tqsdk_source import _SYMBOL_MAP

    import tqsdk  # type: ignore[import-untyped]

    user = os.environ.get("TQSDK_USERNAME", "")
    pwd = os.environ.get("TQSDK_PASSWORD", "")
    from tqsdk import TqAuth

    api = tqsdk.TqApi(auth=TqAuth(user, pwd))

    sym_pairs = [(s, tq) for s in FUTURES_SUBSET if (tq := _SYMBOL_MAP.get(s.upper()))]
    failed: list[str] = []
    rows: list[dict] = []
    asof = ""
    try:
        # 分 4 批订阅，避免单批过多；批内整体容错，失败批回退逐品种
        BATCH = 21
        for i in range(0, len(sym_pairs), BATCH):
            batch = sym_pairs[i : i + BATCH]
            try:
                kl = api.get_kline_serial(
                    [tq for _, tq in batch], duration_seconds=86400, data_length=days
                )
                deadline = time.time() + 30
                while time.time() < deadline:
                    api.wait_update(deadline=time.time() + 5)
                    if len(kl) > 0 and int(kl.iloc[-1]["datetime"]) > 0:
                        break
                if kl is None or len(kl) == 0 or int(kl.iloc[-1]["datetime"]) <= 0:
                    failed.extend(s for s, _ in batch)
                    continue
                df = kl.copy()
                df["dt"] = pd.to_datetime(df["datetime"], unit="ns")
                df = df[df["close"] > 0].dropna(subset=["close", "volume"])
                if df.empty:
                    failed.extend(s for s, _ in batch)
                    continue
                for sym0, _ in batch:
                    g = df[df["symbol"] == _SYMBOL_MAP[sym0.upper()]]
                    if g.empty:
                        failed.append(sym0)
                        continue
                    rows.append(
                        {
                            "symbol": sym0,
                            "contract": g.iloc[-1]["symbol"],
                            "n_days": int(len(g)),
                            "avg_volume": float(g["volume"].mean()),
                            "avg_open_oi": float(g["open_oi"].mean()),
                            "avg_close": float(g["close"].mean()),
                        }
                    )
                    asof = max(asof, g["dt"].max().strftime("%Y-%m-%d"))
            except Exception:  # noqa: BLE001 — 批失败回退逐品种
                for sym0, _ in batch:
                    try:
                        kl1 = api.get_kline_serial(_SYMBOL_MAP[sym0.upper()], duration_seconds=86400, data_length=days)
                        dl = time.time() + 8
                        while time.time() < dl:
                            api.wait_update(deadline=time.time() + 1)
                            if len(kl1) > 0 and int(kl1.iloc[-1]["datetime"]) > 0:
                                break
                        if kl1 is None or len(kl1) == 0 or int(kl1.iloc[-1]["datetime"]) <= 0:
                            failed.append(sym0)
                            continue
                        df1 = kl1.copy()
                        df1["dt"] = pd.to_datetime(df1["datetime"], unit="ns")
                        df1 = df1[df1["close"] > 0].dropna(subset=["close", "volume"])
                        if df1.empty:
                            failed.append(sym0)
                            continue
                        rows.append(
                            {
                                "symbol": sym0,
                                "contract": df1.iloc[-1]["symbol"],
                                "n_days": int(len(df1)),
                                "avg_volume": float(df1["volume"].mean()),
                                "avg_open_oi": float(df1["open_oi"].mean()),
                                "avg_close": float(df1["close"].mean()),
                            }
                        )
                        asof = max(asof, df1["dt"].max().strftime("%Y-%m-%d"))
                    except Exception:  # noqa: BLE001
                        failed.append(sym0)
    finally:
        api.close()

    if failed:
        logger.warning("[liquidity_snapshot] TqSdk 失败品种 %d 个: %s", len(failed), ", ".join(failed[:12]))
    if not rows:
        raise SystemExit("[liquidity_snapshot] TqSdk 全市场拉取失败（账号/网络），请检查 TQSDK_USERNAME/PASSWORD")
    snap = pd.DataFrame(rows)
    snap["mult"] = snap["symbol"].map(_multiplier)
    # 真实成交额（亿元）= 量 × 价 × 合约乘数
    snap["avg_turnover_yi"] = (snap["avg_volume"] * snap["avg_close"] * snap["mult"]) / 1e8
    return snap, asof


def build_snapshot(days: int, min_days: int, top_n: int) -> tuple[pd.DataFrame, str]:
    """TqSdk 流动性聚合 + 够格判定 + 全市场排名。"""
    if _probe_tq_local():
        snap, asof = load_tqlocal_liquidity(days)
    else:
        logger.warning("[liquidity_snapshot] TQ-Local 不可用，降级 TqSdk（天勤）")
        snap, asof = load_tqsdk_liquidity(days)
    core = {s.upper() for s in FUTURES_CORE_SUBSET}
    holdout = {s.upper() for s in FUTURES_HOLDOUT}
    snap = snap.sort_values("avg_turnover_yi", ascending=False).reset_index(drop=True)
    snap["rank"] = snap.index + 1
    snap["in_core"] = snap["symbol"].isin(core)
    snap["in_holdout"] = snap["symbol"].isin(holdout)
    snap["ok_days"] = snap["n_days"] >= min_days
    snap["ok_rank"] = snap["rank"] <= top_n
    snap["qualified"] = snap["ok_days"] & snap["ok_rank"]
    return snap, asof


def main() -> None:
    ap = argparse.ArgumentParser(description="期货品种流动性快照评估（真实主力合约成交额）")
    ap.add_argument("--days", type=int, default=60, help="回溯交易日数（默认 60）")
    ap.add_argument("--min-days", type=int, default=50, help="窗口有效交易日数门槛（默认 50）")
    ap.add_argument("--top-n", type=int, default=60, help="成交额排名门槛（默认 60，前 73%）")
    ap.add_argument("--json", type=str, default="", help="可选：快照 JSON 落盘路径")
    args = ap.parse_args()

    tq_local = _probe_tq_local()
    # TQ-Local 为主力期窗口（最近5日），完整度门槛 3 日；TqSdk 主连为完整窗口，用 --min-days
    eff_min_days = 3 if tq_local else args.min_days
    snap, asof = build_snapshot(args.days, eff_min_days, args.top_n)
    run_at = datetime.now().strftime("%Y-%m-%d")

    src = "TqSdk（天勤实时）" + ("（TQ-Local 通达信未运行，启动后可优先）" if not tq_local else " + TQ-Local")

    print(f"== 期货流动性快照（运行 {run_at}，数据截至 {asof or 'N/A'}，主力期窗口 5 交易日）==")
    print(f"数据源: {src} | 口径: 主力合约成交额(量×价×乘数) + 真实持仓量(open_oi)")
    print(f"判定: 主力期完整度≥{eff_min_days} 天 | 成交额排名≤{args.top_n}")
    print(
        f"{'排名':<4}{'品种':<6}{'主力':<10}{'天数':<5}{'日均额(亿)':<10}{'日均量(手)':<12}{'日均持仓(手)':<13}{'池':<5}{'判定':<6}"
    )
    for _, r in snap.iterrows():
        tag = "★核心" if r["in_core"] else ("△盲测" if r["in_holdout"] else "")
        mark = "够格" if r["qualified"] else ("够格(排名)" if r["ok_days"] else "不足")
        print(
            f"{int(r['rank']):<4}{r['symbol']:<6}{str(r.get('contract', ''))[:9]:<10}{int(r['n_days']):<5}"
            f"{r['avg_turnover_yi']:<10.1f}{r['avg_volume']:<12.0f}"
            f"{r.get('avg_open_oi', 0.0):<13.0f}{tag:<5}{mark:<6}"
        )

    core_view = snap[snap["in_core"]]
    qualified_core = core_view[core_view["qualified"]]
    print("\n== 25 核心品种评估 ==")
    print(
        f"入库 {len(core_view)} 个 | 全部达标 {len(qualified_core)} 个 | 未达标 {len(core_view) - len(qualified_core)} 个"
    )
    for _, r in core_view[~core_view["qualified"]].iterrows():
        fails = []
        if not r["ok_days"]:
            fails.append("天数不足")
        if not r["ok_rank"]:
            fails.append("排名靠后")
        print(f"  ✗ {r['symbol']} 排名{int(r['rank'])} 日均额 {r['avg_turnover_yi']:.1f} 亿 — {'/'.join(fails)}")

    missing_dom = [s for s in FUTURES_CORE_SUBSET if s.upper() not in set(snap["symbol"].unique())]
    if missing_dom:
        print(f"  ⚠ 核心池声明但 TqSdk 无数据: {', '.join(missing_dom)}")

    if asof:
        print(f"\n数据截至 {asof}（TqSdk 实时；若滞后请检查 TqApi wait_update 配额）")
    print(
        f"\n全市场 {len(snap)} 个品种（TqSdk 口径）中够格 {int(snap['qualified'].sum())} 个（前 {args.top_n} 名可入池候选）"
    )

    if args.json:
        out = Path(args.json)
        out.write_text(
            json.dumps(
                {
                    "run_at": run_at,
                    "data_asof": asof,
                    "window_days": args.days,
                    "min_days": args.min_days,
                    "top_n": args.top_n,
                    "note": "TqSdk 主连成交额(量×价×乘数) + 真实持仓量 open_oi；TQ-Local 探活成功时优先",
                    "source": "tqsdk" if not _probe_tq_local() else "tq_local",
                    "symbols": snap.to_dict(orient="records"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("快照已落盘: %s", out)


if __name__ == "__main__":
    main()
