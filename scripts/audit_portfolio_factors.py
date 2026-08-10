"""
scripts/audit_portfolio_factors.py — 补审计组合因子并落盘

对 fut_basis_momentum / fut_basis_momentum_g38 两个因子执行
FactorAuditor 6 项强制审计（真实期货数据），并将 audit_report
写回 futures_elite 因子 JSON 文件。

用法:
    python scripts/audit_portfolio_factors.py

输出:
    - 因子 JSON 的 audit_report 字段更新（落盘）
    - 控制台审计摘要
"""

from __future__ import annotations

import json
import logging
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data_futures import FUTURES_CORE_SUBSET, get_futures_provider
from fts.factor_engine.audit import FactorAuditConfig, FactorAuditor
from scripts.run_factor_audit_real import (
    compute_forward_returns,
    compute_ic,
    compute_oos_metrics,
    generate_stress_test_inputs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("audit_portfolio")

ELITE_DIR = PROJECT_ROOT / "memory" / "knowledge" / "factors" / "futures_elite"
TARGETS = {
    "fct_5bf469e0": "fut_basis_momentum",
    "fct_83d42ab0": "fut_basis_momentum_g38",
}


def load_factor(fid: str) -> dict:
    """从 futures_elite 读取因子；已退役因子回退到 _retired/ 读取。"""
    for base in (ELITE_DIR, ELITE_DIR / "_retired"):
        path = base / f"{fid}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"因子 {fid} 不存在（elite 与 _retired 均未找到）")


def safe_execute(code: str, ohlcv: pd.DataFrame, params: dict):
    """执行因子代码，兼容前导缩进/空行问题。"""
    if not code or not code.strip():
        return None
    code = textwrap.dedent(code).strip()
    try:
        local_vars: dict = {"np": np, "pd": pd}
        exec(code, {"__builtins__": __builtins__}, local_vars)
        fn = local_vars.get("factor_program")
        if fn is None:
            return None
        return fn(ohlcv, params or {})
    except Exception as e:
        logger.debug("因子执行异常: %s", e)
        return None


def build_factor_values(code: str, params: dict, df: pd.DataFrame) -> np.ndarray | None:
    """执行因子代码并返回对齐后的因子值数组。"""
    fv = safe_execute(code, df, params)
    if fv is None:
        return None
    fv = np.asarray(fv, dtype=float)
    if len(fv) < 30:
        return None
    return fv


def audit_one(fid: str, name: str, panel: dict[str, pd.DataFrame]) -> dict:
    """对单个因子执行完整审计，返回 audit_report dict。"""
    factor = load_factor(fid)
    code = factor.get("code", "")
    params = factor.get("params", {})
    logger.info("开始审计 %s (%s)", name, fid)

    # 1) 跨品种 IC
    ic_results: dict[str, dict] = {}
    for sym, df in panel.items():
        fv = build_factor_values(code, params, df)
        if fv is None:
            continue
        close = df["close"].values
        fwd = compute_forward_returns(close, 5)
        n = min(len(fv), len(fwd))
        ic_mean, ic_ir = compute_ic(fv[:n], fwd[:n])
        ic_results[sym] = {"ic": ic_mean, "ic_ir": ic_ir}

    if not ic_results:
        logger.error("%s 无有效 IC 结果，跳过", name)
        return {}

    symbol_ic_map = {s: r["ic"] for s, r in ic_results.items()}
    ic_vals = [r["ic"] for r in ic_results.values()]
    mean_ic = float(np.mean(ic_vals))
    positive_ratio = sum(1 for ic in ic_vals if ic > 0) / len(ic_vals)

    # 2) OOS（使用第一个有数据的品种）
    first_sym = next(iter(panel))
    df0 = panel[first_sym]
    fv0 = build_factor_values(code, params, df0)
    fwd0 = compute_forward_returns(df0["close"].values, 5)
    n0 = min(len(fv0), len(fwd0))
    oos_result = compute_oos_metrics(fv0[:n0], fwd0[:n0])

    # 3) 压力测试输入
    stress_signals, stress_ohlcv = generate_stress_test_inputs(panel, factor)

    # 4) 多重检验 p 值（基于 ICIR 近似）
    p_values = [max(0.001, min(0.5, 2 / (1 + abs(r["ic_ir"])))) for r in ic_results.values()]

    # 5) 执行审计
    config = FactorAuditConfig(min_cross_symbol_ratio=0.8, min_oos_pass_ratio=0.5)
    auditor = FactorAuditor(config=config)
    report = auditor.audit(
        factor={"factor_id": fid, "name": name, "code": code},
        data=df0,
        forward_returns=fwd0,
        symbol_ic_map=symbol_ic_map,
        signals_by_symbol=stress_signals,
        ohlcv_by_symbol=stress_ohlcv,
        oos_result=oos_result,
        p_values=p_values,
    )

    audit_dict = report.to_dict()
    audit_dict["mean_ic"] = round(mean_ic, 4)
    audit_dict["positive_ic_ratio"] = round(positive_ratio, 4)
    logger.info(
        "[%s] passed=%s pass_rate=%.0f%% mean_ic=%.4f 正IC占比=%.0f%% | 通过=%d 跳过=%d 失败=%s",
        name,
        report.passed,
        report.pass_rate * 100,
        mean_ic,
        positive_ratio * 100,
        report.summary["passed"],
        report.summary["skipped"],
        report.summary["failed_items"],
    )
    return audit_dict


def persist(fid: str, audit_dict: dict) -> None:
    path = None
    for base in (ELITE_DIR, ELITE_DIR / "_retired"):
        cand = base / f"{fid}.json"
        if cand.exists():
            path = cand
            break
    if path is None:
        path = ELITE_DIR / f"{fid}.json"
    factor = json.loads(path.read_text(encoding="utf-8"))
    factor["audit_report"] = audit_dict
    factor["audit_updated_at"] = datetime.now().isoformat()
    path.write_text(json.dumps(factor, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("审计报告已落盘: %s", path)


def main() -> int:
    # 获取真实期货数据（核心 15 品种，500 交易日）
    provider = get_futures_provider()
    symbols = FUTURES_CORE_SUBSET[:15]
    panel: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = provider.get_ohlcv(sym, days=500)
            if df is not None and len(df) >= 60:
                if "date" not in df.columns:
                    df = df.reset_index()
                if "date" not in df.columns:
                    df["date"] = df.index
                for col in ("open", "high", "low", "close", "volume"):
                    if col not in df.columns:
                        df[col] = 0.0
                panel[sym] = df
        except Exception as e:
            logger.warning("获取 %s 数据失败: %s", sym, e)
    logger.info("数据面板 [n_symbols=%d, days=500]", len(panel))
    if not panel:
        logger.error("未能获取任何真实数据")
        return 1

    for fid, name in TARGETS.items():
        try:
            audit_dict = audit_one(fid, name, panel)
            if audit_dict:
                persist(fid, audit_dict)
        except Exception as e:
            logger.error("%s 审计异常: %s", name, e, exc_info=True)
            return 1

    logger.info("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
