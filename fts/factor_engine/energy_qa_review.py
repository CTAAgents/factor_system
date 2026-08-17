"""
fts/factor_engine/energy_qa_review.py — 能化链评审+质检统一管道（方案 A，2026-08-17）

合并 plans/45 评审（l2_review_energy_job）与 energy 链定期质检
（scripts/energy_chain_degradation_dryrun.py 三路检测）为单一管道：

    [0] 公共面板 → [1] 准入重审 → [2] 退化检测落库 → [3] 生命周期收口（含冷却期回归）
      → [4] Inspector 血缘 → [5] 统一报告

原则（用户确认）：宁可错过，也不能让不合格因子参与组合——任一退化信号命中即降级
（shadow 观察池兜底；冷却期默认 30 交易日自动回归复核；持续不达标才 retire）。

灰度：cfg.apply=False 时全管道 dry-run（不落库、不改 tracking），输出逐因子处置明细，
与现质检（reports/energy_chain/{date}/qa/）+ 评审结果对比一致后再切 apply=True。
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

Decision = Literal["active", "shadow", "degraded", "retire"]


# ─── 配置 ─────────────────────────────────────────────────


class EnergyQaReviewConfig(BaseModel):
    """能化链评审+质检统一管道配置（全部可配置，Pydantic）。"""

    days: int = 300                 # 面板回溯窗口（交易日）
    apply: bool = False             # 落库开关（灰度 dry-run=False）
    ic_threshold: float = 0.02      # |IC| 下限（退化判据）
    drop_threshold: float = 0.30    # IC 降幅比例阈值（→ shadow）
    drop_severe: float = 0.50       # IC 降幅严重阈值（→ degraded）
    sharpe_drop: float = -0.20      # Inspector Sharpe 相对变化阈值
    observe_slope: float = 0.10     # 6M IC 斜率观察阈值
    retire_slope: float = 0.20      # 6M IC 斜率退役阈值
    cooldown_days: int = 30         # 冷却期（交易日，到期自动回归复核）
    cooldown_max_attempts: int = 2  # 冷却期满仍不达标的最大次数 → retire
    out_dir: str = "reports/energy_chain/{date}/qa_review"


# ─── 单因子处置决策 ───────────────────────────────────────


@dataclass
class FactorDisposition:
    """单因子的本轮处置结论（宁严勿松）。"""

    factor_id: str
    name: str
    prev_status: str
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def decide_factor(
    *,
    factor_id: str,
    name: str,
    prev_status: Literal["active", "shadow"],
    reaudit_fail: bool,
    curr_ic: float,
    hist_ic: float,
    slope_grade: str,  # normal / observe / retired
    cfg: EnergyQaReviewConfig,
) -> FactorDisposition:
    """宁严勿松：任一退化信号命中即取严（不做双确认）。

    - shadow: 重审不合格 | |IC|<ic_threshold | IC降幅>drop_threshold | slope=observe
    - degraded: IC降幅>=drop_severe | slope=retired
    - active: 全部达标（含 shadow 观察池达标回归）
    """
    ic_abs = abs(curr_ic)
    ic_drop = 1.0 - (ic_abs / max(hist_ic, 1e-10)) if hist_ic > 1e-10 else 0.0
    severe = ic_drop >= cfg.drop_severe and hist_ic > cfg.ic_threshold

    reasons: list[str] = []
    if reaudit_fail:
        reasons.append("重审不合格")
    if ic_abs < cfg.ic_threshold:
        reasons.append(f"|IC|<{cfg.ic_threshold}")
    if ic_drop > cfg.drop_threshold and hist_ic > cfg.ic_threshold:
        reasons.append(f"IC降幅>{cfg.drop_threshold:.0%}")
    if slope_grade == "observe":
        reasons.append("斜率观察")
    if slope_grade == "retired":
        reasons.append("斜率退役")
    if severe:
        reasons.append(f"IC降幅严重>{cfg.drop_severe:.0%}")

    if severe or slope_grade == "retired":
        decision: Decision = "degraded"
    elif reasons:
        decision = "shadow"
    else:
        decision = "active"

    return FactorDisposition(
        factor_id=factor_id,
        name=name,
        prev_status=prev_status,
        decision=decision,
        reasons=reasons or ["达标"],
        metrics={
            "curr_ic": round(ic_abs, 6),
            "hist_ic": round(hist_ic, 6),
            "ic_drop": round(ic_drop, 6),
            "slope_grade": slope_grade,
        },
    )


# ─── 统一管道 ─────────────────────────────────────────────


class EnergyQaReviewPipeline:
    """能化链评审+质检统一管道。

    Args:
        config: EnergyQaReviewConfig（None 用默认）
        elite_dir: energy elite 目录（None 走 config）
        tracking_dir: energy tracking 目录（None 走 config）
        db_path: 注入测试用隔离库路径（None 走 market 路由）
    """

    def __init__(
        self,
        config: Optional[EnergyQaReviewConfig] = None,
        elite_dir: Optional[str | Path] = None,
        tracking_dir: Optional[str | Path] = None,
        db_path: Optional[str | Path] = None,
    ) -> None:
        from fts.config import get_config
        from fts.data_futures import ENERGY_CHAIN_MARKET, ENERGY_CHAIN_SYMBOLS

        self.cfg = config or EnergyQaReviewConfig()
        self.market = ENERGY_CHAIN_MARKET
        self.chain_symbols = list(ENERGY_CHAIN_SYMBOLS)
        self._app_cfg = get_config()
        self.elite_dir = Path(elite_dir or self._app_cfg.get_elite_dir(self.market))
        self.tracking_dir = Path(tracking_dir or f"{self._app_cfg.memory_dir}/tracking/energy")
        self.db_path = db_path
        self.project_root = Path(__file__).resolve().parent.parent.parent
        self.out_dir: Optional[Path] = None
        self._tracker: Any = None

    # ── 主入口 ─────────────────────────────────────────

    def run(self, trace_id: Optional[str] = None) -> dict[str, Any]:
        trace_id = trace_id or f"fts.l2_qa_review_{datetime.now():%Y%m%d%H%M%S}"
        today = datetime.now().strftime("%Y-%m-%d")
        out_dir = self.project_root / self.cfg.out_dir.format(date=today)
        out_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir = out_dir

        logger.info("[L2评审质检][energy] 启动 trace_id=%s apply=%s", trace_id, self.cfg.apply)

        # [0] 公共面板（一次，消除双任务重复）
        panel, common_dates, _fwd_ret = self._prepare_panel()

        # [1] 准入重审（apply=False 只评估，不合格集合交给 [2] 统一降 shadow）
        reaudit_fails, reaudit_counts = self._stage_reaudit(panel, common_dates, _fwd_ret, trace_id, out_dir)

        # [2] 退化检测 + 统一落库
        dispositions, deg_stats = self._stage_degradation(panel, common_dates, reaudit_fails, trace_id, out_dir)

        # [3] 生命周期收口（AutoRetire + 冷却期回归）
        lifecycle = self._stage_lifecycle(panel, common_dates, trace_id, out_dir)

        # [4] Inspector 血缘（尽力而为）
        inspector = self._stage_inspector(trace_id, out_dir)

        # [5] 统一报告
        summary = self._write_report(
            trace_id=trace_id,
            today=today,
            reaudit_counts=reaudit_counts,
            dispositions=dispositions,
            deg_stats=deg_stats,
            lifecycle=lifecycle,
            inspector=inspector,
        )
        logger.info(
            "[L2评审质检][energy] 完成 trace_id=%s apply=%s status=%s",
            trace_id,
            self.cfg.apply,
            summary.get("status"),
        )
        return summary

    # ── [0] 面板 ─────────────────────────────────────────

    def _prepare_panel(self) -> tuple[dict[str, Any], Any, Any]:
        from fts.cli import _prepare_futures_data

        panel, common_dates, fwd_ret = _prepare_futures_data(days=self.cfg.days, symbols=self.chain_symbols)
        logger.info("[L2评审质检][energy] 面板就绪: %d 品种 × %d 交易日", len(panel), len(common_dates))
        return panel, common_dates, fwd_ret

    # ── [1] 准入重审 ─────────────────────────────────────

    def _stage_reaudit(
        self,
        panel: dict[str, Any],
        common_dates: Any,
        fwd_ret: Any,
        trace_id: str,
        out_dir: Path,
    ) -> tuple[set[str], dict[str, int]]:
        from fts.monitor.reaudit import run_reaudit

        reaudit_trace = f"{trace_id}.reaudit"
        report = run_reaudit(
            market=self.market,
            days=self.cfg.days,
            trace_id=reaudit_trace,
            apply=False,  # 重审不直接落库；不合格统一由 [2] 降 shadow（宁严勿松可回归）
            factor_ids=None,
            panel=panel,
            common_dates=common_dates,
            fwd_ret=fwd_ret,
            out_json=False,
        )
        counts = dict(report.counts or {})
        fails: set[str] = set()
        for r in report.results or []:
            if r.get("decision") != "retain":
                fails.add(r.get("factor_id", ""))
        # 明细落盘
        (out_dir / f"reaudit_energy_elite_{datetime.now():%Y%m%d}.json").write_text(
            json.dumps(
                {"trace_id": reaudit_trace, "total": report.total, "counts": counts, "results": report.results},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        logger.info("[L2评审质检][energy] [1]重审 total=%s counts=%s 不合格=%d", report.total, counts, len(fails))
        return fails, counts

    # ── [2] 退化检测 + 落库 ──────────────────────────────

    def _load_elite_with_hist(self) -> list[dict[str, Any]]:
        """加载 energy elite（active + shadow）并补 hist_ic（与质检脚本同规则）。"""
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository(market=self.market, db_path=self.db_path)
        try:
            rows_active = repo.list_factors(
                market=self.market, status="active", is_elite=True, limit=10000, sort_by="sharpe", sort_order="desc"
            ) or []
            rows_shadow = repo.list_factors(
                market=self.market, status="shadow", is_elite=True, limit=10000, sort_by="sharpe", sort_order="desc"
            ) or []
        finally:
            repo.close()

        result: list[dict[str, Any]] = []
        for f in rows_active + rows_shadow:
            fid = f.get("factor_id", "")
            payload: dict[str, Any] = dict(f)
            payload["_prev_status"] = f.get("status", "active")
            hist_ic = 0.0
            fp = self.elite_dir / f"{fid}.json"
            if fp.exists():
                try:
                    j = json.loads(fp.read_text(encoding="utf-8"))
                    ev = j.get("evaluation") or {}
                    bt = ev.get("level_1_backtest") or {}
                    hist_ic = abs(float(bt.get("ic") or 0.0))
                    payload["_json_data"] = j
                except (json.JSONDecodeError, OSError, ValueError):
                    pass
            if hist_ic <= 0:
                try:
                    hist_ic = abs(float(f.get("ic") or 0.0))
                except (TypeError, ValueError):
                    hist_ic = 0.0
            payload["_hist_ic"] = hist_ic
            result.append(payload)
        return result

    def _compute_curr_ic(self, factor_row: dict[str, Any], panel: dict[str, Any], common_dates: Any) -> float:
        """复用期货路径 _compute_current_ic，保证与质检脚本一致，不复制算法。"""
        from scripts.futures_factor_revalidation import _compute_current_ic

        j = factor_row.get("_json_data")
        if isinstance(j, dict):
            proxy = dict(j)
        else:
            proxy = {
                "factor_id": factor_row.get("factor_id", ""),
                "name": factor_row.get("name", ""),
                "code": factor_row.get("code") or "",
                "params": factor_row.get("params") or {},
                "evaluation": {"level_1_backtest": {"ic": factor_row.get("_hist_ic", 0.0)}},
            }
        return _compute_current_ic(proxy, panel, list(common_dates))

    # ── plans/49 §C3：单元粒度退化旁路（因子×子链质量矩阵） ──

    def _subchain_lifecycle_cfg(self):
        """读取 l3.subchain_quality 退化检测配置（灰度开关，enabled=false 回退全链）。"""
        try:
            from fts.factor_engine.subchain_lifecycle import load_subchain_lifecycle_config

            return load_subchain_lifecycle_config()
        except Exception:  # noqa: BLE001
            from fts.factor_engine.subchain_lifecycle import SubchainLifecycleConfig

            return SubchainLifecycleConfig()

    def _compute_curr_symbol_ic(
        self,
        factor_row: dict[str, Any],
        panel: dict[str, Any],
        common_dates: Any,
    ) -> dict[str, float]:
        """逐品种时序 IC（{品种: spearmanr}，对齐 evaluation.symbol_ic 口径，
        plans/49 §C3 供子链画像重算）。样本 <5 / 常数信号·收益 / 计算失败品种跳过。
        """
        import numpy as np
        import warnings as _w
        from scipy.stats import spearmanr

        from fts.factor_engine.factor_program import FactorExecutor

        j = factor_row.get("_json_data")
        if isinstance(j, dict):
            proxy = dict(j)
        else:
            proxy = {
                "factor_id": factor_row.get("factor_id", ""),
                "name": factor_row.get("name", ""),
                "code": factor_row.get("code") or "",
                "params": factor_row.get("params") or {},
                "evaluation": {"level_1_backtest": {"ic": factor_row.get("_hist_ic", 0.0)}},
            }
        out: dict[str, float] = {}
        for sym, df in panel.items():
            if df is None or df.empty or len(df) < 20:
                continue
            try:
                executor = FactorExecutor(proxy)
                sig = executor.execute(df, proxy.get("params", {}))
                arr = np.where(
                    np.isfinite(np.array(sig, dtype=float)),
                    np.array(sig, dtype=float),
                    np.nan,
                )
                pair = df.reindex(common_dates)
                if len(arr) < len(pair):
                    arr = np.pad(arr, (0, len(pair) - len(arr)), constant_values=np.nan)[: len(pair)]
                closes = pair["close"].values
                fwd_ret = np.zeros(len(closes))
                fwd_ret[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
                valid = ~(np.isnan(arr) | np.isnan(fwd_ret))
                s, r = arr[valid], fwd_ret[valid]
                if len(s) < 5 or np.std(s) < 1e-10 or np.std(r) < 1e-10:
                    continue
                with _w.catch_warnings():
                    _w.filterwarnings("ignore", category=RuntimeWarning)
                    ic_val, _ = spearmanr(s, r)
                if not np.isnan(ic_val):
                    out[sym] = float(ic_val)
            except Exception:  # noqa: BLE001 — 单品种失败跳过不阻断
                continue
        return out

    def _subchain_degradation(
        self,
        f: dict[str, Any],
        panel: dict[str, Any],
        common_dates: Any,
        sc_cfg: Any,
    ) -> tuple[str, list[str]]:
        """单元粒度退化判定 + 写质量矩阵行（plans/49 §C3/C2 闭环）。

        重算当前逐品种 IC → ``build_subchain_quality_rows`` 写当前行（source=review）
        → 历史时序 ``compute_subchain_degradation`` 判定。

        Returns:
            (factor_status, scope_shrink_chains)
        """
        from fts.factor_engine.factor_db.repository import SubchainQualityRepository
        from fts.factor_engine.subchain_lifecycle import compute_subchain_degradation
        from fts.factor_engine.subchain_profile import build_subchain_quality_rows

        fid = f.get("factor_id", "")
        sic = self._compute_curr_symbol_ic(f, panel, common_dates)
        if not sic:
            return "keep", []
        rows = build_subchain_quality_rows(fid, "energy", sic, source="review")
        qrepo = SubchainQualityRepository(market=self.market, db_path=self.db_path)
        try:
            qrepo.save_subchain_quality(rows)
            history = qrepo.query_subchain_quality(fid, "energy")
        finally:
            qrepo.close()
        r = compute_subchain_degradation(history, sc_cfg)
        if r["factor_status"] in ("degrade", "scope_shrink"):
            logger.info("[L2评审质检][energy] 子链退化 factor=%s %s", fid, r["detail"])
        return r["factor_status"], r["scope_shrink_chains"]

    def _shrink_scope(self, f: dict[str, Any], remove_chains: list[str], trace_id: str) -> None:
        """scope 收缩闭环（plans/49 §C2）：更新 metadata.subchain_scope 剔除失效链。

        47 号调制矩阵在 Step 2b 消费最新 metadata 自动重算；scope="all"（≥3 链 effective
        标记）时按当前画像 effective 链剔除，剩余 ≥ all_chains_effective_min 保持 "all"。
        """
        from fts.factor_engine.factor_db.repository import FactorRepository, FactorStatusRepository
        from fts.factor_engine.subchain_profile import SubchainProfileConfig

        fid = f.get("factor_id", "")
        if not remove_chains:
            return
        repo = FactorRepository(market=self.market, db_path=self.db_path)
        srepo = FactorStatusRepository(market=self.market, db_path=self.db_path)
        try:
            row = repo.get_factor(fid)
            if not row:
                return
            meta = row.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            if not isinstance(meta, dict):
                meta = {}
            profile = meta.get("subchain_ic_profile") or {}
            eff_chains = (
                [c for c, st in profile.items() if bool(st.get("effective"))]
                if isinstance(profile, dict)
                else []
            )
            scope = meta.get("subchain_scope")
            base = eff_chains if eff_chains else (scope if isinstance(scope, list) else [])
            remove = set(remove_chains)
            new_scope_list = [c for c in base if c not in remove]
            if not new_scope_list:
                # 全部失效链被剔除 → 交由 degraded 路径（此处不动，避免半状态）
                return
            cfg = SubchainProfileConfig()
            new_scope: Any = (
                "all" if len(new_scope_list) >= cfg.all_chains_effective_min else new_scope_list
            )
            meta["subchain_scope"] = new_scope
            meta["subchain_shrink"] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "trace_id": trace_id,
                "removed": remove_chains,
            }
            repo.update_factor(fid, {"metadata": meta})
            srepo.log_transition(
                fid,
                str(f.get("_prev_status", "active")),
                str(f.get("_prev_status", "active")),
                f"子链 scope 收缩: 剔除 {remove_chains}",
                snapshot={"subchain_shrink": meta["subchain_shrink"]},
            )
            logger.info("[L2评审质检][energy] scope 收缩 factor=%s removed=%s → %s", fid, remove_chains, new_scope)
        finally:
            repo.close()
            srepo.close()

    def _stage_degradation(
        self,
        panel: dict[str, Any],
        common_dates: Any,
        reaudit_fails: set[str],
        trace_id: str,
        out_dir: Path,
    ) -> tuple[list[FactorDisposition], dict[str, int]]:
        factors = self._load_elite_with_hist()
        tracker = self._ensure_tracker()
        dispositions: list[FactorDisposition] = []
        rows: list[dict[str, Any]] = []
        sc_cfg = self._subchain_lifecycle_cfg()
        for f in factors:
            fid = f.get("factor_id", "")
            name = f.get("name", fid)
            hist_ic = float(f.get("_hist_ic") or 0.0)
            try:
                curr_ic = self._compute_curr_ic(f, panel, common_dates)
            except Exception:  # noqa: BLE001
                curr_ic = float("nan")
            if not _isfinite(curr_ic):
                curr_ic = 0.0
            snap = tracker.get(fid) or {}
            slope_grade = str(snap.get("decay_grade") or "normal")
            disp = decide_factor(
                factor_id=fid,
                name=name,
                prev_status="active" if f.get("_prev_status") == "active" else "shadow",
                reaudit_fail=fid in reaudit_fails,
                curr_ic=curr_ic,
                hist_ic=hist_ic,
                slope_grade=slope_grade if slope_grade in ("observe", "retired") else "normal",
                cfg=self.cfg,
            )
            # plans/49 §C3：单元粒度退化旁路（灰度 l3.subchain_quality.enabled 时）——
            # 全有效链退化 → 强制 degrade；部分链失效 → scope 收缩（仍 active，闭环传导 47 调制）
            if sc_cfg.enabled:
                try:
                    sc_status, sc_shrink = self._subchain_degradation(f, panel, common_dates, sc_cfg)
                    if sc_status == "degrade" and disp.decision == "active":
                        disp.decision = "degraded"
                        disp.reasons.append("子链全有效链退化")
                    elif sc_status == "scope_shrink":
                        self._shrink_scope(f, sc_shrink, trace_id)
                except Exception as se:  # noqa: BLE001 — 旁路失败回退全链判定
                    logger.warning("[L2评审质检][energy] 子链退化旁路失败（回退全链）: %s", se)
            dispositions.append(disp)
            rows.append(
                {
                    "factor_id": fid,
                    "name": name,
                    "prev_status": disp.prev_status,
                    "decision": disp.decision,
                    "reasons": "+".join(disp.reasons),
                    **disp.metrics,
                }
            )
        (out_dir / f"degradation_revalidation_energy_{datetime.now():%Y%m%d}.csv").write_text(
            _rows_to_csv(rows),
            encoding="utf-8-sig",
        )

        # 统一落库（apply=True 才生效；shadow/degraded/retire 处置）
        stats = self._apply_dispositions(dispositions, trace_id, out_dir)
        logger.info("[L2评审质检][energy] [2]退化检测 待审=%d stats=%s", len(dispositions), stats)
        return dispositions, stats

    def _apply_dispositions(
        self,
        dispositions: list[FactorDisposition],
        trace_id: str,
        out_dir: Path,
    ) -> dict[str, int]:
        from fts.factor_engine.factor_db.repository import FactorRepository, FactorStatusRepository

        stats = {"active": 0, "shadow": 0, "degraded": 0, "retire": 0, "failed": 0}
        if not self.cfg.apply:
            # dry-run（灰度）：仅统计处置结论，不落库、不改 tracking
            for d in dispositions:
                stats[d.decision] = stats.get(d.decision, 0) + 1
            return stats
        repo = FactorRepository(market=self.market, db_path=self.db_path)
        srepo = FactorStatusRepository(market=self.market, db_path=self.db_path)
        dep_dir = self.elite_dir / "_deprecated"
        try:
            for d in dispositions:
                fid = d.factor_id
                meta_update = {
                    "degradation_revalidation": {
                        "at": datetime.now(timezone.utc).isoformat(),
                        "trace_id": trace_id,
                        "status": d.decision,
                        "reasons": "+".join(d.reasons),
                        **d.metrics,
                    }
                }
                try:
                    f = repo.get_factor(fid)
                    if not f:
                        stats["failed"] += 1
                        continue
                    meta = f.get("metadata") or {}
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except (json.JSONDecodeError, TypeError):
                            meta = {}
                    if not isinstance(meta, dict):
                        meta = {}
                    meta["degradation_revalidation"] = meta_update["degradation_revalidation"]

                    if d.decision == "degraded":
                        repo.update_factor(fid, {"is_elite": False, "status": "degraded", "metadata": meta})
                        srepo.update_factor_status(fid, "degraded")
                        srepo.log_transition(
                            fid, d.prev_status, "degraded", f"统一管道退化降级: {'+'.join(d.reasons)}",
                            snapshot={"degradation_revalidation": meta_update["degradation_revalidation"]},
                        )
                        fp = self.elite_dir / f"{fid}.json"
                        if fp.exists():
                            dep_dir.mkdir(parents=True, exist_ok=True)
                            dest = dep_dir / fp.name
                            if dest.exists():
                                dest.unlink()
                            shutil.move(str(fp), str(dest))
                        stats["degraded"] += 1
                    elif d.decision == "shadow":
                        repo.update_factor(fid, {"status": "shadow", "metadata": meta})
                        srepo.update_factor_status(fid, "shadow")
                        srepo.log_transition(
                            fid, d.prev_status, "shadow", f"统一管道退化观察: {'+'.join(d.reasons)}",
                            snapshot={"degradation_revalidation": meta_update["degradation_revalidation"]},
                        )
                        stats["shadow"] += 1
                    elif d.decision == "retire":
                        if repo.retire_factor(fid, reason=f"统一管道退役: {'+'.join(d.reasons)}", elite_dir=self.elite_dir):
                            stats["retire"] += 1
                        else:
                            stats["failed"] += 1
                    else:  # active
                        repo.update_factor(fid, {"metadata": meta})
                        stats["active"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("[L2评审质检][energy] 因子 %s 落库异常: %s", fid, e)
                    stats["failed"] += 1
        finally:
            repo.close()
            srepo.close()
        return stats

    # ── [3] 生命周期收口 ─────────────────────────────────

    def _ensure_tracker(self) -> Any:
        from fts.monitor.elite_tracker import AutoRetireConfig, EliteFactorTracker

        if self._tracker is None:
            self._tracker = EliteFactorTracker(
                tracking_dir=str(self.tracking_dir),
                retire_config=AutoRetireConfig(
                    cooldown_days=self.cfg.cooldown_days,
                    observe_slope=self.cfg.observe_slope,
                    retire_slope=self.cfg.retire_slope,
                ),
            )
        return self._tracker

    def _stage_lifecycle(
        self,
        panel: dict[str, Any],
        common_dates: Any,
        trace_id: str,
        out_dir: Path,
    ) -> dict[str, Any]:
        from fts.monitor.elite_tracker import AutoRetireManager

        tracker = self._ensure_tracker()
        retire_mgr = AutoRetireManager(tracker)

        # 冷却期回归扫描（degraded 到期自动回归复核）
        regressed, to_retire, held = self._scan_cooldown_regression(panel, common_dates, trace_id, out_dir)

        # AutoRetire 收口（apply=True 才真正退役）
        retired: list[str] = []
        if self.cfg.apply:
            try:
                retired = retire_mgr.run() or []
            except Exception as e:  # noqa: BLE001
                logger.warning("[L2评审质检][energy] AutoRetire 异常: %s", e)
        else:
            retired = self._auto_retire_preview(tracker)

        if retired and self.cfg.apply:
            from fts.factor_engine.factor_db.repository import FactorRepository

            repo = FactorRepository(market=self.market, db_path=self.db_path)
            try:
                n = 0
                for fid in retired:
                    if repo.retire_factor(fid, reason="统一管道AutoRetire退役", elite_dir=self.elite_dir):
                        n += 1
                logger.warning("[L2评审质检][energy] AutoRetire 退役已同步: %d/%d", n, len(retired))
            finally:
                repo.close()

        result = {
            "cooldown_regressed": len(regressed),
            "cooldown_retired": len(to_retire),
            "cooldown_held": len(held),
            "auto_retire": len(retired),
            "regressed_ids": regressed,
        }
        logger.info("[L2评审质检][energy] [3]生命周期 回归=%d 退役=%d 保持=%d auto_retire=%d",
                    len(regressed), len(to_retire), len(held), len(retired))
        return result

    def _auto_retire_preview(self, tracker: Any) -> list[str]:
        """dry-run 预览：按 decay_grade=retired 预判 AutoRetire 名单（不落库）。"""
        return [s.get("factor_id", "") for s in tracker.list_all() if s.get("decay_grade") == "retired"]

    def _scan_cooldown_regression(
        self,
        panel: dict[str, Any],
        common_dates: Any,
        trace_id: str,
        out_dir: Path,
    ) -> tuple[list[str], list[str], list[str]]:
        """degraded 因子冷却期满（>= cooldown_days）→ 重新验证：
        达标 → 恢复 active（is_elite=true + JSON 自 _deprecated 移回）；
        不达标 → cooldown_attempts+1，达到 cooldown_max_attempts → retire；
        否则保持 degraded 重新计时。apply=False 仅报告候选，不落库。
        """
        from fts.factor_engine.factor_db.repository import FactorRepository, FactorStatusRepository

        tracker = self._ensure_tracker()
        now = datetime.now(timezone.utc)
        regressed: list[str] = []
        to_retire: list[str] = []
        held: list[str] = []
        dep_dir = self.elite_dir / "_deprecated"

        repo = FactorRepository(market=self.market, db_path=self.db_path)
        srepo = FactorStatusRepository(market=self.market, db_path=self.db_path)
        try:
            for snap in tracker.list_all():
                if snap.get("status") != "degraded":
                    continue
                fid = snap.get("factor_id", "")
                last = snap.get("last_updated")
                if not last:
                    continue
                try:
                    last_dt = datetime.fromisoformat(last)
                except (TypeError, ValueError):
                    continue
                if (now - last_dt).days < self.cfg.cooldown_days:
                    held.append(fid)  # 未到期：保持 degraded
                    continue

                f = repo.get_factor(fid)
                code = (f or {}).get("code") or ""
                if not code:
                    held.append(fid)
                    continue
                try:
                    curr_ic = self._compute_curr_ic({"factor_id": fid, "code": code}, panel, common_dates)
                except Exception:  # noqa: BLE001
                    curr_ic = 0.0
                passed = abs(curr_ic) >= self.cfg.ic_threshold

                if passed:
                    regressed.append(fid)
                    if self.cfg.apply:
                        repo.update_factor(fid, {"is_elite": True, "status": "active"})
                        srepo.update_factor_status(fid, "active")
                        srepo.log_transition(fid, "degraded", "active", "冷却期满回归达标", snapshot={"trace_id": trace_id})
                        dep = dep_dir / f"{fid}.json"
                        if dep.exists():
                            shutil.move(str(dep), self.elite_dir / dep.name)
                        snap["status"] = "active"
                        snap["cooldown_attempts"] = 0
                        snap["last_updated"] = now.isoformat()
                        tracker._write_snapshot(fid, snap)  # noqa: SLF001
                else:
                    attempts = int(snap.get("cooldown_attempts", 0)) + 1
                    if attempts >= self.cfg.cooldown_max_attempts:
                        to_retire.append(fid)
                        if self.cfg.apply:
                            repo.retire_factor(fid, reason="冷却期满持续不达标", elite_dir=self.elite_dir)
                            snap["status"] = "retired"
                            snap["last_updated"] = now.isoformat()
                            tracker._write_snapshot(fid, snap)  # noqa: SLF001
                    else:
                        held.append(fid)
                        if self.cfg.apply:
                            snap["cooldown_attempts"] = attempts
                            snap["last_updated"] = now.isoformat()
                            tracker._write_snapshot(fid, snap)  # noqa: SLF001
        finally:
            repo.close()
            srepo.close()
        return regressed, to_retire, held

    # ── [4] Inspector 血缘 ───────────────────────────────

    def _stage_inspector(self, trace_id: str, out_dir: Path) -> dict[str, Any]:
        try:
            from fts.factor_engine.factor_inspector import FactorInspector

            insp = FactorInspector(market=self.market)
            result = insp.inspect_and_downgrade(
                threshold=self.cfg.sharpe_drop,
                market=self.market,
                commit=self.cfg.apply,
            )
            logger.info(
                "[L2评审质检][energy] [4]Inspector audited=%s degraded=%s skipped=%s",
                result.get("audited_count", result.get("total_audited", "?")),
                result.get("degraded_count", len(result.get("degraded_factors", []) or [])),
                result.get("skipped_count", 0),
            )
            return dict(result)
        except Exception as e:  # noqa: BLE001
            logger.warning("[L2评审质检][energy] [4]Inspector 跳过: %s", e)
            return {"skipped": True, "error": str(e)}

    # ── [5] 报告 ─────────────────────────────────────────

    def _write_report(
        self,
        *,
        trace_id: str,
        today: str,
        reaudit_counts: dict[str, int],
        dispositions: list[FactorDisposition],
        deg_stats: dict[str, int],
        lifecycle: dict[str, Any],
        inspector: dict[str, Any],
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "trace_id": trace_id,
            "date": today,
            "apply": self.cfg.apply,
            "status": "completed",
            "stages": {
                "reaudit": reaudit_counts,
                "degradation": deg_stats,
                "lifecycle": lifecycle,
                "inspector": inspector,
            },
            "factors": [
                {
                    "factor_id": d.factor_id,
                    "name": d.name,
                    "prev_status": d.prev_status,
                    "decision": d.decision,
                    "reasons": d.reasons,
                    **d.metrics,
                }
                for d in dispositions
            ],
        }
        (self.out_dir / f"energy_qa_review_summary_{today}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        md_lines = [
            f"# 能化链评审+质检统一报告 {today}",
            "",
            f"- trace_id: `{trace_id}`",
            f"- 模式: {'apply(落库)' if self.cfg.apply else 'dry-run(灰度)'}",
            f"- 重审: {reaudit_counts}",
            f"- 退化处置: {deg_stats}",
            f"- 生命周期: {lifecycle}",
            f"- Inspector: skipped={inspector.get('skipped', False)}",
            "",
            "## 逐因子处置",
            "",
            "| 因子 | 前状态 | 处置 | 依据 |",
            "|---|---|---|---|",
        ]
        for d in dispositions:
            md_lines.append(f"| {d.name}({d.factor_id}) | {d.prev_status} | {d.decision} | {'+'.join(d.reasons)} |")
        md_lines.append("")
        (self.out_dir / f"energy_qa_review_summary_{today}.md").write_text("\n".join(md_lines), encoding="utf-8")
        return summary


# ─── 工具 ─────────────────────────────────────────────────


def _isfinite(v: float) -> bool:
    import math

    return math.isfinite(v)


def _rows_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    cols = list(rows[0].keys())
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")) for c in cols))
    return "\n".join(lines)
