"""
fts.factor_engine.recalibration — 因子自动重校准队列（C6，v2.100.1）。

GAP-I305 衰减退役闭环的延伸：decayed 因子不再一刀切退役，先进入重校准队列 →
micro_evolution 两阶段漏斗（低 trials 参数微调）→ 微调后 IC 有提升（>= min_ic_gap）
则标记 done 并回写 elite 元数据（recalibrated_at/ic/params）；无提升标记 skipped
（保持既有退役路径）；异常标记 failed（不阻断）。

设计原则:
    - 队列 JSON 幂等落盘（对齐 dynamic_pool/tick_cache 模式），损坏回退空队列
    - 重校准不自动改写因子 params（只记录 best_params 供决策），保守默认
    - 无 optuna / 执行异常 → failed 降级，绝不影响其他队列项

用法:
    q = RecalibrationQueue(queue_path)
    q.enqueue("fct_x", reason="decayed")
    stats = process_recalibration_queue(elite_dir, data, fwd, config)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class RecalibrationStatus(str, Enum):
    """重校准队列状态机。"""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"  # 微调后 IC 有提升
    SKIPPED = "skipped"  # 微调后 IC 无提升（保持退役路径）
    FAILED = "failed"  # 执行异常/无 optuna


@dataclass
class RecalibrationItem:
    """单因子重校准记录。"""

    factor_id: str
    name: str = ""
    status: str = RecalibrationStatus.PENDING.value
    reason: str = ""  # 触发源（decayed 等）
    created_at: str = ""
    updated_at: str = ""
    baseline_ic: float = 0.0
    recalibrated_ic: float = 0.0
    best_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "baseline_ic": self.baseline_ic,
            "recalibrated_ic": self.recalibrated_ic,
            "best_params": self.best_params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecalibrationItem":
        return cls(
            factor_id=str(data.get("factor_id", "")),
            name=str(data.get("name", "")),
            status=str(data.get("status", RecalibrationStatus.PENDING.value)),
            reason=str(data.get("reason", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            baseline_ic=float(data.get("baseline_ic", 0.0)),
            recalibrated_ic=float(data.get("recalibrated_ic", 0.0)),
            best_params=dict(data.get("best_params", {}) or {}),
        )


@dataclass
class RecalibrationConfig:
    """重校准配置（C6）。"""

    enabled: bool = True
    max_queue: int = 50  # 队列上限（超出拒绝新入队）
    n_trials: int = 40  # 精筛试验数（衰减因子微调用低预算，远低于 DEFAULT_N_TRIALS=100）
    coarse_trials: int = 20  # 粗筛试验数（复用 GAP-I205 两阶段漏斗）
    min_ic_gap: float = 0.0  # 微调后 IC 提升下限（>= 判定 done）
    queue_path: str = ""  # 空则用默认 {memory_dir}/portfolio/recalibration_queue.json


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _compute_ic(signal: Any, returns: Any) -> float:
    """信号与收益的 Spearman IC（尾部对齐，常数/过短返回 0）。"""
    sig = np.asarray(signal, dtype=float).ravel()
    ret = np.asarray(returns, dtype=float).ravel()
    min_len = min(len(sig), len(ret))
    if min_len < 2:
        return 0.0
    s = sig[-min_len:]
    r = ret[-min_len:]
    if float(np.std(s)) < 1e-10 or float(np.std(r)) < 1e-10:
        return 0.0
    from scipy import stats as sp_stats

    ic, _ = sp_stats.spearmanr(s, r)
    return 0.0 if np.isnan(ic) else float(ic)


class RecalibrationQueue:
    """重校准队列（JSON 幂等落盘）。"""

    def __init__(self, queue_path: str | Path) -> None:
        self._path = Path(queue_path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[RecalibrationItem]:
        """加载队列；缺失/损坏回退空列表（幂等）。"""
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            items = data.get("items", data) if isinstance(data, dict) else data
            return [RecalibrationItem.from_dict(i) for i in items if isinstance(i, dict)]
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("[Recal] 重校准队列损坏，回退空队列: %s", self._path)
            return []

    def save(self, items: list[RecalibrationItem]) -> None:
        """落盘队列（原子写：临时文件 + rename）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"items": [i.to_dict() for i in items]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    def enqueue(
        self,
        factor_id: str,
        name: str = "",
        reason: str = "",
        max_queue: int = 50,
    ) -> bool:
        """入队（重复 pending 去重；超上限拒绝）。

        Returns:
            True=入队成功；False=已在队列/超上限/空 factor_id
        """
        if not factor_id:
            return False
        items = self.load()
        if any(i.factor_id == factor_id and i.status == RecalibrationStatus.PENDING.value for i in items):
            return False
        if len(items) >= max_queue:
            logger.warning("[Recal] 队列已达上限 %d，拒绝 %s", max_queue, factor_id)
            return False
        now = _now_iso()
        items.append(
            RecalibrationItem(
                factor_id=factor_id,
                name=name,
                status=RecalibrationStatus.PENDING.value,
                reason=reason,
                created_at=now,
                updated_at=now,
            )
        )
        self.save(items)
        return True

    def list_pending(self, limit: int = 50) -> list[RecalibrationItem]:
        return [i for i in self.load() if i.status == RecalibrationStatus.PENDING.value][:limit]

    def transition(
        self,
        factor_id: str,
        status: str,
        **fields: Any,
    ) -> Optional[RecalibrationItem]:
        """更新队列项状态与附加字段；不存在返回 None。"""
        items = self.load()
        for i in items:
            if i.factor_id != factor_id:
                continue
            i.status = status
            i.updated_at = _now_iso()
            for key, value in fields.items():
                if hasattr(i, key):
                    setattr(i, key, value)
            self.save(items)
            return i
        return None


def recalibrate_factor(
    factor: dict[str, Any],
    data: pd.DataFrame,
    forward_returns: np.ndarray,
    config: RecalibrationConfig | None = None,
) -> tuple[dict[str, Any], float, float, str]:
    """对单因子做参数微调重校准。

    Args:
        factor: FactorProgram 契约 dict（含 code/params）
        data: OHLCV 数据
        forward_returns: 前向收益
        config: 重校准配置

    Returns:
        (best_params, new_ic, baseline_ic, status)：
        status = done（new_ic - baseline_ic >= min_ic_gap）/ skipped / failed
    """
    cfg = config or RecalibrationConfig()
    try:
        from .factor_program import FactorExecutor
        from .micro_evolution import optimize_params_staged

        executor = FactorExecutor(factor)
        executor.compile()
        baseline_signal = executor.execute(data, factor.get("params", {}) or {})
        baseline_ic = _compute_ic(baseline_signal, forward_returns)

        best_params, best_score, passed = optimize_params_staged(
            factor,
            data,
            forward_returns,
            n_trials=cfg.n_trials,
            coarse_trials=cfg.coarse_trials,
        )
        new_ic = float(best_score) if passed else baseline_ic
        if new_ic - baseline_ic >= cfg.min_ic_gap and passed:
            status = RecalibrationStatus.DONE.value
        else:
            status = RecalibrationStatus.SKIPPED.value
        return best_params, new_ic, baseline_ic, status
    except Exception as e:  # noqa: BLE001
        logger.warning("[Recal] 因子 %s 重校准失败: %s", factor.get("factor_id", "?"), e)
        return factor.get("params", {}) or {}, 0.0, 0.0, RecalibrationStatus.FAILED.value


def _locate_elite_file(elite_dir: Path, factor_id: str) -> Optional[Path]:
    """在 elite 目录定位 factor_id 对应的 JSON 快照文件。"""
    for fp in sorted(elite_dir.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("factor_id") == factor_id:
            return fp
    return None


def process_recalibration_queue(
    elite_dir: str | Path,
    data: pd.DataFrame,
    forward_returns: np.ndarray,
    config: RecalibrationConfig | None = None,
    queue: Optional[RecalibrationQueue] = None,
    factor_db_path: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """处理重校准队列：逐项微调 + 回写 elite 元数据。

    Args:
        elite_dir: elite 因子目录（股票 stocks_elite/ 或期货 futures_elite/）
        data: OHLCV 数据
        forward_returns: 前向收益
        config: 重校准配置
        queue: 队列实例（None 用 config.queue_path 构造）
        factor_db_path: 可选 DuckDB 路径（存在时同步 metadata）
        dry_run: 只评估不入队结果（不落盘队列/不写 elite）

    Returns:
        处理统计 {processed, done, skipped, failed, not_found}
    """
    cfg = config or RecalibrationConfig()
    elite_path = Path(elite_dir)
    q = queue or RecalibrationQueue(cfg.queue_path or "memory/portfolio/recalibration_queue.json")
    pending = q.list_pending()
    stats = {"processed": 0, "done": 0, "skipped": 0, "failed": 0, "not_found": 0}

    for item in pending:
        fp = _locate_elite_file(elite_path, item.factor_id)
        if fp is None:
            stats["not_found"] += 1
            if not dry_run:
                q.transition(item.factor_id, RecalibrationStatus.FAILED.value, reason=f"{item.reason};not_found")
            continue
        if not dry_run:
            q.transition(item.factor_id, RecalibrationStatus.PROCESSING.value)
        try:
            factor = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError):
            stats["failed"] += 1
            if not dry_run:
                q.transition(item.factor_id, RecalibrationStatus.FAILED.value, reason="bad_json")
            continue

        best_params, new_ic, baseline_ic, status = recalibrate_factor(factor, data, forward_returns, cfg)
        stats["processed"] += 1
        stats[status if status in stats else "failed"] += 1

        if dry_run:
            continue
        if status == RecalibrationStatus.DONE.value:
            # 回写 elite JSON 元数据（保留原内容，仅追加）
            factor["recalibrated_at"] = _now_iso()
            factor["recalibrated_ic"] = round(new_ic, 4)
            factor["recalibrated_params"] = best_params
            fp.write_text(json.dumps(factor, ensure_ascii=False, indent=2), encoding="utf-8")
            if factor_db_path:
                try:
                    from .factor_db.repository import FactorRepository

                    FactorRepository(str(factor_db_path)).update_factor(
                        item.factor_id,
                        {
                            "recalibrated_at": factor["recalibrated_at"],
                            "recalibrated_ic": factor["recalibrated_ic"],
                            "recalibrated_params": json.dumps(best_params, ensure_ascii=False),
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("[Recal] DuckDB metadata 同步失败 %s: %s", item.factor_id, e)
        q.transition(
            item.factor_id,
            status,
            baseline_ic=round(baseline_ic, 4),
            recalibrated_ic=round(new_ic, 4),
            best_params=best_params,
        )
    return stats


__all__ = [
    "RecalibrationStatus",
    "RecalibrationItem",
    "RecalibrationConfig",
    "RecalibrationQueue",
    "recalibrate_factor",
    "process_recalibration_queue",
]
