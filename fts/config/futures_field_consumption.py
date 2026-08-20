"""
fts.config.futures_field_consumption — FTS 期货字段消费字典（SSOT）

每日同步任务 `sync_futures_data_job` 严格按本字典执行：字典登记的全部字段
必须在每日同步中产出数据；无消费的字段不拉取、不同步。

三类字段组（v2.103.0 起全字段每日同步）:
  ① kline（17）         : 通道 kline_cache（DuckDB），已有每日同步
  ② fundamental（9）    : 通道 futures_fundamental（Parquet 缓存），新增每日同步
  ③ term_structure（3） : 通道 futures_term_structure（Parquet 缓存），新增每日同步

字段消费来源:
  - 因子引擎期货种子因子（seed_data_futures_full.py）input_fields / 因子代码
  - FTSDataProvider.enrich_futures_fundamental（fts/data.py）注入字段
  - 信号管道（scripts/futures_signal_pipeline.py）与 L3 Portfolio Loop 消费字段

HARNESS §契约优先: 本字典为每日同步范围的权威清单（SSOT），
新增被消费字段必须先登记本字典，再扩展同步通道。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 字段组类型：kline 行情 / fundamental 基本面 / term_structure 期限结构
FieldGroup = Literal["kline", "fundamental", "term_structure"]


class FieldConsumption(BaseModel):
    """单个字段的消费登记条目。

    Attributes:
        field: 字段名（与数据产出 DataFrame/表列名一致）。
        group: 所属字段组。
        channel: 同步通道（kline_cache / futures_fundamental / futures_term_structure）。
        source: 数据源说明。
        coverage: 覆盖范围（历史长度 / 品种限制等已知边界）。
        consumers: 消费方清单（因子族 / 管道 / 下游）。
    """

    field: str
    group: FieldGroup
    channel: str
    source: str
    coverage: str
    consumers: list[str] = Field(default_factory=list)


# ─── 三组字段定义（与 kline_cache schema / enrich_futures_fundamental / 期限结构计算对齐）──

_KLINE_FIELDS: list[FieldConsumption] = [
    FieldConsumption(
        field="symbol", group="kline", channel="kline_cache",
        source="QUANTDATA（唯一数据源 v3.0.0+1，+ DUCKDB_CACHE 读取缓存 + SYNTHETIC 兜底）",
        coverage="全 82 品种连续合约（v2.89.0 起全品种）",
        consumers=["全部期货下游"],
    ),
    FieldConsumption(
        field="period", group="kline", channel="kline_cache",
        source="同上", coverage="daily 为主，分钟级另走 minute_cache",
        consumers=["全部期货下游"],
    ),
    FieldConsumption(
        field="date", group="kline", channel="kline_cache",
        source="同上", coverage="120 天回溯增量",
        consumers=["全部期货下游"],
    ),
    FieldConsumption(
        field="open", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种",
        consumers=["种子因子", "信号管道", "L3 Portfolio Loop"],
    ),
    FieldConsumption(
        field="high", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种",
        consumers=["种子因子", "信号管道", "L3 Portfolio Loop"],
    ),
    FieldConsumption(
        field="low", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种",
        consumers=["种子因子", "信号管道", "L3 Portfolio Loop"],
    ),
    FieldConsumption(
        field="close", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种",
        consumers=["种子因子", "信号管道", "L3 Portfolio Loop"],
    ),
    FieldConsumption(
        field="volume", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种",
        consumers=["种子因子（换手/成交类）", "信号管道"],
    ),
    FieldConsumption(
        field="amount", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种",
        consumers=["成交额类因子"],
    ),
    FieldConsumption(
        field="hold", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种（持仓量）",
        consumers=["持仓量类因子", "数据级监控 key_fields"],
    ),
    FieldConsumption(
        field="settle", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种（结算价）",
        consumers=["结算价偏离/基差近似因子"],
    ),
    FieldConsumption(
        field="pre_settle", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种（前结算价）",
        consumers=["结算价缺口勾稽"],
    ),
    FieldConsumption(
        field="oi_change", group="kline", channel="kline_cache",
        source="TQ 增强源提供", coverage="全 82 品种（持仓量变化）",
        consumers=["持仓变化类因子"],
    ),
    FieldConsumption(
        field="vwap", group="kline", channel="kline_cache",
        source="同上", coverage="全 82 品种（成交均价）",
        consumers=["VWAP 改造类因子"],
    ),
    FieldConsumption(
        field="source", group="kline", channel="kline_cache",
        source="降级链命中源记录", coverage="血缘元数据",
        consumers=["数据血缘追踪"],
    ),
    FieldConsumption(
        field="fetched_at", group="kline", channel="kline_cache",
        source="同步执行时间", coverage="血缘元数据",
        consumers=["数据新鲜度监控"],
    ),
    FieldConsumption(
        field="trace_id", group="kline", channel="kline_cache",
        source="HARNESS trace_id", coverage="血缘元数据",
        consumers=["全链路追踪"],
    ),
]

_FUNDAMENTAL_FIELDS: list[FieldConsumption] = [
    FieldConsumption(
        field="fut_inventory", group="fundamental", channel="futures_fundamental",
        source="AKShare futures_inventory_em（东财）→ futures_inventory_99 兜底",
        coverage="商品品种；股指无库存；东财近期为主、99 期货 2009 起",
        consumers=["enrich_futures_fundamental", "库存类因子"],
    ),
    FieldConsumption(
        field="fut_inventory_chg", group="fundamental", channel="futures_fundamental",
        source="同上（库存增减）", coverage="随 fut_inventory 可用性",
        consumers=["enrich_futures_fundamental", "库存变化类因子"],
    ),
    FieldConsumption(
        field="fut_warehouse_receipt", group="fundamental", channel="futures_fundamental",
        source="CZCE/GFEX AKShare 官方；SHFE/DCE/INE 东财 Choice 归一化口径",
        coverage="CZCE/GFEX 真实；SHFE/DCE/INE 仅近 3 个月；股指无仓单",
        consumers=["enrich_futures_fundamental", "仓单类因子"],
    ),
    FieldConsumption(
        field="fut_warehouse_receipt_chg", group="fundamental", channel="futures_fundamental",
        source="同上（仓单增减）", coverage="随 fut_warehouse_receipt 可用性",
        consumers=["enrich_futures_fundamental"],
    ),
    FieldConsumption(
        field="fut_spot_price", group="fundamental", channel="futures_fundamental",
        source="AKShare futures_spot_price_daily；缺失时 WebSearch 补充并校验",
        coverage="2021 起；缺失时 WebSearch 补充（新鲜度/正确性/单位对齐）",
        consumers=["enrich_futures_fundamental", "基差类因子"],
    ),
    FieldConsumption(
        field="fut_near_basis", group="fundamental", channel="futures_fundamental",
        source="AKShare futures_spot_price_daily（近月基差）", coverage="2021 起",
        consumers=["enrich_futures_fundamental", "基差类因子"],
    ),
    FieldConsumption(
        field="fut_dom_basis", group="fundamental", channel="futures_fundamental",
        source="AKShare futures_spot_price_daily（主力基差）", coverage="2021 起",
        consumers=["enrich_futures_fundamental", "基差类因子"],
    ),
    FieldConsumption(
        field="fut_near_basis_rate", group="fundamental", channel="futures_fundamental",
        source="AKShare futures_spot_price_daily（近月基差率）", coverage="2021 起",
        consumers=["enrich_futures_fundamental", "基差率因子"],
    ),
    FieldConsumption(
        field="fut_dom_basis_rate", group="fundamental", channel="futures_fundamental",
        source="AKShare futures_spot_price_daily（主力基差率）", coverage="2021 起",
        consumers=["enrich_futures_fundamental", "基差率因子"],
    ),
]

_TERM_STRUCTURE_FIELDS: list[FieldConsumption] = [
    FieldConsumption(
        field="term_spread", group="term_structure", channel="futures_term_structure",
        source="contract_kline 多合约截面计算（近月-远月价差率）",
        coverage="全 82 品种（活跃合约数 ≥2 时产出）",
        consumers=["fut_roll_yield_carry", "fut_stable_term_structure", "fut_basis_factor"],
    ),
    FieldConsumption(
        field="roll_yield", group="term_structure", channel="futures_term_structure",
        source="contract_kline 截面展期收益（年化）", coverage="全 82 品种",
        consumers=["fut_roll_yield_carry 家族"],
    ),
    FieldConsumption(
        field="near_contract", group="term_structure", channel="futures_term_structure",
        source="contract_kline 截面合约标识", coverage="血缘元数据",
        consumers=["期限结构因子的可解释性/血缘"],
    ),
    FieldConsumption(
        field="far_contract", group="term_structure", channel="futures_term_structure",
        source="contract_kline 截面合约标识", coverage="血缘元数据",
        consumers=["期限结构因子的可解释性/血缘"],
    ),
]


class FuturesFieldConsumptionConfig(BaseModel):
    """FTS 期货字段消费字典（SSOT）——每日同步的权威字段清单。

    Attributes:
        fields: 全部已登记消费字段。
    """

    fields: list[FieldConsumption]

    def field_names(self, group: FieldGroup | None = None) -> list[str]:
        """返回字段名列表；group 非空时仅返回该组字段。"""
        if group is None:
            return [f.field for f in self.fields]
        return [f.field for f in self.fields if f.group == group]

    def groups(self) -> dict[FieldGroup, list[str]]:
        """返回 {组名: [字段名]} 分组映射。"""
        result: dict[FieldGroup, list[str]] = {"kline": [], "fundamental": [], "term_structure": []}
        for f in self.fields:
            result[f.group].append(f.field)  # type: ignore[index]
        return result

    def channels(self) -> dict[str, list[str]]:
        """返回 {通道: [字段名]} 映射（供同步任务按通道扫描）。"""
        result: dict[str, list[str]] = {}
        for f in self.fields:
            result.setdefault(f.channel, []).append(f.field)
        return result

    def validate_unique(self) -> None:
        """校验字段名唯一性（重复登记立即报错，防止字典污染）。"""
        names = [f.field for f in self.fields]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"字段消费字典存在重复登记: {dupes}")


# 全字段消费字典（SSOT 实例，每日同步任务引用）
FUTURES_FIELD_CONSUMPTION: FuturesFieldConsumptionConfig = FuturesFieldConsumptionConfig(
    fields=[*_KLINE_FIELDS, *_FUNDAMENTAL_FIELDS, *_TERM_STRUCTURE_FIELDS]
)

# 启动时校验字典唯一性
FUTURES_FIELD_CONSUMPTION.validate_unique()

__all__ = [
    "FieldConsumption",
    "FuturesFieldConsumptionConfig",
    "FUTURES_FIELD_CONSUMPTION",
    "FieldGroup",
]
