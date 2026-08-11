"""fts.data_sources.macro_eastmoney_source — 东财 + 中债登宏观数据源。

替代 iFinD EDB 作为宏观注入默认源（iFinD MCP 需 API Key，2026-08-11 实测不可用，
get_macro_series 返回 None）。提供与 `IFindSource.get_macro_series` 兼容接口，
供 `MacroFieldAligner` 无感替换。

数据源（GAP-088 宏观闭环补强，2026-08-11）:
  - cpi（中国CPI当月同比）: 东财 RPT_ECONOMY_CPI NATIONAL_SAME（月频，2013 起）
  - export（中国出口金额当月值）: 东财 RPT_ECONOMY_CUSTOMS EXIT_BASE（万元人民币，月频）
  - import_data（中国进口金额当月值）: 东财 RPT_ECONOMY_CUSTOMS IMPORT_BASE（万元人民币，月频）
  - rate（中国1年期国债收益率）: 中债登 bond_china_yield「中债国债收益率曲线 1年」（日频）
  - us_bond（美国10年期国债收益率）: bond_zh_us_rate「美国国债收益率10年」（日频）

缓存: 复用 edb_cache 表（indicator/date/value），与 IFindSource 同构互操作——
    iFinD 缓存过的指标新源直接复用，新源拉取后 iFinD 亦可复用。

HARNESS §契约优先: get_macro_series 签名与返回契约对齐 IFindSource。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 东财数据中心 API
_EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/126.0.0.0 Safari/537.36"
)

# 东财报表: 字段名 → (报表, 取值列, 日期列)
_EM_REPORTS: dict[str, tuple[str, str, str]] = {
    "cpi": ("RPT_ECONOMY_CPI", "NATIONAL_SAME", "REPORT_DATE"),
    "export": ("RPT_ECONOMY_CUSTOMS", "EXIT_BASE", "REPORT_DATE"),
    "import_data": ("RPT_ECONOMY_CUSTOMS", "IMPORT_BASE", "REPORT_DATE"),
}

# 中债登曲线名（bond_china_yield 返回多曲线，取国债收益率曲线）
_CN_BOND_CURVE = "中债国债收益率曲线"

# 因子字段中文查询词（MACRO_FIELD_QUERIES）→ 内部 key
_INDICATOR_KEYS: dict[str, str] = {
    "中国CPI当月同比": "cpi",
    "中国出口金额当月值": "export",
    "中国进口金额当月值": "import_data",
    "中国1年期国债收益率": "rate",
    "美国10年期国债收益率": "us_bond",
}


class EastmoneyMacroSource:
    """东财/中债登宏观数据源（get_macro_series 兼容 IFindSource）。

    Args:
        cache_db_path: edb_cache DuckDB 路径（None = 默认 data/fts_history.duckdb）。
    """

    source_name = "EASTMONEY"

    def __init__(self, cache_db_path: Any | None = None) -> None:
        self._cache_db_path = cache_db_path

    # ─── 对外接口（与 IFindSource 对齐）───

    def get_macro_series(
        self,
        indicator: str,
        start_date: str = "",
        end_date: str = "",
        db_path: Any | None = None,
        trace_id: str = "",
    ) -> Optional[pd.Series]:
        """获取宏观指标时序（优先读 edb_cache 缓存，miss 拉取并写回）。

        Args:
            indicator: 指标中文名（MACRO_FIELD_QUERIES 查询词）
            start_date: 起始日期 YYYY-MM-DD（可选）
            end_date: 截止日期 YYYY-MM-DD（可选）
            db_path: DuckDB 路径（默认 data/fts_history.duckdb）
            trace_id: 链路追踪 ID

        Returns:
            DatetimeIndex Series（date → value），拉取失败返回 None。
        """
        key = _INDICATOR_KEYS.get(indicator)
        if key is None:
            logger.warning("[EASTMONEY] 未映射指标 %s，返回 None", indicator)
            return None
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent / "data" / "fts_history.duckdb"
        db_path = Path(db_path)

        cached = self._read_edb_cache(db_path, indicator, start_date, end_date)
        if cached is not None and not cached.empty:
            logger.debug("[EASTMONEY] edb_cache 命中 [%s]: %d 点", indicator, len(cached))
            return cached

        try:
            series = self._fetch(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("[EASTMONEY] 拉取失败 [%s]: %s", indicator, e)
            return None
        if series is None or series.empty:
            return None

        rows = [
            {
                "indicator": indicator,
                "date": idx.date().isoformat(),
                "value": float(val),
                "unit": key,
                "source": self.source_name,
                "fetched_at": pd.Timestamp.now().isoformat(),
                "trace_id": trace_id or f"fts.macro_em.{int(time.time())}",
            }
            for idx, val in series.items()
            if pd.notna(val)
        ]
        self._write_edb_cache(db_path, rows)
        logger.info("[EASTMONEY] 拉取 [%s]: %d 点，已写 edb_cache", indicator, len(series))
        return series

    # ─── 拉取器 ───

    def _fetch(self, key: str) -> Optional[pd.Series]:
        if key in _EM_REPORTS:
            return self._fetch_em(*_EM_REPORTS[key])
        if key == "rate":
            return self._fetch_cn_1y()
        if key == "us_bond":
            return self._fetch_us_10y()
        return None

    @staticmethod
    def _fetch_em(report: str, column: str, date_col: str) -> Optional[pd.Series]:
        """东财数据中心报表 → DatetimeIndex Series（升序）。"""
        import requests

        params = {
            "reportName": report,
            "columns": f"{date_col},{column}",
            "pageNumber": "1", "pageSize": "5000",
            "sortTypes": "1", "sortColumns": date_col,
            "source": "WEB", "client": "WEB",
        }
        r = requests.get(_EM_URL, params=params, headers={"User-Agent": _UA}, timeout=20)
        j = r.json()
        rows = (j.get("result") or {}).get("data") or []
        if not rows:
            return None
        df = pd.DataFrame(rows)
        s = pd.Series(
            pd.to_numeric(df[column], errors="coerce").values,
            index=pd.to_datetime(df[date_col], errors="coerce"),
        )
        s = s.dropna().sort_index()
        return s if not s.empty else None

    @staticmethod
    def _fetch_cn_1y() -> Optional[pd.Series]:
        """中债登 1 年期国债收益率（日频）。接口限 end-start < 1 年 → 分年拼接。"""
        import akshare as ak  # type: ignore[import-untyped]

        end = pd.Timestamp.now().normalize()
        frames: list[pd.Series] = []
        # 覆盖约 3.5 年（700 交易日 + 余量）
        for i in range(4):
            end_i = end - pd.Timedelta(days=365 * i)
            start_i = end_i - pd.Timedelta(days=360)
            try:
                df = ak.bond_china_yield(
                    start_date=start_i.strftime("%Y%m%d"),
                    end_date=end_i.strftime("%Y%m%d"),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EASTMONEY] bond_china_yield 分段 %s 失败: %s", start_i.date(), e)
                continue
            if df is None or df.empty or "1年" not in df.columns:
                continue
            sub = df[df["曲线名称"] == _CN_BOND_CURVE]
            if sub.empty:
                continue
            s = pd.Series(
                pd.to_numeric(sub["1年"], errors="coerce").values,
                index=pd.to_datetime(sub["日期"], errors="coerce"),
            )
            frames.append(s.dropna())
        if not frames:
            return None
        s = pd.concat(frames).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        return s if not s.empty else None

    @staticmethod
    def _fetch_us_10y() -> Optional[pd.Series]:
        """美国 10 年期国债收益率（日频，bond_zh_us_rate）。"""
        import akshare as ak  # type: ignore[import-untyped]

        start = (pd.Timestamp.now().normalize() - pd.Timedelta(days=1400)).strftime("%Y%m%d")
        try:
            df = ak.bond_zh_us_rate(start_date=start)
        except Exception as e:  # noqa: BLE001
            logger.warning("[EASTMONEY] bond_zh_us_rate 失败: %s", e)
            return None
        if df is None or df.empty or "美国国债收益率10年" not in df.columns:
            return None
        s = pd.Series(
            pd.to_numeric(df["美国国债收益率10年"], errors="coerce").values,
            index=pd.to_datetime(df["日期"], errors="coerce"),
        )
        s = s.dropna().sort_index()
        return s if not s.empty else None

    # ─── edb_cache 缓存（与 IFindSource 同构）───

    @staticmethod
    def _read_edb_cache(
        db_path: Path,
        indicator: str,
        start_date: str = "",
        end_date: str = "",
    ) -> Optional[pd.Series]:
        if not db_path.exists():
            return None
        try:
            import duckdb  # type: ignore[import-untyped]

            con = duckdb.connect(str(db_path), read_only=True)
            try:
                sql = (
                    "SELECT date, value FROM edb_cache "
                    "WHERE indicator = ? AND value IS NOT NULL"
                )
                params: list[Any] = [indicator]
                if start_date:
                    sql += " AND date >= CAST(? AS DATE)"
                    params.append(start_date)
                if end_date:
                    sql += " AND date <= CAST(? AS DATE)"
                    params.append(end_date)
                sql += " ORDER BY date"
                df = con.execute(sql, params).df()
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("[EASTMONEY] edb_cache 读取失败 [%s]: %s", indicator, e)
            return None
        if df is None or df.empty:
            return None
        s = pd.Series(
            pd.to_numeric(df["value"], errors="coerce").values,
            index=pd.to_datetime(df["date"], errors="coerce"),
        )
        return s.dropna().sort_index()

    @staticmethod
    def _write_edb_cache(db_path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        try:
            import duckdb  # type: ignore[import-untyped]
            from fts.data_sources.migrate import migrate_schema

            migrate_schema(str(db_path))
            con = duckdb.connect(str(db_path))
            try:
                for row in rows:
                    con.execute(
                        """
                        INSERT OR REPLACE INTO edb_cache
                            (indicator, date, value, unit, source, fetched_at, trace_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["indicator"],
                            row["date"],
                            row["value"],
                            row.get("unit", ""),
                            row.get("source", ""),
                            row.get("fetched_at", ""),
                            row.get("trace_id", ""),
                        ),
                    )
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("[EASTMONEY] edb_cache 写入失败 [%s]: %s", rows[0]["indicator"], e)


__all__ = ["EastmoneyMacroSource"]
