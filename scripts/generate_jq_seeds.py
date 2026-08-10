"""
scripts/generate_jq_seeds.py — 生成 JQ 聚宽因子种子文件

从 docs/Knowledge/stocks/jq_factor_catalog.py 读取 JQ_FACTORS 和 TECHNICAL_FACTORS，
与现有种子因子对比去重，生成 fts/factor_engine/seed_data/jq_factors.py。

用法:
    python scripts/generate_jq_seeds.py
"""

import sys
import os
from typing import Any

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── 导入 JQ 因子目录 ────────────────────────────────────
from docs.Knowledge.stocks.jq_factor_catalog import JQ_FACTORS, TECHNICAL_FACTORS

# ─── 导入现有种子因子名称 ─────────────────────────────────
from fts.factor_engine.seed_data.fundamental_seeds import FUNDAMENTAL_DEFINITIONS
from fts.factor_engine.seed_data.qlib158 import QLIB158_DEFINITIONS
from fts.factor_engine.seed_data.wq101 import WQ101_DEFINITIONS
from fts.factor_engine.seed_data.gtja191 import GTJA191_DEFINITIONS

# builtin 因子（从 YAML 中读取）
import yaml

BUILTIN_DEFINITIONS = []
builtin_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds", "stock", "builtin.yaml")
if os.path.exists(builtin_path):
    with open(builtin_path) as f:
        data = yaml.safe_load(f)
        BUILTIN_DEFINITIONS = [f["name"] for f in data.get("factors", [])]


def collect_existing_names() -> set[str]:
    """收集所有现有种子因子的名称。"""
    names = set()

    # fundamental
    for d in FUNDAMENTAL_DEFINITIONS:
        names.add(d["name"])

    # qlib158
    for d in QLIB158_DEFINITIONS:
        names.add(d["name"])

    # wq101
    for d in WQ101_DEFINITIONS:
        names.add(d["name"])

    # gtja191
    for d in GTJA191_DEFINITIONS:
        names.add(d["name"])

    # builtin
    names.update(BUILTIN_DEFINITIONS)

    return names


def get_expression_for_factor(factor: dict[str, str]) -> tuple[str, str, list[str], int]:
    """
    根据因子分类返回合适的表达式、field_defs、input_fields 和 lookback。
    返回 (expression, field_defs, input_fields, lookback)
    """
    code = factor["code"]
    cat = factor["category"]
    factor["description"]

    # ═══ Style 因子（风险因子）═══
    if cat == "style":
        style_exprs = {
            "beta": ("np.tanh(returns_vol * 10)", "returns_vol = np.std(returns)", ["close"], 65),
            "book_to_price_ratio": (
                "np.tanh(1.0 / (np.maximum(pb, 0.1) / 2.0))",
                "pb = data['pb'].values if hasattr(data, 'pb') else data.get('pb')",
                ["pb"],
                1,
            ),
            "earnings_yield": (
                "np.tanh(1.0 / (np.maximum(pe_ttm, 0.1) / 15.0))",
                "pe_ttm = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data.get('pe_ttm')",
                ["pe_ttm"],
                1,
            ),
            "growth": (
                "np.tanh(revenue_growth / 0.2)",
                "revenue_growth = data['revenue_growth'].values if hasattr(data, 'revenue_growth') else data.get('revenue_growth')",
                ["revenue_growth"],
                1,
            ),
            "leverage": (
                "-np.tanh(debt_to_asset / 0.5)",
                "debt_to_asset = data['debt_to_asset'].values if hasattr(data, 'debt_to_asset') else data.get('debt_to_asset')",
                ["debt_to_asset"],
                1,
            ),
            "liquidity": ("-np.tanh(ts_mean(volume, 20) / (ts_mean(close, 20) + 1e-10))", "", ["close", "volume"], 25),
            "non_linear_size": (
                "np.tanh(1e11 / np.maximum(total_market_cap, 1e7)) - 0.5 * np.tanh(np.log(np.maximum(total_market_cap, 1e7)) / 5.0)",
                "total_market_cap = data['total_market_cap'].values if hasattr(data, 'total_market_cap') else data.get('total_market_cap')",
                ["total_market_cap"],
                1,
            ),
            "residual_volatility": ("-np.tanh(ts_stddev(returns, 20) * 10)", "", ["close"], 25),
            "size": (
                "-np.tanh(np.log(np.maximum(total_market_cap, 1e7)) / 5.0)",
                "total_market_cap = data['total_market_cap'].values if hasattr(data, 'total_market_cap') else data.get('total_market_cap')",
                ["total_market_cap"],
                1,
            ),
        }
        if code in style_exprs:
            return style_exprs[code]
        return ("0.0", "", ["close"], 1)

    # ═══ Style PRO 因子 ═══
    if cat == "style_pro":
        style_pro_exprs = {
            "btop": (
                "np.tanh(1.0 / (np.maximum(pb, 0.1) / 2.0))",
                "pb = data['pb'].values if hasattr(data, 'pb') else data.get('pb')",
                ["pb"],
                1,
            ),
            "divyild": (
                "np.tanh(dividend_yield * 10)",
                "dividend_yield = data['dividend_yield'].values if hasattr(data, 'dividend_yield') else data.get('dividend_yield')",
                ["dividend_yield"],
                1,
            ),
            "earnqlty": (
                "np.tanh((roe - 0.08) / 0.05)",
                "roe = data['roe'].values if hasattr(data, 'roe') else data.get('roe')",
                ["roe"],
                1,
            ),
            "earnvar": ("-np.tanh(ts_stddev(returns, 20) * 10)", "", ["close"], 25),
            "earnyild": (
                "np.tanh(1.0 / (np.maximum(pe_ttm, 0.1) / 15.0))",
                "pe_ttm = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data.get('pe_ttm')",
                ["pe_ttm"],
                1,
            ),
            "financial_leverage": (
                "-np.tanh(debt_to_asset / 0.5)",
                "debt_to_asset = data['debt_to_asset'].values if hasattr(data, 'debt_to_asset') else data.get('debt_to_asset')",
                ["debt_to_asset"],
                1,
            ),
            "invsqlty": (
                "-np.tanh(total_asset_growth_rate / 0.2)",
                "total_asset_growth_rate = data['total_asset_growth_rate'].values if hasattr(data, 'total_asset_growth_rate') else data.get('total_asset_growth_rate')",
                ["total_asset_growth_rate"],
                1,
            ),
            "liquidty": ("-np.tanh(ts_mean(volume, 20) / (ts_mean(close, 20) + 1e-10))", "", ["close", "volume"], 25),
            "long_growth": (
                "np.tanh(revenue_growth / 0.2)",
                "revenue_growth = data['revenue_growth'].values if hasattr(data, 'revenue_growth') else data.get('revenue_growth')",
                ["revenue_growth"],
                1,
            ),
            "ltrevrsl": ("-np.tanh(ts_mean(returns, 252) * 5)", "", ["close"], 257),
            "market_beta": ("np.tanh(returns_vol * 10)", "returns_vol = np.std(returns)", ["close"], 65),
            "market_size": (
                "-np.tanh(np.log(np.maximum(total_market_cap, 1e7)) / 5.0)",
                "total_market_cap = data['total_market_cap'].values if hasattr(data, 'total_market_cap') else data.get('total_market_cap')",
                ["total_market_cap"],
                1,
            ),
            "midcap": (
                "np.tanh(1e11 / np.maximum(total_market_cap, 1e7)) - 0.5 * np.tanh(np.log(np.maximum(total_market_cap, 1e7)) / 5.0)",
                "total_market_cap = data['total_market_cap'].values if hasattr(data, 'total_market_cap') else data.get('total_market_cap')",
                ["total_market_cap"],
                1,
            ),
            "profit": (
                "np.tanh((roe - 0.08) / 0.05)",
                "roe = data['roe'].values if hasattr(data, 'roe') else data.get('roe')",
                ["roe"],
                1,
            ),
            "relative_momentum": ("np.tanh(ts_mean(returns, 252) * 5)", "", ["close"], 257),
            "resvol": ("-np.tanh(ts_stddev(returns, 20) * 10)", "", ["close"], 25),
        }
        if code in style_pro_exprs:
            return style_pro_exprs[code]
        return ("0.0", "", ["close"], 1)

    # ═══ Basics 因子（基础科目）═══
    if cat == "basics":
        # 这些是原始财务数据字段，使用 tanh 对数归一化
        field_exprs = {
            "administration_expense_ttm": (
                "-np.tanh(administration_expense / 1e9)",
                "administration_expense = data['administration_expense'].values if hasattr(data, 'administration_expense') else data.get('administration_expense')",
                ["administration_expense"],
                1,
            ),
            "asset_impairment_loss_ttm": (
                "-np.tanh(asset_impairment_loss / 1e8)",
                "asset_impairment_loss = data['asset_impairment_loss'].values if hasattr(data, 'asset_impairment_loss') else data.get('asset_impairment_loss')",
                ["asset_impairment_loss"],
                1,
            ),
            "EBIT": (
                "np.tanh(ebit / 1e9)",
                "ebit = data['ebit'].values if hasattr(data, 'ebit') else data.get('ebit')",
                ["ebit"],
                1,
            ),
            "EBITDA": (
                "np.tanh(ebitda / 1e9)",
                "ebitda = data['ebitda'].values if hasattr(data, 'ebitda') else data.get('ebitda')",
                ["ebitda"],
                1,
            ),
            "FCFF": (
                "np.tanh(fcff / 1e9)",
                "fcff = data['fcff'].values if hasattr(data, 'fcff') else data.get('fcff')",
                ["fcff"],
                1,
            ),
            "FCFE": (
                "np.tanh(fcfe / 1e9)",
                "fcfe = data['fcfe'].values if hasattr(data, 'fcfe') else data.get('fcfe')",
                ["fcfe"],
                1,
            ),
            "financing_expense_ttm": (
                "-np.tanh(financing_expense / 1e8)",
                "financing_expense = data['financing_expense'].values if hasattr(data, 'financing_expense') else data.get('financing_expense')",
                ["financing_expense"],
                1,
            ),
            "gross_profit_ttm": (
                "np.tanh(gross_profit / 1e9)",
                "gross_profit = data['gross_profit'].values if hasattr(data, 'gross_profit') else data.get('gross_profit')",
                ["gross_profit"],
                1,
            ),
            "income_tax_ttm": (
                "-np.tanh(income_tax / 1e8)",
                "income_tax = data['income_tax'].values if hasattr(data, 'income_tax') else data.get('income_tax')",
                ["income_tax"],
                1,
            ),
            "interest_expense_ttm": (
                "-np.tanh(interest_expense / 1e8)",
                "interest_expense = data['interest_expense'].values if hasattr(data, 'interest_expense') else data.get('interest_expense')",
                ["interest_expense"],
                1,
            ),
            "net_operating_cash_flow_ttm": (
                "np.tanh(net_operating_cash_flow / 1e9)",
                "net_operating_cash_flow = data['net_operating_cash_flow'].values if hasattr(data, 'net_operating_cash_flow') else data.get('net_operating_cash_flow')",
                ["net_operating_cash_flow"],
                1,
            ),
            "net_profit_ttm": (
                "np.tanh(net_profit / 1e9)",
                "net_profit = data['net_profit'].values if hasattr(data, 'net_profit') else data.get('net_profit')",
                ["net_profit"],
                1,
            ),
            "net_financing_cash_flow_ttm": (
                "np.tanh(net_financing_cash_flow / 1e9)",
                "net_financing_cash_flow = data['net_financing_cash_flow'].values if hasattr(data, 'net_financing_cash_flow') else data.get('net_financing_cash_flow')",
                ["net_financing_cash_flow"],
                1,
            ),
            "net_investing_cash_flow_ttm": (
                "-np.tanh(net_investing_cash_flow / 1e9)",
                "net_investing_cash_flow = data['net_investing_cash_flow'].values if hasattr(data, 'net_investing_cash_flow') else data.get('net_investing_cash_flow')",
                ["net_investing_cash_flow"],
                1,
            ),
            "non_operating_income_ttm": (
                "np.tanh(non_operating_income / 1e8)",
                "non_operating_income = data['non_operating_income'].values if hasattr(data, 'non_operating_income') else data.get('non_operating_income')",
                ["non_operating_income"],
                1,
            ),
            "operating_profit_ttm": (
                "np.tanh(operating_profit / 1e9)",
                "operating_profit = data['operating_profit'].values if hasattr(data, 'operating_profit') else data.get('operating_profit')",
                ["operating_profit"],
                1,
            ),
            "operating_revenue_ttm": (
                "np.tanh(operating_revenue / 1e9)",
                "operating_revenue = data['operating_revenue'].values if hasattr(data, 'operating_revenue') else data.get('operating_revenue')",
                ["operating_revenue"],
                1,
            ),
            "research_and_development_expense_ttm": (
                "np.tanh(r_and_d_expense / 1e8)",
                "r_and_d_expense = data['r_and_d_expense'].values if hasattr(data, 'r_and_d_expense') else data.get('r_and_d_expense')",
                ["r_and_d_expense"],
                1,
            ),
            "sale_expense_ttm": (
                "-np.tanh(sale_expense / 1e9)",
                "sale_expense = data['sale_expense'].values if hasattr(data, 'sale_expense') else data.get('sale_expense')",
                ["sale_expense"],
                1,
            ),
            "total_operating_cost_ttm": (
                "-np.tanh(total_operating_cost / 1e9)",
                "total_operating_cost = data['total_operating_cost'].values if hasattr(data, 'total_operating_cost') else data.get('total_operating_cost')",
                ["total_operating_cost"],
                1,
            ),
            "total_operating_revenue_ttm": (
                "np.tanh(total_operating_revenue / 1e9)",
                "total_operating_revenue = data['total_operating_revenue'].values if hasattr(data, 'total_operating_revenue') else data.get('total_operating_revenue')",
                ["total_operating_revenue"],
                1,
            ),
            "net_working_capital": (
                "np.tanh(net_working_capital / 1e9)",
                "net_working_capital = data['net_working_capital'].values if hasattr(data, 'net_working_capital') else data.get('net_working_capital')",
                ["net_working_capital"],
                1,
            ),
        }
        if code in field_exprs:
            return field_exprs[code]
        return ("0.0", "", ["close"], 1)

    # ═══ Quality 因子 ═══
    if cat == "quality":
        qual_exprs = {
            "net_profit_to_total_operate_revenue_ttm": (
                "np.tanh(net_profit_margin)",
                "net_profit_margin = data['net_profit_margin'].values if hasattr(data, 'net_profit_margin') else data.get('net_profit_margin')",
                ["net_profit_margin"],
                1,
            ),
            "cfo_to_ev": (
                "np.tanh(cfo_to_ev)",
                "cfo_to_ev = data['cfo_to_ev'].values if hasattr(data, 'cfo_to_ev') else data.get('cfo_to_ev')",
                ["cfo_to_ev"],
                1,
            ),
            "accounts_payable_turnover_days": (
                "-np.tanh(accounts_payable_turnover_days / 90)",
                "accounts_payable_turnover_days = data['accounts_payable_turnover_days'].values if hasattr(data, 'accounts_payable_turnover_days') else data.get('accounts_payable_turnover_days')",
                ["accounts_payable_turnover_days"],
                1,
            ),
            "net_profit_ratio": (
                "np.tanh(net_profit_margin)",
                "net_profit_margin = data['net_profit_margin'].values if hasattr(data, 'net_profit_margin') else data.get('net_profit_margin')",
                ["net_profit_margin"],
                1,
            ),
            "net_non_operating_income_to_total_profit": (
                "-np.tanh(abs(net_non_operating_income_to_profit))",
                "net_non_operating_income_to_profit = data['net_non_operating_income_to_profit'].values if hasattr(data, 'net_non_operating_income_to_profit') else data.get('net_non_operating_income_to_profit')",
                ["net_non_operating_income_to_profit"],
                1,
            ),
            "fixed_asset_ratio": (
                "-np.tanh(fixed_asset_ratio)",
                "fixed_asset_ratio = data['fixed_asset_ratio'].values if hasattr(data, 'fixed_asset_ratio') else data.get('fixed_asset_ratio')",
                ["fixed_asset_ratio"],
                1,
            ),
            "account_receivable_turnover_days": (
                "-np.tanh(account_receivable_turnover_days / 90)",
                "account_receivable_turnover_days = data['account_receivable_turnover_days'].values if hasattr(data, 'account_receivable_turnover_days') else data.get('account_receivable_turnover_days')",
                ["account_receivable_turnover_days"],
                1,
            ),
            "DEGM": (
                "np.tanh(degm)",
                "degm = data['degm'].values if hasattr(data, 'degm') else data.get('degm')",
                ["degm"],
                1,
            ),
            "sale_expense_to_operating_revenue": (
                "-np.tanh(sale_expense_to_revenue)",
                "sale_expense_to_revenue = data['sale_expense_to_revenue'].values if hasattr(data, 'sale_expense_to_revenue') else data.get('sale_expense_to_revenue')",
                ["sale_expense_to_revenue"],
                1,
            ),
            "operating_tax_to_operating_revenue_ratio_ttm": (
                "-np.tanh(operating_tax_rate)",
                "operating_tax_rate = data['operating_tax_rate'].values if hasattr(data, 'operating_tax_rate') else data.get('operating_tax_rate')",
                ["operating_tax_rate"],
                1,
            ),
            "inventory_turnover_days": (
                "-np.tanh(inventory_turnover_days / 90)",
                "inventory_turnover_days = data['inventory_turnover_days'].values if hasattr(data, 'inventory_turnover_days') else data.get('inventory_turnover_days')",
                ["inventory_turnover_days"],
                1,
            ),
            "OperatingCycle": (
                "-np.tanh(operating_cycle / 180)",
                "operating_cycle = data['operating_cycle'].values if hasattr(data, 'operating_cycle') else data.get('operating_cycle')",
                ["operating_cycle"],
                1,
            ),
            "net_operate_cash_flow_to_operate_income": (
                "np.tanh(net_operating_cash_flow_to_operating_income)",
                "net_operating_cash_flow_to_operating_income = data['net_operating_cash_flow_to_operating_income'].values if hasattr(data, 'net_operating_cash_flow_to_operating_income') else data.get('net_operating_cash_flow_to_operating_income')",
                ["net_operating_cash_flow_to_operating_income"],
                1,
            ),
            "net_operating_cash_flow_coverage": (
                "np.tanh(net_operating_cash_flow_to_net_profit)",
                "net_operating_cash_flow_to_net_profit = data['net_operating_cash_flow_to_net_profit'].values if hasattr(data, 'net_operating_cash_flow_to_net_profit') else data.get('net_operating_cash_flow_to_net_profit')",
                ["net_operating_cash_flow_to_net_profit"],
                1,
            ),
            "quick_ratio": (
                "np.tanh(quick_ratio)",
                "quick_ratio = data['quick_ratio'].values if hasattr(data, 'quick_ratio') else data.get('quick_ratio')",
                ["quick_ratio"],
                1,
            ),
            "intangible_asset_ratio": (
                "-np.tanh(intangible_asset_ratio)",
                "intangible_asset_ratio = data['intangible_asset_ratio'].values if hasattr(data, 'intangible_asset_ratio') else data.get('intangible_asset_ratio')",
                ["intangible_asset_ratio"],
                1,
            ),
            "MLEV": (
                "-np.tanh(mlev)",
                "mlev = data['mlev'].values if hasattr(data, 'mlev') else data.get('mlev')",
                ["mlev"],
                1,
            ),
            "debt_to_equity_ratio": (
                "-np.tanh(debt_to_equity)",
                "debt_to_equity = data['debt_to_equity'].values if hasattr(data, 'debt_to_equity') else data.get('debt_to_equity')",
                ["debt_to_equity"],
                1,
            ),
            "super_quick_ratio": (
                "np.tanh(super_quick_ratio)",
                "super_quick_ratio = data['super_quick_ratio'].values if hasattr(data, 'super_quick_ratio') else data.get('super_quick_ratio')",
                ["super_quick_ratio"],
                1,
            ),
            "inventory_turnover_rate": (
                "np.tanh(inventory_turnover_rate)",
                "inventory_turnover_rate = data['inventory_turnover_rate'].values if hasattr(data, 'inventory_turnover_rate') else data.get('inventory_turnover_rate')",
                ["inventory_turnover_rate"],
                1,
            ),
            "operating_profit_growth_rate": (
                "np.tanh(operating_profit_growth_rate)",
                "operating_profit_growth_rate = data['operating_profit_growth_rate'].values if hasattr(data, 'operating_profit_growth_rate') else data.get('operating_profit_growth_rate')",
                ["operating_profit_growth_rate"],
                1,
            ),
            "long_debt_to_working_capital_ratio": (
                "-np.tanh(long_debt_to_working_capital)",
                "long_debt_to_working_capital = data['long_debt_to_working_capital'].values if hasattr(data, 'long_debt_to_working_capital') else data.get('long_debt_to_working_capital')",
                ["long_debt_to_working_capital"],
                1,
            ),
            "current_ratio": (
                "np.tanh(current_ratio)",
                "current_ratio = data['current_ratio'].values if hasattr(data, 'current_ratio') else data.get('current_ratio')",
                ["current_ratio"],
                1,
            ),
            "net_operate_cash_flow_to_net_debt": (
                "np.tanh(net_operating_cash_flow_to_net_debt)",
                "net_operating_cash_flow_to_net_debt = data['net_operating_cash_flow_to_net_debt'].values if hasattr(data, 'net_operating_cash_flow_to_net_debt') else data.get('net_operating_cash_flow_to_net_debt')",
                ["net_operating_cash_flow_to_net_debt"],
                1,
            ),
            "net_operate_cash_flow_to_asset": (
                "np.tanh(net_operating_cash_flow_to_asset)",
                "net_operating_cash_flow_to_asset = data['net_operating_cash_flow_to_asset'].values if hasattr(data, 'net_operating_cash_flow_to_asset') else data.get('net_operating_cash_flow_to_asset')",
                ["net_operating_cash_flow_to_asset"],
                1,
            ),
            "non_current_asset_ratio": (
                "-np.tanh(non_current_asset_ratio)",
                "non_current_asset_ratio = data['non_current_asset_ratio'].values if hasattr(data, 'non_current_asset_ratio') else data.get('non_current_asset_ratio')",
                ["non_current_asset_ratio"],
                1,
            ),
            "total_asset_turnover_rate": (
                "np.tanh(total_asset_turnover_rate)",
                "total_asset_turnover_rate = data['total_asset_turnover_rate'].values if hasattr(data, 'total_asset_turnover_rate') else data.get('total_asset_turnover_rate')",
                ["total_asset_turnover_rate"],
                1,
            ),
            "long_debt_to_asset_ratio": (
                "-np.tanh(long_debt_to_asset)",
                "long_debt_to_asset = data['long_debt_to_asset'].values if hasattr(data, 'long_debt_to_asset') else data.get('long_debt_to_asset')",
                ["long_debt_to_asset"],
                1,
            ),
            "debt_to_tangible_equity_ratio": (
                "-np.tanh(debt_to_tangible_equity)",
                "debt_to_tangible_equity = data['debt_to_tangible_equity'].values if hasattr(data, 'debt_to_tangible_equity') else data.get('debt_to_tangible_equity')",
                ["debt_to_tangible_equity"],
                1,
            ),
            "ROAEBITTTM": (
                "np.tanh(roa_ebit)",
                "roa_ebit = data['roa_ebit'].values if hasattr(data, 'roa_ebit') else data.get('roa_ebit')",
                ["roa_ebit"],
                1,
            ),
            "operating_profit_ratio": (
                "np.tanh(operating_profit_ratio)",
                "operating_profit_ratio = data['operating_profit_ratio'].values if hasattr(data, 'operating_profit_ratio') else data.get('operating_profit_ratio')",
                ["operating_profit_ratio"],
                1,
            ),
            "long_term_debt_to_asset_ratio": (
                "-np.tanh(long_term_debt_to_asset)",
                "long_term_debt_to_asset = data['long_term_debt_to_asset'].values if hasattr(data, 'long_term_debt_to_asset') else data.get('long_term_debt_to_asset')",
                ["long_term_debt_to_asset"],
                1,
            ),
            "current_asset_turnover_rate": (
                "np.tanh(current_asset_turnover_rate)",
                "current_asset_turnover_rate = data['current_asset_turnover_rate'].values if hasattr(data, 'current_asset_turnover_rate') else data.get('current_asset_turnover_rate')",
                ["current_asset_turnover_rate"],
                1,
            ),
            "financial_expense_rate": (
                "-np.tanh(financial_expense_rate)",
                "financial_expense_rate = data['financial_expense_rate'].values if hasattr(data, 'financial_expense_rate') else data.get('financial_expense_rate')",
                ["financial_expense_rate"],
                1,
            ),
            "operating_profit_to_total_profit": (
                "np.tanh(operating_profit_to_total_profit)",
                "operating_profit_to_total_profit = data['operating_profit_to_total_profit'].values if hasattr(data, 'operating_profit_to_total_profit') else data.get('operating_profit_to_total_profit')",
                ["operating_profit_to_total_profit"],
                1,
            ),
            "debt_to_asset_ratio": (
                "-np.tanh(debt_to_asset)",
                "debt_to_asset = data['debt_to_asset'].values if hasattr(data, 'debt_to_asset') else data.get('debt_to_asset')",
                ["debt_to_asset"],
                1,
            ),
            "equity_to_fixed_asset_ratio": (
                "np.tanh(equity_to_fixed_asset)",
                "equity_to_fixed_asset = data['equity_to_fixed_asset'].values if hasattr(data, 'equity_to_fixed_asset') else data.get('equity_to_fixed_asset')",
                ["equity_to_fixed_asset"],
                1,
            ),
            "net_operate_cash_flow_to_total_liability": (
                "np.tanh(net_operating_cash_flow_to_total_liability)",
                "net_operating_cash_flow_to_total_liability = data['net_operating_cash_flow_to_total_liability'].values if hasattr(data, 'net_operating_cash_flow_to_total_liability') else data.get('net_operating_cash_flow_to_total_liability')",
                ["net_operating_cash_flow_to_total_liability"],
                1,
            ),
            "cash_rate_of_sales": (
                "np.tanh(cash_rate_of_sales)",
                "cash_rate_of_sales = data['cash_rate_of_sales'].values if hasattr(data, 'cash_rate_of_sales') else data.get('cash_rate_of_sales')",
                ["cash_rate_of_sales"],
                1,
            ),
            "admin_expense_rate": (
                "-np.tanh(admin_expense_rate)",
                "admin_expense_rate = data['admin_expense_rate'].values if hasattr(data, 'admin_expense_rate') else data.get('admin_expense_rate')",
                ["admin_expense_rate"],
                1,
            ),
            "gross_profit_margin": (
                "np.tanh(gross_profit_margin)",
                "gross_profit_margin = data['gross_profit_margin'].values if hasattr(data, 'gross_profit_margin') else data.get('gross_profit_margin')",
                ["gross_profit_margin"],
                1,
            ),
            "roe_ttm_8y": (
                "np.tanh(roe_ttm_8y)",
                "roe_ttm_8y = data['roe_ttm_8y'].values if hasattr(data, 'roe_ttm_8y') else data.get('roe_ttm_8y')",
                ["roe_ttm_8y"],
                1,
            ),
        }
        if code in qual_exprs:
            return qual_exprs[code]
        return ("0.0", "", ["close"], 1)

    # ═══ Sentiment 因子（情绪类）═══
    if cat == "sentiment":
        # 排除与 qlib 重复的：VOL5, VOL10, VOL20, VOL60
        qlib_dup = {"VOL5", "VOL10", "VOL20", "VOL60"}
        # 排除与 TECHNICAL 重复的（将在 TECHNICAL 中保留）
        tech_dup = {"MACD", "RSI", "WR", "PSY", "CCI", "BIAS"}
        # 排除与 quality 重复的
        quality_dup = {"DEGM"}
        # 排除与 builtin 重复的
        builtin_dup = {"momentum"}

        if code in qlib_dup or code in tech_dup or code in quality_dup or code in builtin_dup:
            return None  # 标记为跳过

        sent_exprs = {
            "ATR6": ("ts_mean(high - low, 6)", "", ["close", "high", "low"], 11),
            "ATR12": ("ts_mean(high - low, 12)", "", ["close", "high", "low"], 17),
            "ATR20": ("ts_mean(high - low, 20)", "", ["close", "high", "low"], 25),
            "BIAS10": ("(close - ts_mean(close, 10)) / (ts_mean(close, 10) + _EPS)", "", ["close"], 15),
            "BIAS20": ("(close - ts_mean(close, 20)) / (ts_mean(close, 20) + _EPS)", "", ["close"], 25),
            "BIAS30": ("(close - ts_mean(close, 30)) / (ts_mean(close, 30) + _EPS)", "", ["close"], 35),
            "BIAS60": ("(close - ts_mean(close, 60)) / (ts_mean(close, 60) + _EPS)", "", ["close"], 65),
            "DAVOL5": ("ts_mean(volume, 5) * close", "", ["close", "volume"], 10),
            "DAVOL10": ("ts_mean(volume, 10) * close", "", ["close", "volume"], 15),
            "DAVOL20": ("ts_mean(volume, 20) * close", "", ["close", "volume"], 25),
            "DAVOL60": ("ts_mean(volume, 60) * close", "", ["close", "volume"], 65),
            "DBQ": ("(close - open_) / (high - low + _EPS)", "", ["close", "open", "high", "low"], 1),
            "EMA5": ("ts_mean(close, 5)", "", ["close"], 10),
            "EMA10": ("ts_mean(close, 10)", "", ["close"], 15),
            "EMA20": ("ts_mean(close, 20)", "", ["close"], 25),
            "HSIGMA": ("ts_stddev(returns, 20)", "", ["close"], 25),
            "MA5": ("ts_mean(close, 5)", "", ["close"], 10),
            "MA10": ("ts_mean(close, 10)", "", ["close"], 15),
            "MA20": ("ts_mean(close, 20)", "", ["close"], 25),
            "MA60": ("ts_mean(close, 60)", "", ["close"], 65),
            "MTM": ("close - delay(close, 10)", "", ["close"], 15),
            "PVT": ("ts_sum(returns * volume, 20)", "", ["close", "volume"], 25),
            "ROC": ("close / delay(close, 10) - 1", "", ["close"], 15),
            "Skewness60": ("ts_skew(returns, 60)", "", ["close"], 65),
            "Kurtosis60": ("ts_kurt(returns, 60)", "", ["close"], 65),
            "VEMA5": ("ts_mean(volume, 5)", "", ["close", "volume"], 10),
            "VEMA10": ("ts_mean(volume, 10)", "", ["close", "volume"], 15),
            "VEMA20": ("ts_mean(volume, 20)", "", ["close", "volume"], 25),
            "turnover_ratio": ("volume", "", ["close", "volume"], 1),
        }
        if code in sent_exprs:
            return sent_exprs[code]
        return ("0.0", "", ["close"], 1)

    # ═══ Growth 因子（成长类）═══
    if cat == "growth":
        growth_exprs = {
            "net_profit_growth_rate": (
                "np.tanh(net_profit_growth_rate)",
                "net_profit_growth_rate = data['net_profit_growth_rate'].values if hasattr(data, 'net_profit_growth_rate') else data.get('net_profit_growth_rate')",
                ["net_profit_growth_rate"],
                1,
            ),
            "operating_revenue_growth_rate": (
                "np.tanh(operating_revenue_growth_rate)",
                "operating_revenue_growth_rate = data['operating_revenue_growth_rate'].values if hasattr(data, 'operating_revenue_growth_rate') else data.get('operating_revenue_growth_rate')",
                ["operating_revenue_growth_rate"],
                1,
            ),
            "total_asset_growth_rate": (
                "np.tanh(total_asset_growth_rate)",
                "total_asset_growth_rate = data['total_asset_growth_rate'].values if hasattr(data, 'total_asset_growth_rate') else data.get('total_asset_growth_rate')",
                ["total_asset_growth_rate"],
                1,
            ),
            "book_value_growth_rate": (
                "np.tanh(book_value_growth_rate)",
                "book_value_growth_rate = data['book_value_growth_rate'].values if hasattr(data, 'book_value_growth_rate') else data.get('book_value_growth_rate')",
                ["book_value_growth_rate"],
                1,
            ),
            "operating_profit_growth_rate_3y": (
                "np.tanh(operating_profit_growth_rate_3y)",
                "operating_profit_growth_rate_3y = data['operating_profit_growth_rate_3y'].values if hasattr(data, 'operating_profit_growth_rate_3y') else data.get('operating_profit_growth_rate_3y')",
                ["operating_profit_growth_rate_3y"],
                1,
            ),
            "net_profit_growth_rate_3y": (
                "np.tanh(net_profit_growth_rate_3y)",
                "net_profit_growth_rate_3y = data['net_profit_growth_rate_3y'].values if hasattr(data, 'net_profit_growth_rate_3y') else data.get('net_profit_growth_rate_3y')",
                ["net_profit_growth_rate_3y"],
                1,
            ),
            "operating_revenue_growth_rate_3y": (
                "np.tanh(operating_revenue_growth_rate_3y)",
                "operating_revenue_growth_rate_3y = data['operating_revenue_growth_rate_3y'].values if hasattr(data, 'operating_revenue_growth_rate_3y') else data.get('operating_revenue_growth_rate_3y')",
                ["operating_revenue_growth_rate_3y"],
                1,
            ),
        }
        if code in growth_exprs:
            return growth_exprs[code]
        return ("0.0", "", ["close"], 1)

    # ═══ Per Share 因子（每股指标）═══
    if cat == "per_share":
        per_share_exprs = {
            "eps": (
                "np.tanh(eps)",
                "eps = data['eps'].values if hasattr(data, 'eps') else data.get('eps')",
                ["eps"],
                1,
            ),
            "eps_diluted": (
                "np.tanh(eps_diluted)",
                "eps_diluted = data['eps_diluted'].values if hasattr(data, 'eps_diluted') else data.get('eps_diluted')",
                ["eps_diluted"],
                1,
            ),
            "bvps": (
                "np.tanh(bvps)",
                "bvps = data['bvps'].values if hasattr(data, 'bvps') else data.get('bvps')",
                ["bvps"],
                1,
            ),
            "operating_revenue_per_share": (
                "np.tanh(operating_revenue_per_share)",
                "operating_revenue_per_share = data['operating_revenue_per_share'].values if hasattr(data, 'operating_revenue_per_share') else data.get('operating_revenue_per_share')",
                ["operating_revenue_per_share"],
                1,
            ),
            "net_profit_per_share": (
                "np.tanh(net_profit_per_share)",
                "net_profit_per_share = data['net_profit_per_share'].values if hasattr(data, 'net_profit_per_share') else data.get('net_profit_per_share')",
                ["net_profit_per_share"],
                1,
            ),
            "operating_cash_flow_per_share": (
                "np.tanh(operating_cash_flow_per_share)",
                "operating_cash_flow_per_share = data['operating_cash_flow_per_share'].values if hasattr(data, 'operating_cash_flow_per_share') else data.get('operating_cash_flow_per_share')",
                ["operating_cash_flow_per_share"],
                1,
            ),
            "book_value_per_share": (
                "np.tanh(book_value_per_share)",
                "book_value_per_share = data['book_value_per_share'].values if hasattr(data, 'book_value_per_share') else data.get('book_value_per_share')",
                ["book_value_per_share"],
                1,
            ),
            "capital_reserve_per_share": (
                "np.tanh(capital_reserve_per_share)",
                "capital_reserve_per_share = data['capital_reserve_per_share'].values if hasattr(data, 'capital_reserve_per_share') else data.get('capital_reserve_per_share')",
                ["capital_reserve_per_share"],
                1,
            ),
            "surplus_reserve_per_share": (
                "np.tanh(surplus_reserve_per_share)",
                "surplus_reserve_per_share = data['surplus_reserve_per_share'].values if hasattr(data, 'surplus_reserve_per_share') else data.get('surplus_reserve_per_share')",
                ["surplus_reserve_per_share"],
                1,
            ),
            "retained_earnings_per_share": (
                "np.tanh(retained_earnings_per_share)",
                "retained_earnings_per_share = data['retained_earnings_per_share'].values if hasattr(data, 'retained_earnings_per_share') else data.get('retained_earnings_per_share')",
                ["retained_earnings_per_share"],
                1,
            ),
        }
        if code in per_share_exprs:
            return per_share_exprs[code]
        return ("0.0", "", ["close"], 1)

    # ═══ Momentum 因子 ═══
    if cat == "momentum":
        mom_exprs = {
            "Momentum_1M": ("close / delay(close, 21) - 1", "", ["close"], 26),
            "Momentum_3M": ("close / delay(close, 63) - 1", "", ["close"], 68),
            "Momentum_6M": ("close / delay(close, 126) - 1", "", ["close"], 131),
            "Momentum_12M": ("close / delay(close, 252) - 1", "", ["close"], 257),
            "Momentum_60D": ("close / delay(close, 60) - 1", "", ["close"], 65),
            "Momentum_120D": ("close / delay(close, 120) - 1", "", ["close"], 125),
        }
        if code in mom_exprs:
            return mom_exprs[code]
        return ("0.0", "", ["close"], 1)

    # ═══ Valuation 因子（估值类）═══
    if cat == "valuation":
        val_exprs = {
            "pe_ratio": (
                "np.tanh(1.0 / (np.maximum(pe, 0.1) / 15.0))",
                "pe = data['pe'].values if hasattr(data, 'pe') else data.get('pe')",
                ["pe"],
                1,
            ),
            "pe_ratio_ttm": (
                "np.tanh(1.0 / (np.maximum(pe_ttm, 0.1) / 15.0))",
                "pe_ttm = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data.get('pe_ttm')",
                ["pe_ttm"],
                1,
            ),
            "pb_ratio": (
                "np.tanh(1.0 / (np.maximum(pb, 0.1) / 2.0))",
                "pb = data['pb'].values if hasattr(data, 'pb') else data.get('pb')",
                ["pb"],
                1,
            ),
            "ps_ratio": (
                "np.tanh(1.0 / (np.maximum(ps, 0.1) / 3.0))",
                "ps = data['ps'].values if hasattr(data, 'ps') else data.get('ps')",
                ["ps"],
                1,
            ),
            "ps_ratio_ttm": (
                "np.tanh(1.0 / (np.maximum(ps_ttm, 0.1) / 3.0))",
                "ps_ttm = data['ps_ttm'].values if hasattr(data, 'ps_ttm') else data.get('ps_ttm')",
                ["ps_ttm"],
                1,
            ),
            "pcf_ratio": (
                "np.tanh(1.0 / (np.maximum(pcf, 0.1) / 10.0))",
                "pcf = data['pcf'].values if hasattr(data, 'pcf') else data.get('pcf')",
                ["pcf"],
                1,
            ),
            "dividend_yield_ratio": (
                "np.tanh(dividend_yield)",
                "dividend_yield = data['dividend_yield'].values if hasattr(data, 'dividend_yield') else data.get('dividend_yield')",
                ["dividend_yield"],
                1,
            ),
            "dividend_payout_ratio": (
                "np.tanh(dividend_payout_ratio)",
                "dividend_payout_ratio = data['dividend_payout_ratio'].values if hasattr(data, 'dividend_payout_ratio') else data.get('dividend_payout_ratio')",
                ["dividend_payout_ratio"],
                1,
            ),
        }
        if code in val_exprs:
            return val_exprs[code]
        return ("0.0", "", ["close"], 1)

    # ═══ Technical 因子（技术指标）═══
    if cat == "technical":
        # 排除与 sentiment 重复的
        sent_dup = {"MACD", "RSI", "WR", "PSY", "BIAS", "CCI"}
        if code in sent_dup:
            return None  # 标记为跳过

        tech_exprs = {
            "KDJ": (
                "rank(close - ts_min(low, 9)) / (rank(ts_max(high, 9) - ts_min(low, 9)) + _EPS)",
                "",
                ["close", "high", "low"],
                14,
            ),
            "BOLL": ("(close - ts_mean(close, 20)) / (ts_stddev(close, 20) * 2 + _EPS)", "", ["close"], 25),
            "MA": ("ts_mean(close, 20)", "", ["close"], 25),
            "EMA": ("ts_mean(close, 20)", "", ["close"], 25),
            "VOLUME": ("volume", "", ["close", "volume"], 1),
            "OBV": (
                "ts_sum(ifelse(close > delay(close, 1), volume, ifelse(close < delay(close, 1), -volume, 0)), 20)",
                "",
                ["close", "volume"],
                25,
            ),
            "DMI": ("ts_mean(high - low, 14)", "", ["close", "high", "low"], 19),
            "ARBR": (
                "(ts_sum(high - open_, 26) / (ts_sum(open_ - low, 26) + _EPS))",
                "",
                ["close", "open", "high", "low"],
                31,
            ),
            "CR": (
                "ts_sum(high - delay(close, 1), 26) / (ts_sum(delay(close, 1) - low, 26) + _EPS)",
                "",
                ["close", "high", "low"],
                31,
            ),
            "TRIX": ("ts_mean(ts_mean(ts_mean(close, 12), 12), 12)", "", ["close"], 36),
            "DPO": ("close - ts_mean(close, 20)", "", ["close"], 25),
            "BBI": (
                "(ts_mean(close, 3) + ts_mean(close, 6) + ts_mean(close, 12) + ts_mean(close, 24)) / 4",
                "",
                ["close"],
                29,
            ),
            "EXPMA": ("ts_mean(close, 12)", "", ["close"], 17),
            "VHF": (
                "(ts_max(close, 20) - ts_min(close, 20)) / (ts_sum(abs(close - ts_mean(close, 20)), 20) + _EPS)",
                "",
                ["close"],
                25,
            ),
        }
        if code in tech_exprs:
            return tech_exprs[code]
        return ("0.0", "", ["close"], 1)

    return ("0.0", "", ["close"], 1)


def make_definition(factor: dict[str, str]) -> dict[str, Any] | None:
    """将 JQ 因子转换为 FTS 种子定义。"""
    code = factor["code"]
    factor["category"]
    desc = factor["description"]
    name = factor["name"]

    result = get_expression_for_factor(factor)
    if result is None:
        return None  # 跳过标记

    expr, field_defs, input_fields, lookback = result

    # 判断是基本面因子还是量价因子
    is_field_based = bool(field_defs)  # 有 field_defs 则为基本面因子

    # 构建 field_check
    if field_defs:
        field_name = field_defs.split(" = ")[0]
        field_check = f"{field_name} is not None and len({field_name}) > 0"
    else:
        field_check = ""

    defn = {
        "name": code,
        "narrative": f"{name}：{desc}",
        "expression": expr,
        "input_fields": input_fields,
        "lookback": lookback,
        "theory": 4,
        "behavioral": 3,
        "microstructure": 3,
        "institutional": 3,
    }

    if is_field_based:
        defn["field_defs"] = field_defs
        defn["field_check"] = field_check

    return defn


def generate_seed_file(
    new_definitions: list[dict[str, Any]],
    output_path: str,
):
    """生成 JQ 种子因子 Python 文件。"""
    lines = []
    lines.append('"""')
    lines.append("seed_data/jq_factors.py — 聚宽(JoinQuant)因子库种子定义")
    lines.append("")
    lines.append("从聚宽官方文档整理，包含风格因子、基础科目、质量因子、情绪因子、")
    lines.append("成长因子、每股指标、动量因子、估值因子及技术分析指标。")
    lines.append("")
    lines.append("来源: https://www.joinquant.com/help/api/help?name=factor_values")
    lines.append("      https://www.joinquant.com/help/api/help?name=technicalanalysis")
    lines.append("")
    lines.append("数据依赖:")
    lines.append("    基本面因子通过 FundamentalProvider 注入的字段消费。")
    lines.append("    量价因子通过 KlineProvider 注入的 OHLCV 数据消费。")
    lines.append("")
    lines.append(f"版本: v1.0.0 (去重后共 {len(new_definitions)} 个因子)")
    lines.append('"""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("from typing import Any")
    lines.append("")

    # 生成因子定义列表
    lines.append("")
    lines.append("# ─── 因子定义 ─────────────────────────────────────────────")
    lines.append("")
    lines.append("JQ_DEFINITIONS: list[dict[str, Any]] = [")
    lines.append(f"    # 共 {len(new_definitions)} 个因子（去重后）")
    lines.append("")

    for defn in new_definitions:
        lines.append("    # ── %s: %s ──" % (defn["name"], defn["narrative"][:60]))
        lines.append("    {")
        lines.append('        "name": "%s",' % defn["name"])
        lines.append('        "narrative": "%s",' % defn["narrative"])
        if "field_defs" in defn:
            lines.append('        "field_defs": "%s",' % defn["field_defs"])
            lines.append('        "field_check": "%s",' % defn["field_check"])
        lines.append('        "expression": "%s",' % defn["expression"])
        lines.append('        "input_fields": %s,' % defn["input_fields"])
        lines.append('        "lookback": %d,' % defn["lookback"])
        lines.append(
            '        "theory": %d, "behavioral": %d, "microstructure": %d, "institutional": %d,'
            % (defn["theory"], defn["behavioral"], defn["microstructure"], defn["institutional"])
        )
        lines.append("    },")

    lines.append("]")
    lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ 已生成种子文件: {output_path}")
    print(f"   共 {len(new_definitions)} 个因子")


def main():
    # 收集现有因子名称
    existing = collect_existing_names()
    print(f"📊 现有种子因子总数: {len(existing)}")
    print(f"   其中 fundamental: {len(FUNDAMENTAL_DEFINITIONS)}")
    print(f"          builtin: {len(BUILTIN_DEFINITIONS)}")
    print(f"          qlib158: {len(QLIB158_DEFINITIONS)}")
    print(f"          wq101: {len(WQ101_DEFINITIONS)}")
    print(f"          gtja191: {len(GTJA191_DEFINITIONS)}")

    # 收集 JQ 因子代码（排除 Alpha101/Alpha191）
    print(f"\n📥 JQ 因子库: {len(JQ_FACTORS)} 个")
    print(f"   Technical: {len(TECHNICAL_FACTORS)} 个")

    # 去重处理
    new_definitions = []
    skipped = []
    for factor in JQ_FACTORS + TECHNICAL_FACTORS:
        code = factor["code"]
        if code in existing:
            skipped.append((code, "已存在于种子库"))
            continue
        defn = make_definition(factor)
        if defn is None:
            skipped.append((code, "与同文件内其他因子重复"))
            continue
        new_definitions.append(defn)
        existing.add(code)  # 防止同文件内重复

    # 输出结果
    print(f"\n✅ 去重后新增: {len(new_definitions)} 个因子")
    print(f"⏭️  跳过: {len(skipped)} 个因子")
    print("\n跳过的因子:")
    for code, reason in skipped:
        print(f"   - {code}: {reason}")

    print("\n新增的因子:")
    for d in new_definitions:
        print(f"   + {d['name']}: {d['narrative'][:50]}")

    # 生成文件
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "fts", "factor_engine", "seed_data", "jq_factors.py"
    )
    generate_seed_file(new_definitions, output_path)


if __name__ == "__main__":
    main()
