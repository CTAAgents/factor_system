"""
fts.workflow.executor — WorkFlow 阶段执行器（真实调用 fts.cli / 脚本）。

执行模型:
    - 单阶段运行: 后台线程 subprocess 调用，UI 轮询状态
    - 端到端运行: 单线程按阶段顺序依次执行（依赖满足即推进），失败停止可重试
    - 动态占位符: ``{factor_id}``（因子库最新 active 因子）/ ``{report_dir}``（运行产物目录）

超时/退出码/日志全量留痕；JSON 产物（--json 输出）解析入库供 UI 展示。
零未来函数（仅当前可观测状态推进阶段）。

版本: v1.0.0
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .stages import StageAction, get_stage
from .store import WorkflowStore

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_ROOT = PROJECT_ROOT / "data" / "reports" / "workflow"


def _extract_json(text: str) -> Any:
    """从命令输出提取 JSON（优先整体解析，回退扫描末尾 JSON 行）。"""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            try:
                return json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
    return None


class WorkflowExecutor:
    """WorkFlow 执行器。"""

    def __init__(self, store: WorkflowStore):
        self._store = store
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    # ─── 动态参数解析 ───────────────────────────────────────────
    def _latest_factor_id(self) -> str:
        """查询因子库最新 active 因子（供 {factor_id} 占位符）。"""
        try:
            from fts.factor_engine.factor_db.repository import FactorRepository

            repo = FactorRepository(market="futures")
            try:
                factors = repo.list_factors(
                    market="futures", status="active", limit=1, sort_by="updated_at", sort_order="desc"
                )
                if not factors:
                    factors = repo.list_factors(market="futures", status="active", limit=1)
                if factors:
                    return str(factors[0].get("factor_id") or factors[0].get("name") or "")
            finally:
                repo.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Workflow] 查询最新因子失败: %s", e)
        return ""

    def resolve_cmd(self, action: StageAction, run_id: str) -> list[str]:
        """解析动作命令（替换动态占位符）。"""
        out: list[str] = []
        for part in action.cmd:
            if part == "{factor_id}":
                fid = self._latest_factor_id()
                if not fid:
                    raise ValueError("因子库无可用 active 因子，无法执行回测类动作")
                out.append(fid)
            elif part == "{report_dir}":
                d = REPORT_ROOT / run_id
                d.mkdir(parents=True, exist_ok=True)
                out.append(str(d))
            else:
                out.append(part)
        return out

    def build_argv(self, action: StageAction, run_id: str) -> list[str]:
        cmd = self.resolve_cmd(action, run_id)
        if action.kind == "script":
            return [sys.executable, *cmd]
        return [sys.executable, "-m", "fts.cli", *cmd]

    # ─── 单阶段执行 ─────────────────────────────────────────────
    def run_stage(self, run_id: str, stage_id: str, action_id: str) -> dict:
        """异步执行单阶段动作，返回 stage_run 记录。"""
        stage = get_stage(stage_id)
        if stage is None:
            return {"ok": False, "error": f"未知阶段: {stage_id}"}
        action = next((a for a in stage.actions if a.id == action_id), None)
        if action is None:
            return {"ok": False, "error": f"未知动作: {stage_id}/{action_id}"}

        stage_run_id = self._store.create_stage_run(run_id, stage_id, action_id)
        self._store.update_run(run_id, current_stage=stage_id)

        thread = threading.Thread(
            target=self._execute,
            args=(stage_run_id, run_id, stage_id, action),
            daemon=True,
        )
        with self._lock:
            self._threads[stage_run_id] = thread
        thread.start()
        return {"ok": True, "stage_run_id": stage_run_id}

    def _execute(self, stage_run_id: int, run_id: str, stage_id: str, action: StageAction) -> None:
        """执行动作并写状态（同步执行，供线程与端到端共用）。"""
        st = self._store
        st.update_stage_run(stage_run_id, status="running", started_at=_now())
        try:
            argv = self.build_argv(action, run_id)
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=action.timeout,
                cwd=str(PROJECT_ROOT),
            )
            log = (proc.stdout or "") + (proc.stderr or "")
            st.update_stage_run(stage_run_id, log=(proc.stdout or "")[-20000:])
            if proc.returncode == 0:
                output = None
                if action.json_output:
                    output = _extract_json(proc.stdout or "")
                st.update_stage_run(
                    stage_run_id,
                    status="success",
                    exit_code=0,
                    ended_at=_now(),
                    output=json.dumps(output, ensure_ascii=False) if output else "",
                )
                logger.info("[Workflow] 阶段成功 %s/%s (%s)", stage_id, action.id, run_id)
            else:
                st.update_stage_run(
                    stage_run_id,
                    status="failed",
                    exit_code=proc.returncode,
                    ended_at=_now(),
                )
                logger.warning(
                    "[Workflow] 阶段失败 %s/%s rc=%s: %s",
                    stage_id,
                    action.id,
                    proc.returncode,
                    (proc.stderr or "")[:300],
                )
        except subprocess.TimeoutExpired:
            st.update_stage_run(
                stage_run_id,
                status="failed",
                exit_code=-1,
                ended_at=_now(),
                log=(log if "log" in locals() else "") + "\n[超时]",
            )
            logger.warning("[Workflow] 阶段超时 %s/%s", stage_id, action.id)
        except Exception as e:  # noqa: BLE001
            st.update_stage_run(stage_run_id, status="failed", exit_code=-2, ended_at=_now(), log=f"[异常] {e}")
            logger.error("[Workflow] 阶段异常 %s/%s: %s", stage_id, action.id, e)
        finally:
            with self._lock:
                self._threads.pop(stage_run_id, None)
            self._sync_run_status(run_id)

    def _sync_run_status(self, run_id: str) -> None:
        """按 stage_runs 汇总批次状态（单动作/端到端通用）。

        同一阶段重复执行时取最新一条记录（get_stage_runs 按 id 升序，dict 覆盖即最新），
        避免旧失败记录主导批次状态（如 qa 单动作重跑成功后 run 仍判 failed）。
        """
        recs = self._store.get_stage_runs(run_id)
        latest: dict[str, str] = {}
        for r in recs:
            latest[r["stage_id"]] = r["status"]
        statuses = list(latest.values())
        if any(s == "running" for s in statuses):
            status = "running"
        elif any(s == "failed" for s in statuses):
            status = "failed"
        elif statuses:
            status = "success"
        else:
            status = "running"
        self._store.update_run(run_id, status=status)

    # ─── 端到端执行 ─────────────────────────────────────────────
    def run_all(self, run_id: str, start_stage: str = "s1", action_id: str | None = None) -> None:
        """端到端执行：从 start_stage 起按顺序运行每阶段动作（默认首动作），失败停止。"""
        from .stages import STAGES

        def _worker() -> None:
            started = False
            for stage in STAGES:
                if stage.id == start_stage:
                    started = True
                if not started:
                    continue
                action = next((a for a in stage.actions if action_id is None or a.id == action_id), None)
                if action is None:
                    action = stage.actions[0]
                if action.kind == "info":
                    continue
                stage_run_id = self._store.create_stage_run(run_id, stage.id, action.id)
                self._store.update_run(run_id, current_stage=stage.id, status="running")
                self._execute(stage_run_id, run_id, stage.id, action)
                rec = self._store.get_stage_runs(run_id)
                cur = next((r for r in rec if r["id"] == stage_run_id), {})
                if cur.get("status") != "success":
                    self._store.update_run(run_id, status="failed")
                    logger.info("[Workflow] 端到端中止于 %s（%s）", stage.id, cur.get("status"))
                    return
            self._store.update_run(run_id, status="success")

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = ["WorkflowExecutor", "REPORT_ROOT", "_extract_json"]
