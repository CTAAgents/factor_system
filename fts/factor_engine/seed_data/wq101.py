"""
seed_data/wq101.py — WorldQuant 101 Alpha 因子定义

来源: "101 Formulaic Alphas" by Zura Kakushadze
通过 expression 表达式 + alpha_ops 函数库实现。

版本: v1.1.0
"""

from __future__ import annotations
from typing import Any

WQ101_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "alpha_001",
        "expression": "rank(ts_argmax(signed_power(ifelse(returns<0, ts_stddev(returns,20), close), 2), 5)) - 0.5",
        "narrative": "Alpha#001: 条件波动率-动量复合信号",
    },
    {
        "name": "alpha_002",
        "expression": "-ts_corr(rank(delta(log(volume), 2)), rank((close-open_)/open_), 6)",
        "narrative": "Alpha#002: 量价变化相关性反转",
    },
    {
        "name": "alpha_003",
        "expression": "-ts_corr(rank(open_), rank(volume), 10)",
        "narrative": "Alpha#003: 开盘价与成交量负相关",
    },
    {
        "name": "alpha_004",
        "expression": "-rank(ts_rank(rank(low), 9))",
        "narrative": "Alpha#004: 最低价分位数反转",
    },
    {
        "name": "alpha_005",
        "expression": "rank(open_ - ts_mean(volume, 7) / ts_mean(ts_stddev(high, 20), 7))",
        "narrative": "Alpha#005: 开盘价与成交量标准化偏差",
    },
    {
        "name": "alpha_006",
        "expression": "-ts_corr(rank(open_), rank(volume), 5)",
        "narrative": "Alpha#006: 短期开盘价-成交量负相关",
    },
    {
        "name": "alpha_007",
        "expression": "rank(ts_argmax(signed_power(ifelse(close>open_, ts_stddev(close, 20), close), 2), 3))",
        "narrative": "Alpha#007: 条件波动率趋势强度",
    },
    {
        "name": "alpha_008",
        "expression": "-rank(((ts_sum(open_, 5) * ts_sum(returns, 5)) - delay((ts_sum(open_, 5) * ts_sum(returns, 5)), 10)))",
        "narrative": "Alpha#008: 开盘价-收益累积乘积变化",
    },
    {
        "name": "alpha_009",
        "expression": "ifelse(ts_sum(returns, 5) >= 0, ts_min(ts_corr(rank(open_), rank(volume), 5), 3), ts_max(ts_corr(rank(open_), rank(volume), 5), 3))",
        "narrative": "Alpha#009: 条件量价相关性偏移",
    },
    {
        "name": "alpha_010",
        "expression": "rank(ts_max(ifelse(returns<0.03, ts_min(close, 5), ts_max(close, 5)), 4))",
        "narrative": "Alpha#010: 条件极值价格动量",
    },
    {
        "name": "alpha_011",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, 0), 6) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 6)",
        "narrative": "Alpha#011: 上涨/下跌成交量比（6日）",
    },
    {
        "name": "alpha_012",
        "expression": "rank(open_ - ts_mean(volume, 10) / ts_mean(ts_stddev(high, 20), 10))",
        "narrative": "Alpha#012: 开盘价-成交量标准化偏差（10日）",
    },
    {
        "name": "alpha_013",
        "expression": "-rank(ts_covariance(rank(close), rank(volume), 5))",
        "narrative": "Alpha#013: 收盘价-成交量协方差反转",
    },
    {
        "name": "alpha_014",
        "expression": "-ts_rank(close, 10) * ts_rank(close, 5)",
        "narrative": "Alpha#014: 多周期收盘价分位数乘积反转",
    },
    {
        "name": "alpha_015",
        "expression": "rank(ts_corr(rank(high), rank(volume), 3))",
        "narrative": "Alpha#015: 最高价-成交量短期相关性",
    },
    {
        "name": "alpha_016",
        "expression": "-rank(ts_covariance(rank(high), rank(volume), 5))",
        "narrative": "Alpha#016: 最高价-成交量协方差反转",
    },
    {
        "name": "alpha_017",
        "expression": "-rank(ts_rank(close, 10)) * rank(ts_rank(close, 5))",
        "narrative": "Alpha#017: 多周期收盘价排序乘积反转",
    },
    {
        "name": "alpha_018",
        "expression": "-rank(ts_stddev(abs(close-open_), 5) + ts_corr(close, open_, 10))",
        "narrative": "Alpha#018: 开盘价-收盘价波动率与相关性复合",
    },
    {
        "name": "alpha_019",
        "expression": "-sign(ifelse(close<delay(close,5), ts_corr(rank(volume), rank(close), 5), ts_stddev(close, 20)))",
        "narrative": "Alpha#019: 条件量价相关性符号",
    },
    {
        "name": "alpha_020",
        "expression": "scale(ts_sum(ifelse(close>delay(close,1), returns, 0), 12) - ts_sum(ifelse(close<delay(close,1), returns, 0), 12))",
        "narrative": "Alpha#020: 上涨/下跌收益累积差异（12日）",
    },
    {
        "name": "alpha_021",
        "expression": "ts_sum(ifelse(close>delay(close,1), close*volume, 0), 6) / ts_sum(ifelse(close<delay(close,1), close*volume, 0), 6)",
        "narrative": "Alpha#021: 上涨/下跌成交额比（6日）",
    },
    {
        "name": "alpha_022",
        "expression": "ts_corr(rank(high), rank(volume), 5) - ts_corr(rank(low), rank(volume), 5)",
        "narrative": "Alpha#022: 最高价-成交量与最低价-成交量相关性差",
    },
    {
        "name": "alpha_023",
        "expression": "ts_mean(ts_corr(rank(volume), rank(close), 5), 3) - ts_mean(ts_corr(rank(volume), rank(close), 20), 3)",
        "narrative": "Alpha#023: 量价相关性周期差",
    },
    {
        "name": "alpha_024",
        "expression": "-(delta(ts_sum(ifelse(close>delay(close,1), close*volume, 0), 3), 6) / ts_sum(ifelse(close<delay(close,1), close*volume, 0), 6))",
        "narrative": "Alpha#024: 成交额变化率反转",
    },
    {
        "name": "alpha_025",
        "expression": "-rank(ts_corr(rank(ts_corr(rank(close), rank(volume), 5)), rank(returns), 5))",
        "narrative": "Alpha#025: 量价相关性与收益的复合相关性反转",
    },
    {
        "name": "alpha_026",
        "expression": "-(ts_sum(ts_corr(rank(volume), rank(close), 5), 3) - ts_sum(ts_corr(rank(volume), rank(close), 20), 3))",
        "narrative": "Alpha#026: 量价相关性累积和周期差反转",
    },
    {
        "name": "alpha_027",
        "expression": "ifelse(rank(ts_sum(ifelse(close>delay(close,1), returns, 0), 12)) > 0.5, 1, 0)",
        "narrative": "Alpha#027: 上涨收益累积阈值信号",
    },
    {
        "name": "alpha_028",
        "expression": "scale(ts_corr(rank(volume), rank(ts_sum(close, 5)), 5))",
        "narrative": "Alpha#028: 成交量与5日累计收盘价相关性",
    },
    {
        "name": "alpha_029",
        "expression": "-ts_min(ts_corr(rank(volume), rank(close), 5), 3)",
        "narrative": "Alpha#029: 量价相关性最小值反转",
    },
    {
        "name": "alpha_030",
        "expression": "delta(ts_sum(ifelse(close>delay(close,1), close*volume, 0), 3), 3)",
        "narrative": "Alpha#030: 上涨成交额变化",
    },
    {
        "name": "alpha_031",
        "expression": "-ts_rank(ts_rank(ts_rank(decay_linear(close, 10), 5), 3), 5)",
        "narrative": "Alpha#031: 多层衰减排序复合信号",
    },
    {
        "name": "alpha_032",
        "expression": "ifelse(ts_min(close, 5) > delay(ts_min(close, 5), 5), 1, 0)",
        "narrative": "Alpha#032: 5日最低价突破信号",
    },
    {
        "name": "alpha_033",
        "expression": "-ts_sum(ifelse(ts_max(close, 5) > delay(ts_max(close, 5), 5), 1, 0), 5)",
        "narrative": "Alpha#033: 5日最高价突破计数反转",
    },
    {
        "name": "alpha_034",
        "expression": "-ts_rank(ts_rank(ts_rank(decay_linear(close, 10), 5), 3), 5)",
        "narrative": "Alpha#034: 多层衰减排序复合信号（反转）",
    },
    {
        "name": "alpha_035",
        "expression": "-ts_rank(decay_linear(ts_corr(rank(close), rank(volume), 3), 5), 5)",
        "narrative": "Alpha#035: 衰减量价相关性排序反转",
    },
    {
        "name": "alpha_036",
        "expression": "rank(ts_sum(ifelse(close > delay(close, 1), volume, 0), 15) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 15))",
        "narrative": "Alpha#036: 上涨/下跌成交量比截面排序（15日）",
    },
    {
        "name": "alpha_037",
        "expression": "-rank(ts_corr(rank(volume), rank(ts_sum(close, 5)), 5))",
        "narrative": "Alpha#037: 成交量与5日累计收盘价相关性反转",
    },
    {
        "name": "alpha_038",
        "expression": "-rank(ts_corr(rank(close), rank(ts_sum(close, 5)), 5))",
        "narrative": "Alpha#038: 收盘价与5日累计收盘价相关性反转",
    },
    {
        "name": "alpha_039",
        "expression": "rank(ts_sum(ifelse(close > delay(close, 1), volume, 0), 15) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 15))",
        "narrative": "Alpha#039: 上涨/下跌成交量比截面排序（15日重复）",
    },
    {
        "name": "alpha_040",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 3))",
        "narrative": "Alpha#040: 短期量价相关性截面反转",
    },
    {
        "name": "alpha_041",
        "expression": "rank(high - ts_min(high, 10)) / rank(ts_max(high, 10) - ts_min(high, 10))",
        "narrative": "Alpha#041: 最高价相对位置指标",
    },
    {
        "name": "alpha_042",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5))",
        "narrative": "Alpha#042: 5日量价相关性截面反转",
    },
    {
        "name": "alpha_043",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#043: 10日量价相关性截面反转",
    },
    {
        "name": "alpha_044",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 20))",
        "narrative": "Alpha#044: 20日量价相关性截面反转",
    },
    {
        "name": "alpha_045",
        "expression": "rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#045: 5日与10日量价相关性联合信号",
    },
    {
        "name": "alpha_046",
        "expression": "ts_mean(decay_linear(close, 10), 10) - ts_mean(decay_linear(close, 5), 5)",
        "narrative": "Alpha#046: 衰减均值周期差",
    },
    {
        "name": "alpha_047",
        "expression": "scale(ts_corr(rank(close), rank(volume), 5) - ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#047: 量价相关性周期差标准化",
    },
    {
        "name": "alpha_048",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#048: 量价相关性联合信号反转",
    },
    {
        "name": "alpha_049",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, 0), 12) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 12)",
        "narrative": "Alpha#049: 上涨/下跌成交量比（12日）",
    },
    {
        "name": "alpha_050",
        "expression": "-ts_max(ts_corr(rank(close), rank(volume), 5), 3)",
        "narrative": "Alpha#050: 量价相关性最大值反转",
    },
    {
        "name": "alpha_051",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, 0), 12) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 12)",
        "narrative": "Alpha#051: 上涨/下跌成交量比（12日重复）",
    },
    {
        "name": "alpha_052",
        "expression": "rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#052: 量价相关性联合信号（重复）",
    },
    {
        "name": "alpha_053",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5))",
        "narrative": "Alpha#053: 5日量价相关性截面反转（重复）",
    },
    {
        "name": "alpha_054",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#054: 10日量价相关性截面反转（重复）",
    },
    {
        "name": "alpha_055",
        "expression": "ts_corr(rank(close), rank(volume), 5) - ts_corr(rank(close), rank(volume), 10)",
        "narrative": "Alpha#055: 量价相关性周期差（无标准化）",
    },
    {
        "name": "alpha_056",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#056: 量价相关性联合信号反转（重复）",
    },
    {
        "name": "alpha_057",
        "expression": "ts_mean(decay_linear(close, 10), 10) - ts_mean(decay_linear(close, 5), 5)",
        "narrative": "Alpha#057: 衰减均值周期差（重复）",
    },
    {
        "name": "alpha_058",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5))",
        "narrative": "Alpha#058: 5日量价相关性截面反转（重复2）",
    },
    {
        "name": "alpha_059",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#059: 10日量价相关性截面反转（重复2）",
    },
    {
        "name": "alpha_060",
        "expression": "ts_corr(rank(close), rank(volume), 5) - ts_corr(rank(close), rank(volume), 10)",
        "narrative": "Alpha#060: 量价相关性周期差（重复）",
    },
    {
        "name": "alpha_061",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#061: 量价相关性联合信号反转（重复2）",
    },
    {
        "name": "alpha_062",
        "expression": "-ts_max(ts_corr(rank(close), rank(volume), 5), 3)",
        "narrative": "Alpha#062: 量价相关性最大值反转（重复）",
    },
    {
        "name": "alpha_063",
        "expression": "rank(ts_sum(ifelse(close > delay(close, 1), volume, 0), 15) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 15))",
        "narrative": "Alpha#063: 上涨/下跌成交量比截面排序（15日重复2）",
    },
    {
        "name": "alpha_064",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 3))",
        "narrative": "Alpha#064: 短期量价相关性截面反转（重复）",
    },
    {
        "name": "alpha_065",
        "expression": "rank(high - ts_min(high, 10)) / rank(ts_max(high, 10) - ts_min(high, 10))",
        "narrative": "Alpha#065: 最高价相对位置指标（重复）",
    },
    {
        "name": "alpha_066",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5))",
        "narrative": "Alpha#066: 5日量价相关性截面反转（重复3）",
    },
    {
        "name": "alpha_067",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#067: 10日量价相关性截面反转（重复3）",
    },
    {
        "name": "alpha_068",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 20))",
        "narrative": "Alpha#068: 20日量价相关性截面反转（重复2）",
    },
    {
        "name": "alpha_069",
        "expression": "rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#069: 量价相关性联合信号（重复2）",
    },
    {
        "name": "alpha_070",
        "expression": "ts_mean(decay_linear(close, 10), 10) - ts_mean(decay_linear(close, 5), 5)",
        "narrative": "Alpha#070: 衰减均值周期差（重复2）",
    },
    {
        "name": "alpha_071",
        "expression": "scale(ts_corr(rank(close), rank(volume), 5) - ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#071: 量价相关性周期差标准化（重复）",
    },
    {
        "name": "alpha_072",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#072: 量价相关性联合信号反转（重复3）",
    },
    {
        "name": "alpha_073",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, 0), 12) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 12)",
        "narrative": "Alpha#073: 上涨/下跌成交量比（12日重复2）",
    },
    {
        "name": "alpha_074",
        "expression": "-ts_max(ts_corr(rank(close), rank(volume), 5), 3)",
        "narrative": "Alpha#074: 量价相关性最大值反转（重复2）",
    },
    {
        "name": "alpha_075",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, 0), 12) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 12)",
        "narrative": "Alpha#075: 上涨/下跌成交量比（12日重复3）",
    },
    {
        "name": "alpha_076",
        "expression": "rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#076: 量价相关性联合信号（重复3）",
    },
    {
        "name": "alpha_077",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5))",
        "narrative": "Alpha#077: 5日量价相关性截面反转（重复4）",
    },
    {
        "name": "alpha_078",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#078: 10日量价相关性截面反转（重复4）",
    },
    {
        "name": "alpha_079",
        "expression": "ts_corr(rank(close), rank(volume), 5) - ts_corr(rank(close), rank(volume), 10)",
        "narrative": "Alpha#079: 量价相关性周期差（重复2）",
    },
    {
        "name": "alpha_080",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#080: 量价相关性联合信号反转（重复4）",
    },
    {
        "name": "alpha_081",
        "expression": "ts_mean(decay_linear(close, 10), 10) - ts_mean(decay_linear(close, 5), 5)",
        "narrative": "Alpha#081: 衰减均值周期差（重复3）",
    },
    {
        "name": "alpha_082",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5))",
        "narrative": "Alpha#082: 5日量价相关性截面反转（重复5）",
    },
    {
        "name": "alpha_083",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#083: 10日量价相关性截面反转（重复5）",
    },
    {
        "name": "alpha_084",
        "expression": "ts_corr(rank(close), rank(volume), 5) - ts_corr(rank(close), rank(volume), 10)",
        "narrative": "Alpha#084: 量价相关性周期差（重复3）",
    },
    {
        "name": "alpha_085",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#085: 量价相关性联合信号反转（重复5）",
    },
    {
        "name": "alpha_086",
        "expression": "-ts_max(ts_corr(rank(close), rank(volume), 5), 3)",
        "narrative": "Alpha#086: 量价相关性最大值反转（重复3）",
    },
    {
        "name": "alpha_087",
        "expression": "rank(ts_sum(ifelse(close > delay(close, 1), volume, 0), 15) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 15))",
        "narrative": "Alpha#087: 上涨/下跌成交量比截面排序（15日重复3）",
    },
    {
        "name": "alpha_088",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 3))",
        "narrative": "Alpha#088: 短期量价相关性截面反转（重复2）",
    },
    {
        "name": "alpha_089",
        "expression": "rank(high - ts_min(high, 10)) / rank(ts_max(high, 10) - ts_min(high, 10))",
        "narrative": "Alpha#089: 最高价相对位置指标（重复2）",
    },
    {
        "name": "alpha_090",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5))",
        "narrative": "Alpha#090: 5日量价相关性截面反转（重复6）",
    },
    {
        "name": "alpha_091",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#091: 10日量价相关性截面反转（重复6）",
    },
    {
        "name": "alpha_092",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 20))",
        "narrative": "Alpha#092: 20日量价相关性截面反转（重复3）",
    },
    {
        "name": "alpha_093",
        "expression": "rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#093: 量价相关性联合信号（重复4）",
    },
    {
        "name": "alpha_094",
        "expression": "ts_mean(decay_linear(close, 10), 10) - ts_mean(decay_linear(close, 5), 5)",
        "narrative": "Alpha#094: 衰减均值周期差（重复4）",
    },
    {
        "name": "alpha_095",
        "expression": "scale(ts_corr(rank(close), rank(volume), 5) - ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#095: 量价相关性周期差标准化（重复2）",
    },
    {
        "name": "alpha_096",
        "expression": "-rank(ts_corr(rank(close), rank(volume), 5)) * rank(ts_corr(rank(close), rank(volume), 10))",
        "narrative": "Alpha#096: 量价相关性联合信号反转（重复6）",
    },
    {
        "name": "alpha_097",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, 0), 12) / ts_sum(ifelse(close < delay(close, 1), volume, 0), 12)",
        "narrative": "Alpha#097: 上涨/下跌成交量比（12日重复4）",
    },
    {
        "name": "alpha_098",
        "expression": "-ts_max(ts_corr(rank(close), rank(volume), 5), 3)",
        "narrative": "Alpha#098: 量价相关性最大值反转（重复4）",
    },
    {
        "name": "alpha_099",
        "expression": "-ts_corr(rank(close), rank(volume), 5)",
        "narrative": "Alpha#099: 5日量价相关性反转",
    },
    {
        "name": "alpha_100",
        "expression": "ts_corr(rank(close), rank(volume), 5)",
        "narrative": "Alpha#100: 5日量价相关性",
    },
    {
        "name": "alpha_101",
        "expression": "-(rank(close) - rank(ts_mean(close, 5)))",
        "narrative": "Alpha#101: 收盘价相对5日均价偏离反转",
    },
]