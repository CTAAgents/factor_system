"""
seed_data/fundamental_seeds.py — 基本面/另类/宏观因子种子定义

为 FTS 提供基于基本面数据（估值、财务质量、成长、宏观）的种子因子，
作为 WQ 101 Alpha 等量价因子的补充，形成"量价+基本面+宏观"全谱系因子生态。

来源: 经典学术因子 + 基本面分析框架
- 估值因子: Fama-French HML, 价值投资 (Graham/Buffett)
- 质量因子: Piotroski F-Score, Novy-Marx Quality, ROE因子
- 成长因子: 盈利增长, 营收增长 (growth investing)
- 宏观因子: PMI, CPI 等宏观指标代理

数据依赖:
    所有因子通过 FundamentalProvider 注入的字段消费，
    字段定义见 fts/data_fundamental.py。

版本: v1.0.0
"""

from __future__ import annotations
from typing import Any


# ─── 基本面因子代码模板 ───────────────────────────────────

_FUNDAMENTAL_TEMPLATE = '''\
def factor_program(data, params):
    """Fundamental Alpha: {name} — {narrative}"""
    import numpy as np
    n = len(data['close'].values) if hasattr(data, 'close') else len(data['close'])
    {field_defs}

    if {field_check}:
        score = {expression}
    else:
        # 基本面字段不可用时的降级：使用 close 代理
        close = data['close'].values if hasattr(data, 'close') else data['close']
        returns = np.zeros(n)
        returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
        score = np.tanh(returns * 10) * 0.3

    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
'''


# ─── 因子定义 ─────────────────────────────────────────────

FUNDAMENTAL_DEFINITIONS: list[dict[str, Any]] = [
    # ══════════════════════════════════════════════════════
    # 估值因子 (Value Factors) — 低估值 = 正信号
    # ══════════════════════════════════════════════════════

    # ── fund_val_pe: 低 PE 价值因子 ──
    {
        "name": "fund_val_pe",
        "narrative": "低PE估值因子：市盈率倒数越大（PE越低）价值越凸显",
        "field_defs": "pe_ttm = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data.get('pe_ttm')",
        "field_check": "pe_ttm is not None and len(pe_ttm) > 0 and np.any(pe_ttm > 0)",
        "expression": "np.tanh(1.0 / (np.maximum(pe_ttm, 0.1) / 15.0))",
        "input_fields": ["pe_ttm"],
        "theory": 5, "behavioral": 3, "microstructure": 3, "institutional": 5,
        "lookback": 1,
    },
    # ── fund_val_pb: 低 PB 价值因子 ──
    {
        "name": "fund_val_pb",
        "narrative": "低PB估值因子：市净率越低价值越凸显",
        "field_defs": "pb = data['pb'].values if hasattr(data, 'pb') else data.get('pb')",
        "field_check": "pb is not None and len(pb) > 0 and np.any(pb > 0)",
        "expression": "np.tanh(1.0 / (np.maximum(pb, 0.1) / 2.0))",
        "input_fields": ["pb"],
        "theory": 5, "behavioral": 3, "microstructure": 3, "institutional": 5,
        "lookback": 1,
    },
    # ── fund_val_ps: 低 PS 价值因子 ──
    {
        "name": "fund_val_ps",
        "narrative": "低PS估值因子：市销率越低价值越凸显",
        "field_defs": "ps_ttm = data['ps_ttm'].values if hasattr(data, 'ps_ttm') else data.get('ps_ttm')",
        "field_check": "ps_ttm is not None and len(ps_ttm) > 0 and np.any(ps_ttm > 0)",
        "expression": "np.tanh(1.0 / (np.maximum(ps_ttm, 0.1) / 3.0))",
        "input_fields": ["ps_ttm"],
        "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
        "lookback": 1,
    },
    # ── fund_val_composite: 综合估值因子 ──
    {
        "name": "fund_val_composite",
        "narrative": "综合估值因子：PE/PB/PS 三因子等权合成",
        "field_defs": (
            "pe_ttm = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data.get('pe_ttm')\n"
            "    pb = data['pb'].values if hasattr(data, 'pb') else data.get('pb')\n"
            "    ps_ttm = data['ps_ttm'].values if hasattr(data, 'ps_ttm') else data.get('ps_ttm')"
        ),
        "field_check": (
            "pe_ttm is not None and pb is not None and ps_ttm is not None "
            "and len(pe_ttm) > 0 and np.any(pe_ttm > 0)"
        ),
        "expression": (
            "0.4 * np.tanh(1.0 / (np.maximum(pe_ttm, 0.1) / 15.0)) + "
            "0.3 * np.tanh(1.0 / (np.maximum(pb, 0.1) / 2.0)) + "
            "0.3 * np.tanh(1.0 / (np.maximum(ps_ttm, 0.1) / 3.0))"
        ),
        "input_fields": ["pe_ttm", "pb", "ps_ttm"],
        "theory": 5, "behavioral": 3, "microstructure": 3, "institutional": 5,
        "lookback": 1,
    },

    # ══════════════════════════════════════════════════════
    # 质量因子 (Quality Factors) — 高盈利/高质量 = 正信号
    # ══════════════════════════════════════════════════════

    # ── fund_quality_roe: ROE 质量因子 ──
    {
        "name": "fund_quality_roe",
        "narrative": "ROE质量因子：净资产收益率越高代表盈利能力越强",
        "field_defs": "roe = data['roe'].values if hasattr(data, 'roe') else data.get('roe')",
        "field_check": "roe is not None and len(roe) > 0",
        "expression": "np.tanh((roe - 0.08) / 0.05)",
        "input_fields": ["roe"],
        "theory": 5, "behavioral": 3, "microstructure": 3, "institutional": 5,
        "lookback": 1,
    },
    # ── fund_quality_roa: ROA 质量因子 ──
    {
        "name": "fund_quality_roa",
        "narrative": "ROA质量因子：总资产收益率越高资产利用效率越好",
        "field_defs": "roa = data['roa'].values if hasattr(data, 'roa') else data.get('roa')",
        "field_check": "roa is not None and len(roa) > 0",
        "expression": "np.tanh((roa - 0.04) / 0.03)",
        "input_fields": ["roa"],
        "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
        "lookback": 1,
    },
    # ── fund_quality_margin: 毛利率质量因子 ──
    {
        "name": "fund_quality_margin",
        "narrative": "毛利率质量因子：高毛利率代表竞争优势和定价权",
        "field_defs": "gross_margin = data['gross_margin'].values if hasattr(data, 'gross_margin') else data.get('gross_margin')",
        "field_check": "gross_margin is not None and len(gross_margin) > 0",
        "expression": "np.tanh((gross_margin - 0.3) / 0.15)",
        "input_fields": ["gross_margin"],
        "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
        "lookback": 1,
    },
    # ── fund_quality_eps: EPS 质量因子 ──
    {
        "name": "fund_quality_eps",
        "narrative": "EPS质量因子：每股收益越高盈利越厚",
        "field_defs": "eps = data['eps'].values if hasattr(data, 'eps') else data.get('eps')",
        "field_check": "eps is not None and len(eps) > 0",
        "expression": "np.tanh(eps / 2.0)",
        "input_fields": ["eps"],
        "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
        "lookback": 1,
    },
    # ── fund_quality_composite: 综合质量因子 ──
    {
        "name": "fund_quality_composite",
        "narrative": "综合质量因子：ROE+ROA+毛利率三因子等权合成",
        "field_defs": (
            "roe = data['roe'].values if hasattr(data, 'roe') else data.get('roe')\n"
            "    roa = data['roa'].values if hasattr(data, 'roa') else data.get('roa')\n"
            "    gross_margin = data['gross_margin'].values if hasattr(data, 'gross_margin') else data.get('gross_margin')"
        ),
        "field_check": "roe is not None and roa is not None and len(roe) > 0",
        "expression": (
            "0.4 * np.tanh((roe - 0.08) / 0.05) + "
            "0.3 * np.tanh((roa - 0.04) / 0.03) + "
            "0.3 * np.tanh((gross_margin - 0.3) / 0.15)"
        ),
        "input_fields": ["roe", "roa", "gross_margin"],
        "theory": 5, "behavioral": 3, "microstructure": 3, "institutional": 5,
        "lookback": 1,
    },

    # ══════════════════════════════════════════════════════
    # 成长因子 (Growth Factors) — 高增长 = 正信号
    # ══════════════════════════════════════════════════════

    # ── fund_growth_revenue: 营收增长因子 ──
    {
        "name": "fund_growth_revenue",
        "narrative": "营收增长因子：营业收入同比增长越高成长性越强",
        "field_defs": (
            "revenue_growth = data['revenue_growth'].values if hasattr(data, 'revenue_growth') else data.get('revenue_growth')"
        ),
        "field_check": "revenue_growth is not None and len(revenue_growth) > 0",
        "expression": "np.tanh(revenue_growth / 0.2)",
        "input_fields": ["revenue_growth"],
        "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
        "lookback": 1,
    },
    # ── fund_growth_profit: 利润增长因子 ──
    {
        "name": "fund_growth_profit",
        "narrative": "利润增长因子：净利润同比增长越高盈利改善越明显",
        "field_defs": (
            "profit_growth = data['profit_growth'].values if hasattr(data, 'profit_growth') else data.get('profit_growth')"
        ),
        "field_check": "profit_growth is not None and len(profit_growth) > 0",
        "expression": "np.tanh(profit_growth / 0.3)",
        "input_fields": ["profit_growth"],
        "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
        "lookback": 1,
    },
    # ── fund_growth_composite: 综合成长因子 ──
    {
        "name": "fund_growth_composite",
        "narrative": "综合成长因子：营收增长+利润增长等权合成",
        "field_defs": (
            "revenue_growth = data['revenue_growth'].values if hasattr(data, 'revenue_growth') else data.get('revenue_growth')\n"
            "    profit_growth = data['profit_growth'].values if hasattr(data, 'profit_growth') else data.get('profit_growth')"
        ),
        "field_check": "revenue_growth is not None and profit_growth is not None and len(revenue_growth) > 0",
        "expression": "0.5 * np.tanh(revenue_growth / 0.2) + 0.5 * np.tanh(profit_growth / 0.3)",
        "input_fields": ["revenue_growth", "profit_growth"],
        "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
        "lookback": 1,
    },

    # ══════════════════════════════════════════════════════
    # 市值因子 (Size Factors) — 小市值 = 正信号（小市值效应）
    # ══════════════════════════════════════════════════════

    # ── fund_size_mcap: 小市值因子 ──
    {
        "name": "fund_size_mcap",
        "narrative": "小市值因子：总市值越小超额收益潜力越大（小市值效应）",
        "field_defs": (
            "total_market_cap = data['total_market_cap'].values if hasattr(data, 'total_market_cap') else data.get('total_market_cap')"
        ),
        "field_check": "total_market_cap is not None and len(total_market_cap) > 0 and np.any(total_market_cap > 0)",
        "expression": "np.tanh(1e11 / np.maximum(total_market_cap, 1e7))",
        "input_fields": ["total_market_cap"],
        "theory": 5, "behavioral": 4, "microstructure": 3, "institutional": 3,
        "lookback": 1,
    },
    # ── fund_size_log: 对数市值因子 ──
    {
        "name": "fund_size_log",
        "narrative": "对数市值因子：ln(市值)的负向信号，经典小市值因子",
        "field_defs": (
            "total_market_cap = data['total_market_cap'].values if hasattr(data, 'total_market_cap') else data.get('total_market_cap')"
        ),
        "field_check": "total_market_cap is not None and len(total_market_cap) > 0 and np.any(total_market_cap > 0)",
        "expression": "-np.tanh(np.log(np.maximum(total_market_cap, 1e7)) / 5.0)",
        "input_fields": ["total_market_cap"],
        "theory": 5, "behavioral": 4, "microstructure": 3, "institutional": 3,
        "lookback": 1,
    },

    # ══════════════════════════════════════════════════════
    # 换手率因子 (Trading Factors) — 另类数据
    # ══════════════════════════════════════════════════════

    # ── fund_turnover: 换手率因子 ──
    {
        "name": "fund_turnover",
        "narrative": "低换手率因子：低换手率代表筹码锁定良好，机构偏好",
        "field_defs": (
            "turnover_rate = data['turnover_rate'].values if hasattr(data, 'turnover_rate') else data.get('turnover_rate')"
        ),
        "field_check": "turnover_rate is not None and len(turnover_rate) > 0",
        "expression": "-np.tanh((turnover_rate - 0.03) / 0.02)",
        "input_fields": ["turnover_rate"],
        "theory": 3, "behavioral": 4, "microstructure": 5, "institutional": 4,
        "lookback": 1,
    },
    # ── fund_turnover_change: 换手率变化因子 ──
    {
        "name": "fund_turnover_change",
        "narrative": "换手率变化因子：换手率从低位回升代表关注度提升",
        "field_defs": (
            "turnover_rate = data['turnover_rate'].values if hasattr(data, 'turnover_rate') else data.get('turnover_rate')"
        ),
        "field_check": "turnover_rate is not None and len(turnover_rate) > 5",
        "expression": "np.tanh((turnover_rate - np.mean(turnover_rate[:5])) / 0.01)",
        "input_fields": ["turnover_rate"],
        "theory": 3, "behavioral": 4, "microstructure": 5, "institutional": 3,
        "lookback": 10,
    },

    # ══════════════════════════════════════════════════════
    # 宏观因子 (Macro Factors) — 宏观环境代理
    # ══════════════════════════════════════════════════════

    # ── fund_macro_pmi: PMI 景气因子 ──
    {
        "name": "fund_macro_pmi",
        "narrative": "PMI景气因子：PMI>50 经济扩张期利好股市",
        "field_defs": "pmi = data['pmi'].values if hasattr(data, 'pmi') else data.get('pmi')",
        "field_check": "pmi is not None and len(pmi) > 0",
        "expression": "np.tanh((pmi - 50.0) / 3.0)",
        "input_fields": ["pmi"],
        "theory": 5, "behavioral": 3, "microstructure": 3, "institutional": 5,
        "lookback": 1,
    },
    # ── fund_macro_cpi: CPI 通胀因子 ──
    {
        "name": "fund_macro_cpi",
        "narrative": "CPI通胀因子：温和通胀利好股市，高通胀利空",
        "field_defs": "cpi = data['cpi'].values if hasattr(data, 'cpi') else data.get('cpi')",
        "field_check": "cpi is not None and len(cpi) > 0",
        "expression": "np.where(cpi < 3.0, np.tanh((3.0 - cpi) / 2.0), -np.tanh((cpi - 3.0) / 2.0))",
        "input_fields": ["cpi"],
        "theory": 5, "behavioral": 3, "microstructure": 3, "institutional": 5,
        "lookback": 1,
    },
    # ── fund_macro_pmi_cpi: PMI+CPI 综合宏观因子 ──
    {
        "name": "fund_macro_pmi_cpi",
        "narrative": "综合宏观因子：PMI景气+CPI温和通胀复合信号",
        "field_defs": (
            "pmi = data['pmi'].values if hasattr(data, 'pmi') else data.get('pmi')\n"
            "    cpi = data['cpi'].values if hasattr(data, 'cpi') else data.get('cpi')"
        ),
        "field_check": "pmi is not None and cpi is not None and len(pmi) > 0",
        "expression": (
            "0.6 * np.tanh((pmi - 50.0) / 3.0) + "
            "0.4 * np.where(cpi < 3.0, np.tanh((3.0 - cpi) / 2.0), -np.tanh((cpi - 3.0) / 2.0))"
        ),
        "input_fields": ["pmi", "cpi"],
        "theory": 5, "behavioral": 3, "microstructure": 3, "institutional": 5,
        "lookback": 1,
    },

    # ══════════════════════════════════════════════════════
    # 另类数据因子 (Alternative Data Factors)
    # ══════════════════════════════════════════════════════

    # ── fund_alt_val_quality: 估值+质量复合因子（价值质量策略）─
    {
        "name": "fund_alt_val_quality",
        "narrative": "价值质量复合因子：低估值+高ROE的优质公司",
        "field_defs": (
            "pe_ttm = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data.get('pe_ttm')\n"
            "    roe = data['roe'].values if hasattr(data, 'roe') else data.get('roe')"
        ),
        "field_check": "pe_ttm is not None and roe is not None and len(pe_ttm) > 0 and np.any(pe_ttm > 0)",
        "expression": (
            "0.5 * np.tanh(1.0 / (np.maximum(pe_ttm, 0.1) / 15.0)) + "
            "0.5 * np.tanh((roe - 0.08) / 0.05)"
        ),
        "input_fields": ["pe_ttm", "roe"],
        "theory": 5, "behavioral": 3, "microstructure": 3, "institutional": 5,
        "lookback": 1,
    },
    # ── fund_alt_value_momentum: 价值+动量复合因子 ──
    {
        "name": "fund_alt_value_momentum",
        "narrative": "价值动量复合因子：低估值+近期价格上涨的强势价值股",
        "field_defs": (
            "pe_ttm = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data.get('pe_ttm')\n"
            "    close = data['close'].values if hasattr(data, 'close') else data['close']"
        ),
        "field_check": "pe_ttm is not None and len(pe_ttm) > 0 and np.any(pe_ttm > 0)",
        "expression": (
            "0.5 * np.tanh(1.0 / (np.maximum(pe_ttm, 0.1) / 15.0)) + "
            "0.5 * np.tanh((close - np.roll(close, 20)) / np.maximum(np.roll(close, 20), 1e-10) / 0.05)"
        ),
        "input_fields": ["pe_ttm", "close"],
        "theory": 4, "behavioral": 4, "microstructure": 4, "institutional": 4,
        "lookback": 25,
    },
    # ── fund_alt_quality_growth: 质量+成长复合因子 ──
    {
        "name": "fund_alt_quality_growth",
        "narrative": "质量成长复合因子：高ROE+高成长的优质成长股（GARP策略）",
        "field_defs": (
            "roe = data['roe'].values if hasattr(data, 'roe') else data.get('roe')\n"
            "    revenue_growth = data['revenue_growth'].values if hasattr(data, 'revenue_growth') else data.get('revenue_growth')"
        ),
        "field_check": "roe is not None and revenue_growth is not None and len(roe) > 0",
        "expression": (
            "0.5 * np.tanh((roe - 0.08) / 0.05) + "
            "0.5 * np.tanh(revenue_growth / 0.2)"
        ),
        "input_fields": ["roe", "revenue_growth"],
        "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
        "lookback": 1,
    },
    # ── fund_alt_small_value: 小盘价值复合因子 ──
    {
        "name": "fund_alt_small_value",
        "narrative": "小盘价值复合因子：小市值+低估值的小盘价值风格",
        "field_defs": (
            "total_market_cap = data['total_market_cap'].values if hasattr(data, 'total_market_cap') else data.get('total_market_cap')\n"
            "    pe_ttm = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data.get('pe_ttm')"
        ),
        "field_check": "total_market_cap is not None and pe_ttm is not None and len(total_market_cap) > 0 and np.any(total_market_cap > 0)",
        "expression": (
            "0.5 * np.tanh(1e11 / np.maximum(total_market_cap, 1e7)) + "
            "0.5 * np.tanh(1.0 / (np.maximum(pe_ttm, 0.1) / 15.0))"
        ),
        "input_fields": ["total_market_cap", "pe_ttm"],
        "theory": 5, "behavioral": 4, "microstructure": 3, "institutional": 3,
        "lookback": 1,
    },
]


# ─── 因子计数 ─────────────────────────────────────────────

def get_fundamental_seed_count() -> int:
    """返回基本面种子因子总数。"""
    return len(FUNDAMENTAL_DEFINITIONS)