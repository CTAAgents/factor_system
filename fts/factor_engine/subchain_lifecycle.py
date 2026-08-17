"""
fts.factor_engine.subchain_lifecycle — 因子×子链单元粒度退化检测（plans/49 §C）

背景
----
评审质检与生命周期管理此前以"全链"单值 IC 为口径，与 plans/47（调制矩阵 m[factor][子链]）
和 plans/48（方向 Gate g[子链]）割裂：子链特异因子的有效链 IC 衰减被无效链稀释掩盖
（假阴性：不降级、因子继续在失效链暴露），或单链失效触发整因子降级（假阳性：误杀
仍健康的链）。本模块把退化检测下沉到**评估单元 (因子, 子链)** 粒度，基于
``subchain_factor_quality`` 质量矩阵时序（plans/49 §A2）判定：

  - 单元级：每"曾是 effective"的子链独立算衰减（最近窗 mean_ic vs 早期基准）
  - 因子级：
    - 全部有效链衰减（decay > drop_severe 且当前不 effective）→ degrade（整因子）
    - 部分有效链衰减 → scope_shrink（失效链从 scope 剔除，写决策行）
    - 单链特异因子其唯一链衰减 → degrade（整因子）
    - 其余 → keep

scope 收缩闭环（§C2）：scope_shrink 后更新 factor_catalog.metadata.subchain_scope
（剔除失效链）→ 47 号调制矩阵在 Step 2b 消费最新 metadata 自动重算；degraded 因子
走 energy_qa_review 冷却期回归复核（复用 cooldown_days）。

HARNESS §契约优先：SubchainLifecycleConfig / compute_subchain_degradation 即对外契约；
接入点（energy_qa_review [2] 退化检测段 / factor_lifecycle 子链重载）由实施阶段接线。

版本: v1.0.0（plans/49 §C1）
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SubchainLifecycleConfig(BaseModel):
    """单元粒度退化检测参数（config/settings.yaml → l3.subchain_quality，禁硬编码）。"""

    enabled: bool = Field(default=False, description="退化检测开关（默认关=回退全链原逻辑）")
    decay_threshold: float = Field(default=0.30, gt=0.0, le=1.0,
        description="有效链 IC 衰减触发阈值（最近窗 vs 早期基准 > 阈值 → 标记 scope_shrink）")
    drop_severe: float = Field(default=0.50, gt=0.0, le=1.0,
        description="全部有效链衰减严重阈值（> 阈值且当前不 effective → degrade）")
    window_days: int = Field(default=60, ge=1,
        description="退化检测回看天数（映射为最近期数窗口 window=max(1, round(window_days/30))）")
    min_periods: int = Field(default=5, ge=2,
        description="子链质量时序最少期数（不足 → keep + 样本不足标注，不误判）")
    cooldown_days: int = Field(default=30, ge=1,
        description="scope 收缩/degraded 冷却期（与 energy_qa_review 一致）")

    @property
    def window(self) -> int:
        """回看期数窗口（window_days/30 取整，至少 1 期）。"""
        return max(1, round(self.window_days / 30))


def load_subchain_lifecycle_config() -> SubchainLifecycleConfig:
    """从 config/settings.yaml → l3.subchain_quality 读取退化检测参数（SSOT）。"""
    try:
        from fts.config import get_config

        cfg = (getattr(get_config(), "l3", {}) or {}).get("subchain_quality") or {}
        return SubchainLifecycleConfig(**cfg)
    except Exception as e:  # noqa: BLE001 — 配置缺失/损坏回退默认
        logger.warning("[subchain_lifecycle] 配置读取失败，回退默认: %s", e)
        return SubchainLifecycleConfig()


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def compute_subchain_degradation(
    rows: list[dict[str, Any]],
    cfg: Optional[SubchainLifecycleConfig] = None,
) -> dict[str, Any]:
    """按因子×子链质量时序判退化（plans/49 §C1，纯函数）。

    Args:
        rows: ``SubchainQualityRepository.query_subchain_quality`` 输出
            （factor_id×chain 多期，每行含 evaluated_at/mean_ic/effective）
        cfg: 退化检测参数（None → 默认）

    Returns:
        {
          "per_chain": {chain: {baseline, recent, decay_ratio, cur_effective, status}},
          "ever_effective_chains": [...],
          "factor_status": "keep" | "scope_shrink" | "degrade",
          "scope_shrink_chains": [...], "degrade_chains": [...],
          "detail": str,
        }

    单元级判定（仅"曾是 effective"的子链参与退化）：
      - 样本 < min_periods → keep（样本不足，不误判）
      - decay_ratio > drop_severe 且当前不 effective → degrade
      - decay_ratio > decay_threshold 或当前不 effective → scope_shrink
      - 其余 → keep
    因子级判定：
      - 全部有效链 degrade → degrade
      - 单链特异（ever_effective 仅 1 链）且该链非 keep → degrade（特异因子其唯一链衰减）
      - 部分链 scope_shrink → scope_shrink
      - 其余 → keep
    """
    cfg = cfg or SubchainLifecycleConfig()
    if not rows:
        return {
            "per_chain": {}, "ever_effective_chains": [], "factor_status": "keep",
            "scope_shrink_chains": [], "degrade_chains": [], "detail": "无质量时序",
        }

    per_chain: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        per_chain.setdefault(r.get("chain", "?"), []).append(r)
    for seq in per_chain.values():
        seq.sort(key=lambda x: str(x.get("evaluated_at", "")))

    ever_effective: list[str] = []
    out: dict[str, dict[str, Any]] = {}
    for chain, seq in per_chain.items():
        effs = [bool(r.get("effective")) for r in seq]
        if not any(effs):
            continue  # 从未 effective 的链不参与退化
        ever_effective.append(chain)
        ics = [float(r.get("mean_ic") or 0.0) for r in seq]
        cur_effective = bool(effs[-1])
        n = len(ics)
        if n < cfg.min_periods:
            out[chain] = {
                "baseline": None, "recent": None, "decay_ratio": None,
                "cur_effective": cur_effective, "status": "keep",
            }
            continue
        baseline = _mean(ics[: cfg.min_periods])
        recent = _mean(ics[-cfg.window :])
        decay = (1.0 - recent / baseline) if abs(baseline) > 1e-12 and baseline > 0 else 0.0
        if decay > cfg.drop_severe and not cur_effective:
            status = "degrade"
        elif decay > cfg.decay_threshold or not cur_effective:
            status = "scope_shrink"
        else:
            status = "keep"
        out[chain] = {
            "baseline": round(baseline, 4), "recent": round(recent, 4),
            "decay_ratio": round(decay, 4), "cur_effective": cur_effective, "status": status,
        }

    degrade_chains = [c for c, s in out.items() if s["status"] == "degrade"]
    shrink_chains = [c for c, s in out.items() if s["status"] == "scope_shrink"]
    # 全部非 keep 的曾经有效链 = scope 收缩候选（degrade 链同样剔除）
    trouble = degrade_chains + shrink_chains
    if not ever_effective:
        factor_status = "keep"
    elif len(degrade_chains) == len(ever_effective):
        factor_status = "degrade"  # 全部有效链失效
    elif len(ever_effective) == 1 and trouble:
        factor_status = "degrade"  # 单链特异因子其唯一链衰减 → 整因子退化
    elif trouble:
        factor_status = "scope_shrink"  # 部分链失效（含 degrade 链）→ 收缩 scope
    else:
        factor_status = "keep"

    detail = (
        f"有效链={ever_effective} degrade={degrade_chains} shrink={shrink_chains} "
        f"→ {factor_status}"
    )
    logger.info("[subchain_lifecycle] factor=%s %s", rows[0].get("factor_id", "?"), detail)
    return {
        "per_chain": out,
        "ever_effective_chains": ever_effective,
        "factor_status": factor_status,
        "scope_shrink_chains": trouble,
        "degrade_chains": degrade_chains,
        "detail": detail,
    }


def scope_without_chains(
    subchain_scope: Any,
    remove_chains: list[str],
) -> tuple[Any, bool]:
    """从现有 scope 剔除失效子链（plans/49 §C2 闭环辅助，纯函数）。

    Args:
        subchain_scope: metadata.subchain_scope（"all"/"unknown"/链名列表）
        remove_chains: 待剔除的失效子链

    Returns:
        (新 scope, 是否变化)。"all" 语义为"≥3 链 effective"——若剔除后剩余链数
        不足 all_chains_effective_min(3) 则降为链名列表（由调用方再判定是否 degrade）；
        本函数仅做集合差，保持"all"不变（all 是"曾经全链有效"的标记，收缩由
        energy_qa_review 重算画像后决定）。
    """
    if not remove_chains or not isinstance(subchain_scope, list):
        return subchain_scope, False
    new = [c for c in subchain_scope if c not in set(remove_chains)]
    changed = new != subchain_scope
    return new, changed


def build_subchain_quality_matrix_snapshot(
    market: str = "energy",
) -> dict[str, dict[str, dict[str, Any]]]:
    """质量矩阵快照（plans/49 §D3 监控）：{factor_id: {chain: 最新行精简}}。

    每 factor×chain 取最新一行（effective/mean_ic/t_stat/source/decision），
    供 L3 质量报告 ``subchain_quality_matrix`` 段与监控消费。空/异常返回 {}。

    Args:
        market: 市场（energy/futures）

    Returns:
        {factor_id: {chain: {effective, mean_ic, t_stat, source, decision}}}
    """
    try:
        from fts.factor_engine.factor_db.repository import SubchainQualityRepository

        repo = SubchainQualityRepository(market=market)
        try:
            rows = repo.list_recent_quality(market)
        finally:
            repo.close()
    except Exception as e:  # noqa: BLE001 — 快照失败不阻断主流程
        logger.warning("[subchain_lifecycle] 质量矩阵快照构建失败（跳过）: %s", e)
        return {}

    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:  # list_recent_quality 按 evaluated_at 升序 → 后写覆盖得每单元最新
        latest[(r["factor_id"], r["chain"])] = r
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for (fid, chain), r in latest.items():
        out.setdefault(fid, {})[chain] = {
            "effective": bool(r.get("effective")),
            "mean_ic": r.get("mean_ic"),
            "t_stat": r.get("t_stat"),
            "source": r.get("source"),
            "decision": r.get("decision"),
        }
    return out


__all__ = [
    "SubchainLifecycleConfig",
    "load_subchain_lifecycle_config",
    "compute_subchain_degradation",
    "scope_without_chains",
    "build_subchain_quality_matrix_snapshot",
]
