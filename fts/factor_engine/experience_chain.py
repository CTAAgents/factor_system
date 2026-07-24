"""
loop_engine/experience_chain.py — 经验链存储

factorengine 核心约束：
    LLM 每次宏观演化必须读取经验链，避免重复踩坑。

存储结构:
    memory/evolution/
    ├── state.json                    # EvolutionState
    ├── success/                      # 成功轨迹（晋升精英池）
    │   └── <trace_id>.json
    ├── failure/                      # 失败轨迹
    │   └── <trace_id>.json
    └── experience_chain.md           # 经验链摘要（LLM 易读格式）

约束:
    - 经验链满 100 条时按时间倒序淘汰最旧的 20 条
    - 失败轨迹的 failure_reasons 必须结构化（不能为空字符串）
    - LLM 每次必须读取最近 20 条（成功 10 + 失败 10）

版本: v1.1.0（与 FTS 同步）
"""
# pylint: disable=too-many-arguments,too-many-positional-arguments

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .contracts import ExperienceTrace, FactorEvaluation


# ─── 常量 ─────────────────────────────────────────────────

MAX_CHAIN_SIZE: int = 100
"""经验链最大条数。超过时淘汰最旧的 20 条。"""

LLM_READ_SUCCESS_COUNT: int = 10
"""LLM 每次读取的成功轨迹数。"""

LLM_READ_FAILURE_COUNT: int = 10
"""LLM 每次读取的失败轨迹数。"""


class ExperienceChainError(Exception):
    """经验链操作失败。"""


# ─── 经验链管理器 ─────────────────────────────────────────

class ExperienceChain:
    """经验链存储管理器。

    Usage:
        chain = ExperienceChain(memory_dir="memory/evolution")
        chain.record_success(trace)
        chain.record_failure(trace)
        recent = chain.read_recent_for_llm()
    """

    def __init__(self, memory_dir: str | Path = "memory/evolution"):
        self.memory_dir = Path(memory_dir)
        self.success_dir = self.memory_dir / "success"
        self.failure_dir = self.memory_dir / "failure"
        self.summary_file = self.memory_dir / "experience_chain.md"

        # 确保目录存在
        self.success_dir.mkdir(parents=True, exist_ok=True)
        self.failure_dir.mkdir(parents=True, exist_ok=True)

    def record_success(self, trace: ExperienceTrace) -> Path:
        """记录成功轨迹到 success/ 目录。"""
        self._validate_trace(trace, expect_success=True)
        return self._write_trace(trace, self.success_dir)

    def record_failure(self, trace: ExperienceTrace) -> Path:
        """记录失败轨迹到 failure/ 目录。"""
        self._validate_trace(trace, expect_success=False)
        return self._write_trace(trace, self.failure_dir)

    def read_recent_for_llm(self) -> dict[str, list[ExperienceTrace]]:
        """读取最近 20 条经验链（成功 10 + 失败 10）供 LLM 参考。

        Returns:
            {
                "success": [ExperienceTrace, ...],  # 最多 10 条
                "failure": [ExperienceTrace, ...],  # 最多 10 条
            }
        """
        success_traces = self._read_dir(self.success_dir, limit=LLM_READ_SUCCESS_COUNT)
        failure_traces = self._read_dir(self.failure_dir, limit=LLM_READ_FAILURE_COUNT)
        return {"success": success_traces, "failure": failure_traces}

    def read_all_success(self) -> list[ExperienceTrace]:
        """读取全部成功轨迹（用于 LLM 上下文）。"""
        return self._read_dir(self.success_dir, limit=None)

    def read_all_failure(self) -> list[ExperienceTrace]:
        """读取全部失败轨迹。"""
        return self._read_dir(self.failure_dir, limit=None)

    def count(self) -> dict[str, int]:
        """返回当前经验链计数。"""
        return {
            "success": len(list(self.success_dir.glob("*.json"))),
            "failure": len(list(self.failure_dir.glob("*.json"))),
            "total": len(list(self.success_dir.glob("*.json")))
                     + len(list(self.failure_dir.glob("*.json"))),
        }

    def cleanup_if_needed(self) -> int:
        """如果经验链超过 MAX_CHAIN_SIZE，淘汰最旧的 20 条。

        Returns:
            被删除的条数
        """
        c = self.count()
        if c["total"] <= MAX_CHAIN_SIZE:
            return 0

        # 按时间倒序排序，删除最旧的 20 条
        all_files: list[tuple[float, Path]] = []
        for d in (self.success_dir, self.failure_dir):
            for f in d.glob("*.json"):
                try:
                    mtime = f.stat().st_mtime
                    all_files.append((mtime, f))
                except OSError:
                    continue

        all_files.sort(key=lambda x: x[0])  # 旧→新
        to_delete = all_files[:20]
        for _, f in to_delete:
            try:
                f.unlink()
            except OSError:
                continue
        return len(to_delete)

    def update_summary(self) -> Path:
        """生成经验链摘要 markdown 文件（LLM 易读格式）。

        Returns:
            摘要文件路径
        """
        recent = self.read_recent_for_llm()
        lines: list[str] = [
            "# 经验链摘要（LLM 参考）",
            "",
            f"> 自动生成: {datetime.now().isoformat()}",
            f"> 总条数: {self.count()}",
            "",
            "## 最近成功轨迹（最多 10 条）",
            "",
        ]
        for i, t in enumerate(recent["success"], 1):
            lines.extend(self._format_trace_for_llm(i, t))

        lines.extend([
            "",
            "## 最近失败轨迹（最多 10 条）",
            "",
        ])
        for i, t in enumerate(recent["failure"], 1):
            lines.extend(self._format_trace_for_llm(i, t))

        self.summary_file.parent.mkdir(parents=True, exist_ok=True)
        self.summary_file.write_text(
            "\n".join(lines), encoding="utf-8"
        )
        return self.summary_file

    # ─── 内部方法 ───

    def _validate_trace(self, trace: ExperienceTrace, expect_success: bool) -> None:
        """验证轨迹结构合法性。"""
        if not trace.get("trace_id"):
            raise ExperienceChainError("trace_id 不能为空")
        if not trace.get("factor_id"):
            raise ExperienceChainError("factor_id 不能为空")
        if not trace.get("mutation_summary", "").strip():
            raise ExperienceChainError("mutation_summary 不能为空字符串")
        if trace.get("success") != expect_success:
            raise ExperienceChainError(
                f"轨迹 success={trace.get('success')} 与期望 {expect_success} 不一致"
            )
        if not expect_success:
            # 失败轨迹必须有 failure_reasons
            eval_ = trace.get("evaluation", {})
            if not eval_.get("failure_reasons"):
                raise ExperienceChainError(
                    "失败轨迹的 evaluation.failure_reasons 不能为空"
                )

    def _write_trace(self, trace: ExperienceTrace, target_dir: Path) -> Path:
        """写入轨迹 JSON 文件。"""
        trace_id = trace["trace_id"]
        # 文件名安全化
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in trace_id)
        fp = target_dir / f"{safe_id}.json"
        fp.write_text(
            json.dumps(trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return fp

    def _read_dir(
        self, dir_path: Path, limit: Optional[int] = None
    ) -> list[ExperienceTrace]:
        """按 mtime 倒序读取目录中的轨迹（最近优先）。"""
        files = list(dir_path.glob("*.json"))
        # 按 mtime 倒序（最新优先）
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        if limit is not None:
            files = files[:limit]
        traces: list[ExperienceTrace] = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                traces.append(ExperienceTrace(**data))  # type: ignore[typeddict-item]
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return traces

    @staticmethod
    def _format_trace_for_llm(idx: int, trace: ExperienceTrace) -> list[str]:
        """格式化轨迹为 LLM 易读的 markdown 行。"""
        eval_ = trace.get("evaluation", {})
        bt = eval_.get("level_1_backtest", {})
        lines = [
            f"### {idx}. {trace.get('factor_id', '?')} — {trace.get('mutation_summary', '')}",
            f"- generation: {trace.get('generation', '?')}",
            f"- mutation_type: {trace.get('mutation_type', '?')}",
            f"- success: {trace.get('success', '?')}",
            f"- IC={bt.get('ic', '?')}, 夏普={bt.get('sharpe', '?')}, 回撤={bt.get('max_drawdown', '?')}",
        ]
        lessons = trace.get("lessons", [])
        if lessons:
            lines.append("- 教训:")
            for l in lessons:
                lines.append(f"  - {l}")
        failure_reasons = eval_.get("failure_reasons", [])
        if failure_reasons:
            lines.append("- 失败原因:")
            for r in failure_reasons:
                lines.append(f"  - {r}")
        lines.append("")
        return lines


def create_trace_from_evaluation(
    factor_id: str,
    parent_id: Optional[str],
    generation: int,
    mutation_type: str,
    mutation_summary: str,
    evaluation: FactorEvaluation,
    lessons: list[str],
    trace_id: Optional[str] = None,
) -> ExperienceTrace:
    """从评估结果创建经验链轨迹。"""
    success = evaluation.get("passed", False)
    return ExperienceTrace(
        trace_id=trace_id or evaluation.get("trace_id", factor_id),
        factor_id=factor_id,
        parent_id=parent_id,
        generation=generation,
        mutation_type=mutation_type,  # type: ignore[typeddict-item]
        mutation_summary=mutation_summary,
        evaluation=evaluation,
        success=success,
        lessons=lessons,
        recorded_at=datetime.now().isoformat(),
    )


__all__ = [
    "MAX_CHAIN_SIZE",
    "LLM_READ_SUCCESS_COUNT",
    "LLM_READ_FAILURE_COUNT",
    "ExperienceChain",
    "ExperienceChainError",
    "create_trace_from_evaluation",
]
