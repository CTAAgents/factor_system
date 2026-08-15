"""
scripts/futures_signal_pipeline.py — 期货每日信号生成管道

从 FTS 期货 Elite 因子库，生成期货横截面交易信号。

用法:
    python scripts/futures_signal_pipeline.py [--max-symbols 25] [--days 120]

输出:
    - 控制台: 信号排名表
    - 文件:     reports/futures/{date}/futures_signals_{date}.md（信号排名报告）
    - 文件:     reports/futures/{date}/trading_advice_{date}.md（交易建议报告）

方向校正方法（v2）:
    期货是多空双向，因子在期货上的 IC 方向可能为负。
    校正方法：计算每个因子最近 N 天的**每日截面 IC**（因子信号与
    未来 5 日收益的 Spearman 秩相关性），如果平均 IC < 0 则反转信号。
    这比 v1 的时序相关性方法更符合横截面因子投资逻辑。

排名方法（v5 — 多空双向 + 信号增量）:
    期货支持多空双向交易，排名按信号强度（绝对值）排序，
    输出分多头信号 (做多) 和空头信号 (做空) 两部分。
    新增信号增量追踪（较昨日变化），用于判断趋势加速/衰竭。

因子选择与基础权重（v4 — L3 组合权威源）:
    因子选择与基础权重分配由 L3 组合层负责（factor_weights.json），
    信号管道只负责信号计算 + 根据 Regime 做因子权重档位缩放调整，
    不再自选因子（全部精英因子）也不自训 Ridge 权重（v2.105.0 起）。
    方向以 L3 组合语义为准，移除截面 IC 方向校正；品种级 IC 自适应保留。
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 抑制 numpy/scipy 运行时警告
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, message=".*treating keys as positions is deprecated.*")
warnings.filterwarnings(
    "ignore", category=FutureWarning, message=".*Series.__setitem__ treating keys as positions is deprecated.*"
)
# 抑制 ConstantInputWarning: scipy 在计算相关性时常数输入导致的警告
try:
    from scipy.stats import ConstantInputWarning

    warnings.filterwarnings("ignore", category=ConstantInputWarning)
except ImportError:
    pass
warnings.filterwarnings("ignore", message=".*An input array is constant.*")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/futures_elite"
REPORTS_ROOT = PROJECT_ROOT / "reports"

# 优化器懒加载（可选依赖，失败时回退到原始实现）
_OPTIMIZER = None


def _get_optimizer():
    """获取或创建因子优化器（懒加载）。"""
    global _OPTIMIZER
    if _OPTIMIZER is None:
        try:
            from fts.factor_engine.factor_optimizer import FactorOptimizer

            _OPTIMIZER = FactorOptimizer()
        except ImportError:
            pass
    return _OPTIMIZER


def _yesterday_str() -> str:
    """返回昨日日期字符串 (YYYY-MM-DD)。"""
    from datetime import timedelta

    return (date.today() - timedelta(days=1)).isoformat()


# ─── 信号得分语义与跨组合可比性校验（v2.104.0+69）────────
# 综合得分 = 品种级 IC 方向翻转（per_variety_sign_flips）后的相对强弱评分，
# 负分表示因子读数相对过热 / IC 反向修正后的均值回归预期，不是趋势方向信号。
# 跨日增量仅在同一因子组合（factor_signature）下可比。
_SIGNAL_SCORE_SEMANTICS: str = (
    "综合得分为品种级 IC 方向翻转后的相对强弱评分"
    "（负分 = 因子读数相对过热 / 回归预期，非趋势方向信号）；"
    "跨日增量仅在同一因子组合下可比"
)


def _factor_set_signature(factors: list[dict[str, Any]]) -> str:
    """因子组合签名：排序后的因子名集合 SHA256（前 16 位）。

    用于校验跨日信号快照的因子组合是否一致：因子集合变化后，
    前后两日综合得分语义不同，不可直接相减（避免 08-16 L3 重算
    8→7 因子导致的虚假信号增量）。
    """
    names = sorted(f.get("name", "") for f in factors)
    return hashlib.sha256(json.dumps(names, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _compute_signal_deltas(
    today_scores: dict[str, float],
    prev_snapshot: dict[str, Any] | None,
    factor_signature: str,
) -> tuple[dict[str, float], dict[str, float], bool, str]:
    """较昨日信号增量计算（跨因子组合可比性校验）。

    Args:
        today_scores: 今日综合得分 {symbol: score}
        prev_snapshot: 昨日信号快照 dict（None = 缺失）
        factor_signature: 今日因子组合签名

    Returns:
        (sym_deltas, prev_scores, has_delta, skip_reason)
        - 因子组合不一致 → sym_deltas 为空、has_delta=False、skip_reason 说明原因
        - 旧快照无 factor_signature 字段 → 兼容处理，正常计算增量
        - 无昨日快照 → has_delta=False、skip_reason 说明
    """
    if prev_snapshot is None:
        return {}, {}, False, "无昨日信号快照，首次运行或数据缺失"
    prev_scores: dict[str, float] = prev_snapshot.get("scores") or {}
    prev_sig = prev_snapshot.get("factor_signature")
    if prev_sig and prev_sig != factor_signature:
        return (
            {},
            prev_scores,
            False,
            f"昨日快照因子组合与今日不一致（{prev_sig} ≠ {factor_signature}），"
            "前后得分不可比，跳过增量计算",
        )
    sym_deltas = {s: sc - prev_scores[s] for s, sc in today_scores.items() if s in prev_scores}
    return sym_deltas, prev_scores, bool(sym_deltas), ""


def _dedup_factors(
    factors: list[dict[str, Any]],
    ic_threshold: float = 0.3,
) -> list[dict[str, Any]]:
    """两层去重（代码哈希 + 回测结果 stat）与 IC 过滤，JSON/DB 加载路径共用。

    去重策略:
        1. 代码哈希去重：对每个因子的 code 字段做 SHA256 哈希，
           相同哈希的因子只保留第一个（按输入顺序）。
        2. 回测结果去重：如果两个因子的 (IC, sharpe, t_stat) 完全相同，
           视为同一因子逻辑的不同符号/参数版本，只保留第一个。

    避免同一因子逻辑被 Ridge 回归重复加权，导致该逻辑获得隐性超额权重。
    """
    seen_codes: set[str] = set()
    seen_stats: set[tuple[float, float, float]] = set()
    duplicate_count = 0
    kept: list[dict[str, Any]] = []
    for data in factors:
        try:
            ev = data.get("evaluation", {})
            bt = ev.get("level_1_backtest", {})
            ic = bt.get("ic", 0)
            if abs(ic) < ic_threshold:
                continue

            # Layer 1: 代码哈希去重（仅对有 code 的因子生效）
            code = data.get("code", "")
            if code:
                code_hash = hashlib.sha256(code.encode()).hexdigest()
                if code_hash in seen_codes:
                    duplicate_count += 1
                    continue
                seen_codes.add(code_hash)

            # Layer 2: 回测结果去重（IC/sharpe/t_stat 完全相同 → 同一因子）
            stat_key = (round(ic, 5), round(bt.get("sharpe", 0), 4), round(bt.get("t_stat", 0), 5))
            if stat_key in seen_stats:
                duplicate_count += 1
                continue
            seen_stats.add(stat_key)

            kept.append(data)
        except Exception:
            continue

    if duplicate_count:
        print(f"      [去重] 跳过 {duplicate_count} 个重复因子")
    return kept


def load_futures_elite_factors(ic_threshold: float = 0.3) -> list[dict[str, Any]]:
    """加载期货顶级 Elite 因子（JSON 快照目录，GAP-097 后为降级回退路径）。

    主路径已切换为 DuckDB 因子资产库（`load_futures_elite_factors_from_db`），
    本函数保留用于：① 数据库不可用/为空时的降级回退；② 既有测试兼容。
    """
    factors: list[dict[str, Any]] = []
    for fp in sorted(ELITE_DIR.glob("*.json")):
        try:
            factors.append(json.loads(fp.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return _dedup_factors(factors, ic_threshold=ic_threshold)


def load_futures_elite_factors_from_db(
    ic_threshold: float = 0.3,
    db_path: str | Path | None = None,
    market: str = "futures",
) -> list[dict[str, Any]]:
    """从 DuckDB 因子资产库（SSOT，plans/29）加载期货精英因子（GAP-097 v2.103.0）。

    factor_catalog 表为权威源：market={market} + is_elite=TRUE + status='active'。
    复用 metadata.evaluation（与 JSON 快照同构，含 level_1_backtest.ic/sharpe/t_stat）
    构造兼容 dict；缺失时用顶层 ic/sharpe/icir 列构造。加载失败/为空返回 []，
    由调用方回退 JSON 快照目录。

    Args:
        ic_threshold: IC 阈值（|ic| < 阈值跳过；信号管道 Ridge 加权模式用 0 全量加载）
        db_path: 注入测试用隔离库路径（默认经 FactorRepository market 路由到
                 data/factor_catalog_futures.duckdb）
        market: 市场过滤（"futures" 通用 / "energy" 能源产业链独立库）

    Returns:
        与 JSON 快照同构的因子 dict 列表
    """
    try:
        from fts.factor_engine.factor_db.repository import FactorRepository

        with FactorRepository(market=market, db_path=db_path) as repo:
            rows = repo.list_factors(
                market=market,
                status="active",
                is_elite=True,
                limit=10000,
                sort_by="sharpe",
                sort_order="desc",
            )
    except Exception as e:  # noqa: BLE001
        print(f"      [警告] DuckDB 因子资产库加载失败（回退 JSON 快照）: {e}")
        return []

    factors: list[dict[str, Any]] = []
    for row in rows:
        try:
            code = row.get("code") or ""
            if not code:
                continue
            metadata = row.get("metadata") or {}
            evaluation = metadata.get("evaluation")
            if not isinstance(evaluation, dict):
                # metadata 无完整评估时，用 factor_catalog 顶层评估列构造 level_1_backtest
                bt = {}
                if isinstance(metadata.get("evaluation"), dict):
                    bt = metadata["evaluation"].get("level_1_backtest") or {}
                evaluation = {
                    "factor_id": row.get("factor_id"),
                    "level_1_backtest": {
                        "ic": row.get("ic") or 0.0,
                        "icir": row.get("icir") or 0.0,
                        "sharpe": row.get("sharpe") or 0.0,
                        "t_stat": bt.get("t_stat", 0.0),
                        "turnover_monthly": row.get("turnover_monthly") or 0.0,
                        "max_drawdown": row.get("max_drawdown") or 0.0,
                    },
                }
            factors.append(
                {
                    "factor_id": row.get("factor_id"),
                    "name": row.get("name"),
                    "code": code,
                    "params": row.get("params") or {},
                    "signature": row.get("signature") or {},
                    "economic_logic": row.get("economic_logic") or {},
                    "market": row.get("market", "futures"),
                    "family": row.get("family"),
                    "source": row.get("source"),
                    "metadata": metadata,
                    "evaluation": evaluation,
                }
            )
        except Exception:
            continue
    return _dedup_factors(factors, ic_threshold=ic_threshold)


def _load_signal_factors(ic_threshold: float = 0.0) -> list[dict[str, Any]]:
    """加载期货信号因子：DuckDB 因子资产库（SSOT）为唯一加载源（GAP-097 强约束）。

    v2.104.0+7 (2026-08-13): 移除 JSON 快照目录静默回退——JSON 仅作只读备份，
    不作为加载源。8/12 曾因 JSON 目录仅有 1 个文件回退导致因子池骤降、权重快照
    被污染（单因子）。DuckDB 无可用精英因子时返回 []，由调用方报错退出，
    不静默回退到不完整的 JSON 快照。

    Args:
        ic_threshold: IC 阈值（信号管道 L3 组合过滤模式下默认 0 = 全量加载不过滤）
    """
    factors = load_futures_elite_factors_from_db(ic_threshold=ic_threshold)
    print(f"      [加载源] DuckDB 因子资产库（SSOT）: {len(factors)} 个")
    if not factors:
        print(
            "[ERROR] DuckDB 因子资产库无可用期货精英因子 "
            "（JSON 快照已降级为只读备份，不作为加载源，请检查 L3 因子库状态）"
        )
    return factors


# ─── L3 组合权重（因子选择与基础权重权威源，v2.105.0）────────
# 信号管道回归「信号计算 + Regime 权重调整」定位：因子选择与基础权重
# 分配由 L3 组合层负责（factor_weights.json），信号管道不再自选因子、
# 不再自训 Ridge 权重。L3 组合为空/不可用 → 严格模式报错退出。


def _load_l3_combo_weights(weights_path: str | Path | None = None) -> dict[str, float]:
    """加载 L3 组合基础权重（权威源：memory/portfolio/futures/factor_weights.json）。

    严格模式：文件缺失 / JSON 损坏 / 权重为空 → 打印 [ERROR] 并 sys.exit(1)，
    信号管道不自行回退到全量精英因子或等权。

    Args:
        weights_path: 覆盖路径（测试注入）；None 用默认 L3 组合权重文件

    Returns:
        {factor_name: weight}（L3 组合原始权重，未归一化，sum=exposure_scale）
    """
    fp = (
        Path(weights_path)
        if weights_path
        else PROJECT_ROOT / "memory" / "portfolio" / "futures" / "factor_weights.json"
    )
    if not fp.exists():
        print(f"[ERROR] L3 组合权重文件缺失: {fp}（严格模式，退出）")
        sys.exit(1)
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[ERROR] L3 组合权重文件损坏: {fp}: {e}（严格模式，退出）")
        sys.exit(1)
    weights = data.get("weights") or {}
    if not isinstance(weights, dict) or not weights:
        print(f"[ERROR] L3 组合权重为空: {fp}（严格模式，退出）")
        sys.exit(1)
    n = data.get("n_factors")
    print(
        f"      [L3 组合] 加载基础权重: {len(weights)} 因子"
        + (f" (n_factors={n})" if n else "")
    )
    return {k: float(v) for k, v in weights.items()}


def _load_l3_combo_factors(
    l3_weights: dict[str, float],
    market: str = "futures",
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """按 L3 组合因子名从 DuckDB 因子资产库加载因子定义（严格模式）。

    复用全量精英因子加载后按 name 交集过滤，保证 code/params 定义完整；
    单个因子缺失 → 警告并跳过该因子（其权重由调用方同步剔除），不阻断主路径。
    L3 组合因子整体无法加载 → [ERROR] 退出。

    Args:
        l3_weights: L3 组合权重 {factor_name: weight}
        market: 因子库市场路由（"futures" 通用 / "energy" 能源链专属库）
        db_path: 因子库路径覆盖（默认经 market 路由；链模式显式传能源库路径）

    Returns:
        因子定义列表（仅 L3 组合中的因子，保持 DB 顺序）
    """
    all_factors = load_futures_elite_factors_from_db(ic_threshold=0, db_path=db_path, market=market)
    known = set(l3_weights.keys())
    kept = [f for f in all_factors if f.get("name") in known]
    missing = known - {f.get("name") for f in kept}
    if missing:
        print(f"      [警告] L3 组合因子在因子资产库中缺失，跳过: {', '.join(sorted(missing))}")
    if not kept:
        print("[ERROR] L3 组合因子均无法从 DuckDB 因子资产库加载（严格模式，退出）")
        sys.exit(1)
    return kept


# ─── Regime 档位缩放权重调整（v2.105.0）────────────────────
# 信号管道唯一允许的权重干预：按市场制度对 L3 基础权重做类别级缩放后
# 归一化。缩放不丢弃因子（不构成因子选择），因子集合与基础权重仍由 L3 决定。

_REGIME_FACTOR_SCALE: dict[str, dict[str, float]] = {
    "bull": {"trend": 1.30, "reversal": 0.80, "volume": 1.00, "neutral": 1.00},
    "bear": {"trend": 1.30, "reversal": 0.80, "volume": 1.00, "neutral": 1.00},
    "oscillate": {"trend": 0.80, "reversal": 1.30, "volume": 1.00, "neutral": 1.00},
    "high_vol": {"trend": 0.80, "reversal": 0.80, "volume": 0.80, "neutral": 0.80},
    "low_vol": {"trend": 1.10, "reversal": 1.10, "volume": 1.10, "neutral": 1.10},
}


# ─── Regime → 信号判定阈值（P0 修复：让主制度真正影响多空判定）────────
# 修复 v2.105.0 硬编码 >0/<0 导致 "bull/bear 判定结果完全相同" 缺陷：
# - bull 顺势做多：做多门槛贴近 0（-0.15），做空门槛抬高（-0.40）
# - bear 顺势做空：做空门槛贴近 0（-0.15），做多门槛抬高（+0.40）
# - high_vol 双向从严；low_vol 双向从宽；oscillate/unknown 对称保守
_REGIME_SIGNAL_THRESHOLDS: dict[str, dict[str, float]] = {
    "bull": {"long_min": -0.15, "short_max": -0.40},
    "bear": {"long_min": 0.40, "short_max": -0.15},
    "oscillate": {"long_min": 0.30, "short_max": -0.30},
    "high_vol": {"long_min": 0.50, "short_max": -0.50},
    "low_vol": {"long_min": 0.20, "short_max": -0.20},
    "unknown": {"long_min": 0.30, "short_max": -0.30},
}


def _apply_regime_direction_bias(
    sym_scores: dict[str, float],
    regime_type: str,
    confidence: float,
    max_bias: float = 0.30,
) -> tuple[dict[str, float], float]:
    """Regime 方向偏移（P0 修复：主制度置信度越高，多空倾向越明显）。

    - bull: 综合得分整体上移 (× (1 + bias)) → 更多做多候选
    - bear: 综合得分整体下移 (× (1 - bias)) → 更多做空候选
    - 其余制度不偏移

    Args:
        sym_scores: 品种 → 综合得分
        regime_type: 主制度
        confidence: 主制度置信度 (0~1)
        max_bias: 最大偏移强度（置信度 100% 时的最大倍数偏移）

    Returns:
        (调整后得分, 实际应用的偏移强度 bias)
    """
    if regime_type not in ("bull", "bear") or confidence <= 0:
        return sym_scores, 0.0
    bias = max_bias * float(confidence)
    factor = 1.0 + bias if regime_type == "bull" else 1.0 - bias
    return {s: sc * factor for s, sc in sym_scores.items()}, bias


def _classify_factor_category(name: str) -> str:
    """按因子名后缀启发式归类（trend / reversal / volume / neutral）。

    优先级 reversal > trend > volume：基差/乖离类（basis/bias）优先判为
    反转类，避免与动量关键词（momentum）歧义。
    """
    n = (name or "").lower()
    reversal_kw = ("bias", "basis", "cross_carry", "roll_yield", "tail_risk", "pcr", "devstop")
    trend_kw = ("adx", "aroon", "momentum", "trix", "kst", "force_index", "chandelier", "up_return", "break", "echo")
    volume_kw = ("volume", "vwap", "crowd", "trade_imbalance", "spread", "flow", "efficiency", "kbar", "ultosc", "intraday")
    for kw in reversal_kw:
        if kw in n:
            return "reversal"
    for kw in trend_kw:
        if kw in n:
            return "trend"
    for kw in volume_kw:
        if kw in n:
            return "volume"
    return "neutral"


def _apply_regime_weight_adjustment(
    factor_weights: dict[str, float],
    market_regime: dict,
    factors: list[dict[str, Any]],
) -> dict[str, float]:
    """Regime 档位缩放：按市场制度对 L3 基础权重做类别级缩放后归一化。

    信号管道仅在此处按 Regime 调整因子权重（缩放不丢弃因子），
    因子选择与基础权重仍由 L3 组合决定。

    Args:
        factor_weights: L3 组合基础权重（已归一化）{factor_name: weight}
        market_regime: Market Regime 检测结果（含 regime/confidence）
        factors: 因子定义列表（含 name，用于类别归属）

    Returns:
        调整后权重 {factor_name: weight}（归一化，和=1）
    """
    regime_type = market_regime.get("regime", "unknown")
    scale_map = _REGIME_FACTOR_SCALE.get(regime_type, {})
    if not scale_map:
        print(f"      [Regime 权重] 制度 {regime_type} 无缩放配置，保持 L3 基础权重")
        total = sum(factor_weights.values()) or 1.0
        return {k: v / total for k, v in factor_weights.items()}

    adjusted: dict[str, float] = {}
    cat_weight: dict[str, float] = {}
    for name, w in factor_weights.items():
        cat = _classify_factor_category(name)
        scale = scale_map.get(cat, 1.0)
        adjusted[name] = w * scale
        cat_weight[cat] = cat_weight.get(cat, 0.0) + w

    total = sum(adjusted.values())
    if not np.isfinite(total) or total <= 1e-12:
        print("      [Regime 权重] 缩放后权重和非法，回退 L3 基础权重")
        return factor_weights
    adjusted = {k: v / total for k, v in adjusted.items()}

    cat_summary = ", ".join(f"{c}={s:.2f}" for c, s in sorted(cat_weight.items()))
    top3 = ", ".join(f"{n}({w:.3f})" for n, w in sorted(adjusted.items(), key=lambda x: -x[1])[:3])
    print(f"      [Regime 权重] {regime_type}: 类别缩放 {cat_summary} → Top: {top3}")
    return adjusted


def _inject_macro_to_panel(
    panel: dict[str, "pd.DataFrame"],
    enabled: bool = True,
    trace_id: str = "",
) -> dict[str, "pd.DataFrame"]:
    """宏观字段注入（GAP-088 v2.103.0 信号管道接线）。

    fut_macro_cpi/interest_rate/export/us_bond 等宏观因子读取 export/
    import_data/cpi/rate/us_bond 5 列真实数据；拉取失败降级不阻断主路径
    （因子走 close 趋势代理），与回测管线 `macro_field_injection` 语义一致。
    """
    if not enabled or not panel:
        return panel
    try:
        from fts.data_sources.macro_aligner import inject_macro_fields_to_panel

        return inject_macro_fields_to_panel(panel, trace_id=trace_id)
    except Exception as e:  # noqa: BLE001
        print(f"      [提示] 宏观注入失败（因子走 close 代理）: {e}")
        return panel


def _compute_signal_matrix(
    panel: dict[str, "pd.DataFrame"],
    factors: list[dict[str, Any]],
    use_optimizer: bool = False,
) -> dict[str, dict[str, np.ndarray]]:
    """一次性计算所有因子 × 所有品种的信号矩阵。

    Args:
        panel: 品种行情面板
        factors: 因子列表
        use_optimizer: 是否使用优化器（并行+缓存，因子>=30自动启用）

    Returns:
        signal_matrix[symbol][factor_name] = np.ndarray (信号值时间序列)
    """
    from fts.factor_engine.factor_program import FactorExecutor

    # 检查是否使用优化器
    optimizer = _get_optimizer() if use_optimizer else None
    if optimizer is not None and len(factors) >= 30:
        try:
            from fts.factor_engine.factor_optimizer import set_panel_ref

            set_panel_ref(panel)
            print("      [优化器] 使用 Tier 1 并行计算...")
            return optimizer.compute_signal_matrix_parallel(panel, factors)
        except Exception as e:
            print(f"      [警告] 优化器计算失败，回退到顺序计算: {e}")

    signal_matrix: dict[str, dict[str, np.ndarray]] = {}
    n_errors = 0

    # 抑制因子编译/执行时的运行时警告（除零等，已通过 NaN 处理防御）
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    for sym, df in panel.items():
        if df.empty or len(df) < 20:
            continue
        sym_signals: dict[str, np.ndarray] = {}
        for factor_data in factors:
            name = factor_data.get("name", "?")
            try:
                executor = FactorExecutor(factor_data)
                sig = executor.execute(df, factor_data.get("params", {}))
                arr = np.array(sig, dtype=float)
                # 只保留有限数值
                arr = np.where(np.isfinite(arr), arr, np.nan)
                sym_signals[name] = arr
            except Exception:
                n_errors += 1
                continue
        if sym_signals:
            signal_matrix[sym] = sym_signals

    if n_errors > 0:
        print(f"      [警告] 信号计算错误: {n_errors} 次")
    return signal_matrix


def _generate_trading_advice(
    regime: dict,
    signal_scores: dict[str, float],
    factor_weights: dict[str, float],
    signal_deltas: dict[str, float] | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """生成交易建议。

    Args:
        regime: Market Regime 检测结果
        signal_scores: 品种信号得分 {symbol: score}
        factor_weights: 因子权重 {factor_name: weight}
        signal_deltas: 信号增量 {symbol: delta}（可选）
        top_n: 推荐品种数量

    Returns:
        交易建议字典，包含：
        - regime_advice: 基于 regime 的方向建议
        - long_candidates: 做多候选品种
        - short_candidates: 做空候选品种
        - key_factors: 关键因子
        - risk_warnings: 风险提示
    """
    regime_type = regime.get("regime", "unknown")
    confidence = regime.get("confidence", 0)

    # 1. 基于 regime 的方向建议
    if regime_type == "bull":
        regime_advice = "趋势上涨，优先做多"
        preferred_direction = "long"
    elif regime_type == "bear":
        regime_advice = "趋势下跌，优先做空"
        preferred_direction = "short"
    elif regime_type == "oscillate":
        regime_advice = "震荡市，反向操作或观望"
        preferred_direction = "neutral"
    elif regime_type == "high_vol":
        regime_advice = "高波动，缩小仓位，谨慎操作"
        preferred_direction = "cautious"
    else:
        regime_advice = "regime 不明确，观望为主"
        preferred_direction = "neutral"

    # 2. 筛选候选品种
    sorted_symbols = sorted(signal_scores.items(), key=lambda x: abs(x[1]), reverse=True)

    long_candidates = []
    short_candidates = []

    for sym, score in sorted_symbols:
        if score > 0 and len(long_candidates) < top_n:
            delta = signal_deltas.get(sym, 0) if signal_deltas else 0
            long_candidates.append(
                {
                    "symbol": sym,
                    "score": score,
                    "delta": delta,
                    "accelerating": delta < -0.01,  # 多头加速
                }
            )
        elif score < 0 and len(short_candidates) < top_n:
            delta = signal_deltas.get(sym, 0) if signal_deltas else 0
            short_candidates.append(
                {
                    "symbol": sym,
                    "score": score,
                    "delta": delta,
                    "accelerating": delta < -0.01,  # 空头加速
                }
            )

    # 3. 提取关键因子（权重最高的 5 个）
    sorted_factors = sorted(factor_weights.items(), key=lambda x: x[1], reverse=True)
    key_factors = [name for name, _ in sorted_factors[:5]]

    # 4. 风险提示
    risk_warnings = []
    if confidence < 0.6:
        risk_warnings.append("regime 置信度较低，信号可靠性下降")
    if regime_type == "high_vol":
        risk_warnings.append("高波动环境，止损幅度需放大")
    if signal_deltas:
        # 检查是否有品种信号剧烈变化
        large_deltas = [abs(d) for d in signal_deltas.values() if abs(d) > 0.1]
        if len(large_deltas) > len(signal_deltas) * 0.3:
            risk_warnings.append("30% 以上品种信号剧烈变化，市场可能处于转折点")

    return {
        "regime_advice": regime_advice,
        "preferred_direction": preferred_direction,
        "regime_confidence": confidence,
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
        "key_factors": key_factors,
        "risk_warnings": risk_warnings,
    }


def _compute_per_variety_weights(
    global_weights: dict[str, float],
    per_variety_ic: dict[str, dict[str, float]],
    min_ic: float = 0.01,
) -> dict[str, dict[str, float]]:
    """将全局权重（L3 组合基础权重经 Regime 调整）与品种级 IC 结合，生成品种级因子权重。

    方法：
        对每个品种 v，因子 f 的有效权重 = global_weight[f] * |IC[f][v]|，
        然后按品种归一化。
        如果品种 v 在因子 f 上无 IC 数据，回退到 global_weight[f]。

    Args:
        global_weights: 全局权重 {factor_name: weight}（L3 组合基础权重经 Regime 调整）
        per_variety_ic: 品种-因子 IC 矩阵 {factor_name: {variety: ic}}
        min_ic: IC 最小绝对值阈值，低于此值视为无效

    Returns:
        per_variety_weights: {variety: {factor_name: weight}}
    """
    # 收集所有因子和品种
    factor_names = list(global_weights.keys())
    varieties: set[str] = set()
    for fname, vics in per_variety_ic.items():
        varieties.update(vics.keys())

    if not varieties:
        # 无品种级 IC 数据，直接返回空
        return {}

    per_variety_weights: dict[str, dict[str, float]] = {}

    for var in varieties:
        raw_weights: dict[str, float] = {}
        for fname in factor_names:
            gw = global_weights.get(fname, 0.0)
            if gw <= 0:
                continue
            vic = per_variety_ic.get(fname, {}).get(var, 0.0)
            # NaN（常数信号导致 Spearman IC 未定义）视为无数据，
            # 按低 IC 回退赋予极低权重，避免 NaN 污染 total 导致整品种被跳过
            if not np.isfinite(vic) or abs(vic) < min_ic:
                # IC 过低/缺失，该因子对此品种无效，赋予极低权重
                raw_weights[fname] = gw * min_ic
            else:
                raw_weights[fname] = gw * abs(vic)

        # 归一化（total 必须有限，防御性兜底）
        total = sum(raw_weights.values())
        if np.isfinite(total) and total > 1e-10:
            per_variety_weights[var] = {f: w / total for f, w in raw_weights.items()}

    return per_variety_weights


def _compute_composite_scores(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    factor_sign_flips: dict[str, float],
    factors: list[dict[str, Any]],
    factor_weights: dict[str, float] | None = None,
    per_variety_weights: dict[str, dict[str, float]] | None = None,
    per_variety_sign_flips: dict[str, dict[str, float]] | None = None,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """合成因子信号（可选权重；方向校正参数 v2.105.0 起恒为空 dict）。

    Args:
        factor_weights: 全局因子权重字典（L3 组合基础权重经 Regime 调整），None 则等权。
        per_variety_weights: 品种级因子权重 {variety: {factor: weight}}，
                              优先于 factor_weights 使用。
        per_variety_sign_flips: 品种级方向翻转 {variety: {factor: +1/-1}}。
                              品种级权重用 abs(IC) 丢弃了符号，此处按 IC 符号在
                              合成层恢复方向（P0 修复：反向因子正信号不再被当正贡献）。

    Returns:
        sym_scores: 品种 → 综合得分
        sym_details: 品种 → {因子名 → 信号值}
    """
    n_factors = len(factors)
    default_weight = 1.0 / n_factors

    sym_scores: dict[str, float] = {}
    sym_details: dict[str, dict[str, float]] = {}

    for sym, sym_signals in signal_matrix.items():
        # 选择品种级权重（优先）或全局权重
        if per_variety_weights and sym in per_variety_weights:
            effective_weights = per_variety_weights[sym]
        else:
            effective_weights = factor_weights

        signal_sum = 0.0
        weight_sum = 0.0
        details: dict[str, float] = {}

        for factor_data in factors:
            name = factor_data.get("name", "?")
            sig = sym_signals.get(name)
            if sig is None or len(sig) == 0:
                continue
            val = float(sig[-1]) if np.isfinite(sig[-1]) else 0.0
            # 方向翻转：先应用品种级符号（IC 符号校准，P0 修复），再应用全局翻转
            flip = factor_sign_flips.get(name, 1.0)
            if per_variety_sign_flips and sym in per_variety_sign_flips:
                flip = per_variety_sign_flips[sym].get(name, flip)
            val *= flip
            w = effective_weights.get(name, default_weight) if effective_weights else default_weight
            signal_sum += val * w
            weight_sum += w
            details[name] = val

        if weight_sum > 0:
            composite = signal_sum / weight_sum
            sym_scores[sym] = composite
            sym_details[sym] = details

    return sym_scores, sym_details


def _compute_per_variety_ic_matrix(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    panel: dict[str, "pd.DataFrame"],
    common_dates: list[str],
    factor_sign_flips: dict[str, float],
) -> dict[str, dict[str, float]]:
    """计算品种-因子有效性矩阵：每个因子 × 每个品种的时序 Spearman IC。

    Returns:
        {因子名: {品种: IC值}}  — 品种维度聚合的因子 IC 矩阵
    """
    n_dates = len(common_dates)
    if n_dates < 10:
        return {}

    # 转置索引：因子名 → {品种名 → IC}
    factor_ic_matrix: dict[str, dict[str, float]] = {}

    for sym, sym_signals in signal_matrix.items():
        df = panel.get(sym)
        if df is None or df.empty:
            continue

        aligned = df.reindex(common_dates)
        closes = aligned["close"].values
        if len(closes) < 10:
            continue

        fwd_ret = np.zeros(len(closes))
        fwd_ret[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)

        for fname, arr in sym_signals.items():
            flip = factor_sign_flips.get(fname, 1.0)
            sig = np.array(arr, dtype=float)
            if len(sig) < len(closes):
                sig = np.pad(sig, (0, len(closes) - len(sig)), constant_values=np.nan)[: len(closes)]
            sig = np.where(np.isfinite(sig), sig, 0.0) * flip

            valid = np.isfinite(sig) & np.isfinite(fwd_ret)
            if valid.sum() < 10:
                continue

            from scipy.stats import spearmanr

            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning)
                try:
                    from scipy.stats import ConstantInputWarning

                    warnings.filterwarnings("ignore", category=ConstantInputWarning)
                except ImportError:
                    pass
                ic, _ = spearmanr(sig[valid], fwd_ret[valid])

            if fname not in factor_ic_matrix:
                factor_ic_matrix[fname] = {}
            factor_ic_matrix[fname][sym] = ic

    return factor_ic_matrix


def _compute_holdout_validation(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    panel: dict[str, "pd.DataFrame"],
    common_dates: list[str],
    factor_sign_flips: dict[str, float],
    holdout_set: set[str],
) -> dict[str, Any]:
    """盲测品种验证：计算盲测品种 vs 训练品种的因子 IC 对比。

    Args:
        signal_matrix: 信号矩阵
        panel: 品种行情面板
        common_dates: 共同交易日列表
        factor_sign_flips: 方向校正
        holdout_set: 盲测品种集合

    Returns:
        dict with keys: holdout_ic, train_ic, ic_retention, warning, details
    """
    n_dates = len(common_dates)
    if n_dates < 10:
        return {"holdout_ic": 0, "train_ic": 0, "ic_retention": 0, "warning": "交易日不足", "details": {}}

    holdout_ics: list[float] = []
    train_ics: list[float] = []
    sym_ics: dict[str, float] = {}

    for sym, sym_signals in signal_matrix.items():
        df = panel.get(sym)
        if df is None or df.empty:
            continue

        # 对齐到共同日期
        aligned = df.reindex(common_dates)
        closes = aligned["close"].values
        if len(closes) < 10:
            continue

        # 5 日前向收益
        fwd_ret = np.zeros(len(closes))
        fwd_ret[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)

        # 所有因子的平均信号（方向校正后）
        composite_signal = np.zeros(len(closes))
        n_active = 0
        for fname, arr in sym_signals.items():
            flip = factor_sign_flips.get(fname, 1.0)
            sig = np.array(arr, dtype=float)
            if len(sig) < len(closes):
                sig = np.pad(sig, (0, len(closes) - len(sig)), constant_values=np.nan)[: len(closes)]
            sig = np.where(np.isfinite(sig), sig, 0.0) * flip
            composite_signal += sig
            n_active += 1

        if n_active > 0:
            composite_signal /= n_active

        # 计算时序 Spearman IC
        valid = np.isfinite(composite_signal) & np.isfinite(fwd_ret)
        if valid.sum() < 10:
            continue

        from scipy.stats import spearmanr

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            try:
                from scipy.stats import ConstantInputWarning

                warnings.filterwarnings("ignore", category=ConstantInputWarning)
            except ImportError:
                pass
            ic, _ = spearmanr(composite_signal[valid], fwd_ret[valid])
        sym_ics[sym] = ic

        if sym in holdout_set:
            holdout_ics.append(ic)
        else:
            train_ics.append(ic)

    avg_holdout = float(np.mean(holdout_ics)) if holdout_ics else 0.0
    avg_train = float(np.mean(train_ics)) if train_ics else 0.0
    retention = abs(avg_holdout / max(abs(avg_train), 1e-10)) if abs(avg_train) > 1e-10 else 0.0

    warning = ""
    if abs(avg_train) > 0.02 and retention < 0.5:
        warning = f"⚠️ 盲测 IC ({avg_holdout:.4f}) 不足训练 IC ({avg_train:.4f}) 的 50%，因子泛化能力较弱"
    elif len(holdout_ics) < 3:
        warning = f"盲测品种有效数据不足 ({len(holdout_ics)}/{len(holdout_set)})"

    return {
        "holdout_ic": avg_holdout,
        "train_ic": avg_train,
        "ic_retention": retention,
        "warning": warning,
        "details": sym_ics,
        "n_holdout_valid": len(holdout_ics),
        "n_train_valid": len(train_ics),
    }


def _classify_delta_moves(
    sym_scores: dict[str, float],
    sym_deltas: dict[str, float],
    accel_threshold: float = -0.02,
    decel_threshold: float = 0.02,
    top_n: int = 5,
) -> tuple[list[tuple[str, float, str]], list[tuple[str, float, str]]]:
    """按信号方向与增量方向分类加速/减速品种（trading_advice 第 5 节）。

    v2.104.0+7 (2026-08-13): 修正逻辑——① 减速按增量降序取最大正增量
    （原升序取最小正增量，与主报告减速清单不一致）；② 按品种信号方向判定
    标注（多头品种增量正应标"多头加强"，原实现一律标"做空减弱"错误）。

    Args:
        sym_scores: 品种信号得分 {symbol: score}
        sym_deltas: 品种信号增量 {symbol: delta}
        accel_threshold: 增量低于此值视为加速（默认 -0.02）
        decel_threshold: 增量高于此值视为减速/反转萌芽（默认 0.02）
        top_n: 各方向最多返回数量

    Returns:
        (accel, decel)：加速与减速列表，元素 (symbol, delta, 操作标注)；
        加速按增量升序（最负优先），减速按增量降序（最大正增量优先）
    """
    accel = sorted(((s, d) for s, d in sym_deltas.items() if d < accel_threshold), key=lambda x: x[1])[:top_n]
    decel = sorted(((s, d) for s, d in sym_deltas.items() if d > decel_threshold), key=lambda x: -x[1])[:top_n]

    accel_out: list[tuple[str, float, str]] = []
    for sym, delta in accel:
        if sym_scores.get(sym, 0) < 0:
            label = "做空信号加强中"
        else:
            label = "多头信号减弱中，警惕回撤"
        accel_out.append((sym, delta, label))

    decel_out: list[tuple[str, float, str]] = []
    for sym, delta in decel:
        if sym_scores.get(sym, 0) > 0:
            label = "多头信号加强中，做多关注"
        else:
            label = "做空信号减弱中，建议减仓"
        decel_out.append((sym, delta, label))

    return accel_out, decel_out


def _generate_trading_advice_report(
    today: str,
    report_dir: Path,
    sym_scores: dict[str, float],
    sym_deltas: dict[str, float],
    has_delta: bool,
    market_regime: dict,
    regime_label: str,
    sector_breakdown: dict[str, str],
    active_sector_map: dict[str, list[str]],
    factor_weights: dict[str, float],
    per_variety_weights: dict[str, dict[str, float]] | None,
    panel: dict[str, "pd.DataFrame"],
    long_signals: list[tuple[str, float]],
    short_signals: list[tuple[str, float]],
    name_fn: callable,
    price_fn: callable,
    contract_fn: callable,
) -> None:
    """生成交易建议报告（独立于信号排名报告）。

    输出文件: reports/futures/{date}/trading_advice_{date}.md
    """
    regime_type = market_regime.get("regime", "unknown")
    confidence = market_regime.get("confidence", 0)

    lines: list[str] = []

    def w(s=""):
        lines.append(s)

    # ── 标题 ──
    w(f"# 交易建议报告 — {today}")
    w()
    w(f"生成时间: {today} | 基于 FTS L3 Portfolio Loop 信号")
    w(f"覆盖品种: {len(sym_scores)} 个 | 信号范围: [{min(sym_scores.values()):+.4f}, {max(sym_scores.values()):+.4f}]")
    w()

    # ── 1. 市场制度与方向策略 ──
    w("## 1. 市场制度与方向策略")
    w()
    w("| 维度 | 当前状态 |")
    w("|------|----------|")
    w(f"| 主制度 (品种数加权) | **{regime_label}** |")
    w(f"| 主制度置信度 | {confidence:.1%} |")
    if regime_type == "bull":
        w("| 方向策略 | **顺势做多** |")
        w("| 仓位建议 | 正常仓位（70-80%） |")
    elif regime_type == "bear":
        w("| 方向策略 | **顺势做空** |")
        w("| 仓位建议 | 中等仓位（50-60%） |")
    elif regime_type == "oscillate":
        w("| 方向策略 | **反向操作** |")
        w("| 仓位建议 | 谨慎仓位（30-40%） |")
    elif regime_type == "high_vol":
        w("| 方向策略 | **谨慎观望** |")
        w("| 仓位建议 | 低仓位（20-30%） |")
    else:
        w("| 方向策略 | **观望为主** |")
        w("| 仓位建议 | 低仓位（0-20%） |")
    w()

    # 产业链级 Breakdown
    if sector_breakdown:
        w("### 产业链级市场制度 Breakdown")
        w()
        w("| 产业链 | 制度 | 置信度 | 品种数 | 方向建议 |")
        w("|--------|------|--------|--------|----------|")
        for sector in sorted(sector_breakdown.keys()):
            r = sector_breakdown[sector]
            c = market_regime.get("features", {}).get("sector_confidences", {}).get(sector, 0)
            n_syms = len(active_sector_map.get(sector, []))
            if r in ("bull", "bear"):
                dir_advice = "顺势" if r == "bull" else "做空"
            elif r == "oscillate":
                dir_advice = "反向操作"
            elif r in ("high_vol",):
                dir_advice = "谨慎"
            else:
                dir_advice = "观望"
            w(f"| {sector} | {r} | {c:.1%} | {n_syms} | {dir_advice} |")
        w()
        w("> **策略提示**: 不同产业链制度分化时，应以产业链级制度为准调整该板块品种的交易策略，")
        w("> 避免用全市场平均制度掩盖结构性机会或风险。")
        w()

    regime_note = {
        "bull": "趋势上涨环境，做多信号可信度较高，可适当放大仓位。趋势延续概率高，逆势做空风险大。",
        "bear": "趋势下跌环境，做空信号可信度较高，可适当放大仓位。做多信号仅用于对冲，不做主力方向。",
        "oscillate": "震荡环境，反向操作更优：做空加速品种，做多减速品种。趋势持续性弱，以区间交易为主。",
        "high_vol": "波动率异常偏高，信号噪音大，需严格止损。只做增量绝对值 > 0.15 的品种。",
        "low_vol": "波动率偏低，信号可信度较高，可正常仓位。关注波动率突破信号。",
    }.get(regime_type, "市场状态不明确，建议缩小仓位或观望。")
    w(f"> {regime_note}")
    w()

    # ── 2. 信号强度分层与仓位建议 ──
    w("## 2. 信号强度分层与仓位建议")
    w()
    w("| 信号强度 | 仓位比例 | 操作建议 | 适用品种数 |")
    w("|----------|---------|---------|-----------|")
    tiers = [
        (0.60, float("inf"), "15-20%", "主力建仓，分 2 批"),
        (0.40, 0.60, "10-15%", "正常建仓"),
        (0.15, 0.40, "5-10%", "试探性建仓"),
        (0.0, 0.15, "0%", "不交易"),
    ]
    for lo, hi, size, action in tiers:
        if hi == float("inf"):
            count = sum(1 for _, sc in long_signals + short_signals if abs(sc) >= lo)
            label = f"≥ {lo:.2f}"
        else:
            count = sum(1 for _, sc in long_signals + short_signals if lo <= abs(sc) < hi)
            label = f"{lo:.2f} ~ {hi:.2f}"
        w(f"| {label} | {size} | {action} | {count} 个 |")
    w()

    # ── 3. 建仓规则 ──
    w("## 3. 建仓规则")
    w()
    w("### 3.1 分批建仓")
    w()
    w("| 批次 | 比例 | 触发条件 | 动作 |")
    w("|------|------|----------|------|")
    w("| 第 1 批 | 50% | 开盘 | 按信号强度分配仓位，市价入场 |")
    w("| 第 2 批 | 30% | 收盘价比入场价确认方向 | 确认方向正确后加仓 |")
    w("| 第 3 批 | 20% | 次日信号增量仍为正方向 | 趋势延续确认后加仓 |")
    w()
    w("### 3.2 分批条件")
    w()
    w("- 第 2 批条件：收盘价与入场价方向一致（做空：收盘 < 开盘；做多：收盘 > 开盘）")
    w("- 第 3 批条件：次日信号增量未反转（做空：delta < 0；做多：delta > 0）")
    w("- 任一条件不满足则取消后续批次，等待下一次机会")
    w()

    # ── 4. 止损规则 ──
    w("## 4. 止损规则")
    w()
    w("| 条件 | 动作 |")
    w("|------|------|")
    w("| 持仓浮亏 > 2% | 减仓 50% |")
    w("| 持仓浮亏 > 4% | 清仓 |")
    w("| 信号增量反转（做空但 delta > 0，做多但 delta < 0）| 减半仓 |")
    w("| 连续 3 天信号衰减（信号强度持续下降）| 平仓 |")
    w("| 盘中价格突破 20 日 ATR 2 倍 | 无条件止损 |")
    w()

    # ── 5. 信号增量驱动的动态调整 ──
    w("## 5. 信号增量驱动的动态调整")
    w()
    w("> 信号增量 = 今日得分 - 昨日得分，反映信号强度的变化方向和幅度。")
    w()
    if has_delta:
        # 按信号方向 + 增量方向分类（对齐主报告减速清单；v2.104.0+7 双向判定修正）
        accel_short, decel_short = _classify_delta_moves(sym_scores, sym_deltas)

        w("| 信号增量区间 | 空头持仓 | 多头持仓 |")
        w("|-------------|----------|----------|")
        w("| $\\Delta < -0.02$ | 加仓或持有（空头加速） | 减仓或平仓（多头减弱） |")
        w("| $-0.02 \\leq \\Delta \\leq 0.02$ | 持有观望 | 持有观望 |")
        w("| $\\Delta > 0.02$ | 减仓或平仓（空头减速/反转萌芽） | 加仓或持有（多头加强/反转萌芽） |")
        w()

        if accel_short:
            w("**信号加速（增量最负）**：")
            w()
            for sym, delta, label in accel_short:
                score = sym_scores.get(sym, 0)
                w(f"- **{sym}** | 信号={score:+.4f} | 增量={delta:+.4f} | {label}")
            w()

        if decel_short:
            w("**信号减速/反转萌芽（增量最正）**：")
            w()
            for sym, delta, label in decel_short:
                score = sym_scores.get(sym, 0)
                w(f"- **{sym}** | 信号={score:+.4f} | 增量={delta:+.4f} | {label}")
            w()
    else:
        w("- 无昨日信号数据，无法计算增量。首次运行或数据缺失不影响交易。")
        w()

    # ── 6. 品种级差异化权重 ──
    w("## 6. 品种级差异化权重")
    w()
    if per_variety_weights:
        w("> 每个品种的因子权重已根据其自身 IC 矩阵调整，使品种更依赖对其有效的因子。")
        w()
        # 品种级权重偏离度汇总
        deviations = []
        for var, vw in per_variety_weights.items():
            gw = {f: factor_weights.get(f, 0) for f in vw}
            dev = sum(abs(vw[f] - gw.get(f, 0)) for f in vw) / len(vw)
            deviations.append((var, dev))
        deviations.sort(key=lambda x: -x[1])
        w("| 品种 | 与全局权重偏离度 | 说明 |")
        w("|------|----------------|------|")
        for var, dev in deviations[:5]:
            note = "权重差异化大，品种个性强" if dev > 0.02 else "接近全局权重"
            w(f"| {var} | {dev:.4f} | {note} |")
        if len(deviations) > 5:
            w(f"| ... 及其他 {len(deviations) - 5} 个品种 | — | — |")
        w()
    else:
        w("- 使用 L3 组合基础权重（Regime 档位调整后），未做品种级调整。")
        w()

    # 权重最集中的前 3 个因子
    w("**Top 因子权重**：")
    w()
    sorted_factors = sorted(factor_weights.items(), key=lambda x: x[1], reverse=True)
    for name, weight in sorted_factors[:5]:
        w(f"- **{name}**: {weight:.1%}")
    w()

    # ── 7. 风险控制要点 ──
    w("## 7. 风险控制要点")
    w()
    w("| 风险类型 | 具体描述 | 对策 |")
    w("|----------|----------|------|")
    w("| **方向冲突** | 当前做多与做空信号并存 | 分别独立交易，净敞口不超过总资金 50% |")
    w("| **Regime 切换** | 市场制度可能转变 | 每日检查 regime，切换后减仓至 50% 以下 |")
    if confidence < 0.6:
        w("| **Regime 低置信度** | 当前 regime 识别置信度偏低 | 信号可靠性下降，仓位减半 |")
    w("| **因子集中度** | 权重最高因子占比过大 | 单因子不超过总权重 30%，超标需人工核查 |")
    max_weight = max(factor_weights.values()) if factor_weights else 0
    # 加浮点容差：cap=0.30 截断后归一化可能恰好 0.3000，避免误报"30.0% > 30%"
    if max_weight > 0.3 + 1e-9:
        w(f"| **因子集中风险** | 当前 Top 因子权重 {max_weight:.1%} > 30% | 建议增加多样性或手动限制 |")
    w("| **流动性风险** | 部分品种流动性不足 | 主力合约优先，避开持仓量 < 1 万手的品种 |")
    w("| **过拟合风险** | 组合夏普 1.12，Verifier 未通过 | 不过度依赖信号，严格止损 |")
    w()

    # ── 8. 今日交易执行计划 ──
    w("## 8. 今日交易执行计划")
    w()
    w("### 8.1 做空计划（核心方向）")
    w()
    if short_signals:
        w("| 优先级 | 品种 | 名称 | 信号强度 | 增量 | 建议仓位 | 开仓条件 |")
        w("|--------|------|------|----------|------|---------|---------|")
        for i, (sym, score) in enumerate(short_signals[:5], 1):
            delta = sym_deltas.get(sym) if has_delta else None
            delta_str = f"{delta:+.4f}" if delta is not None else "N/A"
            if abs(score) >= 0.60:
                size = "15-20%"
                condition = "开盘直接建仓"
            elif abs(score) >= 0.40:
                size = "10-15%"
                condition = "开盘建仓，观察 30 分钟"
            elif abs(score) >= 0.15:
                size = "5-10%"
                condition = "等待盘中确认方向"
            else:
                size = "0%"
                condition = "不交易"
            name = name_fn(sym)
            w(f"| {i} | {sym} | {name} | {score:+.4f} | {delta_str} | {size} | {condition} |")
        w()
    else:
        w("- 当前无做空信号。")
        w()

    w("### 8.2 做多计划（对冲/辅助）")
    w()
    if long_signals:
        w("| 优先级 | 品种 | 名称 | 信号强度 | 增量 | 建议仓位 | 开仓条件 |")
        w("|--------|------|------|----------|------|---------|---------|")
        for i, (sym, score) in enumerate(long_signals[:3], 1):
            delta = sym_deltas.get(sym) if has_delta else None
            delta_str = f"{delta:+.4f}" if delta is not None else "N/A"
            if abs(score) >= 0.60:
                size = "15-20%"
                condition = "开盘直接建仓"
            elif abs(score) >= 0.40:
                size = "10-15%"
                condition = "开盘建仓，观察 30 分钟"
            elif abs(score) >= 0.15:
                size = "5-10%"
                condition = "等待盘中确认方向"
            else:
                size = "0%"
                condition = "不交易"
            name = name_fn(sym)
            w(f"| {i} | {sym} | {name} | {score:+.4f} | {delta_str} | {size} | {condition} |")
        w()
    else:
        w("- 当前无做多信号。")
        w()

    # 总仓位汇总
    total_short_pct = 0
    for _, score in short_signals[:5]:
        if abs(score) >= 0.60:
            total_short_pct += 17.5
        elif abs(score) >= 0.40:
            total_short_pct += 12.5
        elif abs(score) >= 0.15:
            total_short_pct += 7.5
    total_long_pct = 0
    for _, score in long_signals[:3]:
        if abs(score) >= 0.60:
            total_long_pct += 17.5
        elif abs(score) >= 0.40:
            total_long_pct += 12.5
        elif abs(score) >= 0.15:
            total_long_pct += 7.5

    w("### 8.3 仓位汇总")
    w()
    w("| 方向 | 品种数 | 总仓位估值 |")
    w("|------|--------|-----------|")
    w(f"| 做空 | {min(len(short_signals), 5)} 个 | {total_short_pct:.0f}% |")
    w(f"| 做多 | {min(len(long_signals), 3)} 个 | {total_long_pct:.0f}% |")
    w(f"| **合计** | — | **{total_short_pct + total_long_pct:.0f}%** |")
    if total_short_pct + total_long_pct > 100:
        w("> ⚠ 总仓位超过 100%，建议缩减至 80% 以内。按信号强度从低到高依次取消。")
    w()

    # ── 9. 执行检查清单 ──
    w("## 9. 执行检查清单")
    w()
    w("- [ ] 确认主力合约有足够流动性（持仓量 > 1 万手）")
    w("- [ ] 设置止损单（入场价 ± 2% ATR）")
    w("- [ ] 记录每笔交易的开仓时间、价格、仓位")
    w("- [ ] 设置止盈条件（盈利达 ATR 3 倍后移动止损至成本）")
    w("- [ ] 检查同板块品种合计仓位不超过 30%")
    w("- [ ] 检查净敞口（多空相抵后）不超过总资金 50%")
    w("- [ ] 设置收盘前 15 分钟检查提醒（是否需要隔夜持仓）")
    w()

    # ── 写入文件 ──
    out_path = report_dir / f"trading_advice_{today}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 交易建议报告已保存: {out_path}")


def main(
    max_symbols: int = 25,
    days: int = 120,
    universe: str = "core",
    macro_injection: bool = True,
    chain: str = "",
    force_regime: str = "",
) -> int:
    t0 = time.time()
    today = date.today().isoformat()
    print("=" * 60)
    title = "期货信号生成管道 v5 (多空双向 + 信号增量)" if not chain else "能源产业链信号生成管道 (链专属工作流)"
    print(f"  {title} — {today}")
    print("=" * 60)

    # ── Step 1: 加载因子（因子选择与基础权重权威源） ──
    # 通用模式 = L3 组合（factor_weights.json，严格模式）；
    # 链模式 = 链级 L3 组合（memory/portfolio/energy/factor_weights.json，
    #   v2.104.0+39 起 active）：因子选择与基础权重由链级 L3 组合决定，
    #   按名从能源库加载因子定义，不再全量精英因子等权。
    if chain == "energy":
        from fts.data_futures import ENERGY_CHAIN_MARKET
        from fts.factor_engine.factor_db.schema import get_db_path

        l3_weights = _load_l3_combo_weights(
            weights_path=PROJECT_ROOT / "memory" / "portfolio" / "energy" / "factor_weights.json"
        )
        factors = _load_l3_combo_factors(
            l3_weights,
            market=ENERGY_CHAIN_MARKET,
            db_path=Path(get_db_path(ENERGY_CHAIN_MARKET)),
        )
        kept_names = {f.get("name") for f in factors}
        l3_weights = {k: v for k, v in l3_weights.items() if k in kept_names}
        print(f"\n[1/5] 加载能源链 L3 组合因子: {len(factors)} 个（基础权重 {len(l3_weights)} 个）")
    else:
        l3_weights = _load_l3_combo_weights()
        factors = _load_l3_combo_factors(l3_weights)
        # 同步剔除 DuckDB 中缺失因子的权重（与因子池保持一致）
        kept_names = {f.get("name") for f in factors}
        l3_weights = {k: v for k, v in l3_weights.items() if k in kept_names}
        print(f"\n[1/5] 加载 L3 组合因子: {len(factors)} 个（基础权重 {len(l3_weights)} 个）")

    if not factors:
        print("[ERROR] 无 L3 组合因子，退出")
        return 1

    # ── Step 2: 获取期货数据 ──
    from fts.data import FTSDataProvider
    from fts.data_futures import FUTURES_CORE_SUBSET, FUTURES_HOLDOUT, FUTURES_SUBSET

    provider = FTSDataProvider()

    if chain == "energy":
        # 能源链专属：9 训练 + 其余化工产业链盲测（泛化到全化工链）
        from fts.data_futures import ENERGY_CHAIN_HOLDOUT, ENERGY_CHAIN_SYMBOLS

        symbols = list(ENERGY_CHAIN_SYMBOLS) + list(ENERGY_CHAIN_HOLDOUT)
        print(f"[2/4] 获取期货数据: 能源链训练 {len(ENERGY_CHAIN_SYMBOLS)} + 化工盲测 {len(ENERGY_CHAIN_HOLDOUT)} = {len(symbols)} 个品种, days={days}")
    elif universe == "all":
        # 全量商品期货：FUTURES_SUBSET 剔除中金所金融期货
        FINANCIAL = {"IF0", "TF0", "IH0", "IC0", "TS0", "IM0"}
        symbols = [s for s in FUTURES_SUBSET if s not in FINANCIAL][:max_symbols]
        print(f"[2/4] 获取期货数据: 全量商品 {len(symbols)} 个品种, days={days}")
    else:
        symbols = FUTURES_CORE_SUBSET[:max_symbols]
        print(f"[2/4] 获取期货数据: {len(symbols)} 个品种, days={days}")

    panel, common_dates = provider.get_futures_panel(
        symbols=symbols,
        days=days,
    )
    print(f"      面板: {len(panel)} 个品种, {len(common_dates)} 个交易日")

    if not panel:
        print("[ERROR] 无数据，退出")
        return 1

    # 过滤数据陈旧的品种（最新交易日不在共同日期末端，如已停更品种）
    if len(common_dates) > 0:
        last_common = common_dates[-1]
        stale = [sym for sym, df in panel.items() if df.index[-1] < last_common]
        for sym in stale:
            panel.pop(sym)
        if stale:
            print(f"      [提示] 剔除 {len(stale)} 个停更/陈旧品种: {', '.join(stale)} (数据止于共同交易日之前)")

    # ── Step 2c: 宏观字段注入（GAP-088 v2.103.0 信号管道接线）──
    # fut_macro_cpi/interest_rate/export/us_bond 等宏观因子读取 export/
    # import_data/cpi/rate/us_bond 5 列真实数据；失败降级不阻断（close 代理）
    panel = _inject_macro_to_panel(panel, enabled=macro_injection, trace_id=f"futures_signal_{today}")
    n_macro = sum(
        1 for df in panel.values() if df is not None and not df.empty and "export" in df.columns
    )
    if macro_injection:
        print(f"      [宏观注入] 宏观字段已注入: {n_macro}/{len(panel)} 个品种")

    # ── Step 2b: 产业链级 Market Regime 检测 ──
    from fts.factor_engine.regime import SectorRegimeSelector
    from fts.data_futures import FUTURES_SECTOR_MAP

    sector_selector = SectorRegimeSelector(lookback_days=60)
    # 用 panel 中实际存在的品种构建 sector_map
    active_sector_map: dict[str, list[str]] = {}
    for sector, syms in FUTURES_SECTOR_MAP.items():
        active = [s for s in syms if s in panel]
        if len(active) >= 2:
            active_sector_map[sector] = active

    if active_sector_map:
        sector_regimes = sector_selector.detect_all(panel, sector_map=active_sector_map)
    else:
        sector_regimes = {}

    # ── 品种-链对齐度计算 ──
    alignment_scores = sector_selector.compute_alignment(panel, sector_regimes, sector_map=active_sector_map)
    n_aligned = sum(1 for v in alignment_scores.values() if v >= 0.7)
    n_misaligned = sum(1 for v in alignment_scores.values() if v < 0.5)
    if alignment_scores:
        print(
            f"\n[对齐度] 品种-链对齐度: {len(alignment_scores)} 个品种, "
            f"高对齐(≥0.7): {n_aligned}, 低对齐(<0.5): {n_misaligned}"
        )

    def _compute_primary_regime(
        sr: dict[str, dict],
        asm: dict[str, list[str]],
    ) -> dict:
        """从各产业链 regime 计算主制度（P1-2：软投票 = 品种数 × sector 置信度）。

        修复原硬投票（仅品种数）缺陷：对"勉强判 bear (conf=50%)"的 sector 与
        "强判 bear (conf=100%)"的 sector 一视同仁给满票，推高主制度假置信度。
        现按 sector 置信度打折，主制度置信度 = 软票占比，语义更诚实。
        同时剔除"合成身份分组"（炼化聚酯链：训练池并集，品种被四真子链重复覆盖，
        若参与投票会把每个品种重复计票 → 用显式名单剔除，防集合启发式误伤能源等真子链）。
        """
        if not sr or not asm:
            return {"regime": "unknown", "confidence": 0.0, "detected_at": datetime.now().isoformat(), "features": {}}
        regime_votes: dict[str, float] = {}
        vote_log: dict[str, dict] = {}
        skipped_identity: list[str] = []
        # 炼化聚酯链 = 训练池并集身份分组（能源3+聚酯3+油化3+煤化3），其品种与四真子链完全重复
        _IDENTITY_SECTORS = {"炼化聚酯链"}
        for sector, regime in sr.items():
            if sector in _IDENTITY_SECTORS:
                skipped_identity.append(sector)
                continue
            r = regime["regime"]
            c = float(regime.get("confidence", 0.5))
            n_syms = len(asm.get(sector, []))
            # 软票 = 品种数 × 置信度（低置信度判定打折；置信度 0.05 下限防零票）
            votes = n_syms * max(0.05, c)
            regime_votes[r] = regime_votes.get(r, 0) + votes
            vote_log[sector] = {"regime": r, "confidence": round(c, 4), "n_syms": n_syms, "votes": round(votes, 4)}
        total = sum(regime_votes.values())
        if total <= 1e-12:
            return {"regime": "unknown", "confidence": 0.0, "detected_at": datetime.now().isoformat(), "features": {}}
        sorted_regimes = sorted(regime_votes.items(), key=lambda x: -x[1])
        primary = sorted_regimes[0][0]
        primary_weight = sorted_regimes[0][1] / total
        return {
            "regime": primary,
            "confidence": round(primary_weight, 4),
            "detected_at": datetime.now().isoformat(),
            "features": {
                "sector_breakdown": {s: sr[s]["regime"] for s in sr},
                "sector_confidences": {s: sr[s]["confidence"] for s in sr},
                "sector_vote_log": vote_log,
                "skipped_identity_sectors": skipped_identity,
                "primary_regime_weight": round(primary_weight, 4),
            },
        }

    market_regime = _compute_primary_regime(sector_regimes, active_sector_map)

    # ── force-regime 覆盖（用于验证 Regime 对输出的实际影响） ──
    if force_regime and force_regime in {"bull", "bear", "oscillate", "high_vol", "low_vol"}:
        original_regime = market_regime.get("regime", "unknown")
        original_conf = market_regime.get("confidence", 0.0)
        features = market_regime.setdefault("features", {})
        features["forced_regime"] = True
        features["original_regime"] = original_regime
        features["original_confidence"] = original_conf
        market_regime["regime"] = force_regime
        market_regime["confidence"] = 1.0
        print(
            f"\n[Regime] ⚠️  强制覆盖: {original_regime}({original_conf:.0%}) → "
            f"{force_regime}(100%) [force-regime 验证模式]"
        )

    _REGIME_LABELS = {
        "bull": "趋势上涨 (bull)",
        "bear": "趋势下跌 (bear)",
        "high_vol": "高波动 (high_vol)",
        "low_vol": "低波动 (low_vol)",
        "oscillate": "震荡 (oscillate)",
        "unknown": "未知",
    }
    regime_label = _REGIME_LABELS.get(market_regime["regime"], market_regime["regime"])
    features = market_regime.get("features", {})
    sector_breakdown = features.get("sector_breakdown", {})
    sector_confidences = features.get("sector_confidences", {})
    print(f"\n[Regime] 主市场制度: {regime_label} (品种数加权)")
    print(f"         置信度: {market_regime['confidence']:.2%}")
    if sector_breakdown:
        print("         产业链 Breakdown:")
        for sector, r in sorted(sector_breakdown.items()):
            c = sector_confidences.get(sector, 0)
            print(f"           {sector}: {r} (conf={c:.2%})")

    # ── Step 3: 计算信号 ──
    n_factors = len(factors)
    print(f"\n[3/5] 计算信号 ({n_factors} 因子 × {len(panel)} 品种)...")

    # 3a: 一次性计算所有因子×品种的信号矩阵
    signal_matrix = _compute_signal_matrix(panel, factors, use_optimizer=True)
    print(f"      信号矩阵: {sum(len(v) for v in signal_matrix.values())} 项")

    # 3b-3f: L3 组合基础权重 + Regime 档位调整 + 品种级 IC 自适应 + 加权合成
    # v2.105.0: 移除 Ridge 权重自训与截面 IC 方向校正（方向以 L3 组合为准），
    # 因子集合与基础权重来自 L3 组合；品种级 IC 自适应保留。
    factor_sign_flips: dict[str, float] = {}  # 方向校正已移除，统一 +1

    # 3b: L3 组合基础权重（归一化到和=1）
    total_base = sum(l3_weights.values()) or 1.0
    factor_weights = {k: v / total_base for k, v in l3_weights.items()}

    # 3c: Regime 档位缩放权重调整（信号管道唯一权重干预：缩放不丢弃因子）
    factor_weights = _apply_regime_weight_adjustment(factor_weights, market_regime, factors)

    # 3d: 品种-因子 IC 矩阵（每个因子 × 每个品种的时序 IC，无方向校正）
    print("      品种-因子 IC 矩阵计算...")
    per_variety_ic = _compute_per_variety_ic_matrix(
        signal_matrix,
        panel,
        common_dates,
        factor_sign_flips,
    )
    n_factor_ic = len(per_variety_ic)
    n_variety_ic = len(set(v for vics in per_variety_ic.values() for v in vics)) if per_variety_ic else 0
    print(f"      IC 矩阵: {n_factor_ic} 因子 × {n_variety_ic} 品种")

    # 3e: 品种级权重（L3 基础权重经 Regime 调整 × 品种级 IC 自适应，保留）
    per_variety_weights = _compute_per_variety_weights(
        factor_weights,
        per_variety_ic,
    )

    if per_variety_weights:
        # 统计品种级权重相对于全局权重的平均偏离度
        total_dev = 0.0
        count = 0
        for var, vw in per_variety_weights.items():
            for fname, w in vw.items():
                gw = factor_weights.get(fname, 0)
                if gw > 0:
                    total_dev += abs(w - gw)
                    count += 1
        avg_dev = total_dev / count if count > 0 else 0
        print(f"      品种级权重: {len(per_variety_weights)} 个品种, 平均偏离度: {avg_dev:.4f}")
    else:
        print("      品种级权重: 无数据，回退到全局权重")

    # 3e2: 品种级方向翻转（IC 符号校准，P0 修复）
    # 品种级权重用 abs(IC) 丢弃了符号：反向因子（如 fut_ma_crossover_simplified
    # 在 20/20 品种上 IC 全负）的正信号被当作正贡献累加 → 综合得分恒正、
    # bear 制度下永远无空头。此处按 IC 符号恢复每个 (品种×因子) 的方向。
    per_variety_sign_flips: dict[str, dict[str, float]] = {}
    for _fname, _vics in per_variety_ic.items():
        for _var, _ic in _vics.items():
            per_variety_sign_flips.setdefault(_var, {})[_fname] = 1.0 if _ic >= 0 else -1.0
    n_flipped = sum(1 for v in per_variety_sign_flips.values() for f in v.values() if f < 0)
    if n_flipped:
        print(f"      [IC 方向] 品种级方向翻转: {n_flipped} 个 (因子×品种) 组合 IC<0，信号符号已反转")

    # 3f: 加权合成（L3 基础权重经 Regime 调整 + 品种级权重 + 品种级方向翻转）
    sym_scores, sym_details = _compute_composite_scores(
        signal_matrix,
        factor_sign_flips,
        factors,
        factor_weights,
        per_variety_weights=per_variety_weights if per_variety_weights else None,
        per_variety_sign_flips=per_variety_sign_flips or None,
    )

    # ── 品种-链对齐度修正信号权重 ──
    _ALIGNMENT_BLEND = 0.20  # 对齐度修正强度 (0.0=关闭, 0.3=最大)
    n_adjusted_align = 0
    if _ALIGNMENT_BLEND > 0 and alignment_scores:
        n_adjusted_align = 0
        for sym in list(sym_scores.keys()):
            align = alignment_scores.get(sym, 0.5)
            # 对齐度偏离 0.5 越大，修正越强
            alignment_factor = 1.0 + _ALIGNMENT_BLEND * (align - 0.5)
            sym_scores[sym] *= alignment_factor
            n_adjusted_align += 1
        print(f"      [对齐度] 应用品种-链对齐度修正: {n_adjusted_align} 个品种, blend={_ALIGNMENT_BLEND}")

    # 3g: 对比全局权重 vs 品种级权重的合成结果（两侧同用品种级方向翻转，保证 ρ 仅反映权重差异）
    if per_variety_weights:
        sym_scores_global, _ = _compute_composite_scores(
            signal_matrix,
            factor_sign_flips,
            factors,
            factor_weights,
            per_variety_sign_flips=per_variety_sign_flips or None,
        )
        # 计算两种合成结果的排名差异
        sorted_var = sorted(sym_scores.keys())
        var_global = [(s, sym_scores_global.get(s, 0)) for s in sorted_var]
        var_variety = [(s, sym_scores.get(s, 0)) for s in sorted_var]
        from scipy.stats import spearmanr

        g_scores = [v for _, v in var_global]
        v_scores = [v for _, v in var_variety]
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            try:
                from scipy.stats import ConstantInputWarning

                warnings.filterwarnings("ignore", category=ConstantInputWarning)
            except ImportError:
                pass
            rank_corr, _ = spearmanr(g_scores, v_scores)
        print(f"      品种级 vs 全局排名一致性: Spearman ρ={rank_corr:.4f}")

    elapsed = time.time() - t0
    print(f"\n  耗时: {elapsed:.1f}s, 成功: {len(sym_scores)} 个品种")

    # ── Step 3h: 实时价格动量叠加 — 修复信号"僵化"问题 ──
    # 因子信号基于 120 天日线面板数据，sig[-1] 为最新日线收盘价，
    # 120 天窗口滚动加 1 天新数据，因子值变化仅 ~0.8%，
    # 导致信号对盘中大幅波动不敏感（如黄金今日大涨但信号几乎不变）。
    # 此处用实时价计算盘中涨跌幅，叠加到综合信号上。
    _PRICE_MOMENTUM_BLEND = 0.15  # 动量权重 (0.0=关闭, 0.3=最大)
    _PRICE_MOMENTUM_CLIP = 3.0  # 标准化回报的截断边界
    if _PRICE_MOMENTUM_BLEND > 0:
        # 1) 计算各品种的典型波动率
        typical_vols: dict[str, float] = {}
        for sym, df in panel.items():
            if df is None or df.empty or len(df) < 20:
                continue
            returns = df["close"].pct_change().dropna().values
            if len(returns) > 0:
                typical_vols[sym] = max(float(np.nanstd(returns)), 1e-8)
        # 2) 获取实时价
        try:
            from fts.data_futures import get_realtime_prices

            rt_prices = get_realtime_prices(list(sym_scores.keys()))
        except Exception:
            rt_prices = {}
        # 3) 动量调整
        n_adjusted = 0
        n_skipped = 0
        for sym in list(sym_scores.keys()):
            df = panel.get(sym)
            if df is None or df.empty:
                n_skipped += 1
                continue
            latest_close = float(df["close"].iloc[-1])
            rt_price = rt_prices.get(sym)
            if rt_price is None or latest_close <= 0:
                n_skipped += 1
                continue
            intraday_return = (rt_price - latest_close) / latest_close
            if abs(intraday_return) < 1e-8:
                n_skipped += 1
                continue
            vol = typical_vols.get(sym, 0.01)
            norm_return = np.clip(intraday_return / vol, -_PRICE_MOMENTUM_CLIP, _PRICE_MOMENTUM_CLIP)
            # 正回报 → 提高得分（减弱空头信号）
            adjustment = _PRICE_MOMENTUM_BLEND * norm_return
            sym_scores[sym] += adjustment
            n_adjusted += 1
        if n_adjusted > 0:
            print(f"      [价格动量] 调整 {n_adjusted} 个品种的信号 (blend={_PRICE_MOMENTUM_BLEND}, skip={n_skipped})")

    # ── Step 3h2: Regime 方向偏移（P0 修复：主制度置信度越高，多空倾向越明显）
    # 放在价格动量之后、快照/判定之前：快照与报告保存一致的最终得分。
    # 注意：盲测 IC 由 signal_matrix 原始信号计算，不受此处偏移影响（诊断口径保持纯净）。
    regime_type = market_regime.get("regime", "unknown")
    sym_scores, _regime_bias = _apply_regime_direction_bias(
        sym_scores,
        regime_type,
        market_regime.get("confidence", 0.0),
    )
    if _regime_bias > 0:
        _bias_sign = "+" if regime_type == "bull" else "-"
        print(
            f"      [Regime 方向] {regime_type}: 综合得分 ×(1{_bias_sign}{_regime_bias:.3f}) "
            f"(bias={_regime_bias:.1%}, conf={market_regime.get('confidence', 0.0):.0%})"
        )

    # ── Step 3e: 盲测品种验证（泛化能力检查） ──
    # 链模式盲测池 = 其余化工产业链品种（链外盲测，泛化到全化工链）
    if chain == "energy":
        from fts.data_futures import ENERGY_CHAIN_HOLDOUT as _CHAIN_HOLDOUT

        holdout_set = set(_CHAIN_HOLDOUT) & set(panel.keys())
    else:
        holdout_set = set(FUTURES_HOLDOUT) & set(panel.keys())
    holdout_result = _compute_holdout_validation(
        signal_matrix,
        panel,
        common_dates,
        factor_sign_flips,
        holdout_set,
    )
    print(
        f"\n[盲测验证] 盲测品种: {len(holdout_set)} 个, "
        f"有效: {holdout_result['n_holdout_valid']}/{holdout_result['n_train_valid']} (盲测/训练)"
    )
    print(
        f"          盲测平均 IC: {holdout_result['holdout_ic']:.4f}, "
        f"训练平均 IC: {holdout_result['train_ic']:.4f}, "
        f"保持率: {holdout_result['ic_retention']:.1%}"
    )
    if holdout_result["warning"]:
        print(f"          {holdout_result['warning']}")

    # 链模式控制台：盲测池化工链分层（外延泛化差异）
    if chain == "energy" and holdout_result.get("details"):
        from fts.data_futures import ENERGY_CHAIN_CHEMICAL_SECTORS, FUTURES_SECTOR_MAP

        _sym_ics = holdout_result["details"]
        for sec in ENERGY_CHAIN_CHEMICAL_SECTORS:
            members = [
                s for s in sorted(set(FUTURES_SECTOR_MAP.get(sec, [])) & holdout_set)
                if s in _sym_ics
            ]
            ics = [_sym_ics[s] for s in members]
            avg = float(np.mean(ics)) if ics else 0.0
            print(f"          化工链分层 [{sec}]: 有效 {len(ics)}/{len(members)} 品种, 平均 IC {avg:.4f}")

    # ── Step 4: 保存信号快照 + 加载昨日信号计算增量 ──
    chain_report_root = "energy_chain" if chain == "energy" else "futures"
    report_dir = REPORTS_ROOT / chain_report_root / today
    report_dir.mkdir(parents=True, exist_ok=True)

    # 因子组合签名（跨日增量可比性校验；因子集合变化 → 增量标记无效）
    factor_signature = _factor_set_signature(factors)

    # 保存今日信号快照 (JSON)，附带因子组合签名与得分语义说明
    snapshot_payload = {
        "date": today,
        "scores": sym_scores,
        "factor_signature": factor_signature,
        "semantics": _SIGNAL_SCORE_SEMANTICS,
    }
    snapshot_path = report_dir / "signal_scores.json"
    snapshot_path.write_text(
        json.dumps(snapshot_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 追加到历史 JSONL
    history_path = REPORTS_ROOT / chain_report_root / "signal_scores_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "a", encoding="utf-8") as hf:
        hf.write(json.dumps(snapshot_payload, ensure_ascii=False) + "\n")

    # 加载昨日信号快照，计算增量（跨因子组合校验，组合不一致 → 增量无效）
    prev_snapshot: dict[str, Any] | None = None
    try:
        yesterday_snapshot = report_dir.parent / _yesterday_str() / "signal_scores.json"
        if yesterday_snapshot.exists():
            prev_snapshot = json.loads(yesterday_snapshot.read_text(encoding="utf-8"))
    except Exception:
        prev_snapshot = None
    sym_deltas, prev_scores, has_delta, delta_skip_reason = _compute_signal_deltas(
        sym_scores, prev_snapshot, factor_signature
    )
    if delta_skip_reason:
        print(f"      [增量] {delta_skip_reason}")

    # ── Step 5: 输出信号排名 ──
    if not sym_scores:
        print("[ERROR] 无有效信号")
        return 1

    # 多空双向排名：按信号强度（绝对值）排序
    ranked = sorted(sym_scores.items(), key=lambda x: -abs(x[1]))
    # P0 修复：按主制度取多空阈值（bear 顺势做空/做多门槛抬高、bull 反之），
    # 替换 v2.105.0 硬编码 >0/<0（该阈值导致 bull/bear 判定完全相同）。
    _thr = _REGIME_SIGNAL_THRESHOLDS.get(regime_type, _REGIME_SIGNAL_THRESHOLDS["unknown"])
    long_signals = [(s, sc) for s, sc in ranked if sc > _thr["long_min"]]
    short_signals = [(s, sc) for s, sc in ranked if sc < _thr["short_max"]]

    # 4a: 品种元数据（名称 / 主力合约 / 盘中实时价）
    from fts.data_futures import (
        FUTURES_SECTOR_MAP,
        FUTURES_SYMBOL_NAMES,
        get_dominant_contracts,
        get_realtime_prices,
    )

    # 品种 → 产业链 反向映射（后序覆盖前序，与通用中性化语义一致）
    symbol_sector: dict[str, str] = {}
    for _sector, _syms in FUTURES_SECTOR_MAP.items():
        for _sym in _syms:
            symbol_sector[_sym] = _sector

    sym_list = [s for s, _ in ranked]
    dominant = get_dominant_contracts(sym_list)
    print("      获取盘中实时价（TQ-Local 优先 → AKShare 降级）...")
    rt_prices = get_realtime_prices(sym_list)
    rt_hit = len(rt_prices)
    print(f"      实时价: {rt_hit}/{len(sym_list)} 个品种可用")

    def _name(sym: str) -> str:
        return FUTURES_SYMBOL_NAMES.get(sym, sym)

    def _contract(sym: str) -> str:
        return dominant.get(sym, "")

    def _price(sym: str, df) -> float:
        # 优先盘中实时价，缺失则用面板最新收盘价
        if sym in rt_prices:
            return rt_prices[sym]
        return df.iloc[-1]["close"] if df is not None and not df.empty else 0.0

    # 控制台输出 — 多空双向
    header = f"{'排名':>4s} {'品种':>6s} {'名称':>8s} {'产业链':>8s} {'主力合约':>9s} {'得分':>10s} {'实时价':>10s} {'Top因子':>28s}"
    sep = f"{'-' * 4} {'-' * 6} {'-' * 8} {'-' * 8} {'-' * 9} {'-' * 10} {'-' * 10} {'-' * 28}"

    def _print_signal_rows(signals, label, show_n=20):
        if not signals:
            print(f"\n  [{label}] 无信号")
            return
        print(f"\n{'=' * 76}")
        print(f"  {label} (按信号强度排序)")
        print(f"{'=' * 76}")
        print(header)
        print(sep)
        for i, (sym, score) in enumerate(signals[:show_n], 1):
            df = panel.get(sym)
            price = _price(sym, df)
            details = sym_details.get(sym, {})
            top_factors = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
            top_str = ", ".join(f"{n}({v:+.3f})" for n, v in top_factors)
            print(
                f"{i:>4d} {sym:>6s} {_name(sym):>8s} {symbol_sector.get(sym, '-'):>8s} "
                f"{_contract(sym):>9s} {score:>+10.4f} {price:>10.2f} {top_str:<28s}"
            )

    print(f"\n  [信号阈值] 主制度 {regime_type}: 做多 > {_thr['long_min']:.2f} / 做空 < {_thr['short_max']:.2f}")
    _print_signal_rows(long_signals, "多头信号 (做多)")
    _print_signal_rows(short_signals, "空头信号 (做空)")

    # 信号增量控制台输出
    if has_delta:
        delta_ranked = sorted(sym_deltas.items(), key=lambda x: x[1])
        accel = [(s, d) for s, d in delta_ranked if d < 0][:5]
        decel = [(s, d) for s, d in delta_ranked if d > 0][:5]
        print(f"\n  [信号增量] 空头加速 Top5: {', '.join(f'{s}({d:+.3f})' for s, d in accel)}")
        print(f"  [信号增量] 减速/反转 Top5: {', '.join(f'{s}({d:+.3f})' for s, d in decel)}")

    # ── Step 6: 写入 Markdown 报告 ──
    suffix = "_all_commodities" if universe == "all" else ""
    out_path = report_dir / f"futures_signals{suffix}_{today}.md"
    lines: list[str] = []

    def w(s=""):
        lines.append(s)

    w(f"# 期货信号报告 — {today}")
    w()
    w(f"生成时间: {today} | 耗时: {elapsed:.1f}s")
    w(f"因子池: {len(factors)} 个（L3 组合因子） | 覆盖品种: {len(sym_scores)} 个")
    if per_variety_weights:
        w("合成方法: 品种级权重（L3 组合基础权重 × Regime 档位调整 × 品种 IC 自适应）")
    else:
        w("合成方法: L3 组合基础权重（Regime 档位调整）")
    w("权重来源: L3 组合（factor_weights.json）| Regime 调整: 档位缩放（bull/bear/oscillate/high_vol）")
    w(f"最新价: 盘中实时价（TQ-Local 优先，AKShare 降级） | 实时价覆盖 {rt_hit}/{len(sym_list)} 个品种")
    w()
    w()
    w("## 信号语义说明")
    w()
    w("> 综合得分 = 品种级 IC 方向翻转（per_variety_sign_flips）后的**相对强弱评分**。")
    w("> **负分表示该品种因子读数相对过热（IC 反向修正后的均值回归预期），不等于趋势看空**；")
    w("> 正分表示相对走强。方向判定请结合 Market Regime 与原始因子读数，勿将得分直接解读为方向信号。")
    w("> 跨日信号增量仅在同一因子组合（factor_signature）下可比，因子组合变更时增量被标记无效。")
    w()
    w()
    w("## 市场制度 (Market Regime) — 产业链级")
    w()
    w(f"- **主制度** (品种数加权): {regime_label}")
    w(f"- **主制度置信度**: {market_regime['confidence']:.2%}")
    _thr_r = _REGIME_SIGNAL_THRESHOLDS.get(regime_type, _REGIME_SIGNAL_THRESHOLDS["unknown"])
    w(f"- **信号阈值** (主制度生效): 做多 > {_thr_r['long_min']:.2f} / 做空 < {_thr_r['short_max']:.2f}")
    if _regime_bias > 0:
        _bias_sign_r = "+" if regime_type == "bull" else "-"
        w(f"- **方向偏移**: {regime_type} 置信度 {market_regime['confidence']:.0%} → 综合得分 ×(1{_bias_sign_r}{_regime_bias:.3f})")
    if sector_breakdown:
        w()
        w("### 产业链 Breakdown")
        w()
        w("| 产业链 | 制度 | 置信度 | 品种数 | 方向建议 |")
        w("|--------|------|--------|--------|----------|")
        for sector in sorted(sector_breakdown.keys()):
            r = sector_breakdown[sector]
            c = sector_confidences.get(sector, 0)
            n_syms = len(active_sector_map.get(sector, []))
            if r in ("bull", "bear"):
                dir_advice = "顺势" if r == "bull" else "做空"
            elif r == "oscillate":
                dir_advice = "反向操作"
            elif r in ("high_vol",):
                dir_advice = "谨慎"
            else:
                dir_advice = "观望"
            w(f"| {sector} | {r} | {c:.1%} | {n_syms} | {dir_advice} |")
        w()
    w()
    w()

    # ── 品种-链对齐度 ──
    if alignment_scores:
        w("## 品种-链对齐度")
        w()
        w("> 对齐度 = 品种独立检测的制度与产业链综合制度的一致性评分。")
        w("> 高对齐度(≥0.7)的品种与产业链趋势一致，信号更可靠；")
        w("> 低对齐度(<0.5)的品种偏离产业链趋势，信号需降权处理。")
        w()
        w("| 对齐度等级 | 品种数 | 品种列表 |")
        w("|----------|--------|----------|")
        high_align = [(s, v) for s, v in alignment_scores.items() if v >= 0.7]
        mid_align = [(s, v) for s, v in alignment_scores.items() if 0.5 <= v < 0.7]
        low_align = [(s, v) for s, v in alignment_scores.items() if v < 0.5]
        if high_align:
            w(f"| 高对齐 (≥0.7) | {len(high_align)} | {', '.join(s for s, _ in high_align[:10])} |")
        if mid_align:
            w(f"| 中等对齐 (0.5~0.7) | {len(mid_align)} | {', '.join(s for s, _ in mid_align[:10])} |")
        if low_align:
            w(f"| 低对齐 (<0.5) | {len(low_align)} | {', '.join(s for s, _ in low_align[:10])} |")
        w()
        w(f"> 对齐度修正强度: {_ALIGNMENT_BLEND:.0%}，{n_adjusted_align} 个品种的权重已调整。")
        w()

    # ── Regime 调整后的交易建议 ──
    regime_type = market_regime["regime"]
    if regime_type in ("bull", "bear"):
        w("> **Regime 解读 (趋势友好)**")
        w("> 市场处于明确趋势中，优先做空/做多增量最强的品种，可适当放大仓位。")
        w("> 趋势延续概率高，逆势交易风险大。")
    elif regime_type == "oscillate":
        w("> **Regime 解读 (均值回归)**")
        w("> 市场处于震荡状态，反向操作更优：做空减速品种（即将反转），做多加速品种。")
        w("> 趋势持续性弱，应以区间交易为主。")
    elif regime_type in ("high_vol",):
        w("> **Regime 解读 (高波动/混沌)**")
        w("> 市场波动率异常偏高，缩小仓位，只做增量绝对值 > 0.15 的品种。")
        w("> 高波动环境下信号噪音大，需严格止损。")
    elif regime_type == "low_vol":
        w("> **Regime 解读 (低波动)**")
        w("> 市场波动率偏低，信号可信度较高，可正常仓位操作。")
        w("> 关注波动率突破信号，低波环境可能孕育趋势行情。")
    w()

    # ── 交易建议 ──
    w("## 交易建议")
    w()
    w("### 方向策略")
    w()
    if regime_type in ("bull", "bear"):
        direction = "做多" if regime_type == "bull" else "做空"
        w(f"- 当前处于 **{regime_label}** regime，建议 **顺势{direction}**")
        w("- 优先选择信号强度 > 0.60 的品种作为核心标的")
        if short_signals and regime_type == "bear":
            top_shorts = [f"{_name(s)}({s})" for s, _ in short_signals[:5]]
            w(f"- 空头核心标的: {', '.join(top_shorts)}")
        if long_signals and regime_type == "bull":
            top_longs = [f"{_name(s)}({s})" for s, _ in long_signals[:5]]
            w(f"- 多头核心标的: {', '.join(top_longs)}")
    elif regime_type == "oscillate":
        w("- 当前处于 **震荡** regime，建议 **反向操作**")
        w("- 做空信号加速品种（增量最负），做多信号减速品种（增量最正）")
        w("- 避免追已到极值但增量停滞的品种（趋势衰竭）")
    else:
        w("- 当前 regime 信号较弱，建议 **缩小仓位** 或 **观望**")
    w()

    w("### 仓位管理")
    w()
    w("- 按 **波动率目标化** 原则分配仓位（参考 Robert Carver 框架）")
    w("- 各品种按 ATR 等波动率标准化后等权分配")
    w("- 单一品种风险敞口不超过总资金 **2-3%**")
    w()

    w("### 风控红线")
    w()
    w("- **止损**: 每笔交易设置 ATR 2 倍止损")
    w("- **止盈**: 盈利达 ATR 3 倍后移动止损至成本")
    w("- **最大持仓**: 同时持仓不超过 8-10 个品种")
    w("- **相关性**: 同板块品种合计仓位不超过 30%")
    w()

    w("### 重点关注")
    w()
    # 空头加速品种
    if has_delta and sym_deltas:
        accel_syms = [(s, d) for s, d in sorted(sym_deltas.items(), key=lambda x: x[1]) if d < -0.02][:3]
        if accel_syms:
            accel_str = ", ".join(f"{_name(s)}(增量{d:+.3f})" for s, d in accel_syms)
            w(f"- **空头加速**: {accel_str} — 做空优先关注")
        decel_syms = [(s, d) for s, d in sorted(sym_deltas.items(), key=lambda x: -x[1]) if d > 0.02][:3]
        if decel_syms:
            decel_str = ", ".join(f"{_name(s)}(增量{d:+.3f})" for s, d in decel_syms)
            w(f"- **空头减速/反转萌芽**: {decel_str} — 做多关注")
    w()
    w()
    # ── 盲测品种验证 ──
    w("## 盲测品种验证 (泛化能力)")
    w()
    w(f"盲测品种: {len(holdout_set)} 个 ({', '.join(sorted(holdout_set))})")
    w()
    w(
        f"盲测 IC: {holdout_result['holdout_ic']:.4f} | 训练 IC: {holdout_result['train_ic']:.4f} | "
        f"保持率: {holdout_result['ic_retention']:.1%}"
    )
    if holdout_result["warning"]:
        w(f"\n> {holdout_result['warning']}")
    # 盲测品种 IC 详情
    sym_ics = holdout_result.get("details", {})
    holdout_sym_ics = {s: sym_ics.get(s, 0) for s in holdout_set if s in sym_ics}
    if holdout_sym_ics:
        w()
        w("| 盲测品种 | IC | 判断 |")
        w("|----------|----|------|")
        holdout_result["train_ic"]
        for sym, ic in sorted(holdout_sym_ics.items(), key=lambda x: -abs(x[1])):
            verdict = "✔ 有效" if abs(ic) > 0.02 else "⚠ 偏弱" if abs(ic) > 0.01 else "❌ 失效"
            w(f"| {sym} | {ic:.4f} | {verdict} |")
    w()

    # 链模式：盲测池按化工产业链分层（聚酯链/油化工/煤化工），评估外延泛化差异
    if chain == "energy" and holdout_sym_ics:
        from fts.data_futures import (
            ENERGY_CHAIN_CHEMICAL_SECTORS,
            FUTURES_SECTOR_MAP,
        )

        w("### 盲测池化工链分层泛化")
        w()
        w("> 链因子向不同化工子链的外延泛化能力：分层 IC 越低、离能源链越远的子链泛化衰减越明显。")
        w()
        w("| 化工子链 | 盲测品种数 | 有效品种数 | 平均 IC | 有效占比 |")
        w("|----------|-----------|-----------|---------|---------|")
        chain_sec_syms: dict[str, set[str]] = {}
        for sec in ENERGY_CHAIN_CHEMICAL_SECTORS:
            chain_sec_syms[sec] = set(FUTURES_SECTOR_MAP.get(sec, [])) & holdout_set
        for sec in ENERGY_CHAIN_CHEMICAL_SECTORS:
            members = [s for s in sorted(chain_sec_syms[sec]) if s in holdout_sym_ics]
            ics = [holdout_sym_ics[s] for s in members]
            avg = float(np.mean(ics)) if ics else 0.0
            n_total = len(chain_sec_syms[sec])
            n_valid = len(ics)
            ratio = f"{n_valid / n_total:.0%}" if n_total else "—"
            w(f"| {sec} | {n_total} | {n_valid} | {avg:.4f} | {ratio} |")
        w()

    # ── 品种-因子 IC 矩阵概览 ──
    w("## 品种-因子有效性矩阵 (IC)")
    w()
    w("> 每个因子 × 每个品种的时序 Spearman IC，反映因子在各品种上的预测有效性。")
    w("> 品种级权重基于此矩阵调整，使每个品种更依赖对其有效的因子。")
    w()
    if per_variety_ic:
        # 按品种聚合：每个品种 Top 3 最有效因子
        # 先转置：品种 → {因子: IC}
        variety_factor_ic: dict[str, dict[str, float]] = {}
        for fname, vics in per_variety_ic.items():
            for var, ic_val in vics.items():
                if var not in variety_factor_ic:
                    variety_factor_ic[var] = {}
                variety_factor_ic[var][fname] = ic_val

        w("### 各品种 Top 3 最有效因子")
        w()
        w("| 品种 | Top 1 因子 | IC | Top 2 因子 | IC | Top 3 因子 | IC |")
        w("|------|-----------|----|-----------|----|-----------|----|")
        for var in sorted(variety_factor_ic.keys()):
            # 过滤掉 NaN IC 值
            valid_ics = [(f, ic) for f, ic in variety_factor_ic[var].items() if np.isfinite(ic)]
            top3 = sorted(valid_ics, key=lambda x: -abs(x[1]))[:3]
            # 构建表格行: 1 (品种) + 3 (因子名+IC 对) = 4 个字符串段
            row = [f"| {var} "]
            for fname, ic_val in top3:
                row.append(f"| {fname} | {ic_val:.4f} ")
            # 补足到 3 个因子段（保持表格列数一致）
            while len(row) < 4:
                row.append("| — | — ")
            row.append("|")
            w("".join(row))
        w()

        # 全局最有效的因子（按在所有品种上的平均 |IC| 排序）
        w("### 跨品种最有效因子 Top 10")
        w()
        factor_avg_ic: dict[str, float] = {}
        for fname, vics in per_variety_ic.items():
            ics = [abs(v) for v in vics.values() if np.isfinite(v)]
            if ics:
                factor_avg_ic[fname] = float(np.mean(ics))
        if factor_avg_ic:
            w("| 排名 | 因子名称 | 平均 |IC| | 覆盖品种数 |")
            w("|------|----------|----------|----------|")
            for i, (fname, avg_ic) in enumerate(sorted(factor_avg_ic.items(), key=lambda x: -x[1])[:10], 1):
                n_vars = len(per_variety_ic.get(fname, {}))
                w(f"| {i} | {fname} | {avg_ic:.4f} | {n_vars} |")
            w()

        # 品种级权重 vs 全局权重对比
        if per_variety_weights:
            w("### 品种级权重 vs 全局权重偏离度")
            w()
            w("| 品种 | 最大偏离因子 | 偏离幅度 | 权重变化摘要 |")
            w("|------|-------------|----------|-------------|")
            for var in sorted(per_variety_weights.keys()):
                vw = per_variety_weights[var]
                # 找偏离最大的因子
                max_dev = 0.0
                max_dev_fname = ""
                changes: list[str] = []
                for fname, w_val in vw.items():
                    gw = factor_weights.get(fname, 0)
                    dev = abs(w_val - gw)
                    if dev > max_dev:
                        max_dev = dev
                        max_dev_fname = fname
                    if dev > 0.02:
                        direction = "↑" if w_val > gw else "↓"
                        changes.append(f"{fname}{direction}{dev:.3f}")
                if max_dev_fname:
                    change_summary = ", ".join(changes[:3])
                    w(f"| {var} | {max_dev_fname} | {max_dev:.4f} | {change_summary} |")
            w()

    w("## 多头信号 (做多) — Top 20")
    w()
    w("| 排名 | 品种 | 名称 | 产业链 | 主力合约 | 方向 | 信号强度 | 最新价 | Top 3 因子贡献 |")
    w("|------|------|------|--------|----------|------|----------|--------|----------------|")
    for i, (sym, score) in enumerate(long_signals[:20], 1):
        df = panel.get(sym)
        price = _price(sym, df)
        details = sym_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = " ".join(f"{n}({v:+.3f})" for n, v in top3)
        w(f"| {i} | {sym} | {_name(sym)} | {symbol_sector.get(sym, '-')} | {_contract(sym)} | 多 | {abs(score):.4f} | {price:.2f} | {top_str} |")
    if not long_signals:
        w("| — | — | 无多头信号 | — | — | — | — | — |")
    w()

    w("## 空头信号 (做空) — Top 20")
    w()
    w("| 排名 | 品种 | 名称 | 产业链 | 主力合约 | 方向 | 信号强度 | 最新价 | Top 3 因子贡献 |")
    w("|------|------|------|--------|----------|------|----------|--------|----------------|")
    for i, (sym, score) in enumerate(short_signals[:20], 1):
        df = panel.get(sym)
        price = _price(sym, df)
        details = sym_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = " ".join(f"{n}({v:+.3f})" for n, v in top3)
        w(f"| {i} | {sym} | {_name(sym)} | {symbol_sector.get(sym, '-')} | {_contract(sym)} | 空 | {abs(score):.4f} | {price:.2f} | {top_str} |")
    if not short_signals:
        w("| — | — | 无空头信号 | — | — | — | — | — |")
    w()

    # 信号分布
    scores = [s for _, s in ranked]
    abs_scores = [abs(s) for s in scores]
    w("## 信号分布")
    w()
    w(f"- 多头信号: {len(long_signals)} 个  |  空头信号: {len(short_signals)} 个")
    w(f"- 信号强度均值: {np.mean(abs_scores):.4f}")
    w(f"- 信号强度中位数: {np.median(abs_scores):.4f}")
    w(f"- 信号强度标准差: {np.std(abs_scores):.4f}")
    w(f"- 最强信号: {max(abs_scores):.4f}")
    w(f"- 最弱信号: {min(abs_scores):.4f}")
    w(f"- 综合得分范围: [{min(scores):+.4f}, {max(scores):+.4f}]")
    w()

    # 信号变化（增量）— 用于判断趋势加速/衰竭
    if has_delta:
        delta_ranked = sorted(sym_deltas.items(), key=lambda x: -x[1])
        accelerating = [(s, d) for s, d in delta_ranked if d < 0][:10]  # 空头加速
        decelerating = [(s, d) for s, d in delta_ranked if d > 0][:10]  # 空头减速/多头萌芽
        w("## 信号变化 (较昨日增量)")
        w()
        w("> 增量 = 今日得分 - 昨日得分。负增量 = 空头信号加强（加速下跌），")
        w("> 正增量 = 空头信号减弱或向多头方向移动（减速/反转萌芽）。")
        w("> **交易含义**：做空选加速品种（增量最负），做多关注减速品种（增量最正），")
        w("> 避免追已到极值但增量停滞的品种（趋势衰竭）。")
        w()
        w("### 空头加速 Top 10（做空优先关注）")
        w()
        w("| 品种 | 名称 | 今日得分 | 昨日得分 | 增量 | 方向 |")
        w("|------|------|----------|----------|------|------|")
        for sym, delta in accelerating:
            today_score = sym_scores.get(sym, 0)
            prev_score = prev_scores.get(sym, 0)
            direction = "加速下跌" if delta < 0 else "减速"
            w(f"| {sym} | {_name(sym)} | {today_score:+.4f} | {prev_score:+.4f} | {delta:+.4f} | {direction} |")
        w()
        w("### 空头减速/反转萌芽 Top 10（做多关注）")
        w()
        w("| 品种 | 名称 | 今日得分 | 昨日得分 | 增量 | 方向 |")
        w("|------|------|----------|----------|------|------|")
        for sym, delta in decelerating:
            today_score = sym_scores.get(sym, 0)
            prev_score = prev_scores.get(sym, 0)
            direction = "反转萌芽" if today_score > 0 else "空头减弱"
            w(f"| {sym} | {_name(sym)} | {today_score:+.4f} | {prev_score:+.4f} | {delta:+.4f} | {direction} |")
        w()

    # 因子贡献排名
    w("## 因子贡献排名（当前市场最有效的因子）")
    w()
    w("> 注：v2.105.0 起移除截面 IC 方向校正，此处为各品种最新因子读数的均值，")
    w("> 仅反映因子信号水平，不作方向判定；方向语义见「信号语义说明」。")
    w()
    factor_contribs: dict[str, list[float]] = {}
    for sym, details in sym_details.items():
        for name, val in details.items():
            if name not in factor_contribs:
                factor_contribs[name] = []
            factor_contribs[name].append(val)
    factor_avg = {n: np.mean(v) for n, v in factor_contribs.items()}
    factor_ranked = sorted(factor_avg.items(), key=lambda x: -abs(x[1]))[:20]
    w("| 排名 | 因子名称 | 平均信号值 | 标准差 |")
    w("|------|----------|------------|--------|")
    for i, (name, avg) in enumerate(factor_ranked, 1):
        std = np.std(factor_contribs[name])
        w(f"| {i} | {name} | {avg:+.4f} | {std:.4f} |")
    w()

    # 全部品种信号排名（按信号强度，含多空方向）
    w("## 全部品种信号排名")
    w()
    w("| 排名 | 品种 | 名称 | 产业链 | 主力合约 | 方向 | 信号强度 | 最新价 |")
    w("|------|------|------|--------|----------|------|----------|--------|")
    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = _price(sym, df)
        direction = "多" if score > 0 else "空"
        w(f"| {i} | {sym} | {_name(sym)} | {symbol_sector.get(sym, '-')} | {_contract(sym)} | {direction} | {abs(score):.4f} | {price:.2f} |")
    w()

    report_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] 信号排名报告已保存: {out_path}")

    # ── 生成交易建议报告 ──
    _generate_trading_advice_report(
        today=today,
        report_dir=report_dir,
        sym_scores=sym_scores,
        sym_deltas=sym_deltas,
        has_delta=has_delta,
        market_regime=market_regime,
        regime_label=regime_label,
        sector_breakdown=sector_breakdown,
        active_sector_map=active_sector_map,
        factor_weights=factor_weights,
        per_variety_weights=per_variety_weights if per_variety_weights else None,
        panel=panel,
        long_signals=long_signals,
        short_signals=short_signals,
        name_fn=_name,
        price_fn=_price,
        contract_fn=_contract,
    )

    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="期货信号生成管道")
    parser.add_argument("--max-symbols", type=int, default=25, help="最大品种数")
    parser.add_argument("--days", type=int, default=120, help="回溯天数")
    parser.add_argument(
        "--universe",
        type=str,
        default="core",
        choices=["core", "all"],
        help="品种池: core=25 核心品种 / all=全量商品期货（FUTURES_SUBSET 剔除金融期货）",
    )
    parser.add_argument(
        "--chain",
        type=str,
        default="",
        choices=["", "energy"],
        help="产业链专属工作流: energy（能源链 9 训练 + 其余化工链盲测，泛化到全化工产业链）",
    )
    parser.add_argument(
        "--no-macro-injection",
        action="store_false",
        dest="macro_injection",
        help="关闭宏观字段注入（GAP-088；默认开启——fut_macro_* 因子读取真实宏观数据，"
        "拉取失败降级 close 代理，不阻断管道）",
    )
    parser.add_argument(
        "--force-regime",
        type=str,
        default="",
        choices=["", "bull", "bear", "oscillate", "high_vol", "low_vol"],
        help="验证用：强制覆盖主市场制度（不影响 SectorRegime 计算，仅对最终投票结果覆盖）",
    )
    args = parser.parse_args()
    sys.exit(
        main(
            max_symbols=args.max_symbols,
            days=args.days,
            universe=args.universe,
            macro_injection=args.macro_injection,
            chain=args.chain,
            force_regime=args.force_regime,
        )
    )
