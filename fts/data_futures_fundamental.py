"""
fts.data_futures_fundamental — 期货基本面数据提供者（库存 / 基差 / 仓单）

为 FTS 因子引擎提供期货基本面字段（库存、基差、仓单）的获取与注入能力。
基于 AKShare 公开接口 + 东财数据中心 API（GAP-083 期货字段缺口补充，2026-08-11）:
    - 库存: futures_inventory_em（东财，近期）主源，futures_inventory_99（99 期货，2009 起）兜底
    - 基差: futures_spot_price_daily（交易所现货/基差，2021 起）
    - 仓单: CZCE/GFEX 走 AKShare 官方接口（并行逐日）;
            SHFE/DCE/INE 走东财 RPT_FUTU_STOCKDATA（2026-08-11 阶段 2 接入，
            因官方接口官网改版+WAF 反爬不可用; 东财口径为 Choice 归一化注册仓单，
            与官方原始口径存在单位差异，历史仅保留近 3 个月）

数据流:
    因子引擎 → FTSDataProvider.enrich_futures_fundamental → AkshareFuturesFundamentalProvider
                                                          → AKShare 公开接口 / 东财 API
                                                          ↘ 空 DataFrame（接口异常降级，列契约保留）

HARNESS §契约优先: get_inventory / get_basis / get_warehouse_receipt 输出列契约与 data.py 注入点对齐。
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ─── 品种映射 ─────────────────────────────────────────────

# 品种代码（连续合约去尾 0）→ (东财库存小写代码, 99期货中文名)
# 股指（IF/IC/IH/IM）无现货库存，仅映射基差（spot_price_daily 原生支持）
VARIETY_MAP: dict[str, tuple[str, str]] = {
    # 黑色系
    "RB": ("rb", "螺纹钢"),
    "HC": ("hc", "热轧卷板"),
    "I": ("i", "铁矿石"),
    "J": ("j", "焦炭"),
    "JM": ("jm", "焦煤"),
    "SF": ("sf", "硅铁"),
    "SM": ("sm", "锰硅"),
    # 有色金属
    "CU": ("cu", "铜"),
    "AL": ("al", "铝"),
    "ZN": ("zn", "锌"),
    "PB": ("pb", "铅"),
    "NI": ("ni", "镍"),
    "SN": ("sn", "锡"),
    "AU": ("au", "黄金"),
    "AG": ("ag", "白银"),
    "AO": ("ao", "氧化铝"),
    "SI": ("si", "工业硅"),
    "LC": ("lc", "碳酸锂"),
    # 能源
    "SC": ("sc", "原油"),
    "FU": ("fu", "燃料油"),
    "BU": ("bu", "沥青"),
    "LU": ("lu", "低硫燃料油"),
    "NR": ("nr", "20号胶"),
    "RU": ("ru", "天然橡胶"),
    "EC": ("ec", "集运欧线"),
    # 化工
    "TA": ("ta", "PTA"),
    "MA": ("ma", "甲醇"),
    "EG": ("eg", "乙二醇"),
    "L": ("l", "聚乙烯"),
    "PP": ("pp", "聚丙烯"),
    "V": ("v", "聚氯乙烯"),
    "FG": ("fg", "玻璃"),
    "UR": ("ur", "尿素"),
    "SA": ("sa", "纯碱"),
    "EB": ("eb", "苯乙烯"),
    "PG": ("pg", "液化石油气"),
    # 农产品
    "M": ("m", "豆粕"),
    "Y": ("y", "豆油"),
    "P": ("p", "棕榈油"),
    "C": ("c", "玉米"),
    "CS": ("cs", "玉米淀粉"),
    "A": ("a", "豆一"),
    "B": ("b", "豆二"),
    "JD": ("jd", "鸡蛋"),
    "LH": ("lh", "生猪"),
    "CF": ("cf", "棉花"),
    "SR": ("sr", "白糖"),
    "AP": ("ap", "苹果"),
    "PK": ("pk", "花生"),
    "CY": ("cy", "棉纱"),
    "OI": ("oi", "菜籽油"),
    "RM": ("rm", "菜籽粕"),
    "SP": ("sp", "纸浆"),
    "PF": ("pf", "短纤"),
    "PX": ("px", "对二甲苯"),
    "SH": ("sh", "烧碱"),
    "PR": ("pr", "瓶片"),
    "WH": ("wh", "强麦"),
    "RS": ("rs", "油菜籽"),
    "ZC": ("zc", "动力煤"),
    "CJ": ("cj", "红枣"),
    "PM": ("pm", "普麦"),
    "RI": ("ri", "早籼稻"),
    "LR": ("lr", "晚籼稻"),
    "JR": ("jr", "粳稻"),
    # 股指（无现货库存，基差可用）
    "IF": ("if", ""),
    "IC": ("ic", ""),
    "IH": ("ih", ""),
    "IM": ("im", ""),
}

# 输出列契约（与 data.py enrich_futures_fundamental 对齐）
INVENTORY_COLUMNS: list[str] = ["inventory", "change"]
BASIS_COLUMNS: list[str] = ["spot_price", "near_basis", "dom_basis", "near_basis_rate", "dom_basis_rate"]
WAREHOUSE_COLUMNS: list[str] = ["warehouse_receipt", "change"]


# ─── 仓单交易所路由 ────────────────────────────────────────

# 品种代码 → 所属交易所（仓单接口按交易所分发）。
# 阶段 2（GAP-091 关闭，2026-08-11）：CZCE/GFEX 走 AKShare 官方接口；
# SHFE/DCE/INE 官方接口因官网改版+WAF 反爬程序化不可用，改走东财 RPT_FUTU_STOCKDATA；
# 中金所股指无商品仓单。
VARIETY_EXCHANGE: dict[str, str] = {
    # 上期所 SHFE
    "CU": "shfe",
    "AL": "shfe",
    "ZN": "shfe",
    "PB": "shfe",
    "NI": "shfe",
    "SN": "shfe",
    "AU": "shfe",
    "AG": "shfe",
    "RB": "shfe",
    "HC": "shfe",
    "FU": "shfe",
    "BU": "shfe",
    "RU": "shfe",
    "SP": "shfe",
    "SS": "shfe",
    "AO": "shfe",
    "BR": "shfe",
    # 大商所 DCE
    "A": "dce",
    "B": "dce",
    "M": "dce",
    "Y": "dce",
    "P": "dce",
    "C": "dce",
    "CS": "dce",
    "J": "dce",
    "JM": "dce",
    "I": "dce",
    "L": "dce",
    "V": "dce",
    "PP": "dce",
    "EG": "dce",
    "EB": "dce",
    "PG": "dce",
    "JD": "dce",
    "LH": "dce",
    "RR": "dce",
    "FB": "dce",
    "BB": "dce",
    # 郑商所 CZCE
    "SR": "czce",
    "CF": "czce",
    "CY": "czce",
    "RM": "czce",
    "OI": "czce",
    "TA": "czce",
    "MA": "czce",
    "FG": "czce",
    "UR": "czce",
    "SA": "czce",
    "AP": "czce",
    "PK": "czce",
    "PF": "czce",
    "PX": "czce",
    "SH": "czce",
    "PR": "czce",
    "WH": "czce",
    "PM": "czce",
    "RI": "czce",
    "LR": "czce",
    "JR": "czce",
    "RS": "czce",
    "SF": "czce",
    "SM": "czce",
    "ZC": "czce",
    "CJ": "czce",
    # 广期所 GFEX
    "SI": "gfex",
    "LC": "gfex",
    # 上海国际能源交易中心 INE
    "SC": "ine",
    "NR": "ine",
    "LU": "ine",
    "BC": "ine",
    "EC": "ine",
    # 中金所 CFFEX（股指，无商品仓单）
    "IF": "cffex",
    "IC": "cffex",
    "IH": "cffex",
    "IM": "cffex",
}

# 东财仓单 SECURITY_CODE（RPT_FUTU_STOCKDATA）。SHFE/DCE 大写，INE 小写。
# 仅覆盖官方接口不可用的交易所；CZCE/GFEX 走官方接口不需要东财映射。
# 注: 东财仓单为 Choice 归一化注册仓单口径，历史仅保留近 3 个月。
EM_WAREHOUSE_MAP: dict[str, str] = {
    # SHFE
    "CU": "CU",
    "AL": "AL",
    "ZN": "ZN",
    "PB": "PB",
    "NI": "NI",
    "SN": "SN",
    "AU": "AU",
    "AG": "AG",
    "RB": "RB",
    "HC": "HC",
    "FU": "FU",
    "BU": "BU",
    "RU": "RU",
    "SP": "SP",
    "SS": "SS",
    "AO": "AO",
    "BR": "BR",
    # DCE
    "A": "A",
    "B": "B",
    "M": "M",
    "Y": "Y",
    "P": "P",
    "C": "C",
    "CS": "CS",
    "J": "J",
    "JM": "JM",
    "I": "I",
    "L": "L",
    "V": "V",
    "PP": "PP",
    "EG": "EG",
    "EB": "EB",
    "PG": "PG",
    "JD": "JD",
    "LH": "LH",
    "RR": "RR",
    # INE（小写）
    "SC": "sc",
    "NR": "nr",
    "LU": "lu",
    "BC": "bc",
}


# ─── 期货基本面数据提供者 ─────────────────────────────────


class AkshareFuturesFundamentalProvider:
    """期货基本面数据提供者 — 库存 / 基差 / 仓单。

    数据源优先级:
        1. 库存: futures_inventory_em（东财）→ futures_inventory_99（99 期货，历史更长）
        2. 基差: futures_spot_price_daily（交易所现货/基差，客户端并行逐日）
        3. 仓单: CZCE/GFEX 走 AKShare 官方接口（并行逐日）;
                 SHFE/DCE/INE 走东财 RPT_FUTU_STOCKDATA（官方接口反爬，阶段 2 接入）

    失败降级: 返回空 DataFrame（列契约保留），由 data.py 注入点 NaN 填充兜底。

    用法:
        provider = AkshareFuturesFundamentalProvider()
        inv = provider.get_inventory("RB0")      # columns: inventory, change
        basis = provider.get_basis("RB0", days=60)  # columns: spot_price, near_basis, ...
        wr = provider.get_warehouse_receipt("SR0")  # columns: warehouse_receipt, change
    """

    def __init__(
        self,
        cache_ttl_inventory: int = 21600,
        cache_ttl_basis: int = 3600,
        cache_ttl_warehouse: int = 21600,
    ) -> None:
        """
        Args:
            cache_ttl_inventory: 库存缓存秒数（交易所日更，默认 6 小时）。
            cache_ttl_basis: 基差缓存秒数（默认 1 小时）。
            cache_ttl_warehouse: 仓单缓存秒数（交易所日更，默认 6 小时）。
        """
        self._cache_ttl_inventory = cache_ttl_inventory
        self._cache_ttl_basis = cache_ttl_basis
        self._cache_ttl_warehouse = cache_ttl_warehouse
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    # ── 品种解析 ──

    def _parse_variety(self, symbol: str) -> Optional[str]:
        """从连续合约代码提取品种代码（RB0 → RB，RB → RB）。"""
        s = symbol.strip().upper().rstrip("0")
        return s if s in VARIETY_MAP else None

    # ── 缓存 ──

    def _cache_get(self, key: str, ttl: int) -> Optional[pd.DataFrame]:
        hit = self._cache.get(key)
        if hit is not None and time.time() - hit[0] < ttl:
            return hit[1]
        return None

    def _cache_set(self, key: str, df: pd.DataFrame) -> None:
        self._cache[key] = (time.time(), df)

    # ── 库存 ──

    def get_inventory(self, symbol: str) -> pd.DataFrame:
        """获取品种库存时序（inventory / change），index=date 升序。"""
        empty = pd.DataFrame(columns=INVENTORY_COLUMNS)
        variety = self._parse_variety(symbol)
        if variety is None:
            return empty
        key = f"inv:{variety}"
        cached = self._cache_get(key, self._cache_ttl_inventory)
        if cached is not None:
            return cached
        df = self._fetch_inventory(variety)
        if not df.empty:
            self._cache_set(key, df)
        return df

    def _fetch_inventory(self, variety: str) -> pd.DataFrame:
        """主源东财库存，失败回退 99 期货库存。"""
        em_code, name99 = VARIETY_MAP[variety]
        try:
            import akshare as ak  # type: ignore[import-untyped]

            raw = ak.futures_inventory_em(symbol=em_code)
            df = self._normalize_inventory_em(raw)
            if not df.empty:
                logger.info("期货库存[%s] 东财 %d 行", variety, len(df))
                return df
        except Exception as e:  # noqa: BLE001
            logger.warning("期货库存[%s] 东财接口失败: %s", variety, e)
        if name99:
            try:
                import akshare as ak  # type: ignore[import-untyped]

                raw = ak.futures_inventory_99(symbol=name99)
                df = self._normalize_inventory_99(raw)
                if not df.empty:
                    logger.info("期货库存[%s] 99期货 %d 行（东财回退）", variety, len(df))
                    return df
            except Exception as e:  # noqa: BLE001
                logger.warning("期货库存[%s] 99期货接口失败: %s", variety, e)
        return pd.DataFrame(columns=INVENTORY_COLUMNS)

    @staticmethod
    def _normalize_inventory_em(raw: pd.DataFrame) -> pd.DataFrame:
        """东财库存: 日期/库存/增减 → inventory/change。"""
        if raw is None or raw.empty or not {"日期", "库存"}.issubset(raw.columns):
            return pd.DataFrame(columns=INVENTORY_COLUMNS)
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(raw["日期"]),
                "inventory": pd.to_numeric(raw["库存"], errors="coerce"),
                "change": pd.to_numeric(raw["增减"], errors="coerce") if "增减" in raw.columns else float("nan"),
            }
        ).dropna(subset=["date"])
        df = df.drop_duplicates(subset=["date"]).sort_values("date").set_index("date")
        return df[INVENTORY_COLUMNS]

    @staticmethod
    def _normalize_inventory_99(raw: pd.DataFrame) -> pd.DataFrame:
        """99期货库存: 日期/库存 → inventory/change（change 由差分派生）。"""
        if raw is None or raw.empty or not {"日期", "库存"}.issubset(raw.columns):
            return pd.DataFrame(columns=INVENTORY_COLUMNS)
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(raw["日期"]),
                "inventory": pd.to_numeric(raw["库存"], errors="coerce"),
            }
        ).dropna(subset=["date"])
        df = df.drop_duplicates(subset=["date"]).sort_values("date").set_index("date")
        df["change"] = df["inventory"].diff()
        return df[INVENTORY_COLUMNS]

    # ── 基差 ──

    def get_basis(self, symbol: str, days: int = 60) -> pd.DataFrame:
        """获取品种基差时序，index=date 升序。"""
        empty = pd.DataFrame(columns=BASIS_COLUMNS)
        variety = self._parse_variety(symbol)
        if variety is None:
            return empty
        key = f"basis:{variety}"
        cached = self._cache_get(key, self._cache_ttl_basis)
        if cached is not None:
            return cached
        df = self._fetch_basis(variety, days)
        if not df.empty:
            self._cache_set(key, df)
        return df

    def _fetch_basis(self, variety: str, days: int) -> pd.DataFrame:
        """获取品种基差时序。

        100ppi 源（akshare futures_spot_price_daily）为逐自然日串行请求，
        60 天窗口 = 95 次 HTTP 请求（实测 >40s）。优化：客户端按自然日窗口
        生成候选并跳过周末，ThreadPoolExecutor 并行逐日请求，硬上限 25 个
        自然日（约 18 个交易日，首调 ~3-8s），失败/非交易日跳过。
        """
        try:
            import akshare as ak  # type: ignore[import-untyped]
            from concurrent.futures import ThreadPoolExecutor, as_completed

            end = pd.Timestamp.now().normalize()
            # 硬上限 25 个自然日，控制 100ppi 逐日请求数量（约 18 个交易日）
            span = min(int(days * 1.5) + 5, 25)
            start = end - pd.Timedelta(days=span)

            dates: list[str] = []
            d = start
            while d <= end:
                if d.weekday() < 5:  # 跳过周末（非交易日由源端返回空跳过）
                    dates.append(d.strftime("%Y%m%d"))
                d += pd.Timedelta(days=1)

            def _fetch(day: str) -> pd.DataFrame | None:
                try:
                    return ak.futures_spot_price(day, [variety])
                except Exception as e:  # noqa: BLE001
                    logger.warning("期货基差[%s] 单日 %s 请求失败: %s", variety, day, e)
                    return None

            frames: list[pd.DataFrame] = []
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(_fetch, day) for day in dates]
                for fut in as_completed(futures):
                    raw = fut.result()
                    if raw is not None and not raw.empty:
                        frames.append(raw)
            if not frames:
                return pd.DataFrame(columns=BASIS_COLUMNS)
            merged = pd.concat(frames, ignore_index=True)
            df = self._normalize_basis(merged, variety)
            if not df.empty:
                logger.info("期货基差[%s] %d 行（并行 %d 日）", variety, len(df), len(dates))
                return df
        except Exception as e:  # noqa: BLE001
            logger.warning("期货基差[%s] 接口失败: %s", variety, e)
        return pd.DataFrame(columns=BASIS_COLUMNS)

    @staticmethod
    def _normalize_basis(raw: pd.DataFrame, variety: str) -> pd.DataFrame:
        """基差表 → 契约列（仅保留目标品种行，date 转 datetime 索引）。"""
        if raw is None or raw.empty or "date" not in raw.columns:
            return pd.DataFrame(columns=BASIS_COLUMNS)
        sub = raw[raw["symbol"] == variety] if "symbol" in raw.columns else raw
        cols = [c for c in BASIS_COLUMNS if c in sub.columns]
        if not cols:
            return pd.DataFrame(columns=BASIS_COLUMNS)
        df = sub[["date"] + cols].copy()
        df["date"] = pd.to_datetime(df["date"])
        df[cols] = df[cols].apply(pd.to_numeric, errors="coerce")
        df = df.dropna(subset=["date"]).drop_duplicates(subset=["date"]).sort_values("date").set_index("date")
        return df.reindex(columns=BASIS_COLUMNS)

    # ── 仓单 ──

    def get_warehouse_receipt(self, symbol: str, days: int = 60) -> pd.DataFrame:
        """获取品种交易所仓单时序（warehouse_receipt / change），index=date 升序。

        GAP-091（2026-08-11 关闭）: CZCE/GFEX 走官方接口，SHFE/DCE/INE 走东财
        RPT_FUTU_STOCKDATA（Choice 归一化注册仓单口径，与官方原始口径存在单位差异）；
        中金所股指返回空 df（降级，由注入点 NaN 兜底）。
        """
        empty = pd.DataFrame(columns=WAREHOUSE_COLUMNS)
        variety = self._parse_variety(symbol)
        if variety is None:
            return empty
        key = f"wr:{variety}"
        cached = self._cache_get(key, self._cache_ttl_warehouse)
        if cached is not None:
            return cached
        df = self._fetch_warehouse_receipt(variety, days)
        if not df.empty:
            self._cache_set(key, df)
        return df

    def _fetch_warehouse_receipt(self, variety: str, days: int) -> pd.DataFrame:
        """按交易所路由拉取仓单时序（CZCE/GFEX 官方并行逐日；SHFE/DCE/INE 东财单接口）。"""
        exchange = VARIETY_EXCHANGE.get(variety)
        if exchange in ("shfe", "dce", "ine"):
            return self._fetch_warehouse_receipt_em(variety, days)
        # 阶段 2：仅 CZCE/GFEX 走官方接口；中金所股指无商品仓单 → 空
        if exchange not in ("czce", "gfex"):
            return pd.DataFrame(columns=WAREHOUSE_COLUMNS)
        try:
            import akshare as ak  # type: ignore[import-untyped]
            from concurrent.futures import ThreadPoolExecutor, as_completed

            end = pd.Timestamp.now().normalize()
            # 硬上限 25 个自然日（约 18 个交易日），与基差窗口一致
            span = min(int(days * 1.5) + 5, 25)
            start = end - pd.Timedelta(days=span)

            dates: list[str] = []
            d = start
            while d <= end:
                if d.weekday() < 5:  # 跳过周末（非交易日由源端返回空跳过）
                    dates.append(d.strftime("%Y%m%d"))
                d += pd.Timedelta(days=1)

            api = getattr(
                ak, "futures_warehouse_receipt_czce" if exchange == "czce" else "futures_gfex_warehouse_receipt"
            )

            def _fetch(day: str) -> pd.DataFrame | None:
                try:
                    raw = api(day)
                    return self._normalize_warehouse_czce_gfex(raw, variety, day)
                except Exception as e:  # noqa: BLE001
                    logger.warning("期货仓单[%s] 单日 %s 请求失败: %s", variety, day, e)
                    return None

            frames: list[pd.DataFrame] = []
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(_fetch, day) for day in dates]
                for fut in as_completed(futures):
                    raw = fut.result()
                    if raw is not None and not raw.empty:
                        frames.append(raw)
            if not frames:
                return pd.DataFrame(columns=WAREHOUSE_COLUMNS)
            df = pd.concat(frames).sort_index()
            df = df[~df.index.duplicated(keep="first")]
            logger.info("期货仓单[%s] %d 行（并行 %d 日，%s 官方接口）", variety, len(df), len(dates), exchange)
            return df[WAREHOUSE_COLUMNS]
        except Exception as e:  # noqa: BLE001
            logger.warning("期货仓单[%s] 接口失败: %s", variety, e)
        return pd.DataFrame(columns=WAREHOUSE_COLUMNS)

    def _fetch_warehouse_receipt_em(self, variety: str, days: int) -> pd.DataFrame:
        """东财 RPT_FUTU_STOCKDATA 仓单（SHFE/DCE/INE，单接口返回全历史时序）。

        字段映射: ON_WARRANT_NUM → warehouse_receipt，ADDCHANGE → change。
        东财为 Choice 归一化注册仓单口径，历史仅保留近 3 个月。
        """
        code = EM_WAREHOUSE_MAP.get(variety)
        if not code:
            return pd.DataFrame(columns=WAREHOUSE_COLUMNS)
        try:
            import requests as _requests

            end = pd.Timestamp.now().normalize()
            span = min(int(days * 1.5) + 5, 200)
            start = (end - pd.Timedelta(days=span)).strftime("%Y-%m-%d")
            params = {
                "reportName": "RPT_FUTU_STOCKDATA",
                "columns": "SECURITY_CODE,TRADE_DATE,ON_WARRANT_NUM,ADDCHANGE",
                "filter": f"(SECURITY_CODE=\"{code}\")(TRADE_DATE>='{start}')",
                "pageNumber": "1",
                "pageSize": "2000",
                "sortTypes": "1",
                "sortColumns": "TRADE_DATE",
                "source": "WEB",
                "client": "WEB",
            }
            r = _requests.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                params=params,
                timeout=20,
            )
            j = r.json()
            rows = (j.get("result") or {}).get("data") or []
            if not rows:
                return pd.DataFrame(columns=WAREHOUSE_COLUMNS)
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["TRADE_DATE"], errors="coerce")
            df["warehouse_receipt"] = pd.to_numeric(df["ON_WARRANT_NUM"], errors="coerce")
            df["change"] = pd.to_numeric(df["ADDCHANGE"], errors="coerce")
            df = df.dropna(subset=["date"]).drop_duplicates(subset=["date"]).sort_values("date").set_index("date")
            logger.info("期货仓单[%s] %d 行（东财 RPT_FUTU_STOCKDATA）", variety, len(df))
            return df[WAREHOUSE_COLUMNS]
        except Exception as e:  # noqa: BLE001
            logger.warning("期货仓单[%s] 东财接口失败: %s", variety, e)
        return pd.DataFrame(columns=WAREHOUSE_COLUMNS)

    @staticmethod
    def _normalize_warehouse_czce_gfex(raw: object, variety: str, date: str) -> pd.DataFrame:
        """CZCE/GFEX 仓单单日 dict → 契约列。

        接口返回 {品种: DataFrame}，品种表按仓库/年度/等级/品牌分列，
        当日仓单总量 = 该品种表内仓单数量求和，当日增减同求。
        """
        if not isinstance(raw, dict) or variety not in raw:
            return pd.DataFrame(columns=WAREHOUSE_COLUMNS)
        df = raw[variety]
        if df is None or df.empty or "仓单数量" not in df.columns:
            return pd.DataFrame(columns=WAREHOUSE_COLUMNS)
        total = pd.to_numeric(df["仓单数量"], errors="coerce").sum()
        chg = float("nan")
        for col in ("当日增减", "仓单增减", "增减"):
            if col in df.columns:
                chg = pd.to_numeric(df[col], errors="coerce").sum()
                break
        return pd.DataFrame(
            {"warehouse_receipt": [total], "change": [chg]},
            index=pd.DatetimeIndex([pd.Timestamp(date)]),
        )


_default_futures_fundamental_provider: Optional[AkshareFuturesFundamentalProvider] = None


def get_futures_fundamental_provider() -> AkshareFuturesFundamentalProvider:
    """获取全局期货基本面 provider（惰性初始化）。"""
    global _default_futures_fundamental_provider
    if _default_futures_fundamental_provider is None:
        _default_futures_fundamental_provider = AkshareFuturesFundamentalProvider()
    return _default_futures_fundamental_provider
