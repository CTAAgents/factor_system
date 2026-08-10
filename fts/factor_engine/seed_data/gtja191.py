"""
seed_data/gtja191.py — 国泰君安 191 Alpha 因子定义

来源: "191 Alpha Formulas" by GuotaiJunAn Securities (DolphinDB → Python)
通过 expression 表达式 + alpha_ops 函数库实现。

函数映射说明:
  DolphinDB      → Python (alpha_ops)
  msum(x,d)      → ts_sum(x,d)
  mavg(x,d)      → ts_mean(x,d)
  mavg(x,1..d)   → decay_linear(x,d)  (线性加权)
  mstd(x,d)      → ts_stddev(x,d)
  mcorr(x,y,d)   → ts_corr(x,y,d)
  mcovar(x,y,d)  → ts_covariance(x,y,d)
  mmax(x,d)      → ts_max(x,d)
  mmin(x,d)      → ts_min(x,d)
  mrank(x,true,d) → ts_rank(x,d)
  rowRank(x,percent=true) → rank(x)
  mfirst(x,d)    → delay(x,d-1)
  move(x,d)      → delay(x,d)
  iif            → ifelse
  ewmMean        → ts_mean (近似)
  mcount         → ts_sum(ifelse(cond,1,0),d)
  mbeta          → ts_corr*ts_stddev/ts_stddev
  linearTimeTrend → decay_linear - ts_mean (斜率近似)
  mimax(x,d)     → (d-1) - highday(x,d)
  mimin(x,d)     → (d-1) - lowday(x,d)
  rowMax/rowMin  → np.max/np.min
  ratios(x)      → x/delay(x,1)
  &&            → &
  ||            → |

版本: v1.0.0
"""

from __future__ import annotations
from typing import Any

GTJA191_DEFINITIONS: list[dict[str, Any]] = [
    # ─── Alpha 001 ────────────────────────────────────────────────
    {
        "name": "gtja_001",
        "expression": "-ts_corr(rank(delta(log(volume), 1)), rank((close - open_) / open_), 6)",
        "narrative": "GTJA-Alpha#001: 对数成交量变化与开盘收益率的排序相关性反转",
    },
    # ─── Alpha 002 ────────────────────────────────────────────────
    {
        "name": "gtja_002",
        "expression": "-delta((close - low - (high - close)) / (high - low), 1)",
        "narrative": "GTJA-Alpha#002: 价格位置变化率的一阶差分负值",
    },
    # ─── Alpha 003 ────────────────────────────────────────────────
    {
        "name": "gtja_003",
        "expression": "ts_sum(ifelse(close == delay(close, 1), 0, close - ifelse(close > delay(close, 1), np.minimum(low, delay(close, 1)), np.maximum(high, delay(close, 1)))), 6)",
        "narrative": "GTJA-Alpha#003: 条件价格突破与回溯距离的6日累积",
    },
    # ─── Alpha 004 ────────────────────────────────────────────────
    {
        "name": "gtja_004",
        "expression": "ifelse((ts_mean(close, 8) + ts_stddev(close, 8)) < ts_mean(close, 2), -1, ifelse(ts_mean(close, 2) < (ts_mean(close, 8) - ts_stddev(close, 8)), 1, ifelse((1 < volume / ts_mean(volume, 20)) | (volume / ts_mean(volume, 20) == 1), 1, -1)))",
        "narrative": "GTJA-Alpha#004: 价格均值±标准差通道突破与成交量确认的三态信号",
    },
    # ─── Alpha 005 ────────────────────────────────────────────────
    {
        "name": "gtja_005",
        "expression": "-ts_max(ts_corr(ts_rank(volume, 5), ts_rank(high, 5), 5), 3)",
        "narrative": "GTJA-Alpha#005: 量价排序相关性的3日最大值反转",
    },
    # ─── Alpha 006 ────────────────────────────────────────────────
    {
        "name": "gtja_006",
        "expression": "-rank(sign(delta(open_ * 0.85 + high * 0.15, 4)))",
        "narrative": "GTJA-Alpha#006: 加权开盘价方向变化的排序反转",
    },
    # ─── Alpha 007 ────────────────────────────────────────────────
    {
        "name": "gtja_007",
        "expression": "(rank(ts_max(vwap - close, 3)) + rank(ts_min(vwap - close, 3))) * rank(delta(volume, 3))",
        "narrative": "GTJA-Alpha#007: VWAP偏离极值的排序组合与成交量变化排序的乘积",
    },
    # ─── Alpha 008 ────────────────────────────────────────────────
    {
        "name": "gtja_008",
        "expression": "rank(-delta((high + low) / 2 * 0.2 + vwap * 0.8, 4))",
        "narrative": "GTJA-Alpha#008: 加权中间价变化的排序反转",
    },
    # ─── Alpha 009 ────────────────────────────────────────────────
    {
        "name": "gtja_009",
        "expression": "ts_mean(((high + low) / 2 - (delay(high, 1) + delay(low, 1)) / 2) * (high - low) / volume, 7)",
        "narrative": "GTJA-Alpha#009: 中间价变化与振幅的成交量加权均值",
    },
    # ─── Alpha 010 ────────────────────────────────────────────────
    {
        "name": "gtja_010",
        "expression": "rank(ts_max(np.power(ifelse(returns < 0, ts_stddev(returns, 20), close), 2), 5))",
        "narrative": "GTJA-Alpha#010: 条件波动率/价格的平方最大值排序",
    },
    # ─── Alpha 011 ────────────────────────────────────────────────
    {
        "name": "gtja_011",
        "expression": "ts_sum((close - low - (high - close)) / (high - low) * volume, 6)",
        "narrative": "GTJA-Alpha#011: 价格位置比例的成交量加权6日累积",
    },
    # ─── Alpha 012 ────────────────────────────────────────────────
    {
        "name": "gtja_012",
        "expression": "rank(open_ - ts_mean(vwap, 10)) * (-rank(np.abs(close - vwap)))",
        "narrative": "GTJA-Alpha#012: 开盘价偏离VWAP均值与收盘价- VWAP绝对值的排序乘积",
    },
    # ─── Alpha 013 ────────────────────────────────────────────────
    {
        "name": "gtja_013",
        "expression": "np.sqrt(high * low) - vwap",
        "narrative": "GTJA-Alpha#013: 高低价几何均值与VWAP的偏离",
    },
    # ─── Alpha 014 ────────────────────────────────────────────────
    {
        "name": "gtja_014",
        "expression": "close - delay(close, 5)",
        "narrative": "GTJA-Alpha#014: 收盘价5日变化",
    },
    # ─── Alpha 015 ────────────────────────────────────────────────
    {
        "name": "gtja_015",
        "expression": "open_ / delay(close, 1) - 1",
        "narrative": "GTJA-Alpha#015: 开盘相对前收的收益率",
    },
    # ─── Alpha 016 ────────────────────────────────────────────────
    {
        "name": "gtja_016",
        "expression": "-ts_max(rank(ts_corr(rank(volume), rank(vwap), 5)), 5)",
        "narrative": "GTJA-Alpha#016: 量价排序相关性排序的5日最大值反转",
    },
    # ─── Alpha 017 ────────────────────────────────────────────────
    {
        "name": "gtja_017",
        "expression": "np.power(rank(vwap - ts_max(vwap, 15)), close - delay(close, 5))",
        "narrative": "GTJA-Alpha#017: VWAP偏离15日高点的排序的收盘价变化次幂",
    },
    # ─── Alpha 018 ────────────────────────────────────────────────
    {
        "name": "gtja_018",
        "expression": "close / delay(close, 5)",
        "narrative": "GTJA-Alpha#018: 收盘价5日价格比率",
    },
    # ─── Alpha 019 ────────────────────────────────────────────────
    {
        "name": "gtja_019",
        "expression": "ifelse(close < delay(close, 5), (close - delay(close, 5)) / delay(close, 5), ifelse(close == delay(close, 5), 0, (close - delay(close, 5)) / close))",
        "narrative": "GTJA-Alpha#019: 条件5日收益率（涨/跌/平三态）",
    },
    # ─── Alpha 020 ────────────────────────────────────────────────
    {
        "name": "gtja_020",
        "expression": "(close - delay(close, 6)) / delay(close, 6) * 100",
        "narrative": "GTJA-Alpha#020: 收盘价6日收益率百分比",
    },
    # ─── Alpha 021 ────────────────────────────────────────────────
    {
        "name": "gtja_021",
        "expression": "decay_linear(close, 6) - ts_mean(close, 6)",
        "narrative": "GTJA-Alpha#021: 6日线性趋势斜率（decay_linear减均值近似）",
    },
    # ─── Alpha 022 ────────────────────────────────────────────────
    {
        "name": "gtja_022",
        "expression": "ts_mean((close - ts_mean(close, 6)) / ts_mean(close, 6) - delay((close - ts_mean(close, 6)) / ts_mean(close, 6), 3), 12)",
        "narrative": "GTJA-Alpha#022: 相对均值偏离的3日差分12日平滑",
    },
    # ─── Alpha 023 ────────────────────────────────────────────────
    {
        "name": "gtja_023",
        "expression": "ts_mean(ifelse(close > delay(close, 1), ts_stddev(close, 20), 0), 20) / (ts_mean(ifelse(close > delay(close, 1), ts_stddev(close, 20), 0), 20) + ts_mean(ifelse(close <= delay(close, 1), ts_stddev(close, 20), 0), 20)) * 100",
        "narrative": "GTJA-Alpha#023: 上涨日/下跌日波动率占比的20日平滑",
    },
    # ─── Alpha 024 ────────────────────────────────────────────────
    {
        "name": "gtja_024",
        "expression": "ts_mean(close - delay(close, 5), 5)",
        "narrative": "GTJA-Alpha#024: 收盘价5日变化的5日平滑",
    },
    # ─── Alpha 025 ────────────────────────────────────────────────
    {
        "name": "gtja_025",
        "expression": "-rank(delta(close, 7) * (1 - rank(decay_linear(volume / ts_mean(volume, 20), 9)))) * (1 + rank(ts_sum(returns, 250)))",
        "narrative": "GTJA-Alpha#025: 7日价格变化与成交量比衰减排序的乘积，经250日收益调整",
    },
    # ─── Alpha 026 ────────────────────────────────────────────────
    {
        "name": "gtja_026",
        "expression": "ts_mean(close, 7) - close + ts_corr(vwap, delay(close, 5), 230)",
        "narrative": "GTJA-Alpha#026: 7日均线偏离加VWAP与前收的230日相关性",
    },
    # ─── Alpha 027 ────────────────────────────────────────────────
    {
        "name": "gtja_027",
        "expression": "decay_linear((close - delay(close, 3)) / delay(close, 3) * 100 + (close - delay(close, 6)) / delay(close, 6) * 100, 12)",
        "narrative": "GTJA-Alpha#027: 3日与6日收益率之和的12日线性衰减",
    },
    # ─── Alpha 028 ────────────────────────────────────────────────
    {
        "name": "gtja_028",
        "expression": "3 * ts_mean((close - ts_min(low, 9)) / (ts_max(high, 9) - ts_min(low, 9)) * 100, 3) - 2 * ts_mean(ts_mean((close - ts_min(low, 9)) / (ts_max(high, 9) - ts_min(low, 9)) * 100, 3), 3)",
        "narrative": "GTJA-Alpha#028: 3重平滑的随机指标振荡器",
    },
    # ─── Alpha 029 ────────────────────────────────────────────────
    {
        "name": "gtja_029",
        "expression": "(close - delay(close, 6)) / delay(close, 6) * volume",
        "narrative": "GTJA-Alpha#029: 6日收益率乘以成交量",
    },
    # ─── Alpha 030 ────────────────────────────────────────────────
    {
        "name": "gtja_030",
        "expression": "decay_linear(np.power(ts_corr(returns, index_returns, 60), 2), 20)",
        "narrative": "GTJA-Alpha#030: 残差平方的20日衰减（需MKT/SMB/HML三因子，此处用市场收益近似）",
    },
    # ─── Alpha 031 ────────────────────────────────────────────────
    {
        "name": "gtja_031",
        "expression": "(close - ts_mean(close, 12)) / ts_mean(close, 12) * 100",
        "narrative": "GTJA-Alpha#031: 收盘价相对12日均线偏离百分比",
    },
    # ─── Alpha 032 ────────────────────────────────────────────────
    {
        "name": "gtja_032",
        "expression": "-ts_sum(rank(ts_corr(rank(high), rank(volume), 3)), 3)",
        "narrative": "GTJA-Alpha#032: 高价-成交量排序相关性排序的3日累积反转",
    },
    # ─── Alpha 033 ────────────────────────────────────────────────
    {
        "name": "gtja_033",
        "expression": "(-ts_min(low, 5) + delay(ts_min(low, 5), 5)) * rank((ts_sum(returns, 240) - ts_sum(returns, 20)) / 220) * ts_rank(volume, 5)",
        "narrative": "GTJA-Alpha#033: 5日最低价变化乘以长短期收益差排序乘以成交量排序",
    },
    # ─── Alpha 034 ────────────────────────────────────────────────
    {
        "name": "gtja_034",
        "expression": "ts_mean(close, 12) / close",
        "narrative": "GTJA-Alpha#034: 12日均线与收盘价的比值",
    },
    # ─── Alpha 035 ────────────────────────────────────────────────
    {
        "name": "gtja_035",
        "expression": "-np.minimum(rank(decay_linear(delta(open_, 1), 15)), rank(decay_linear(ts_corr(volume, open_ * 0.65 + open_ * 0.35, 17), 7)))",
        "narrative": "GTJA-Alpha#035: 开盘价变化衰减与量价相关性衰减的排序最小值反转",
    },
    # ─── Alpha 036 ────────────────────────────────────────────────
    {
        "name": "gtja_036",
        "expression": "rank(ts_sum(ts_corr(rank(volume), rank(vwap), 6), 2))",
        "narrative": "GTJA-Alpha#036: 量价排序相关性累积的排序",
    },
    # ─── Alpha 037 ────────────────────────────────────────────────
    {
        "name": "gtja_037",
        "expression": "-rank(ts_sum(open_, 5) * ts_sum(returns, 5) - delay(ts_sum(open_, 5) * ts_sum(returns, 5), 10))",
        "narrative": "GTJA-Alpha#037: 开盘价-收益累积乘积的10日变化排序反转",
    },
    # ─── Alpha 038 ────────────────────────────────────────────────
    {
        "name": "gtja_038",
        "expression": "ifelse(ts_mean(high, 20) < high, -delta(high, 2), 0)",
        "narrative": "GTJA-Alpha#038: 高点突破20日均线时的高点变化反转",
    },
    # ─── Alpha 039 ────────────────────────────────────────────────
    {
        "name": "gtja_039",
        "expression": "-(rank(decay_linear(delta(close, 2), 8)) - rank(decay_linear(ts_corr(vwap * 0.3 + open_ * 0.7, ts_sum(ts_mean(volume, 180), 37), 14), 12)))",
        "narrative": "GTJA-Alpha#039: 收盘价变化衰减与VWAP-长期均量相关性衰减的排序差反转",
    },
    # ─── Alpha 040 ────────────────────────────────────────────────
    {
        "name": "gtja_040",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, 0), 26) / ts_sum(ifelse(close <= delay(close, 1), volume, 0), 26) * 100",
        "narrative": "GTJA-Alpha#040: 上涨日/下跌日成交量比的26日累积",
    },
    # ─── Alpha 041 ────────────────────────────────────────────────
    {
        "name": "gtja_041",
        "expression": "-rank(ts_max(delta(vwap, 3), 5))",
        "narrative": "GTJA-Alpha#041: VWAP 3日变化的5日最大值排序反转",
    },
    # ─── Alpha 042 ────────────────────────────────────────────────
    {
        "name": "gtja_042",
        "expression": "-rank(ts_stddev(high, 10)) * ts_corr(high, volume, 10)",
        "narrative": "GTJA-Alpha#042: 高价波动率排序反转乘以量价相关性",
    },
    # ─── Alpha 043 ────────────────────────────────────────────────
    {
        "name": "gtja_043",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, ifelse(close < delay(close, 1), -volume, 0)), 6)",
        "narrative": "GTJA-Alpha#043: 方向性成交量6日累积（上涨正、下跌负）",
    },
    # ─── Alpha 044 ────────────────────────────────────────────────
    {
        "name": "gtja_044",
        "expression": "ts_rank(decay_linear(ts_corr(low, ts_mean(volume, 10), 7), 6), 4) + ts_rank(decay_linear(delta(vwap, 3), 10), 15)",
        "narrative": "GTJA-Alpha#044: 低价-均量相关性衰减排序加VWAP变化衰减排序",
    },
    # ─── Alpha 045 ────────────────────────────────────────────────
    {
        "name": "gtja_045",
        "expression": "rank(delta(close * 0.6 + open_ * 0.4, 1)) * rank(ts_corr(vwap, ts_mean(volume, 150), 15))",
        "narrative": "GTJA-Alpha#045: 加权价格变化排序乘以VWAP-长期均量相关性排序",
    },
    # ─── Alpha 046 ────────────────────────────────────────────────
    {
        "name": "gtja_046",
        "expression": "(ts_mean(close, 3) + ts_mean(close, 6) + ts_mean(close, 12) + ts_mean(close, 24)) / (4 * close)",
        "narrative": "GTJA-Alpha#046: 多周期均线均值与收盘价的比值",
    },
    # ─── Alpha 047 ────────────────────────────────────────────────
    {
        "name": "gtja_047",
        "expression": "ts_mean((ts_max(high, 6) - close) / (ts_max(high, 6) - ts_min(low, 6)) * 100, 9)",
        "narrative": "GTJA-Alpha#047: 6日随机指标倒数的9日平滑",
    },
    # ─── Alpha 048 ────────────────────────────────────────────────
    {
        "name": "gtja_048",
        "expression": "-rank(sign(delta(close, 1)) + sign(delta(close, 2)) + sign(delta(close, 3))) * ts_sum(volume, 5) / ts_sum(volume, 20)",
        "narrative": "GTJA-Alpha#048: 价格方向变化信号累积乘以成交量比率",
    },
    # ─── Alpha 049 ────────────────────────────────────────────────
    {
        "name": "gtja_049",
        "expression": "ts_sum(ifelse((high + low) >= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12) / (ts_sum(ifelse((high + low) >= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12) + ts_sum(ifelse((high + low) <= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12))",
        "narrative": "GTJA-Alpha#049: 下跌日/总日振幅比率的12日累积",
    },
    # ─── Alpha 050 ────────────────────────────────────────────────
    {
        "name": "gtja_050",
        "expression": "ts_sum(ifelse((high + low) <= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12) / (ts_sum(ifelse((high + low) <= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12) + ts_sum(ifelse((high + low) >= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12)) - ts_sum(ifelse((high + low) >= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12) / (ts_sum(ifelse((high + low) >= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12) + ts_sum(ifelse((high + low) <= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12))",
        "narrative": "GTJA-Alpha#050: 上涨日/下跌日振幅比率的差值",
    },
    # ─── Alpha 051 ────────────────────────────────────────────────
    {
        "name": "gtja_051",
        "expression": "ts_sum(ifelse((high + low) <= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12) / (ts_sum(ifelse((high + low) <= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12) + ts_sum(ifelse((high + low) >= (delay(high, 1) + delay(low, 1)), 0, np.maximum(np.abs(high - delay(high, 1)), np.abs(low - delay(low, 1)))), 12))",
        "narrative": "GTJA-Alpha#051: 上涨日振幅占总振幅比率的12日累积",
    },
    # ─── Alpha 052 ────────────────────────────────────────────────
    {
        "name": "gtja_052",
        "expression": "ts_sum(np.maximum(0, high - delay((high + low + close) / 3, 1)), 26) / ts_sum(np.maximum(0, delay((high + low + close) / 3, 1) - low), 26) * 100",
        "narrative": "GTJA-Alpha#052: 典型价格向上/向下突破的26日比率",
    },
    # ─── Alpha 053 ────────────────────────────────────────────────
    {
        "name": "gtja_053",
        "expression": "ts_sum(ifelse(close > delay(close, 1), 1, 0), 12) / 12 * 100",
        "narrative": "GTJA-Alpha#053: 12日上涨比率百分比",
    },
    # ─── Alpha 054 ────────────────────────────────────────────────
    {
        "name": "gtja_054",
        "expression": "-rank(ts_stddev(np.abs(close - open_), 10) + (close - open_) + ts_corr(close, open_, 10))",
        "narrative": "GTJA-Alpha#054: 开盘价差波动率加价差加相关性的复合排序反转",
    },
    # ─── Alpha 055 ────────────────────────────────────────────────
    {
        "name": "gtja_055",
        "expression": "ts_sum(16 * (close - delay(close, 1) + (close - open_) / 2 + delay(close, 1) - delay(open_, 1)) / ifelse(np.abs(high - delay(close, 1)) > np.abs(low - delay(close, 1)) & np.abs(high - delay(close, 1)) > np.abs(high - delay(low, 1)), np.abs(high - delay(close, 1)) + np.abs(low - delay(close, 1)) / 2 + np.abs(delay(close, 1) - delay(open_, 1)) / 4, ifelse(np.abs(low - delay(close, 1)) > np.abs(high - delay(low, 1)) & np.abs(low - delay(close, 1)) > np.abs(high - delay(close, 1)), np.abs(low - delay(close, 1)) + np.abs(high - delay(close, 1)) / 2 + np.abs(delay(close, 1) - delay(open_, 1)) / 4, np.abs(high - delay(low, 1)) + np.abs(delay(close, 1) - delay(open_, 1)) / 4)) * np.maximum(np.abs(high - delay(close, 1)), np.abs(low - delay(close, 1))), 20)",
        "narrative": "GTJA-Alpha#055: 基于价格变化与波动类型的复杂自适应波动率调整累积",
    },
    # ─── Alpha 056 ────────────────────────────────────────────────
    {
        "name": "gtja_056",
        "expression": "rank(open_ - ts_min(open_, 12)) < rank(np.power(rank(ts_corr(ts_sum((high + low) / 2, 19), ts_sum(ts_mean(volume, 40), 19), 13)), 5))",
        "narrative": "GTJA-Alpha#056: 开盘价偏离12日低点与量价相关性排序5次方的比较",
    },
    # ─── Alpha 057 ────────────────────────────────────────────────
    {
        "name": "gtja_057",
        "expression": "ts_mean((close - ts_min(low, 9)) / (ts_max(high, 9) - ts_min(low, 9)) * 100, 3)",
        "narrative": "GTJA-Alpha#057: 9日随机指标的3日平滑",
    },
    # ─── Alpha 058 ────────────────────────────────────────────────
    {
        "name": "gtja_058",
        "expression": "ts_sum(ifelse(close > delay(close, 1), 1, 0), 20) / 20 * 100",
        "narrative": "GTJA-Alpha#058: 20日上涨比率百分比",
    },
    # ─── Alpha 059 ────────────────────────────────────────────────
    {
        "name": "gtja_059",
        "expression": "ts_sum(ifelse(close == delay(close, 1), 0, close - ifelse(close > delay(close, 1), np.minimum(low, delay(close, 1)), np.maximum(high, delay(close, 1)))), 20)",
        "narrative": "GTJA-Alpha#059: 条件价格突破与回溯距离的20日累积",
    },
    # ─── Alpha 060 ────────────────────────────────────────────────
    {
        "name": "gtja_060",
        "expression": "ts_sum((close - low - (high - close)) / (high - low) * volume, 20)",
        "narrative": "GTJA-Alpha#060: 价格位置比例的成交量加权20日累积",
    },
    # ─── Alpha 061 ────────────────────────────────────────────────
    {
        "name": "gtja_061",
        "expression": "-np.maximum(rank(decay_linear(delta(vwap, 1), 12)), rank(decay_linear(rank(ts_corr(low, ts_mean(volume, 80), 8)), 17)))",
        "narrative": "GTJA-Alpha#061: VWAP变化与低价-均量相关性衰减的排序最大值反转",
    },
    # ─── Alpha 062 ────────────────────────────────────────────────
    {
        "name": "gtja_062",
        "expression": "-ts_corr(high, rank(volume), 5)",
        "narrative": "GTJA-Alpha#062: 高价与成交量排序的5日相关性反转",
    },
    # ─── Alpha 063 ────────────────────────────────────────────────
    {
        "name": "gtja_063",
        "expression": "ts_mean(np.maximum(close - delay(close, 1), 0), 6) / ts_mean(np.abs(close - delay(close, 1)), 6) * 100",
        "narrative": "GTJA-Alpha#063: 6日增益/总波动比率（RSI类似指标）",
    },
    # ─── Alpha 064 ────────────────────────────────────────────────
    {
        "name": "gtja_064",
        "expression": "-np.maximum(rank(decay_linear(ts_corr(rank(vwap), rank(volume), 4), 4)), rank(decay_linear(np.maximum(ts_corr(rank(close), rank(ts_mean(volume, 60)), 4), 13), 14)))",
        "narrative": "GTJA-Alpha#064: 量价相关性衰减与收盘价-均量相关性衰减的排序最大值反转",
    },
    # ─── Alpha 065 ────────────────────────────────────────────────
    {
        "name": "gtja_065",
        "expression": "ts_mean(close, 6) / close",
        "narrative": "GTJA-Alpha#065: 6日均线与收盘价的比值",
    },
    # ─── Alpha 066 ────────────────────────────────────────────────
    {
        "name": "gtja_066",
        "expression": "(close - ts_mean(close, 6)) / ts_mean(close, 6) * 100",
        "narrative": "GTJA-Alpha#066: 收盘价相对6日均线偏离百分比",
    },
    # ─── Alpha 067 ────────────────────────────────────────────────
    {
        "name": "gtja_067",
        "expression": "ts_mean(np.maximum(close - delay(close, 1), 0), 24) / ts_mean(np.abs(close - delay(close, 1)), 24) * 100",
        "narrative": "GTJA-Alpha#067: 24日增益/总波动比率",
    },
    # ─── Alpha 068 ────────────────────────────────────────────────
    {
        "name": "gtja_068",
        "expression": "ts_mean(((high + low) / 2 - (delay(high, 1) + delay(low, 1)) / 2) * (high - low) / volume, 15)",
        "narrative": "GTJA-Alpha#068: 中间价变化与振幅的成交量加权15日均值",
    },
    # ─── Alpha 069 ────────────────────────────────────────────────
    {
        "name": "gtja_069",
        "expression": "ifelse(ts_sum(ifelse(open_ <= delay(open_, 1), 0, np.maximum(high - open_, open_ - delay(open_, 1))), 20) > ts_sum(ifelse(open_ >= delay(open_, 1), 0, np.maximum(open_ - low, open_ - delay(open_, 1))), 20), (ts_sum(ifelse(open_ <= delay(open_, 1), 0, np.maximum(high - open_, open_ - delay(open_, 1))), 20) - ts_sum(ifelse(open_ >= delay(open_, 1), 0, np.maximum(open_ - low, open_ - delay(open_, 1))), 20)) / ts_sum(ifelse(open_ <= delay(open_, 1), 0, np.maximum(high - open_, open_ - delay(open_, 1))), 20), ifelse(ts_sum(ifelse(open_ <= delay(open_, 1), 0, np.maximum(high - open_, open_ - delay(open_, 1))), 20) == ts_sum(ifelse(open_ >= delay(open_, 1), 0, np.maximum(open_ - low, open_ - delay(open_, 1))), 20), 0, (ts_sum(ifelse(open_ <= delay(open_, 1), 0, np.maximum(high - open_, open_ - delay(open_, 1))), 20) - ts_sum(ifelse(open_ >= delay(open_, 1), 0, np.maximum(open_ - low, open_ - delay(open_, 1))), 20)) / ts_sum(ifelse(open_ >= delay(open_, 1), 0, np.maximum(open_ - low, open_ - delay(open_, 1))), 20)))",
        "narrative": "GTJA-Alpha#069: DTM/DBM比率（开盘驱动方向动量指标）",
    },
    # ─── Alpha 070 ────────────────────────────────────────────────
    {
        "name": "gtja_070",
        "expression": "ts_stddev(volume * vwap, 6)",
        "narrative": "GTJA-Alpha#070: 成交金额的6日标准差",
    },
    # ─── Alpha 071 ────────────────────────────────────────────────
    {
        "name": "gtja_071",
        "expression": "(close - ts_mean(close, 24)) / ts_mean(close, 24) * 100",
        "narrative": "GTJA-Alpha#071: 收盘价相对24日均线偏离百分比",
    },
    # ─── Alpha 072 ────────────────────────────────────────────────
    {
        "name": "gtja_072",
        "expression": "ts_mean((ts_max(high, 6) - close) / (ts_max(high, 6) - ts_min(low, 6)) * 100, 15)",
        "narrative": "GTJA-Alpha#072: 6日随机指标倒数的15日平滑",
    },
    # ─── Alpha 073 ────────────────────────────────────────────────
    {
        "name": "gtja_073",
        "expression": "-(ts_rank(decay_linear(decay_linear(ts_corr(close, volume, 10), 16), 4), 5) - rank(decay_linear(ts_corr(vwap, ts_mean(volume, 30), 4), 3)))",
        "narrative": "GTJA-Alpha#073: 量价相关性双重衰减排序与VWAP-均量相关性衰减排序的差值反转",
    },
    # ─── Alpha 074 ────────────────────────────────────────────────
    {
        "name": "gtja_074",
        "expression": "rank(ts_corr(ts_sum(low * 0.35 + vwap * 0.65, 20), ts_sum(ts_mean(volume, 40), 20), 7)) + rank(ts_corr(rank(vwap), rank(volume), 6))",
        "narrative": "GTJA-Alpha#074: 加权低价-VWAP与均量累积的相关性排序加量价排序相关性排序",
    },
    # ─── Alpha 075 ────────────────────────────────────────────────
    {
        "name": "gtja_075",
        "expression": "ts_sum(ifelse(close > open_ & index_close < index_open, 1, 0), 50) / ts_sum(ifelse(index_close < index_open, 1, 0), 50)",
        "narrative": "GTJA-Alpha#075: 个股上涨且指数下跌日数占指数下跌日数的50日比率",
    },
    # ─── Alpha 076 ────────────────────────────────────────────────
    {
        "name": "gtja_076",
        "expression": "ts_stddev(np.abs(close / delay(close, 1) - 1) / volume, 20) / ts_mean(np.abs(close / delay(close, 1) - 1) / volume, 20)",
        "narrative": "GTJA-Alpha#076: 单位成交量收益率的波动率与均值之比",
    },
    # ─── Alpha 077 ────────────────────────────────────────────────
    {
        "name": "gtja_077",
        "expression": "np.minimum(rank(decay_linear(((high + low) / 2 + high) - (vwap + high), 20)), rank(decay_linear(ts_corr((high + low) / 2, ts_mean(volume, 40), 3), 6)))",
        "narrative": "GTJA-Alpha#077: 中间价与VWAP偏离衰减与中间价-均量相关性衰减的排序最小值",
    },
    # ─── Alpha 078 ────────────────────────────────────────────────
    {
        "name": "gtja_078",
        "expression": "((high + low + close) / 3 - ts_mean((high + low + close) / 3, 12)) / (0.015 * ts_mean(np.abs(close - ts_mean((high + low + close) / 3, 12)), 12))",
        "narrative": "GTJA-Alpha#078: 典型价格与12日均线的偏离归一化",
    },
    # ─── Alpha 079 ────────────────────────────────────────────────
    {
        "name": "gtja_079",
        "expression": "ts_mean(np.maximum(close - delay(close, 1), 0), 12) / ts_mean(np.abs(close - delay(close, 1)), 12) * 100",
        "narrative": "GTJA-Alpha#079: 12日增益/总波动比率",
    },
    # ─── Alpha 080 ────────────────────────────────────────────────
    {
        "name": "gtja_080",
        "expression": "(volume - delay(volume, 5)) / delay(volume, 5) * 100",
        "narrative": "GTJA-Alpha#080: 成交量5日变化百分比",
    },
    # ─── Alpha 081 ────────────────────────────────────────────────
    {
        "name": "gtja_081",
        "expression": "ts_mean(volume, 21)",
        "narrative": "GTJA-Alpha#081: 21日成交量均值",
    },
    # ─── Alpha 082 ────────────────────────────────────────────────
    {
        "name": "gtja_082",
        "expression": "ts_mean((ts_max(high, 6) - close) / (ts_max(high, 6) - ts_min(low, 6)) * 100, 20)",
        "narrative": "GTJA-Alpha#082: 6日随机指标倒数的20日平滑",
    },
    # ─── Alpha 083 ────────────────────────────────────────────────
    {
        "name": "gtja_083",
        "expression": "-rank(ts_covariance(rank(high), rank(volume), 5))",
        "narrative": "GTJA-Alpha#083: 高价-成交量排序协方差的排序反转",
    },
    # ─── Alpha 084 ────────────────────────────────────────────────
    {
        "name": "gtja_084",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, ifelse(close < delay(close, 1), -volume, 0)), 20)",
        "narrative": "GTJA-Alpha#084: 方向性成交量20日累积",
    },
    # ─── Alpha 085 ────────────────────────────────────────────────
    {
        "name": "gtja_085",
        "expression": "ts_rank(volume / ts_mean(volume, 20), 20) * ts_rank(-delta(close, 7), 8)",
        "narrative": "GTJA-Alpha#085: 成交量相对位置排序乘以收盘价7日变化负值排序",
    },
    # ─── Alpha 086 ────────────────────────────────────────────────
    {
        "name": "gtja_086",
        "expression": "ifelse(0.25 < ((delay(close, 20) - delay(close, 10)) / 10 - (delay(close, 10) - close) / 10), -1, ifelse(((delay(close, 20) - delay(close, 10)) / 10 - (delay(close, 10) - close) / 10) < 0, 1, -(close - delay(close, 1))))",
        "narrative": "GTJA-Alpha#086: 远期-近期斜率变化的三态条件信号",
    },
    # ─── Alpha 087 ────────────────────────────────────────────────
    {
        "name": "gtja_087",
        "expression": "-(rank(decay_linear(delta(vwap, 4), 7)) + ts_rank(decay_linear((low * 0.9 + low * 0.1 - vwap) / (open_ - (high + low) / 2), 11), 7))",
        "narrative": "GTJA-Alpha#087: VWAP变化衰减加低价-VWAP与开盘价偏移比率的衰减排序",
    },
    # ─── Alpha 088 ────────────────────────────────────────────────
    {
        "name": "gtja_088",
        "expression": "(close - delay(close, 20)) / delay(close, 20) * 100",
        "narrative": "GTJA-Alpha#088: 收盘价20日收益率百分比",
    },
    # ─── Alpha 089 ────────────────────────────────────────────────
    {
        "name": "gtja_089",
        "expression": "2 * (ts_mean(close, 13) - ts_mean(close, 27) - ts_mean(ts_mean(close, 13) - ts_mean(close, 27), 10))",
        "narrative": "GTJA-Alpha#089: 双均线差分的二次平滑（MACD变体）",
    },
    # ─── Alpha 090 ────────────────────────────────────────────────
    {
        "name": "gtja_090",
        "expression": "-rank(ts_corr(rank(vwap), rank(volume), 5))",
        "narrative": "GTJA-Alpha#090: VWAP-成交量排序相关性的排序反转",
    },
    # ─── Alpha 091 ────────────────────────────────────────────────
    {
        "name": "gtja_091",
        "expression": "-rank(close - ts_max(close, 5)) * rank(ts_corr(ts_mean(volume, 40), low, 5))",
        "narrative": "GTJA-Alpha#091: 收盘价偏离5日高点排序与均量-低价相关性排序的乘积反转",
    },
    # ─── Alpha 092 ────────────────────────────────────────────────
    {
        "name": "gtja_092",
        "expression": "-np.maximum(rank(decay_linear(delta(close * 0.35 + vwap * 0.65, 2), 3)), ts_rank(decay_linear(np.abs(ts_corr(ts_mean(volume, 180), close, 13)), 5), 15))",
        "narrative": "GTJA-Alpha#092: 加权价格变化衰减与长期均量-收盘价相关性绝对值衰减的排序最大值反转",
    },
    # ─── Alpha 093 ────────────────────────────────────────────────
    {
        "name": "gtja_093",
        "expression": "ts_sum(ifelse(open_ >= delay(open_, 1), 0, np.maximum(open_ - low, open_ - delay(open_, 1))), 20)",
        "narrative": "GTJA-Alpha#093: DBM（开盘下跌动量）的20日累积",
    },
    # ─── Alpha 094 ────────────────────────────────────────────────
    {
        "name": "gtja_094",
        "expression": "ts_sum(ifelse(close > delay(close, 1), volume, ifelse(close < delay(close, 1), -volume, 0)), 30)",
        "narrative": "GTJA-Alpha#094: 方向性成交量30日累积",
    },
    # ─── Alpha 095 ────────────────────────────────────────────────
    {
        "name": "gtja_095",
        "expression": "ts_stddev(volume * vwap, 20)",
        "narrative": "GTJA-Alpha#095: 成交金额的20日标准差",
    },
    # ─── Alpha 096 ────────────────────────────────────────────────
    {
        "name": "gtja_096",
        "expression": "ts_mean(ts_mean((close - ts_min(low, 9)) / (ts_max(high, 9) - ts_min(low, 9)) * 100, 3), 3)",
        "narrative": "GTJA-Alpha#096: 9日随机指标的双重3日平滑",
    },
    # ─── Alpha 097 ────────────────────────────────────────────────
    {
        "name": "gtja_097",
        "expression": "ts_stddev(volume, 10)",
        "narrative": "GTJA-Alpha#097: 成交量的10日标准差",
    },
    # ─── Alpha 098 ────────────────────────────────────────────────
    {
        "name": "gtja_098",
        "expression": "ifelse((delta(ts_mean(close, 100), 100) / delay(close, 100)) <= 0.05, -(close - ts_min(close, 100)), -delta(close, 3))",
        "narrative": "GTJA-Alpha#098: 若100日均线趋势平缓则做空100日最低价偏离，否则做空3日变化",
    },
    # ─── Alpha 099 ────────────────────────────────────────────────
    {
        "name": "gtja_099",
        "expression": "-rank(ts_covariance(rank(close), rank(volume), 5))",
        "narrative": "GTJA-Alpha#099: 收盘价-成交量排序协方差的排序反转",
    },
    # ─── Alpha 100 ────────────────────────────────────────────────
    {
        "name": "gtja_100",
        "expression": "ts_stddev(volume, 20)",
        "narrative": "GTJA-Alpha#100: 成交量的20日标准差",
    },
    # ─── Alpha 101 ────────────────────────────────────────────────
    {
        "name": "gtja_101",
        "expression": "-(rank(ts_corr(close, ts_sum(ts_mean(volume, 30), 37), 15)) < rank(ts_corr(rank(high * 0.1 + vwap * 0.9), rank(volume), 11)))",
        "narrative": "GTJA-Alpha#101: 收盘价-均量累积相关性与加权高价-VWAP量价排序相关性的比较反转",
    },
    # ─── Alpha 102 ────────────────────────────────────────────────
    {
        "name": "gtja_102",
        "expression": "ts_mean(np.maximum(volume - delay(volume, 1), 0), 6) / ts_mean(np.abs(volume - delay(volume, 1)), 6) * 100",
        "narrative": "GTJA-Alpha#102: 成交量增益/总波动比率（成交量RSI），6日",
    },
    # ─── Alpha 103 ────────────────────────────────────────────────
    {
        "name": "gtja_103",
        "expression": "(20 - (19 - lowday(low, 20))) / 20 * 100",
        "narrative": "GTJA-Alpha#103: 20日最低价位置百分比",
    },
    # ─── Alpha 104 ────────────────────────────────────────────────
    {
        "name": "gtja_104",
        "expression": "-delta(ts_corr(high, volume, 5), 5) * rank(ts_stddev(close, 20))",
        "narrative": "GTJA-Alpha#104: 量价相关性的5日变化乘以收盘价波动率排序",
    },
    # ─── Alpha 105 ────────────────────────────────────────────────
    {
        "name": "gtja_105",
        "expression": "-ts_corr(rank(open_), rank(volume), 10)",
        "narrative": "GTJA-Alpha#105: 开盘价-成交量排序的10日相关性反转",
    },
    # ─── Alpha 106 ────────────────────────────────────────────────
    {
        "name": "gtja_106",
        "expression": "close - delay(close, 20)",
        "narrative": "GTJA-Alpha#106: 收盘价20日变化",
    },
    # ─── Alpha 107 ────────────────────────────────────────────────
    {
        "name": "gtja_107",
        "expression": "-rank(open_ - delay(high, 1)) * rank(open_ - delay(close, 1)) * rank(open_ - delay(low, 1))",
        "narrative": "GTJA-Alpha#107: 开盘价相对前日高/收/低偏离的排序乘积",
    },
    # ─── Alpha 108 ────────────────────────────────────────────────
    {
        "name": "gtja_108",
        "expression": "-np.power(rank(high - ts_min(high, 2)), rank(ts_corr(vwap, ts_mean(volume, 120), 6)))",
        "narrative": "GTJA-Alpha#108: 高价短期偏离排序的VWAP-均量相关性排序次幂",
    },
    # ─── Alpha 109 ────────────────────────────────────────────────
    {
        "name": "gtja_109",
        "expression": "ts_mean(high - low, 10) / ts_mean(ts_mean(high - low, 10), 10)",
        "narrative": "GTJA-Alpha#109: 振幅均值与振幅均值的比值（波动率归一化）",
    },
    # ─── Alpha 110 ────────────────────────────────────────────────
    {
        "name": "gtja_110",
        "expression": "ts_sum(np.maximum(0, high - delay(close, 1)), 20) / ts_sum(np.maximum(0, delay(close, 1) - low), 20) * 100",
        "narrative": "GTJA-Alpha#110: 向上/向下突破幅度的20日比率",
    },
    # ─── Alpha 111 ────────────────────────────────────────────────
    {
        "name": "gtja_111",
        "expression": "ts_mean(volume * ((close - low) - (high - close)) / (high - low), 11) - ts_mean(volume * ((close - low) - (high - close)) / (high - low), 4)",
        "narrative": "GTJA-Alpha#111: 成交量加权价格位置的11日与4日均值差",
    },
    # ─── Alpha 112 ────────────────────────────────────────────────
    {
        "name": "gtja_112",
        "expression": "(ts_sum(ifelse(delta(close, 1) > 0, delta(close, 1), 0), 12) - ts_sum(ifelse(delta(close, 1) < 0, np.abs(delta(close, 1)), 0), 12)) / (ts_sum(ifelse(delta(close, 1) > 0, delta(close, 1), 0), 12) + ts_sum(ifelse(delta(close, 1) < 0, np.abs(delta(close, 1)), 0), 12)) * 100",
        "narrative": "GTJA-Alpha#112: 12日增益/总波动的比率（RSI归一化）",
    },
    # ─── Alpha 113 ────────────────────────────────────────────────
    {
        "name": "gtja_113",
        "expression": "-rank(ts_sum(delay(close, 5), 20) / 20) * ts_corr(close, volume, 2) * rank(ts_corr(ts_sum(close, 5), ts_sum(close, 20), 2))",
        "narrative": "GTJA-Alpha#113: 5日前收盘价均值排序乘以短期量价相关性乘以两周期收盘价累积相关性",
    },
    # ─── Alpha 114 ────────────────────────────────────────────────
    {
        "name": "gtja_114",
        "expression": "rank(delay((high - low) / ts_mean(close, 5), 2)) * rank(rank(volume)) / ((high - low) / ts_mean(close, 5) / (vwap - close))",
        "narrative": "GTJA-Alpha#114: 振幅比率延迟排序乘以成交量排序除以振幅比率与VWAP偏离的比值",
    },
    # ─── Alpha 115 ────────────────────────────────────────────────
    {
        "name": "gtja_115",
        "expression": "np.power(rank(ts_corr(high * 0.9 + close * 0.1, ts_mean(volume, 30), 10)), rank(ts_corr(ts_rank((high + low) / 2, 4), ts_rank(volume, 10), 7)))",
        "narrative": "GTJA-Alpha#115: 加权高价-收盘价与均量相关性排序的中间价-成交量排序相关性排序次幂",
    },
    # ─── Alpha 116 ────────────────────────────────────────────────
    {
        "name": "gtja_116",
        "expression": "decay_linear(close, 20) - ts_mean(close, 20)",
        "narrative": "GTJA-Alpha#116: 20日线性趋势斜率",
    },
    # ─── Alpha 117 ────────────────────────────────────────────────
    {
        "name": "gtja_117",
        "expression": "ts_rank(volume, 32) * (1 - ts_rank(close + high - low, 16)) * (1 - ts_rank(returns, 32))",
        "narrative": "GTJA-Alpha#117: 成交量排序乘以价格范围排序补数乘以收益排序补数",
    },
    # ─── Alpha 118 ────────────────────────────────────────────────
    {
        "name": "gtja_118",
        "expression": "ts_sum(high - open_, 20) / ts_sum(open_ - low, 20) * 100",
        "narrative": "GTJA-Alpha#118: 上影线/下影线的20日比率",
    },
    # ─── Alpha 119 ────────────────────────────────────────────────
    {
        "name": "gtja_119",
        "expression": "rank(decay_linear(ts_corr(vwap, ts_sum(ts_mean(volume, 5), 26), 5), 7)) - rank(decay_linear(ts_rank(ts_min(ts_corr(rank(open_), rank(ts_mean(volume, 15)), 21), 9), 7), 8))",
        "narrative": "GTJA-Alpha#119: VWAP-均量累积相关性衰减排序与开盘价-均量排序相关性最小值衰减排序的差值",
    },
    # ─── Alpha 120 ────────────────────────────────────────────────
    {
        "name": "gtja_120",
        "expression": "rank(vwap - close) / rank(vwap + close)",
        "narrative": "GTJA-Alpha#120: VWAP偏离度排序与VWAP中枢排序的比值",
    },
    # ─── Alpha 121 ────────────────────────────────────────────────
    {
        "name": "gtja_121",
        "expression": "-np.power(rank(vwap - ts_min(vwap, 12)), ts_rank(ts_corr(ts_rank(vwap, 20), ts_rank(ts_mean(volume, 60), 2), 18), 3))",
        "narrative": "GTJA-Alpha#121: VWAP偏离12日低点排序的VWAP-均量排序相关性排序次幂",
    },
    # ─── Alpha 122 ────────────────────────────────────────────────
    {
        "name": "gtja_122",
        "expression": "(ts_mean(ts_mean(ts_mean(log(close), 13), 13), 13) - delay(ts_mean(ts_mean(ts_mean(log(close), 13), 13), 13), 1)) / delay(ts_mean(ts_mean(ts_mean(log(close), 13), 13), 13), 1)",
        "narrative": "GTJA-Alpha#122: 对数收盘价三重平滑的收益率",
    },
    # ─── Alpha 123 ────────────────────────────────────────────────
    {
        "name": "gtja_123",
        "expression": "-(rank(ts_corr(ts_sum((high + low) / 2, 20), ts_sum(ts_mean(volume, 60), 20), 9)) < rank(ts_corr(low, volume, 6)))",
        "narrative": "GTJA-Alpha#123: 中间价-均量累积相关性与低价-成交量相关性的排序比较反转",
    },
    # ─── Alpha 124 ────────────────────────────────────────────────
    {
        "name": "gtja_124",
        "expression": "(close - vwap) / decay_linear(rank(ts_max(close, 30)), 2)",
        "narrative": "GTJA-Alpha#124: VWAP偏离除以30日高点排序的衰减",
    },
    # ─── Alpha 125 ────────────────────────────────────────────────
    {
        "name": "gtja_125",
        "expression": "rank(decay_linear(ts_corr(vwap, ts_mean(volume, 80), 17), 20)) / rank(decay_linear(delta(close * 0.5 + vwap * 0.5, 3), 16))",
        "narrative": "GTJA-Alpha#125: VWAP-均量相关性衰减排序与加权价格变化衰减排序的比值",
    },
    # ─── Alpha 126 ────────────────────────────────────────────────
    {
        "name": "gtja_126",
        "expression": "(close + high + low) / 3",
        "narrative": "GTJA-Alpha#126: 典型价格（TP）",
    },
    # ─── Alpha 127 ────────────────────────────────────────────────
    {
        "name": "gtja_127",
        "expression": "np.sqrt(ts_mean(np.power(100 * (close - ts_max(close, 12)) / ts_max(close, 12), 2), 12))",
        "narrative": "GTJA-Alpha#127: 收盘价偏离12日高点百分比的RMS",
    },
    # ─── Alpha 128 ────────────────────────────────────────────────
    {
        "name": "gtja_128",
        "expression": "100 - (100 / (1 + ts_sum(ifelse((high + low + close) / 3 > delay((high + low + close) / 3, 1), (high + low + close) / 3 * volume, 0), 14) / ts_sum(ifelse((high + low + close) / 3 < delay((high + low + close) / 3, 1), (high + low + close) / 3 * volume, 0), 14)))",
        "narrative": "GTJA-Alpha#128: 典型价格方向性成交量比的14日累积指标",
    },
    # ─── Alpha 129 ────────────────────────────────────────────────
    {
        "name": "gtja_129",
        "expression": "ts_sum(ifelse(delta(close, 1) < 0, np.abs(delta(close, 1)), 0), 12)",
        "narrative": "GTJA-Alpha#129: 12日下跌幅度累积",
    },
    # ─── Alpha 130 ────────────────────────────────────────────────
    {
        "name": "gtja_130",
        "expression": "rank(decay_linear(ts_corr((high + low) / 2, ts_mean(volume, 40), 9), 10)) / rank(decay_linear(ts_corr(rank(vwap), rank(volume), 7), 3))",
        "narrative": "GTJA-Alpha#130: 中间价-均量相关性衰减排序与VWAP-成交量排序相关性衰减排序的比值",
    },
    # ─── Alpha 131 ────────────────────────────────────────────────
    {
        "name": "gtja_131",
        "expression": "np.power(rank(delta(vwap, 1)), ts_rank(ts_corr(close, ts_mean(volume, 50), 18), 18))",
        "narrative": "GTJA-Alpha#131: VWAP变化排序的收盘价-均量相关性排序次幂",
    },
    # ─── Alpha 132 ────────────────────────────────────────────────
    {
        "name": "gtja_132",
        "expression": "ts_mean(volume * vwap, 20)",
        "narrative": "GTJA-Alpha#132: 20日成交金额均值",
    },
    # ─── Alpha 133 ────────────────────────────────────────────────
    {
        "name": "gtja_133",
        "expression": "(20 - (19 - highday(high, 20))) / 20 * 100 - (20 - (19 - lowday(low, 20))) / 20 * 100",
        "narrative": "GTJA-Alpha#133: 20日最高价位置与最低价位置的百分比差",
    },
    # ─── Alpha 134 ────────────────────────────────────────────────
    {
        "name": "gtja_134",
        "expression": "(close - delay(close, 12)) / delay(close, 12) * volume",
        "narrative": "GTJA-Alpha#134: 12日收益率乘以成交量",
    },
    # ─── Alpha 135 ────────────────────────────────────────────────
    {
        "name": "gtja_135",
        "expression": "ts_mean(delay(close / delay(close, 20), 1), 20)",
        "narrative": "GTJA-Alpha#135: 20日价格比率的延迟20日平滑",
    },
    # ─── Alpha 136 ────────────────────────────────────────────────
    {
        "name": "gtja_136",
        "expression": "-rank(delta(returns, 3)) * ts_corr(open_, volume, 10)",
        "narrative": "GTJA-Alpha#136: 收益率的3日变化排序乘以开盘价-成交量相关性",
    },
    # ─── Alpha 137 ────────────────────────────────────────────────
    {
        "name": "gtja_137",
        "expression": "16 * (close - delay(close, 1) + (close - open_) / 2 + delay(close, 1) - delay(open_, 1)) / ifelse(np.abs(high - delay(close, 1)) > np.abs(low - delay(close, 1)) & np.abs(high - delay(close, 1)) > np.abs(high - delay(low, 1)), np.abs(high - delay(close, 1)) + np.abs(low - delay(close, 1)) / 2 + np.abs(delay(close, 1) - delay(open_, 1)) / 4, ifelse(np.abs(low - delay(close, 1)) > np.abs(high - delay(low, 1)) & np.abs(low - delay(close, 1)) > np.abs(high - delay(close, 1)), np.abs(low - delay(close, 1)) + np.abs(high - delay(close, 1)) / 2 + np.abs(delay(close, 1) - delay(open_, 1)) / 4, np.abs(high - delay(low, 1)) + np.abs(delay(close, 1) - delay(open_, 1)) / 4)) * np.maximum(np.abs(high - delay(close, 1)), np.abs(low - delay(close, 1)))",
        "narrative": "GTJA-Alpha#137: 基于价格变化与波动类型自适应调整的波动率修正",
    },
    # ─── Alpha 138 ────────────────────────────────────────────────
    {
        "name": "gtja_138",
        "expression": "-(rank(decay_linear(delta(low * 0.7 + vwap * 0.3, 3), 20)) - ts_rank(decay_linear(ts_rank(ts_corr(ts_rank(low, 8), ts_rank(ts_mean(volume, 60), 17), 5), 19), 16), 7))",
        "narrative": "GTJA-Alpha#138: 加权低价-VWAP变化衰减与低价-均量排序相关性多重衰减排序的差值反转",
    },
    # ─── Alpha 139 ────────────────────────────────────────────────
    {
        "name": "gtja_139",
        "expression": "-ts_corr(open_, volume, 10)",
        "narrative": "GTJA-Alpha#139: 开盘价-成交量10日相关性反转",
    },
    # ─── Alpha 140 ────────────────────────────────────────────────
    {
        "name": "gtja_140",
        "expression": "np.minimum(rank(decay_linear(rank(open_) + rank(low) - (rank(high) + rank(close)), 8)), ts_rank(decay_linear(ts_corr(ts_rank(close, 8), ts_rank(ts_mean(volume, 60), 20), 8), 7), 3))",
        "narrative": "GTJA-Alpha#140: 价格模式排序衰减与收盘价-均量排序相关性衰减排序的最小值",
    },
    # ─── Alpha 141 ────────────────────────────────────────────────
    {
        "name": "gtja_141",
        "expression": "-rank(ts_corr(rank(high), rank(ts_mean(volume, 15)), 9))",
        "narrative": "GTJA-Alpha#141: 高价-均量排序相关性的排序反转",
    },
    # ─── Alpha 142 ────────────────────────────────────────────────
    {
        "name": "gtja_142",
        "expression": "-rank(ts_rank(close, 10)) * rank(delta(delta(close, 1), 1)) * rank(ts_rank(volume / ts_mean(volume, 20), 5))",
        "narrative": "GTJA-Alpha#142: 收盘价排序反转乘以加速度乘以成交量相对位置排序",
    },
    # ─── Alpha 143 ────────────────────────────────────────────────
    {
        "name": "gtja_143",
        "expression": "ts_product(1 + ifelse(close > delay(close, 1), returns, 0), 20)",
        "narrative": "GTJA-Alpha#143: 上涨日收益率累积乘积（状态依赖累积近似）",
    },
    # ─── Alpha 144 ────────────────────────────────────────────────
    {
        "name": "gtja_144",
        "expression": "ts_sum(ifelse(close < delay(close, 1), np.abs(close / delay(close, 1) - 1) / (volume * vwap), 0), 20) / ts_sum(ifelse(close < delay(close, 1), 1, 0), 20)",
        "narrative": "GTJA-Alpha#144: 下跌日单位成交金额收益率均值",
    },
    # ─── Alpha 145 ────────────────────────────────────────────────
    {
        "name": "gtja_145",
        "expression": "(ts_mean(volume, 9) - ts_mean(volume, 26)) / ts_mean(volume, 12) * 100",
        "narrative": "GTJA-Alpha#145: 成交量9日与26日均线差除以12日均线",
    },
    # ─── Alpha 146 ────────────────────────────────────────────────
    {
        "name": "gtja_146",
        "expression": "ts_mean(returns - ts_mean(returns, 61), 20) * (returns - ts_mean(returns, 61)) / ts_mean(np.power(ts_mean(returns, 61), 2), 60)",
        "narrative": "GTJA-Alpha#146: 收益率偏离61日均值的20日均值乘以当前偏离除以偏离平方的60日均值",
    },
    # ─── Alpha 147 ────────────────────────────────────────────────
    {
        "name": "gtja_147",
        "expression": "decay_linear(ts_mean(close, 12), 12) - ts_mean(ts_mean(close, 12), 12)",
        "narrative": "GTJA-Alpha#147: 12日均线的线性趋势斜率",
    },
    # ─── Alpha 148 ────────────────────────────────────────────────
    {
        "name": "gtja_148",
        "expression": "-(rank(ts_corr(open_, ts_sum(ts_mean(volume, 60), 9), 6)) < rank(open_ - ts_min(open_, 14)))",
        "narrative": "GTJA-Alpha#148: 开盘价-均量累积相关性与开盘价偏离14日低点的排序比较反转",
    },
    # ─── Alpha 149 ────────────────────────────────────────────────
    {
        "name": "gtja_149",
        "expression": "ts_corr(ifelse(index_close < delay(index_close, 1), returns, 0), ifelse(index_close < delay(index_close, 1), index_returns, 0), 252) * ts_stddev(ifelse(index_close < delay(index_close, 1), returns, 0), 252) / ts_stddev(ifelse(index_close < delay(index_close, 1), index_returns, 0), 252)",
        "narrative": "GTJA-Alpha#149: 指数下跌日的下行Beta（252日）",
    },
    # ─── Alpha 150 ────────────────────────────────────────────────
    {
        "name": "gtja_150",
        "expression": "(close + high + low) / 3 * volume",
        "narrative": "GTJA-Alpha#150: 典型价格乘以成交量",
    },
    # ─── Alpha 151 ────────────────────────────────────────────────
    {
        "name": "gtja_151",
        "expression": "ts_mean(close - delay(close, 20), 20)",
        "narrative": "GTJA-Alpha#151: 收盘价20日变化的20日平滑",
    },
    # ─── Alpha 152 ────────────────────────────────────────────────
    {
        "name": "gtja_152",
        "expression": "ts_mean(ts_mean(delay(ts_mean(delay(close / delay(close, 9), 1), 9), 1), 12) - ts_mean(delay(ts_mean(delay(close / delay(close, 9), 1), 9), 1), 26), 9)",
        "narrative": "GTJA-Alpha#152: 9日价格比率平滑的12日与26日均线差的9日平滑",
    },
    # ─── Alpha 153 ────────────────────────────────────────────────
    {
        "name": "gtja_153",
        "expression": "(ts_mean(close, 3) + ts_mean(close, 6) + ts_mean(close, 12) + ts_mean(close, 24)) / 4",
        "narrative": "GTJA-Alpha#153: 多周期均线等权均值",
    },
    # ─── Alpha 154 ────────────────────────────────────────────────
    {
        "name": "gtja_154",
        "expression": "(vwap - ts_min(vwap, 16)) < ts_corr(vwap, ts_mean(volume, 180), 18)",
        "narrative": "GTJA-Alpha#154: VWAP偏离16日低点与VWAP-长期均量相关性的比较",
    },
    # ─── Alpha 155 ────────────────────────────────────────────────
    {
        "name": "gtja_155",
        "expression": "ts_mean(volume, 13) - ts_mean(volume, 27) - ts_mean(ts_mean(volume, 13) - ts_mean(volume, 27), 10)",
        "narrative": "GTJA-Alpha#155: 成交量MACD（13/27/10）",
    },
    # ─── Alpha 156 ────────────────────────────────────────────────
    {
        "name": "gtja_156",
        "expression": "-np.maximum(rank(decay_linear(delta(vwap, 5), 3)), rank(decay_linear(delta(open_ * 0.15 + low * 0.85, 2) / (open_ * 0.15 + low * 0.85), 3)))",
        "narrative": "GTJA-Alpha#156: VWAP变化衰减与加权开盘-低价变化率衰减的排序最大值反转",
    },
    # ─── Alpha 157 ────────────────────────────────────────────────
    {
        "name": "gtja_157",
        "expression": "np.minimum(rank(rank(log(ts_min(rank(rank(-rank(delta(close - 1, 5)))), 2)))), 5) + ts_rank(delay(-returns, 6), 5)",
        "narrative": "GTJA-Alpha#157: 多层嵌套排序的对数最小值与延迟收益排序之和",
    },
    # ─── Alpha 158 ────────────────────────────────────────────────
    {
        "name": "gtja_158",
        "expression": "((high - ts_mean(close, 15)) - (low - ts_mean(close, 15))) / close",
        "narrative": "GTJA-Alpha#158: 高低价相对15日均线的偏离差除以收盘价",
    },
    # ─── Alpha 159 ────────────────────────────────────────────────
    {
        "name": "gtja_159",
        "expression": "((close - ts_sum(np.minimum(low, delay(close, 1)), 6)) / ts_sum(np.maximum(high, delay(close, 1)) - np.minimum(low, delay(close, 1)), 6) * 12 * 24 + (close - ts_sum(np.minimum(low, delay(close, 1)), 12)) / ts_sum(np.maximum(high, delay(close, 1)) - np.minimum(low, delay(close, 1)), 12) * 6 * 24 + (close - ts_sum(np.minimum(low, delay(close, 1)), 24)) / ts_sum(np.maximum(high, delay(close, 1)) - np.minimum(low, delay(close, 1)), 24) * 6 * 24) * 100 / (6 * 12 + 6 * 24 + 12 * 24)",
        "narrative": "GTJA-Alpha#159: 多周期(6/12/24日)价格位置加权平均指标",
    },
    # ─── Alpha 160 ────────────────────────────────────────────────
    {
        "name": "gtja_160",
        "expression": "ts_mean(ifelse(close <= delay(close, 1), ts_stddev(close, 20), 0), 20)",
        "narrative": "GTJA-Alpha#160: 下跌日波动率的20日平滑",
    },
    # ─── Alpha 161 ────────────────────────────────────────────────
    {
        "name": "gtja_161",
        "expression": "ts_mean(np.maximum(np.maximum(high - low, np.abs(delay(close, 1) - high)), np.abs(delay(close, 1) - low)), 12)",
        "narrative": "GTJA-Alpha#161: 真实波幅均值（ATR），12日",
    },
    # ─── Alpha 162 ────────────────────────────────────────────────
    {
        "name": "gtja_162",
        "expression": "(ts_mean(np.maximum(close - delay(close, 1), 0), 12) / ts_mean(np.abs(close - delay(close, 1)), 12) * 100 - ts_min(ts_mean(np.maximum(close - delay(close, 1), 0), 12) / ts_mean(np.abs(close - delay(close, 1)), 12) * 100, 12)) / (ts_max(ts_mean(np.maximum(close - delay(close, 1), 0), 12) / ts_mean(np.abs(close - delay(close, 1)), 12) * 100, 12) - ts_min(ts_mean(np.maximum(close - delay(close, 1), 0), 12) / ts_mean(np.abs(close - delay(close, 1)), 12) * 100, 12))",
        "narrative": "GTJA-Alpha#162: RSI的12日随机指标归一化",
    },
    # ─── Alpha 163 ────────────────────────────────────────────────
    {
        "name": "gtja_163",
        "expression": "rank(-returns * ts_mean(volume, 20) * vwap * (high - close))",
        "narrative": "GTJA-Alpha#163: 负收益乘以均量乘以VWAP乘以高价差的排序",
    },
    # ─── Alpha 164 ────────────────────────────────────────────────
    {
        "name": "gtja_164",
        "expression": "ts_mean((ifelse(close > delay(close, 1), 1 / (close - delay(close, 1)), 1) - ts_min(ifelse(close > delay(close, 1), 1 / (close - delay(close, 1)), 1), 12)) / (high - low) * 100, 13)",
        "narrative": "GTJA-Alpha#164: 收盘价变化倒数归一化后的13日平滑",
    },
    # ─── Alpha 165 ────────────────────────────────────────────────
    {
        "name": "gtja_165",
        "expression": "np.max(ts_sum(close - ts_mean(close, 48), 48)) - np.min(ts_sum(close - ts_mean(close, 48), 48)) / ts_stddev(close, 48)",
        "narrative": "GTJA-Alpha#165: 48日累积偏离的极值差除以标准差",
    },
    # ─── Alpha 166 ────────────────────────────────────────────────
    {
        "name": "gtja_166",
        "expression": "-20 * np.power(19, 1.5) * ts_sum(returns - ts_mean(returns, 20), 20) / (19 * 18 * np.power(ts_sum(np.power(ts_mean(close / delay(close, 1), 20), 2), 20), 1.5))",
        "narrative": "GTJA-Alpha#166: 收益率偏度指标（20日）",
    },
    # ─── Alpha 167 ────────────────────────────────────────────────
    {
        "name": "gtja_167",
        "expression": "ts_sum(ifelse(delta(close, 1) > 0, delta(close, 1), 0), 12)",
        "narrative": "GTJA-Alpha#167: 12日上涨幅度累积",
    },
    # ─── Alpha 168 ────────────────────────────────────────────────
    {
        "name": "gtja_168",
        "expression": "-volume / ts_mean(volume, 20)",
        "narrative": "GTJA-Alpha#168: 负成交量与20日均量的比值",
    },
    # ─── Alpha 169 ────────────────────────────────────────────────
    {
        "name": "gtja_169",
        "expression": "ts_mean(ts_mean(delay(ts_mean(delta(close, 1), 9), 1), 12) - ts_mean(delay(ts_mean(delta(close, 1), 9), 1), 26), 10)",
        "narrative": "GTJA-Alpha#169: 价格变化平滑的12日与26日均线差的10日平滑",
    },
    # ─── Alpha 170 ────────────────────────────────────────────────
    {
        "name": "gtja_170",
        "expression": "rank(1 / close) * volume / ts_mean(volume, 20) * high * rank(high - close) / ts_mean(high, 5) - rank(vwap - delay(vwap, 5))",
        "narrative": "GTJA-Alpha#170: 多因子复合排序减去VWAP变化排序",
    },
    # ─── Alpha 171 ────────────────────────────────────────────────
    {
        "name": "gtja_171",
        "expression": "-(low - close) * np.power(open_, 5) / ((close - high) * np.power(close, 5))",
        "narrative": "GTJA-Alpha#171: 低价差与高价差的5次方加权比率",
    },
    # ─── Alpha 172 ────────────────────────────────────────────────
    {
        "name": "gtja_172",
        "expression": "ts_mean(np.abs(ts_sum(ifelse((delay(low, 1) - low > 0) & (delay(low, 1) - low > high - delay(high, 1)), delay(low, 1) - low, 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14) - ts_sum(ifelse((high - delay(high, 1) > 0) & (high - delay(high, 1) > delay(low, 1) - low), high - delay(high, 1), 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14)) / (ts_sum(ifelse((delay(low, 1) - low > 0) & (delay(low, 1) - low > high - delay(high, 1)), delay(low, 1) - low, 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14) + ts_sum(ifelse((high - delay(high, 1) > 0) & (high - delay(high, 1) > delay(low, 1) - low), high - delay(high, 1), 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14)) * 100, 6)",
        "narrative": "GTJA-Alpha#172: 上涨/下跌波动率差异的6日平滑（ADX变体）",
    },
    # ─── Alpha 173 ────────────────────────────────────────────────
    {
        "name": "gtja_173",
        "expression": "3 * ts_mean(close, 13) - 2 * ts_mean(ts_mean(close, 13), 13) + ts_mean(ts_mean(ts_mean(log(close), 13), 13), 13)",
        "narrative": "GTJA-Alpha#173: 三重平滑复合指标",
    },
    # ─── Alpha 174 ────────────────────────────────────────────────
    {
        "name": "gtja_174",
        "expression": "ts_mean(ifelse(close > delay(close, 1), ts_stddev(close, 20), 0), 20)",
        "narrative": "GTJA-Alpha#174: 上涨日波动率的20日平滑",
    },
    # ─── Alpha 175 ────────────────────────────────────────────────
    {
        "name": "gtja_175",
        "expression": "ts_mean(np.maximum(np.maximum(high - low, np.abs(delay(close, 1) - high)), np.abs(delay(close, 1) - low)), 6)",
        "narrative": "GTJA-Alpha#175: 真实波幅均值（ATR），6日",
    },
    # ─── Alpha 176 ────────────────────────────────────────────────
    {
        "name": "gtja_176",
        "expression": "ts_corr(rank((close - ts_min(low, 12)) / (ts_max(high, 12) - ts_min(low, 12))), rank(volume), 6)",
        "narrative": "GTJA-Alpha#176: 12日随机指标排序与成交量排序的6日相关性",
    },
    # ─── Alpha 177 ────────────────────────────────────────────────
    {
        "name": "gtja_177",
        "expression": "(20 - (19 - highday(high, 20))) / 20 * 100",
        "narrative": "GTJA-Alpha#177: 20日最高价位置百分比",
    },
    # ─── Alpha 178 ────────────────────────────────────────────────
    {
        "name": "gtja_178",
        "expression": "(close - delay(close, 1)) / delay(close, 1) * volume",
        "narrative": "GTJA-Alpha#178: 日收益率乘以成交量",
    },
    # ─── Alpha 179 ────────────────────────────────────────────────
    {
        "name": "gtja_179",
        "expression": "rank(ts_corr(vwap, volume, 4)) * rank(ts_corr(rank(low), rank(ts_mean(volume, 50)), 12))",
        "narrative": "GTJA-Alpha#179: VWAP-成交量相关性排序与低价-均量排序相关性排序的乘积",
    },
    # ─── Alpha 180 ────────────────────────────────────────────────
    {
        "name": "gtja_180",
        "expression": "ifelse(ts_mean(volume, 20) < volume, -ts_rank(np.abs(delta(close, 7)), 60) * sign(delta(close, 7)), -volume)",
        "narrative": "GTJA-Alpha#180: 成交量高于均量时用价格变化方向排序，否则用负成交量",
    },
    # ─── Alpha 181 ────────────────────────────────────────────────
    {
        "name": "gtja_181",
        "expression": "ts_sum((returns - ts_mean(returns, 20)) - np.power(index_returns - ts_mean(index_returns, 20), 2), 20) / ts_sum(np.power(index_returns - ts_mean(index_returns, 20), 3), 20)",
        "narrative": "GTJA-Alpha#181: 个股收益与指数收益的协偏度",
    },
    # ─── Alpha 182 ────────────────────────────────────────────────
    {
        "name": "gtja_182",
        "expression": "ts_sum(ifelse((close > open_ & index_close > index_open) | (close < open_ & index_close < index_open), 1, 0), 20) / 20",
        "narrative": "GTJA-Alpha#182: 个股与指数方向一致的20日比率",
    },
    # ─── Alpha 183 ────────────────────────────────────────────────
    {
        "name": "gtja_183",
        "expression": "np.max(ts_sum(close - ts_mean(close, 24), 24)) - np.min(ts_sum(close - ts_mean(close, 24), 24)) / ts_stddev(close, 24)",
        "narrative": "GTJA-Alpha#183: 24日累积偏离的极值差除以标准差",
    },
    # ─── Alpha 184 ────────────────────────────────────────────────
    {
        "name": "gtja_184",
        "expression": "rank(ts_corr(delay(open_ - close, 1), close, 200)) + rank(open_ - close)",
        "narrative": "GTJA-Alpha#184: 前日开盘收盘价差与收盘价的200日相关性排序加当日开盘收盘价差排序",
    },
    # ─── Alpha 185 ────────────────────────────────────────────────
    {
        "name": "gtja_185",
        "expression": "rank(-np.power(1 - open_ / close, 2))",
        "narrative": "GTJA-Alpha#185: 开盘收盘价比率偏离的平方取负排序",
    },
    # ─── Alpha 186 ────────────────────────────────────────────────
    {
        "name": "gtja_186",
        "expression": "(ts_mean(np.abs(ts_sum(ifelse((delay(low, 1) - low > 0) & (delay(low, 1) - low > high - delay(high, 1)), delay(low, 1) - low, 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14) - ts_sum(ifelse((high - delay(high, 1) > 0) & (high - delay(high, 1) > delay(low, 1) - low), high - delay(high, 1), 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14)) / (ts_sum(ifelse((delay(low, 1) - low > 0) & (delay(low, 1) - low > high - delay(high, 1)), delay(low, 1) - low, 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14) + ts_sum(ifelse((high - delay(high, 1) > 0) & (high - delay(high, 1) > delay(low, 1) - low), high - delay(high, 1), 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14)) * 100, 6) + delay(ts_mean(np.abs(ts_sum(ifelse((delay(low, 1) - low > 0) & (delay(low, 1) - low > high - delay(high, 1)), delay(low, 1) - low, 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14) - ts_sum(ifelse((high - delay(high, 1) > 0) & (high - delay(high, 1) > delay(low, 1) - low), high - delay(high, 1), 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14)) / (ts_sum(ifelse((delay(low, 1) - low > 0) & (delay(low, 1) - low > high - delay(high, 1)), delay(low, 1) - low, 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14) + ts_sum(ifelse((high - delay(high, 1) > 0) & (high - delay(high, 1) > delay(low, 1) - low), high - delay(high, 1), 0), 14) * 100 / ts_sum(np.maximum(np.maximum(high - low, np.abs(high - delay(close, 1))), np.abs(low - delay(close, 1))), 14)) * 100, 6), 6)) / 2",
        "narrative": "GTJA-Alpha#186: 上涨/下跌波动率差异的6日平滑与其6日延迟的均值",
    },
    # ─── Alpha 187 ────────────────────────────────────────────────
    {
        "name": "gtja_187",
        "expression": "ts_sum(ifelse(open_ <= delay(open_, 1), 0, np.maximum(high - open_, open_ - delay(open_, 1))), 20)",
        "narrative": "GTJA-Alpha#187: DTM（开盘上涨动量）的20日累积",
    },
    # ─── Alpha 188 ────────────────────────────────────────────────
    {
        "name": "gtja_188",
        "expression": "((high - low) - ts_mean(high - low, 11)) / ts_mean(high - low, 11) * 100",
        "narrative": "GTJA-Alpha#188: 振幅偏离11日均线的百分比",
    },
    # ─── Alpha 189 ────────────────────────────────────────────────
    {
        "name": "gtja_189",
        "expression": "ts_mean(np.abs(close - ts_mean(close, 6)), 6)",
        "narrative": "GTJA-Alpha#189: 收盘价相对6日均线的平均绝对偏离",
    },
    # ─── Alpha 190 ────────────────────────────────────────────────
    {
        "name": "gtja_190",
        "expression": "log((ts_sum(ifelse(returns > (np.power(close / delay(close, 19), 1/20) - 1), 1, 0), 20) - 1) * ts_sum(ifelse(returns < (np.power(close / delay(close, 19), 1/20) - 1), np.power(returns - (np.power(close / delay(close, 19), 1/20) - 1), 2), 0), 20) / (ts_sum(ifelse(returns < (np.power(close / delay(close, 19), 1/20) - 1), 1, 0), 20) * ts_sum(ifelse(returns > (np.power(close / delay(close, 19), 1/20) - 1), np.power(returns - (np.power(close / delay(close, 19), 1/20) - 1), 2), 0), 20)))",
        "narrative": "GTJA-Alpha#190: 收益率偏度比率（基于20日增长趋势基准）",
    },
    # ─── Alpha 191 ────────────────────────────────────────────────
    {
        "name": "gtja_191",
        "expression": "ts_corr(ts_mean(volume, 20), low, 5) + (high + low) / 2 - close",
        "narrative": "GTJA-Alpha#191: 均量-低价相关性加中间价减收盘价",
    },
]
