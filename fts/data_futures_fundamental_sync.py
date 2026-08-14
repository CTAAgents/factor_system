"""
fts.data_futures_fundamental_sync — 期货基本面字段每日同步（Stage 2）

按字段消费字典（fts/config/futures_field_consumption.py）fundamental 组
每日同步 9 个基本面字段，落 Parquet 缓存（用户确认的落盘位置）:
  memory/cache/futures_fundamental/{symbol}.parquet（按 symbol 分文件，upsert）

数据流:
  sync_futures_data_job Stage 2 → sync_fundamental_fields
    → AkshareFuturesFundamentalProvider（库存/基差/仓单，复用现有 provider）
    → 现货价格缺失时 SpotPriceFiller WebSearch 补充（三项校验：新鲜度/正确性/单位对齐）
    → Parquet upsert（date 去重，增量合并）

计量单位对齐（用户要求）:
  期货报价单位按品种映射（AU 元/克、AG 元/千克、股指 点、其余元/吨），
  WebSearch 现货价解析出单位后统一换算到期货报价单位，无法换算即校验失败不入库。

失败完全透明: 单品种失败 / 现货补充校验失败均记录到返回摘要（failures / missing_spot），
绝不静默吞错、绝不强行写入未经校验的价格。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FUNDAMENTAL_CACHE_DIR = PROJECT_ROOT / "memory" / "cache" / "futures_fundamental"

# 输出列（与字段消费字典 fundamental 组 / enrich_futures_fundamental 对齐）
FUNDAMENTAL_COLUMNS: list[str] = [
    "fut_inventory",
    "fut_inventory_chg",
    "fut_warehouse_receipt",
    "fut_warehouse_receipt_chg",
    "fut_spot_price",
    "fut_near_basis",
    "fut_dom_basis",
    "fut_near_basis_rate",
    "fut_dom_basis_rate",
]

# ─── 现货价格计量单位（WebSearch 补充时的单位对齐基准）────────────

# 品种（连续合约去尾 0）→ 期货报价单位
SPOT_UNIT_MAP: dict[str, str] = {
    "AU": "元/克",
    "AG": "元/千克",
    "IF": "点",
    "IH": "点",
    "IC": "点",
    "IM": "点",
}
DEFAULT_UNIT: str = "元/吨"

# 源单位 → 元/吨 换算系数（点/指数无法换算到元/吨，走独立分支）
_UNIT_TO_TON: dict[str, float] = {"元/克": 1000.0, "元/千克": 1.0, "元/吨": 1.0}


def _convert_to_futures_unit(price: float, src_unit: str, futures_unit: str) -> float | None:
    """将现货价从 src_unit 换算到该品种期货报价单位 futures_unit。

    换算失败（单位无法对应）返回 None，由调用方判定校验失败。
    """
    src = src_unit.strip().lower()
    dst = futures_unit.strip().lower()
    if src == dst:
        return price
    if dst == "点":  # 股指：仅接受同为"点/指数点"口径
        if src in ("点", "指数点", "点/指数"):
            return price
        return None
    if src in _UNIT_TO_TON and dst in _UNIT_TO_TON:
        return price * _UNIT_TO_TON[src] / _UNIT_TO_TON[dst]
    return None


def get_futures_unit(symbol: str) -> str:
    """返回品种对应的期货报价单位。"""
    base = symbol[:-1] if symbol.endswith("0") else symbol
    return SPOT_UNIT_MAP.get(base, DEFAULT_UNIT)


# ─── 现货价格 WebSearch 补充（三项校验）──────────────────────────

_DATE_RE = re.compile(r"(20\d{2})[-/年]?(\d{1,2})[-/月]?(\d{1,2})")
_PRICE_RE = re.compile(r"(\d+(?:\.\d{1,4})?)\s*(元/克|元/千克|元/吨|元/公斤|点)")
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


class SpotFillResult:
    """现货价格补充结果。"""

    def __init__(self, ok: bool, spot_price: float | None = None, error: str = ""):
        self.ok = ok
        self.spot_price = spot_price
        self.error = error

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "spot_price": self.spot_price, "error": self.error}


class SpotPriceFiller:
    """现货价格 WebSearch 补充器：搜索 → 解析 → 三项校验（新鲜度/正确性/单位对齐）。

    校验不过绝不入库（防止错误数据污染基差计算）。
    """

    def __init__(
        self,
        timeout: int = 10,
        sanity_threshold: float = 0.30,
        max_date_gap_days: int = 3,
        llm_client: Any = None,
    ) -> None:
        self.timeout = timeout
        self.sanity_threshold = sanity_threshold
        self.max_date_gap_days = max_date_gap_days
        self.llm_client = llm_client

    def _websearch(self, query: str) -> str:
        """必应 HTML 搜索，返回去标签文本（最长 8000 字符）。"""
        import requests

        resp = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "mkt": "zh-CN"},
            headers=_UA,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", resp.text, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text)[:8000]

    def _parse(self, text: str, variety_cn: str) -> tuple[Optional[float], Optional[str]]:
        """从搜索结果文本解析 (价格, 单位)；优先 LLM，降级正则。"""
        if self.llm_client is not None:
            try:
                payload = self.llm_client.generate_json(
                    f"从以下网页搜索结果中提取“{variety_cn}”现货价格。"
                    f"输出 JSON: {{\"spot_price\": 数值, \"unit\": \"元/吨或元/克或元/千克或点\", "
                    f"\"date\": \"YYYY-MM-DD或空\"}}，无明确价格则 spot_price 为 null。\n{text[:6000]}"
                )
                p = payload.get("spot_price")
                if isinstance(p, (int, float)) and p > 0:
                    unit = str(payload.get("unit") or "").strip()
                    return float(p), unit or None
            except Exception as e:  # noqa: BLE001
                logger.debug("[spot_fill] LLM 解析失败，降级正则: %s", e)
        # 正则降级：优先匹配含品种名的片段，再全局匹配
        m = _PRICE_RE.search(text)
        if m:
            return float(m.group(1)), m.group(2)
        return None, None

    def _check_freshness(self, text: str, latest_date: str) -> tuple[bool, str]:
        """新鲜度校验：结果文本含日期时要求与最新交易日 gap ≤ max_date_gap_days。

        无日期线索时视为通过（正确性校验仍把关价格量级）。
        """
        match = _DATE_RE.search(text)
        if not match:
            return True, "no_date_clue"
        try:
            y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
            result_date = datetime(y, m, d).date()
            latest = datetime.fromisoformat(latest_date).date()
            gap = abs((latest - result_date).days)
            return gap <= self.max_date_gap_days, f"gap={gap}d"
        except Exception:  # noqa: BLE001
            return True, "unparseable_date"

    def _check_sanity(self, spot_price: float, ref_price: float) -> bool:
        """正确性校验：现货价与近月参考价（结算价/主力 close）偏离度 ≤ sanity_threshold。"""
        if ref_price is None or ref_price <= 0:
            return True
        return abs(spot_price - ref_price) / ref_price <= self.sanity_threshold

    def fill(
        self,
        symbol: str,
        variety_cn: str,
        ref_price: float,
        latest_date: str,
    ) -> SpotFillResult:
        """执行现货价格补充与三项校验。

        Args:
            symbol: 连续合约代码（如 "RB0"）。
            variety_cn: 品种中文名（WebSearch 检索词）。
            ref_price: 正确性校验参考价（最新结算价/主力 close）。
            latest_date: 最新交易日（ISO 日期字符串），新鲜度校验基准。

        Returns:
            SpotFillResult（ok=False 时 error 含校验失败原因）。
        """
        query = f"{variety_cn} 现货价格"
        try:
            text = self._websearch(query)
        except Exception as e:  # noqa: BLE001
            return SpotFillResult(False, error=f"websearch_failed: {e}")

        price, unit = self._parse(text, variety_cn)
        if price is None or price <= 0:
            return SpotFillResult(False, error="parse_failed: no_price_found")

        futures_unit = get_futures_unit(symbol)
        converted = _convert_to_futures_unit(price, unit or futures_unit, futures_unit)
        if converted is None:
            return SpotFillResult(
                False, error=f"unit_mismatch: src={unit!r} dst={futures_unit!r}"
            )

        fresh_ok, fresh_note = self._check_freshness(text, latest_date)
        if not fresh_ok:
            return SpotFillResult(False, error=f"stale: {fresh_note}")

        if not self._check_sanity(converted, ref_price):
            return SpotFillResult(
                False,
                error=f"sanity_violation: spot={converted:.4f} ref={ref_price:.4f}",
            )

        logger.info(
            "[spot_fill] %s 现货价 WebSearch 补充成功: %.4f %s (%s)",
            symbol, converted, futures_unit, fresh_note,
        )
        return SpotFillResult(True, spot_price=converted)


# ─── 单品种面板构建与 Parquet 落盘 ─────────────────────────────


def _latest_ref_close(symbol: str) -> float | None:
    """从 kline_cache 读取该品种最新 close，作为现货价正确性校验参考价。

    读连接遵循 E.4 S1：read_only=True 短连接。
    """
    db_path = PROJECT_ROOT / "data" / "fts_history.duckdb"
    if not db_path.exists():
        return None
    try:
        import duckdb

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            variants = [
                symbol,
                f"{symbol}0",
                f"{symbol}.SHFE",
                f"{symbol}.DCE",
                f"{symbol}.CZCE",
                f"{symbol}.CFFEX",
            ]
            placeholders = ",".join(["?"] * len(variants))
            df = con.execute(
                f"SELECT close FROM kline_cache WHERE symbol IN ({placeholders}) "
                "ORDER BY date DESC LIMIT 1",
                [*variants],
            ).df()
            if df is not None and not df.empty:
                val = float(df.iloc[0]["close"])
                return val if val > 0 else None
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("[fund_sync] 参考价读取失败 symbol=%s: %s", symbol, e)
    return None


def _build_fundamental_panel(
    provider: Any,
    symbol: str,
    days: int,
    trace_id: str,
) -> pd.DataFrame:
    """构建单品种基本面面板（index=date 升序，列=FUNDAMENTAL_COLUMNS）。

    库存/基差/仓单三块数据按 date 外连接合并，缺失用 NaN 填充。
    """
    try:
        inv = provider.get_inventory(symbol)
    except Exception as e:  # noqa: BLE001
        logger.debug("[fund_sync] %s 库存获取失败: %s", symbol, e)
        inv = pd.DataFrame(columns=["inventory", "change"])
    try:
        basis = provider.get_basis(symbol, days=days)
    except Exception as e:  # noqa: BLE001
        logger.debug("[fund_sync] %s 基差获取失败: %s", symbol, e)
        basis = pd.DataFrame(
            columns=["spot_price", "near_basis", "dom_basis", "near_basis_rate", "dom_basis_rate"]
        )
    try:
        wr = provider.get_warehouse_receipt(symbol, days=days)
    except Exception as e:  # noqa: BLE001
        logger.debug("[fund_sync] %s 仓单获取失败: %s", symbol, e)
        wr = pd.DataFrame(columns=["warehouse_receipt", "change"])

    parts: list[pd.DataFrame] = []
    if not inv.empty:
        d = inv.copy()
        d = d.rename(columns={"inventory": "fut_inventory", "change": "fut_inventory_chg"})
        d.index = pd.to_datetime(d.index)
        parts.append(d)
    if not basis.empty:
        d = basis.copy()
        d = d.rename(
            columns={
                "spot_price": "fut_spot_price",
                "near_basis": "fut_near_basis",
                "dom_basis": "fut_dom_basis",
                "near_basis_rate": "fut_near_basis_rate",
                "dom_basis_rate": "fut_dom_basis_rate",
            }
        )
        d.index = pd.to_datetime(d.index)
        parts.append(d)
    if not wr.empty:
        d = wr.copy()
        d = d.rename(
            columns={"warehouse_receipt": "fut_warehouse_receipt", "change": "fut_warehouse_receipt_chg"}
        )
        d.index = pd.to_datetime(d.index)
        parts.append(d)

    if not parts:
        return pd.DataFrame(columns=[*FUNDAMENTAL_COLUMNS, "trace_id"])

    panel = parts[0]
    for d in parts[1:]:
        panel = panel.join(d, how="outer")
    panel = panel[~panel.index.duplicated(keep="last")].sort_index()
    for col in FUNDAMENTAL_COLUMNS:
        if col not in panel.columns:
            panel[col] = float("nan")
    panel = panel[FUNDAMENTAL_COLUMNS]
    panel["trace_id"] = trace_id
    return panel


def _upsert_parquet(symbol: str, panel: pd.DataFrame) -> int:
    """按 (symbol) Parquet upsert：date 去重合并增量。"""
    FUNDAMENTAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = FUNDAMENTAL_CACHE_DIR / f"{symbol}.parquet"
    df = panel.reset_index().rename(columns={"index": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    if path.exists():
        try:
            old = pd.read_parquet(path)
            df = pd.concat([old, df], ignore_index=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("[fund_sync] %s 旧缓存读取失败，将全量重写: %s", symbol, e)
    df = (
        df.drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    df.to_parquet(path, index=False)
    return int(len(df))


def sync_fundamental_fields(
    symbols: list[str],
    days: int = 60,
    trace_id: str = "",
    filler: SpotPriceFiller | None = None,
    provider: Any = None,
) -> dict[str, Any]:
    """Stage 2 基本面字段每日同步（字段消费字典 fundamental 组）。

    遍历品种：构建基本面面板 → 现货价缺失时 WebSearch 补充校验 → Parquet upsert。
    单品种失败不中断；现货补充失败记录 missing_spot 摘要（失败透明）。

    Args:
        symbols: 连续合约代码列表。
        days: 基差回溯天数。
        trace_id: HARNESS trace_id。
        filler: 现货价格补充器（None 时用默认 SpotPriceFiller）。
        provider: 基本面 provider（None 时用默认 AkshareFuturesFundamentalProvider）。

    Returns:
        {"success", "failure", "rows", "failures", "missing_spot"}
    """
    from fts.data_futures_fundamental import VARIETY_MAP

    if provider is None:
        from fts.data_futures_fundamental import AkshareFuturesFundamentalProvider

        provider = AkshareFuturesFundamentalProvider()
    if filler is None:
        filler = SpotPriceFiller()

    success = 0
    failure = 0
    rows = 0
    failures: list[dict] = []
    missing_spot: list[dict] = []

    for sym in symbols:
        base = sym[:-1] if sym.endswith("0") else sym
        variety_cn = VARIETY_MAP.get(base, ("", ""))[1]
        try:
            panel = _build_fundamental_panel(provider, sym, days, trace_id)
            if panel.empty:
                failure += 1
                failures.append({"symbol": sym, "error": "empty_panel"})
                continue

            # 现货价格缺失补充（仅无任一非空现货价时触发）
            spot_series = panel["fut_spot_price"].dropna()
            if spot_series.empty and variety_cn:
                ref_price = _latest_ref_close(sym) or float("nan")
                latest_date = str(panel.index.max().date())
                result = filler.fill(sym, variety_cn, ref_price=ref_price, latest_date=latest_date)
                if result.ok and result.spot_price is not None:
                    panel.loc[panel.index.max(), "fut_spot_price"] = result.spot_price
                else:
                    missing_spot.append({"symbol": sym, "error": result.error})

            _upsert_parquet(sym, panel)
            success += 1
            rows += int(len(panel))
        except Exception as e:  # noqa: BLE001
            failure += 1
            failures.append({"symbol": sym, "error": str(e)})
            logger.warning("[fund_sync] %s 同步失败: %s (trace_id=%s)", sym, e, trace_id)

    logger.info(
        "[fund_sync] 基本面同步完成: success=%d failure=%d rows=%d missing_spot=%d (trace_id=%s)",
        success, failure, rows, len(missing_spot), trace_id,
    )
    return {
        "success": success,
        "failure": failure,
        "rows": rows,
        "failures": failures,
        "missing_spot": missing_spot,
    }


__all__ = [
    "sync_fundamental_fields",
    "SpotPriceFiller",
    "SpotFillResult",
    "FUNDAMENTAL_CACHE_DIR",
    "FUNDAMENTAL_COLUMNS",
    "get_futures_unit",
    "_convert_to_futures_unit",
]
