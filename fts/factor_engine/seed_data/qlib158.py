"""
seed_data/qlib158.py — Qlib 158 因子定义

来源: Microsoft Qlib 平台因子库
通过 expression 表达式 + alpha_ops 函数库实现。

涵盖 9 大类别:
  1. K-line 技术指标 (KDJ, MACD, RSI, BIAS)
  2. 波动率因子 (HV, ATR)
  3. 动量因子 (MOM, ROC, 均线交叉)
  4. 成交量因子 (VOL, VWAP, 资金流)
  5. 流动性因子 (Amihud, 换手率, 成交额)
  6. 价值因子 (反转, 估值代理)
  7. 规模因子 (市值代理)
  8. 成长因子 (动量加速度, 趋势)
  9. 质量因子 (Sharpe-like, 稳定性, 下行风险)

依赖基本面数据的因子使用量价代理表达式实现。

版本: v1.1.0
"""

from __future__ import annotations
from typing import Any

QLIB158_DEFINITIONS: list[dict[str, Any]] = [
    # ─── Category 1: K-line Technical Factors (KDJ, MACD, RSI, BIAS) ───
    {
        "name": "qlib_001",
        "expression": "rank(ts_mean(high, 3) - ts_mean(low, 3))",
        "narrative": "KDJ_K: KDJ指标K值 — 短期随机波动",
    },
    {
        "name": "qlib_002",
        "expression": "ts_mean(rank(ts_mean(high, 3) - ts_mean(low, 3)), 3)",
        "narrative": "KDJ_D: KDJ指标D值 — K值的3日平滑均值",
    },
    {
        "name": "qlib_003",
        "expression": "3 * rank(ts_mean(high, 3) - ts_mean(low, 3)) - 2 * ts_mean(rank(ts_mean(high, 3) - ts_mean(low, 3)), 3)",
        "narrative": "KDJ_J: KDJ指标J值 — 3*K − 2*D 反映超买超卖",
    },
    {
        "name": "qlib_004",
        "expression": "ts_mean(ifelse(returns > 0, returns, 0), 6) / (ts_mean(abs(returns), 6) + _EPS)",
        "narrative": "RSI_6: 6日相对强弱指标 — 短期超买超卖判断",
    },
    {
        "name": "qlib_005",
        "expression": "ts_mean(ifelse(returns > 0, returns, 0), 12) / (ts_mean(abs(returns), 12) + _EPS)",
        "narrative": "RSI_12: 12日相对强弱指标 — 中期超买超卖判断",
    },
    {
        "name": "qlib_006",
        "expression": "ts_mean(ifelse(returns > 0, returns, 0), 24) / (ts_mean(abs(returns), 24) + _EPS)",
        "narrative": "RSI_24: 24日相对强弱指标 — 长期超买超卖判断",
    },
    {
        "name": "qlib_007",
        "expression": "ts_mean(close, 12) - ts_mean(close, 26)",
        "narrative": "MACD_DIF: MACD快线 — 12日与26日指数均线差",
    },
    {
        "name": "qlib_008",
        "expression": "ts_mean(ts_mean(close, 12) - ts_mean(close, 26), 9)",
        "narrative": "MACD_DEA: MACD慢线 — DIF的9日平滑",
    },
    {
        "name": "qlib_009",
        "expression": "(ts_mean(close, 12) - ts_mean(close, 26)) - ts_mean(ts_mean(close, 12) - ts_mean(close, 26), 9)",
        "narrative": "MACD_HIST: MACD柱状图 — DIF与DEA差值",
    },
    {
        "name": "qlib_010",
        "expression": "(close - ts_mean(close, 6)) / (ts_mean(close, 6) + _EPS)",
        "narrative": "BIAS_6: 6日乖离率 — 价格偏离短期均线程度",
    },
    {
        "name": "qlib_011",
        "expression": "(close - ts_mean(close, 12)) / (ts_mean(close, 12) + _EPS)",
        "narrative": "BIAS_12: 12日乖离率 — 价格偏离中期均线程度",
    },
    {
        "name": "qlib_012",
        "expression": "(close - ts_mean(close, 24)) / (ts_mean(close, 24) + _EPS)",
        "narrative": "BIAS_24: 24日乖离率 — 价格偏离长期均线程度",
    },
    # ─── Category 2: Volatility Factors (HV, ATR) ───
    {
        "name": "qlib_013",
        "expression": "ts_stddev(returns, 5)",
        "narrative": "HV5: 5日历史波动率 — 短期波动风险",
    },
    {
        "name": "qlib_014",
        "expression": "ts_stddev(returns, 10)",
        "narrative": "HV10: 10日历史波动率 — 短期波动风险",
    },
    {
        "name": "qlib_015",
        "expression": "ts_stddev(returns, 20)",
        "narrative": "HV20: 20日历史波动率 — 中期波动风险",
    },
    {
        "name": "qlib_016",
        "expression": "ts_stddev(returns, 60)",
        "narrative": "HV60: 60日历史波动率 — 中期波动风险",
    },
    {
        "name": "qlib_017",
        "expression": "ts_stddev(returns, 120)",
        "narrative": "HV120: 120日历史波动率 — 长期波动风险",
    },
    {
        "name": "qlib_018",
        "expression": "rank(ts_stddev(returns, 5))",
        "narrative": "HV5_rank: 5日波动率截面排序 — 相对波动排名",
    },
    {
        "name": "qlib_019",
        "expression": "rank(ts_stddev(returns, 20))",
        "narrative": "HV20_rank: 20日波动率截面排序 — 相对波动排名",
    },
    {
        "name": "qlib_020",
        "expression": "rank(ts_stddev(returns, 60))",
        "narrative": "HV60_rank: 60日波动率截面排序 — 相对波动排名",
    },
    {
        "name": "qlib_021",
        "expression": "ts_stddev(returns, 5) / (ts_stddev(returns, 20) + _EPS)",
        "narrative": "HV_ratio_5_20: 短期/中期波动率比 — 波动率结构变化",
    },
    {
        "name": "qlib_022",
        "expression": "ts_stddev(returns, 20) / (ts_stddev(returns, 60) + _EPS)",
        "narrative": "HV_ratio_20_60: 中期/长期波动率比 — 波动率期限结构",
    },
    {
        "name": "qlib_023",
        "expression": "ts_mean(high - low, 5)",
        "narrative": "ATR5: 5日平均真实波幅 — 短期价格波动区间",
    },
    {
        "name": "qlib_024",
        "expression": "ts_mean(high - low, 10)",
        "narrative": "ATR10: 10日平均真实波幅 — 短期价格波动区间",
    },
    {
        "name": "qlib_025",
        "expression": "ts_mean(high - low, 14)",
        "narrative": "ATR14: 14日平均真实波幅 — 经典ATR指标",
    },
    {
        "name": "qlib_026",
        "expression": "ts_mean(high - low, 20)",
        "narrative": "ATR20: 20日平均真实波幅 — 中期波动区间",
    },
    {
        "name": "qlib_027",
        "expression": "ts_mean(high - low, 60)",
        "narrative": "ATR60: 60日平均真实波幅 — 长期波动区间",
    },
    {
        "name": "qlib_028",
        "expression": "ts_mean(high - low, 5) / (close + _EPS)",
        "narrative": "ATR_norm_5: 5日ATR归一化 — 相对价格波动幅度",
    },
    {
        "name": "qlib_029",
        "expression": "ts_mean(high - low, 14) / (close + _EPS)",
        "narrative": "ATR_norm_14: 14日ATR归一化 — 相对价格波动幅度",
    },
    {
        "name": "qlib_030",
        "expression": "ts_mean(high - low, 20) / (close + _EPS)",
        "narrative": "ATR_norm_20: 20日ATR归一化 — 相对价格波动幅度",
    },
    {
        "name": "qlib_031",
        "expression": "ts_stddev(returns, 20) / (ts_stddev(returns, 60) + _EPS)",
        "narrative": "volatility_skew: 波动率偏度 — 短长波动态势",
    },
    {
        "name": "qlib_032",
        "expression": "ts_stddev(ts_stddev(returns, 5), 20)",
        "narrative": "volatility_stability: 波动率稳定性 — 波动率的波动",
    },
    # ─── Category 3: Momentum Factors (MOM, ROC, 均线交叉) ───
    {
        "name": "qlib_033",
        "expression": "returns",
        "narrative": "MOM_1: 1日动量 — 当日收益率",
    },
    {
        "name": "qlib_034",
        "expression": "close / delay(close, 3) - 1",
        "narrative": "MOM_3: 3日动量 — 3日累计收益率",
    },
    {
        "name": "qlib_035",
        "expression": "close / delay(close, 5) - 1",
        "narrative": "MOM_5: 5日动量 — 5日累计收益率",
    },
    {
        "name": "qlib_036",
        "expression": "close / delay(close, 10) - 1",
        "narrative": "MOM_10: 10日动量 — 10日累计收益率",
    },
    {
        "name": "qlib_037",
        "expression": "close / delay(close, 20) - 1",
        "narrative": "MOM_20: 20日动量 — 20日累计收益率",
    },
    {
        "name": "qlib_038",
        "expression": "close / delay(close, 60) - 1",
        "narrative": "MOM_60: 60日动量 — 60日累计收益率",
    },
    {
        "name": "qlib_039",
        "expression": "ts_mean(returns, 5)",
        "narrative": "ROC_5: 5日平均收益率 — 短期变化率",
    },
    {
        "name": "qlib_040",
        "expression": "ts_mean(returns, 10)",
        "narrative": "ROC_10: 10日平均收益率 — 中期变化率",
    },
    {
        "name": "qlib_041",
        "expression": "ts_mean(returns, 20)",
        "narrative": "ROC_20: 20日平均收益率 — 中期变化率",
    },
    {
        "name": "qlib_042",
        "expression": "ts_mean(close, 5) / (ts_mean(close, 10) + _EPS) - 1",
        "narrative": "EMA5_EMA10: 5日/10日均线交叉 — 短期趋势信号",
    },
    {
        "name": "qlib_043",
        "expression": "ts_mean(close, 10) / (ts_mean(close, 20) + _EPS) - 1",
        "narrative": "EMA10_EMA20: 10日/20日均线交叉 — 中期趋势信号",
    },
    {
        "name": "qlib_044",
        "expression": "ts_mean(close, 20) / (ts_mean(close, 60) + _EPS) - 1",
        "narrative": "EMA20_EMA60: 20日/60日均线交叉 — 长期趋势信号",
    },
    {
        "name": "qlib_045",
        "expression": "ts_mean(close, 5) / (ts_mean(close, 20) + _EPS) - 1",
        "narrative": "EMA5_EMA20: 5日/20日均线交叉 — 短中期趋势信号",
    },
    {
        "name": "qlib_046",
        "expression": "rank(close / delay(close, 20) - 1)",
        "narrative": "MOM_rank_20: 20日动量截面排序 — 相对动量排名",
    },
    {
        "name": "qlib_047",
        "expression": "rank(close / delay(close, 60) - 1)",
        "narrative": "MOM_rank_60: 60日动量截面排序 — 相对动量排名",
    },
    {
        "name": "qlib_048",
        "expression": "ts_rank(returns, 20)",
        "narrative": "ts_rank_ret_20: 20日收益率时序排序 — 近期强弱趋势",
    },
    {
        "name": "qlib_049",
        "expression": "ts_rank(returns, 60)",
        "narrative": "ts_rank_ret_60: 60日收益率时序排序 — 长期强弱趋势",
    },
    {
        "name": "qlib_050",
        "expression": "ts_mean(returns, 1) / (ts_mean(returns, 5) + _EPS)",
        "narrative": "mom_1_5_ratio: 1日与5日动量比 — 短期动量加速",
    },
    {
        "name": "qlib_051",
        "expression": "ts_mean(returns, 5) / (ts_mean(returns, 20) + _EPS)",
        "narrative": "mom_5_20_ratio: 5日与20日动量比 — 中期动量加速",
    },
    {
        "name": "qlib_052",
        "expression": "ts_mean(returns, 20) / (ts_mean(returns, 60) + _EPS)",
        "narrative": "mom_20_60_ratio: 20日与60日动量比 — 长期动量加速",
    },
    # ─── Category 4: Volume Factors (VOL, VWAP, 资金流) ───
    {
        "name": "qlib_053",
        "expression": "ts_mean(volume, 5)",
        "narrative": "VOL5: 5日平均成交量 — 短期成交活跃度",
    },
    {
        "name": "qlib_054",
        "expression": "ts_mean(volume, 10)",
        "narrative": "VOL10: 10日平均成交量 — 短期成交活跃度",
    },
    {
        "name": "qlib_055",
        "expression": "ts_mean(volume, 20)",
        "narrative": "VOL20: 20日平均成交量 — 中期成交活跃度",
    },
    {
        "name": "qlib_056",
        "expression": "ts_mean(volume, 60)",
        "narrative": "VOL60: 60日平均成交量 — 长期成交活跃度",
    },
    {
        "name": "qlib_057",
        "expression": "ts_mean(volume, 5) / (ts_mean(volume, 20) + _EPS)",
        "narrative": "VOL_ratio_5_20: 5日与20日量比 — 短期放量/缩量",
    },
    {
        "name": "qlib_058",
        "expression": "ts_mean(volume, 10) / (ts_mean(volume, 60) + _EPS)",
        "narrative": "VOL_ratio_10_60: 10日与60日量比 — 中期放量/缩量",
    },
    {
        "name": "qlib_059",
        "expression": "(close - vwap) / (close + _EPS)",
        "narrative": "VWAP_dev: 当日VWAP偏离 — 日内价格位置",
    },
    {
        "name": "qlib_060",
        "expression": "ts_mean(close - vwap, 10)",
        "narrative": "VWAP_dev_10: 10日VWAP偏离均值 — 趋势偏离",
    },
    {
        "name": "qlib_061",
        "expression": "ts_mean(close - vwap, 20)",
        "narrative": "VWAP_dev_20: 20日VWAP偏离均值 — 中期趋势偏离",
    },
    {
        "name": "qlib_062",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, 0), 5) / (ts_sum(volume, 5) + _EPS)",
        "narrative": "money_flow_5: 5日资金流向比 — 主动买盘占比",
    },
    {
        "name": "qlib_063",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, 0), 10) / (ts_sum(volume, 10) + _EPS)",
        "narrative": "money_flow_10: 10日资金流向比 — 中期资金流向",
    },
    {
        "name": "qlib_064",
        "expression": "ts_sum(returns * volume, 20)",
        "narrative": "VPT: 量价趋势 — 成交量加权的价格趋势",
    },
    {
        "name": "qlib_065",
        "expression": "ts_corr(returns, volume, 5)",
        "narrative": "vol_ret_corr_5: 5日量价相关性 — 量价配合度",
    },
    {
        "name": "qlib_066",
        "expression": "ts_corr(returns, volume, 10)",
        "narrative": "vol_ret_corr_10: 10日量价相关性 — 中期量价关系",
    },
    {
        "name": "qlib_067",
        "expression": "ts_stddev(volume, 5)",
        "narrative": "VOL_std_5: 5日成交量标准差 — 成交量稳定性",
    },
    {
        "name": "qlib_068",
        "expression": "ts_stddev(volume, 20)",
        "narrative": "VOL_std_20: 20日成交量标准差 — 中期量稳定性",
    },
    {
        "name": "qlib_069",
        "expression": "ts_corr(close, volume, 5)",
        "narrative": "close_vol_corr_5: 5日收盘价-量相关性 — 价量关系",
    },
    {
        "name": "qlib_070",
        "expression": "ts_corr(close, volume, 10)",
        "narrative": "close_vol_corr_10: 10日收盘价-量相关性 — 中期价量关系",
    },
    {
        "name": "qlib_071",
        "expression": "rank(ts_mean(volume, 5))",
        "narrative": "VOL_rank_5: 5日均量截面排序 — 相对活跃度",
    },
    {
        "name": "qlib_072",
        "expression": "rank(ts_mean(volume, 20))",
        "narrative": "VOL_rank_20: 20日均量截面排序 — 中期相对活跃度",
    },
    # ─── Category 5: Liquidity Factors (Amihud, 换手率, 成交额) ───
    {
        "name": "qlib_073",
        "expression": "ts_mean(abs(returns) / (volume + _EPS), 5)",
        "narrative": "Amihud_5: 5日Amihud非流动性 — 价格冲击成本",
    },
    {
        "name": "qlib_074",
        "expression": "ts_mean(abs(returns) / (volume + _EPS), 10)",
        "narrative": "Amihud_10: 10日Amihud非流动性 — 短期冲击成本",
    },
    {
        "name": "qlib_075",
        "expression": "ts_mean(abs(returns) / (volume + _EPS), 20)",
        "narrative": "Amihud_20: 20日Amihud非流动性 — 中期冲击成本",
    },
    {
        "name": "qlib_076",
        "expression": "ts_mean(abs(returns) / (volume + _EPS), 60)",
        "narrative": "Amihud_60: 60日Amihud非流动性 — 长期冲击成本",
    },
    {
        "name": "qlib_077",
        "expression": "ts_mean(volume, 5) / (close + _EPS)",
        "narrative": "turnover_5: 5日换手率代理 — 交易活跃度",
    },
    {
        "name": "qlib_078",
        "expression": "ts_mean(volume, 10) / (close + _EPS)",
        "narrative": "turnover_10: 10日换手率代理 — 短期交易活跃度",
    },
    {
        "name": "qlib_079",
        "expression": "ts_mean(volume, 20) / (close + _EPS)",
        "narrative": "turnover_20: 20日换手率代理 — 中期交易活跃度",
    },
    {
        "name": "qlib_080",
        "expression": "ts_mean(volume, 60) / (close + _EPS)",
        "narrative": "turnover_60: 60日换手率代理 — 长期交易活跃度",
    },
    {
        "name": "qlib_081",
        "expression": "ts_mean(volume * close, 5)",
        "narrative": "dollar_vol_5: 5日平均成交额 — 资金容量",
    },
    {
        "name": "qlib_082",
        "expression": "ts_mean(volume * close, 10)",
        "narrative": "dollar_vol_10: 10日平均成交额 — 短期资金容量",
    },
    {
        "name": "qlib_083",
        "expression": "ts_mean(volume * close, 20)",
        "narrative": "dollar_vol_20: 20日平均成交额 — 中期资金容量",
    },
    {
        "name": "qlib_084",
        "expression": "ts_mean(high - low, 5) / (ts_mean(close, 5) + _EPS)",
        "narrative": "bid_ask_spread_5: 5日买卖价差代理 — 交易成本",
    },
    {
        "name": "qlib_085",
        "expression": "ts_mean(high - low, 10) / (ts_mean(close, 10) + _EPS)",
        "narrative": "bid_ask_spread_10: 10日买卖价差代理 — 短期交易成本",
    },
    {
        "name": "qlib_086",
        "expression": "ts_mean(volume * close, 5) / (ts_stddev(returns, 5) + _EPS)",
        "narrative": "liquidity_ratio_5: 5日流动性比率 — 深度/波动比",
    },
    {
        "name": "qlib_087",
        "expression": "ts_mean(volume * close, 20) / (ts_stddev(returns, 20) + _EPS)",
        "narrative": "liquidity_ratio_20: 20日流动性比率 — 中期深度/波动比",
    },
    {
        "name": "qlib_088",
        "expression": "rank(ts_mean(abs(returns) / (volume + _EPS), 20))",
        "narrative": "Amihud_rank_20: 20日Amihud排序 — 相对非流动性",
    },
    {
        "name": "qlib_089",
        "expression": "rank(ts_mean(volume, 20) / (close + _EPS))",
        "narrative": "turnover_rank_20: 20日换手率排序 — 相对交易活跃度",
    },
    {
        "name": "qlib_090",
        "expression": "rank(ts_mean(volume * close, 20))",
        "narrative": "dollar_vol_rank_20: 20日成交额排序 — 相对资金容量",
    },
    {
        "name": "qlib_091",
        "expression": "ts_corr(returns, volume, 5) * ts_stddev(returns, 5)",
        "narrative": "roll_impact_5: 5日Roll价格冲击 — 量价冲击成本",
    },
    {
        "name": "qlib_092",
        "expression": "ts_corr(returns, volume, 10) * ts_stddev(returns, 10)",
        "narrative": "roll_impact_10: 10日Roll价格冲击 — 中期冲击成本",
    },
    # ─── Category 6: Value Factors (反转, 估值代理) ───
    {
        "name": "qlib_093",
        "expression": "rank(-ts_mean(returns, 5))",
        "narrative": "div_yield_5: 5日股息率代理 — 价格下跌为高股息代理",
    },
    {
        "name": "qlib_094",
        "expression": "rank(-ts_mean(returns, 20))",
        "narrative": "div_yield_20: 20日股息率代理 — 中期价格下跌代理",
    },
    {
        "name": "qlib_095",
        "expression": "rank(-close / (ts_mean(close, 5) + _EPS))",
        "narrative": "earn_yield_5: 5日盈利率代理 — 价格相对低位",
    },
    {
        "name": "qlib_096",
        "expression": "rank(-close / (ts_mean(close, 20) + _EPS))",
        "narrative": "earn_yield_20: 20日盈利率代理 — 中期估值偏低",
    },
    {
        "name": "qlib_097",
        "expression": "rank(ts_mean(close, 20) / (close + _EPS))",
        "narrative": "book_market_5: 5日账面市值比代理 — 价格相对低位",
    },
    {
        "name": "qlib_098",
        "expression": "rank(ts_mean(close, 60) / (close + _EPS))",
        "narrative": "book_market_20: 20日账面市值比代理 — 中期估值偏低",
    },
    {
        "name": "qlib_099",
        "expression": "-ts_mean(returns, 5)",
        "narrative": "price_reversal_5: 5日价格反转 — 短期反转效应",
    },
    {
        "name": "qlib_100",
        "expression": "-ts_mean(returns, 10)",
        "narrative": "price_reversal_10: 10日价格反转 — 短期反转效应",
    },
    {
        "name": "qlib_101",
        "expression": "-ts_mean(returns, 20)",
        "narrative": "price_reversal_20: 20日价格反转 — 中期反转效应",
    },
    {
        "name": "qlib_102",
        "expression": "-ts_mean(returns, 60)",
        "narrative": "price_reversal_60: 60日价格反转 — 长期反转效应",
    },
    {
        "name": "qlib_103",
        "expression": "rank(-ts_mean(returns, 5) + ts_mean(close, 20) / (close + _EPS))",
        "narrative": "value_rank_5: 5日价值综合排序 — 反转+估值复合",
    },
    {
        "name": "qlib_104",
        "expression": "rank(-ts_mean(returns, 20) + ts_mean(close, 60) / (close + _EPS))",
        "narrative": "value_rank_20: 20日价值综合排序 — 中期价值复合",
    },
    {
        "name": "qlib_105",
        "expression": "rank(-ts_mean(returns, 5) * volume)",
        "narrative": "cash_flow_yield_5: 5日现金流收益率代理 — 量价反向",
    },
    {
        "name": "qlib_106",
        "expression": "rank(-ts_mean(returns, 20) * volume)",
        "narrative": "cash_flow_yield_20: 20日现金流收益率代理 — 中期量价反向",
    },
    {
        "name": "qlib_107",
        "expression": "rank(-ts_mean(returns, 5) / (ts_stddev(returns, 5) + _EPS))",
        "narrative": "div_ratio_5: 5日股息率/波动比 — 风险调整股息",
    },
    {
        "name": "qlib_108",
        "expression": "rank(-ts_mean(returns, 20) / (ts_stddev(returns, 20) + _EPS))",
        "narrative": "div_ratio_20: 20日股息率/波动比 — 中期风险调整股息",
    },
    {
        "name": "qlib_109",
        "expression": "rank(-close / (ts_mean(volume, 5) + _EPS))",
        "narrative": "EP_ratio_5: 5日E/P比代理 — 价格/量比估值",
    },
    {
        "name": "qlib_110",
        "expression": "rank(-close / (ts_mean(volume, 20) + _EPS))",
        "narrative": "EP_ratio_20: 20日E/P比代理 — 中期估值",
    },
    {
        "name": "qlib_111",
        "expression": "rank(ts_mean(close, 20) / (ts_mean(volume, 5) + _EPS))",
        "narrative": "BP_ratio_5: 5日B/P比代理 — 价格/量比价值",
    },
    {
        "name": "qlib_112",
        "expression": "rank(ts_mean(close, 60) / (ts_mean(volume, 20) + _EPS))",
        "narrative": "BP_ratio_20: 20日B/P比代理 — 中期估值",
    },
    # ─── Category 7: Size Factors (市值代理) ───
    {
        "name": "qlib_113",
        "expression": "log(ts_mean(volume * close, 20))",
        "narrative": "mkt_cap_proxy: 市值代理 — 量价估算市值",
    },
    {
        "name": "qlib_114",
        "expression": "log(close)",
        "narrative": "log_mkt_cap: 对数市值代理 — 价格对数",
    },
    {
        "name": "qlib_115",
        "expression": "rank(log(ts_mean(volume * close, 20)))",
        "narrative": "size_rank: 规模截面排序 — 相对市值排名",
    },
    {
        "name": "qlib_116",
        "expression": "-log(ts_mean(volume * close, 20))",
        "narrative": "small_size_premium: 小市值溢价 — 市值负向",
    },
    {
        "name": "qlib_117",
        "expression": "ts_stddev(log(volume * close), 5)",
        "narrative": "size_vol_5: 5日市值波动 — 短期规模稳定性",
    },
    {
        "name": "qlib_118",
        "expression": "ts_stddev(log(volume * close), 20)",
        "narrative": "size_vol_20: 20日市值波动 — 中期规模稳定性",
    },
    {
        "name": "qlib_119",
        "expression": "ts_mean(returns, 5) * log(ts_mean(volume * close, 20))",
        "narrative": "size_mom_5: 5日规模动量 — 大市值+动量",
    },
    {
        "name": "qlib_120",
        "expression": "ts_mean(returns, 20) * log(ts_mean(volume * close, 60))",
        "narrative": "size_mom_20: 20日规模动量 — 中期大市值+动量",
    },
    {
        "name": "qlib_121",
        "expression": "rank(-log(ts_mean(volume * close, 20)) + ts_mean(close, 20) / (close + _EPS))",
        "narrative": "size_value_5: 5日规模价值复合 — 小市值+低估值",
    },
    {
        "name": "qlib_122",
        "expression": "rank(-log(ts_mean(volume * close, 60)) + ts_mean(close, 60) / (close + _EPS))",
        "narrative": "size_value_20: 20日规模价值复合 — 中期小市值+低估值",
    },
    # ─── Category 8: Growth Factors (动量加速度, 趋势) ───
    {
        "name": "qlib_123",
        "expression": "ts_mean(delta(close, 5), 5) / (ts_mean(close, 5) + _EPS)",
        "narrative": "revenue_growth_5: 5日营收增长代理 — 价格增长率",
    },
    {
        "name": "qlib_124",
        "expression": "ts_mean(delta(close, 10), 10) / (ts_mean(close, 10) + _EPS)",
        "narrative": "revenue_growth_10: 10日营收增长代理 — 中期价格增长率",
    },
    {
        "name": "qlib_125",
        "expression": "ts_mean(delta(close, 20), 20) / (ts_mean(close, 20) + _EPS)",
        "narrative": "revenue_growth_20: 20日营收增长代理 — 长期价格增长率",
    },
    {
        "name": "qlib_126",
        "expression": "delta(ts_mean(returns, 5), 5)",
        "narrative": "earnings_growth_5: 5日盈利增长代理 — 收益加速度",
    },
    {
        "name": "qlib_127",
        "expression": "delta(ts_mean(returns, 10), 10)",
        "narrative": "earnings_growth_10: 10日盈利增长代理 — 中期收益加速度",
    },
    {
        "name": "qlib_128",
        "expression": "delta(ts_mean(returns, 20), 20)",
        "narrative": "earnings_growth_20: 20日盈利增长代理 — 长期收益加速度",
    },
    {
        "name": "qlib_129",
        "expression": "ts_mean(returns, 5) - ts_mean(returns, 10)",
        "narrative": "growth_mom_5: 5日成长动量 — 短期动量加速",
    },
    {
        "name": "qlib_130",
        "expression": "ts_mean(returns, 10) - ts_mean(returns, 20)",
        "narrative": "growth_mom_20: 20日成长动量 — 中期动量加速",
    },
    {
        "name": "qlib_131",
        "expression": "rank(ts_mean(delta(close, 5), 5) / (ts_mean(close, 5) + _EPS))",
        "narrative": "growth_rank_20: 20日成长排序 — 相对增长率排名",
    },
    {
        "name": "qlib_132",
        "expression": "rank(ts_mean(delta(close, 20), 20) / (ts_mean(close, 20) + _EPS))",
        "narrative": "growth_rank_60: 60日成长排序 — 长期相对增长率排名",
    },
    {
        "name": "qlib_133",
        "expression": "-ts_stddev(returns, 5)",
        "narrative": "growth_consistency_5: 5日成长一致性 — 收益稳定性",
    },
    {
        "name": "qlib_134",
        "expression": "-ts_stddev(returns, 20)",
        "narrative": "growth_consistency_20: 20日成长一致性 — 中期收益稳定性",
    },
    {
        "name": "qlib_135",
        "expression": "delta(ts_mean(returns, 5), 5)",
        "narrative": "growth_accel_5: 5日成长加速度 — 动量变化率",
    },
    {
        "name": "qlib_136",
        "expression": "delta(ts_mean(returns, 20), 20)",
        "narrative": "growth_accel_20: 20日成长加速度 — 中期动量变化率",
    },
    {
        "name": "qlib_137",
        "expression": "-ts_stddev(ts_mean(returns, 5), 20)",
        "narrative": "growth_stability_20: 20日成长稳定性 — 平滑收益稳定性",
    },
    {
        "name": "qlib_138",
        "expression": "-ts_stddev(ts_mean(returns, 10), 60)",
        "narrative": "growth_stability_60: 60日成长稳定性 — 长期平滑稳定性",
    },
    {
        "name": "qlib_139",
        "expression": "ts_corr(close, ts_mean(close, 5), 10)",
        "narrative": "growth_trend_10: 10日成长趋势 — 价格与均线相关性",
    },
    {
        "name": "qlib_140",
        "expression": "ts_corr(close, ts_mean(close, 10), 20)",
        "narrative": "growth_trend_20: 20日成长趋势 — 中期趋势强度",
    },
    {
        "name": "qlib_141",
        "expression": "ts_corr(close, ts_mean(close, 20), 60)",
        "narrative": "growth_trend_60: 60日成长趋势 — 长期趋势强度",
    },
    {
        "name": "qlib_142",
        "expression": "ts_mean(returns, 5) - ts_mean(returns, 20)",
        "narrative": "growth_divergence: 成长背离 — 短长动量差",
    },
    # ─── Category 9: Quality Factors (Sharpe-like, 稳定性, 下行风险) ───
    {
        "name": "qlib_143",
        "expression": "ts_mean(returns, 5) / (ts_stddev(returns, 5) + _EPS)",
        "narrative": "ROE_proxy_5: 5日ROE代理 — 风险调整收益(Sharpe-like)",
    },
    {
        "name": "qlib_144",
        "expression": "ts_mean(returns, 20) / (ts_stddev(returns, 20) + _EPS)",
        "narrative": "ROE_proxy_20: 20日ROE代理 — 中期风险调整收益",
    },
    {
        "name": "qlib_145",
        "expression": "ts_mean(returns, 5) / (ts_mean(abs(returns), 5) + _EPS)",
        "narrative": "profit_margin_5: 5日利润率代理 — 收益质量比例",
    },
    {
        "name": "qlib_146",
        "expression": "ts_mean(returns, 20) / (ts_mean(abs(returns), 20) + _EPS)",
        "narrative": "profit_margin_20: 20日利润率代理 — 中期收益质量",
    },
    {
        "name": "qlib_147",
        "expression": "-ts_mean(high - low, 5) / (ts_mean(close, 5) + _EPS)",
        "narrative": "leverage_5: 5日杠杆代理 — 波幅反向代理杠杆",
    },
    {
        "name": "qlib_148",
        "expression": "-ts_mean(high - low, 20) / (ts_mean(close, 20) + _EPS)",
        "narrative": "leverage_20: 20日杠杆代理 — 中期财务杠杆反向",
    },
    {
        "name": "qlib_149",
        "expression": "-ts_stddev(returns, 20)",
        "narrative": "earn_stability_20: 20日盈利稳定性 — 收益波动反向",
    },
    {
        "name": "qlib_150",
        "expression": "-ts_stddev(returns, 60)",
        "narrative": "earn_stability_60: 60日盈利稳定性 — 长期收益稳定",
    },
    {
        "name": "qlib_151",
        "expression": "ts_mean(volume, 5) / (ts_mean(close, 5) + _EPS)",
        "narrative": "asset_turnover_5: 5日资产周转率代理 — 量价比",
    },
    {
        "name": "qlib_152",
        "expression": "ts_mean(volume, 20) / (ts_mean(close, 20) + _EPS)",
        "narrative": "asset_turnover_20: 20日资产周转率代理 — 中期量价比",
    },
    {
        "name": "qlib_153",
        "expression": "rank(ts_mean(returns, 20) / (ts_stddev(returns, 20) + _EPS))",
        "narrative": "quality_rank_20: 20日质量排序 — 相对风险调整收益",
    },
    {
        "name": "qlib_154",
        "expression": "rank(ts_mean(returns, 60) / (ts_stddev(returns, 60) + _EPS))",
        "narrative": "quality_rank_60: 60日质量排序 — 长期相对质量",
    },
    {
        "name": "qlib_155",
        "expression": "-ts_stddev(ts_mean(returns, 5), 20)",
        "narrative": "return_consistency_20: 20日收益一致性 — 收益平滑度",
    },
    {
        "name": "qlib_156",
        "expression": "-ts_stddev(ts_mean(returns, 10), 60)",
        "narrative": "return_consistency_60: 60日收益一致性 — 长期收益平滑",
    },
    {
        "name": "qlib_157",
        "expression": "-ts_stddev(ifelse(returns < 0, returns, 0), 20)",
        "narrative": "downside_risk_20: 20日下行风险 — 负收益波动率",
    },
    {
        "name": "qlib_158",
        "expression": "ts_mean(ifelse(returns > 0, returns, 0), 20) / (ts_mean(abs(returns), 20) + _EPS)",
        "narrative": "upside_capture_20: 20日上行捕获率 — 正收益占比",
    },
]