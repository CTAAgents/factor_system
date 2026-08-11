"""
fts/factor_engine/experiment_log.py — 结构化实验日志（Phase 2 P1-2，26 号计划 §7）

每次 L2 run 导出统一 `data/experiments-{run_id}.json`（对齐 autoresearch experiments
结构），支撑事后元分析（哪种演化方法/参数模式最有效）。

Schema（写入 01-architecture.md）:
    {
      "run_id", "trace_id", "market", "started_at", "generations_completed",
      "rounds": [
        {"generation", "parent_id", "variants": [
          {"candidate_id", "method", "summary", "scores", "outcome"}
        ], "promoted_count"}
      ],
      "summary": {"total_evaluated", "total_promoted", "promote_rate", "by_method"}
    }

设计:
    - 聚合 run 内全部候选（含预筛拦截/失败/晋升）→ 导出 JSON
    - 幂等: run_id 唯一，重复导出覆盖同 run
    - 契约校验（轻量手动校验，项目未引入 pydantic 依赖）后再落盘；
      非法 schema 记录 warning，不阻断 run（非阻塞）

版本: v1.0.0（Phase 2，与 FTS 同步）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── 候选结局枚举（对齐计划 §7.2） ─────────────────────────

ALLOWED_OUTCOMES = frozenset(
    {
        "prefilter_rejected",  # 快速预筛/粗筛拦截（源头）
        "verifier_failed",  # 运行时校验/Verifier 失败
        "audit_failed",  # 准入链失败（审计/消融/因果/鲁棒性/质检）
        "promoted",  # 晋升 elite
        "retired",  # 退役（预留）
    }
)


def extract_scores(evaluation: Optional[dict]) -> dict[str, Any]:
    """从评估结果提取 scores 字段（对齐计划 §7.2 schema）。

    Args:
        evaluation: FactorEvaluation dict；None/空 → {}

    Returns:
        非 None 的指标字段；turnover 映射自 level_1_backtest.turnover_monthly
    """
    if not evaluation:
        return {}
    bt = evaluation.get("level_1_backtest", {}) or {}
    scores: dict[str, Any] = {
        "ic": bt.get("ic"),
        "icir": bt.get("icir"),
        "sharpe": bt.get("sharpe"),
        "max_drawdown": bt.get("max_drawdown"),
        "turnover": bt.get("turnover_monthly"),
        "monotonicity": bt.get("monotonicity"),
    }
    return {k: v for k, v in scores.items() if v is not None}


class ExperimentLogWriter:
    """聚合 run 内候选 → 导出统一 experiments JSON（幂等覆盖同 run_id）。

    Usage:
        writer = ExperimentLogWriter(output_dir="data")
        path = writer.export(run_id, trace_id, market, started_at,
                             generations_completed, variants)
    """

    def __init__(self, output_dir: str | Path = "data"):
        self.output_dir = Path(output_dir)

    def export(
        self,
        run_id: str,
        trace_id: str,
        market: str,
        started_at: str,
        generations_completed: int,
        variants: list[dict],
    ) -> Optional[Path]:
        """导出实验日志 JSON。

        Args:
            run_id: L2 run 唯一 ID
            trace_id: 全链路 trace_id
            market: 交易市场（futures/stock）
            started_at: run 开始时间（ISO）
            generations_completed: 完成代数
            variants: 候选变体列表（_record_experiment_variant 产出）

        Returns:
            文件路径；schema 非法返回 None（warning，不阻断）
        """
        payload = self._build_payload(
            run_id=run_id,
            trace_id=trace_id,
            market=market,
            started_at=started_at,
            generations_completed=generations_completed,
            variants=variants,
        )
        if not self._validate_payload(payload):
            logger.warning(
                "实验日志 schema 校验失败，跳过导出（run_id=%s）", run_id
            )
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.output_dir / f"experiments-{run_id}.json"
        filepath.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return filepath

    # ─── 内部方法 ───

    def _build_payload(
        self,
        run_id: str,
        trace_id: str,
        market: str,
        started_at: str,
        generations_completed: int,
        variants: list[dict],
    ) -> dict[str, Any]:
        """聚合 variants 为 rounds（按 generation+parent_id 分组）+ summary。"""
        # 按 (generation, parent_id) 分组，保持插入序
        round_map: dict[tuple[int, Optional[str]], list[dict]] = {}
        for v in variants:
            key = (int(v.get("generation", 0)), v.get("parent_id"))
            round_map.setdefault(key, []).append(v)

        rounds: list[dict[str, Any]] = []
        for (gen, pid), vs in round_map.items():
            rounds.append(
                {
                    "generation": gen,
                    "parent_id": pid,
                    "variants": [
                        {
                            "candidate_id": v.get("candidate_id", "?"),
                            "method": v.get("method", "unknown"),
                            "summary": v.get("summary", ""),
                            "scores": v.get("scores", {}) or {},
                            "outcome": v.get("outcome", "audit_failed"),
                        }
                        for v in vs
                    ],
                    "promoted_count": sum(1 for v in vs if v.get("outcome") == "promoted"),
                }
            )

        total_evaluated = len(variants)
        total_promoted = sum(1 for v in variants if v.get("outcome") == "promoted")
        by_method_raw: dict[str, dict[str, int]] = {}
        for v in variants:
            method = v.get("method", "unknown")
            stat = by_method_raw.setdefault(method, {"evaluated": 0, "promoted": 0})
            stat["evaluated"] += 1
            if v.get("outcome") == "promoted":
                stat["promoted"] += 1
        by_method = {
            m: {
                "evaluated": d["evaluated"],
                "promoted": d["promoted"],
                "rate": d["promoted"] / d["evaluated"] if d["evaluated"] else 0.0,
            }
            for m, d in by_method_raw.items()
        }

        return {
            "run_id": run_id,
            "trace_id": trace_id,
            "market": market,
            "started_at": started_at,
            "generations_completed": generations_completed,
            "rounds": rounds,
            "summary": {
                "total_evaluated": total_evaluated,
                "total_promoted": total_promoted,
                "promote_rate": (total_promoted / total_evaluated) if total_evaluated else 0.0,
                "by_method": by_method,
            },
        }

    @staticmethod
    def _validate_payload(payload: dict) -> bool:
        """轻量契约校验（项目未引入 pydantic，手动校验必填键与枚举）。"""
        if not payload.get("run_id") or not payload.get("trace_id"):
            return False
        for r in payload.get("rounds", []):
            for v in r.get("variants", []):
                if not v.get("candidate_id") or v.get("method") is None:
                    return False
                if v.get("outcome") not in ALLOWED_OUTCOMES:
                    return False
        return True


__all__ = [
    "ALLOWED_OUTCOMES",
    "extract_scores",
    "ExperimentLogWriter",
]
