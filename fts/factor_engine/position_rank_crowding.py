"""会员持仓排名拥挤度因子（GAP-069，Phase I / plans 25 §9）。

基于交易所会员持仓排名（前 N 会员多空持仓）计算持仓集中度/拥挤度信号：

- **数据源抽象**：`PositionRankProvider` 协议 + `AKSharePositionRankProvider` 实现
  （按品种前缀路由 dce/shfe/czce/cffex 四交易所）。
- **拥挤度指标**：CR_top_n（前 N 会员净持仓集中度）、多空比、净持仓占比，
  综合为 [0,1] 拥挤度分数。
- **信号方向**：低拥挤 = 持仓分散、趋势延续（看多 +1）；高拥挤 = 反转风险（-1）。
- **降级策略**：Provider 异常 / 空数据 / 单日行数不足 → 丢弃该日；全部不可用
  返回 None，调用方跳过，不阻断主流程。

数据字段规约（Provider 输出归一化列）：
    date / member / long_position / short_position / long_change / short_change
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 交易所 → AKShare 持仓排名接口名（getattr 动态获取，接口缺失/异常均降级）
_AKSHARE_RANK_API: dict[str, str] = {
    "dce": "futures_dce_position_rank",
    "shfe": "futures_shfe_position_rank",
    "czce": "futures_czce_position_rank",
    "cffex": "futures_cffex_position_rank",
}
# 品种前缀 → 交易所路由（覆盖 25 核心 + 全品种子集）
_PREFIX_EXCHANGE: dict[str, str] = {
    # DCE 大商所
    "m": "dce", "y": "dce", "p": "dce", "c": "dce", "cs": "dce",
    "i": "dce", "l": "dce", "v": "dce", "pp": "dce", "eg": "dce",
    "j": "dce", "jm": "dce", "jd": "dce", "pg": "dce", "lh": "dce",
    "eb": "dce", "rr": "dce", "b": "dce", "a": "dce",
    # SHFE 上期所（含能源中心 sc/lu/nr/ec）
    "cu": "shfe", "al": "shfe", "zn": "shfe", "pb": "shfe", "ni": "shfe",
    "sn": "shfe", "au": "shfe", "ag": "shfe", "rb": "shfe", "hc": "shfe",
    "ss": "shfe", "fu": "shfe", "bu": "shfe", "ru": "shfe", "sp": "shfe",
    "sc": "shfe", "lu": "shfe", "nr": "shfe", "ec": "shfe",
    # CZCE 郑商所
    "TA": "czce", "MA": "czce", "SR": "czce", "CF": "czce", "RM": "czce",
    "OI": "czce", "FG": "czce", "SA": "czce", "UR": "czce", "AP": "czce",
    "CJ": "czce", "PF": "czce", "PK": "czce", "CY": "czce", "SF": "czce",
    "SM": "czce", "ZC": "czce", "WH": "czce", "PM": "czce", "RI": "czce",
    # CFFEX 中金所
    "IF": "cffex", "IH": "cffex", "IC": "cffex", "IM": "cffex",
    "T": "cffex", "TF": "cffex", "TS": "cffex", "TL": "cffex",
}

_RANK_COLUMNS: tuple[str, ...] = (
    "date", "member", "long_position", "short_position",
    "long_change", "short_change",
)


@dataclass
class PositionRankConfig:
    """会员持仓排名拥挤度配置（契约见 plans/25 §9.2）。"""

    top_n: int = 20            # 前 N 会员
    min_rank_rows: int = 5     # 单日最少会员行数，不足降级跳过
    lookback_days: int = 5     # 时序拥挤度滚动窗口（信号平滑用）
    high_crowding: float = 0.7   # 高拥挤阈值（信号 -1）
    low_crowding: float = 0.3    # 低拥挤阈值（信号 +1）


@dataclass
class CrowdingResult:
    """单日拥挤度结果。"""

    symbol: str
    date: str
    cr_top_n: float           # 前 N 会员净持仓占会员总净持仓比
    long_short_ratio: float   # 前 N 多头持仓 / 空头持仓
    net_holding_ratio: float  # 前 N 净持仓 / 会员总净持仓
    crowding_score: float     # 综合拥挤度 ∈ [0,1]（越高越拥挤）
    rank_available: bool      # 数据可用标记（False=降级跳过）
    detail: dict[str, Any] = field(default_factory=dict)


class PositionRankProvider(Protocol):
    """会员持仓排名数据源协议。

    Returns:
        归一化 DataFrame（date/member/long_position/short_position/
        long_change/short_change）；异常或空数据返回空 DataFrame。
    """

    def get_rank(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame: ...


def _normalize_rank_columns(df: pd.DataFrame) -> pd.DataFrame:
    """归一化持仓排名列为统一规约；必需列缺失返回空 DataFrame（降级）。

    兼容 ak 接口列名差异：日期/会员/买量/卖量（常见于 dce/shfe/czce），
    变动量缺失时填 NaN（不参与计算）。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=list(_RANK_COLUMNS))
    mapping: dict[str, str] = {}
    for col in df.columns:
        cl = str(col)
        lc = cl.lower()
        if "日期" in cl or "date" in lc:
            mapping[cl] = "date"
        elif "会员" in cl or "member" in lc or "简称" in cl:
            mapping[cl] = "member"
        elif ("买单量" in cl or "long_position" in lc or "持买" in cl
              or "买持仓" in cl):
            mapping[cl] = "long_position"
        elif ("卖单量" in cl or "short_position" in lc or "持卖" in cl
              or "卖持仓" in cl):
            mapping[cl] = "short_position"
        elif "买单变化" in cl or "long_change" in lc or "买变" in cl:
            mapping[cl] = "long_change"
        elif "卖单变化" in cl or "short_change" in lc or "卖变" in cl:
            mapping[cl] = "short_change"
    norm = df.rename(columns=mapping)
    required = {"date", "member", "long_position", "short_position"}
    if not required.issubset(norm.columns):
        logger.warning("持仓排名列映射不足（缺 %s），降级返回空", required - set(norm.columns))
        return pd.DataFrame(columns=list(_RANK_COLUMNS))
    for c in _RANK_COLUMNS:
        if c not in norm.columns:
            norm[c] = np.nan
    out = norm[list(_RANK_COLUMNS)].copy()
    out["long_position"] = pd.to_numeric(out["long_position"], errors="coerce")
    out["short_position"] = pd.to_numeric(out["short_position"], errors="coerce")
    out["long_change"] = pd.to_numeric(out["long_change"], errors="coerce")
    out["short_change"] = pd.to_numeric(out["short_change"], errors="coerce")
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    return out.dropna(subset=["date", "long_position", "short_position"])


def _route_exchange(symbol: str) -> str | None:
    """品种代码 → 交易所路由（去掉数字后缀后按前缀最长匹配）。"""
    code = "".join(ch for ch in str(symbol).upper() if not ch.isdigit())
    best: str | None = None
    for prefix in _PREFIX_EXCHANGE:
        if code.startswith(prefix.upper()):
            if best is None or len(prefix) > len(best):
                best = prefix
    return _PREFIX_EXCHANGE.get(best) if best else None


class AKSharePositionRankProvider:
    """AKShare 四交易所持仓排名实现。

    接口名按交易所动态获取（`getattr(ak, api_name, None)`），接口缺失/网络
    异常/数据异常均捕获并返回空 DataFrame（降级，不阻断调用方）。
    """

    def __init__(self, symbol_to_contract: Any = None) -> None:
        self._symbol_to_contract = symbol_to_contract

    def get_rank(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        try:
            import akshare as ak
        except Exception as e:  # pragma: no cover - 缺依赖
            logger.warning("akshare 不可用: %s，持仓排名降级", e)
            return pd.DataFrame(columns=list(_RANK_COLUMNS))

        exchange = _route_exchange(symbol)
        if exchange is None:
            logger.debug("品种 %s 未路由到交易所，持仓排名跳过", symbol)
            return pd.DataFrame(columns=list(_RANK_COLUMNS))
        api_name = _AKSHARE_RANK_API.get(exchange)
        if api_name is None:
            return pd.DataFrame(columns=list(_RANK_COLUMNS))
        api = getattr(ak, api_name, None)
        if api is None:
            logger.warning("akshare 接口 %s 不存在，持仓排名降级", api_name)
            return pd.DataFrame(columns=list(_RANK_COLUMNS))

        # 合约代码（主连 RB0 → RB2610 或 RB）；优先真实主力，否则用品种代码
        contract = symbol
        if self._symbol_to_contract is not None:
            try:
                mapped = self._symbol_to_contract(symbol)
                if mapped:
                    contract = mapped
            except Exception:  # noqa: BLE001
                pass
        try:
            df = api(contract=contract) if start_date is None else api(
                date=start_date, symbols=contract
            )
        except Exception:
            # 部分接口需要 date 参数
            try:
                df = api(date=start_date, symbols=contract)
            except Exception as e:
                logger.warning("akshare 持仓排名获取失败 [%s/%s]: %s", symbol, exchange, e)
                return pd.DataFrame(columns=list(_RANK_COLUMNS))
        return _normalize_rank_columns(df)


def compute_crowding(
    rank_df: pd.DataFrame,
    config: PositionRankConfig,
    symbol: str = "",
) -> pd.DataFrame:
    """逐日计算拥挤度指标。

    Args:
        rank_df: 归一化持仓排名 DataFrame（date/member/long_position/short_position）。
        config: 拥挤度配置。
        symbol: 品种代码（写入结果）。

    Returns:
        DataFrame（date/cr_top_n/long_short_ratio/net_holding_ratio/
        crowding_score/rank_available）；数据不足返回空 DataFrame。
    """
    if rank_df is None or rank_df.empty:
        return pd.DataFrame()
    if "date" not in rank_df.columns:
        return pd.DataFrame()
    df = rank_df.copy()
    df["net_position"] = df["long_position"] - df["short_position"]
    df["date"] = pd.to_datetime(df["date"])
    top_n = max(int(config.top_n), 1)
    min_rows = max(int(config.min_rank_rows), 1)

    rows: list[dict[str, Any]] = []
    for date, grp in df.groupby(df["date"].dt.normalize()):
        grp = grp.dropna(subset=["net_position"])
        if len(grp) < min_rows:
            continue
        grp = grp.reindex(grp["net_position"].abs().sort_values(ascending=False).index)
        top = grp.head(top_n)
        total_net = float(grp["net_position"].sum())
        top_net = float(top["net_position"].sum())
        top_long = float(top["long_position"].sum())
        top_short = float(top["short_position"].sum())

        cr = top_net / total_net if abs(total_net) > 1e-9 else 0.0
        lsr = top_long / top_short if top_short > 1e-9 else 0.0
        net_ratio = top_net / total_net if abs(total_net) > 1e-9 else 0.0
        rows.append(
            {
                "date": date,
                "cr_top_n": float(cr),
                "long_short_ratio": float(lsr),
                "net_holding_ratio": float(net_ratio),
                "crowding_score": crowding_score(cr, lsr, net_ratio),
                "rank_available": True,
                "symbol": symbol,
            }
        )
    return pd.DataFrame(rows)


def crowding_score(cr: float, lsr: float, net_ratio: float) -> float:
    """综合拥挤度 ∈ [0,1]。

    拥挤度 = 0.5·|CR| + 0.3·min(|多空比-1|,1) + 0.2·min(|净占比|,1)——
    集中度（CR）为主，多空失衡与净持仓集中为辅；越接近 1 越拥挤。
    """
    s1 = min(abs(float(cr)), 1.0)
    s2 = min(abs(float(lsr) - 1.0), 1.0)
    s3 = min(abs(float(net_ratio)), 1.0)
    return float(np.clip(0.5 * s1 + 0.3 * s2 + 0.2 * s3, 0.0, 1.0))


def position_rank_crowding_signal(
    symbol: str,
    config: PositionRankConfig,
    provider: PositionRankProvider,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.Series | None:
    """拥挤度 → 方向信号（按日）。

    低拥挤（< low_crowding）= 持仓分散、趋势延续 → +1；
    高拥挤（> high_crowding）= 反转风险 → -1；中间 → 0。
    数据不可用返回 None（调用方跳过）。

    Args:
        symbol: 品种代码。
        config: 拥挤度配置。
        provider: 持仓排名数据源。
        start_date/end_date: 日期过滤（可选）。

    Returns:
        按日期索引的信号 Series（值 ∈ {-1,0,1}），数据不可用返回 None。
    """
    try:
        rank_df = provider.get_rank(symbol, start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning("持仓排名获取异常 [%s]: %s，信号跳过", symbol, e)
        return None
    metrics = compute_crowding(rank_df, config, symbol=symbol)
    if metrics is None or metrics.empty:
        return None
    metrics = metrics.sort_values("date").reset_index(drop=True)
    scores = metrics["crowding_score"]
    if config.lookback_days > 1:
        scores = scores.rolling(config.lookback_days, min_periods=1).mean()
    sig = np.where(
        scores <= config.low_crowding,
        1.0,
        np.where(scores >= config.high_crowding, -1.0, 0.0),
    )
    return pd.Series(sig, index=metrics["date"], name=f"{symbol}_crowding_signal")
