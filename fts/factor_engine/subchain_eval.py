"""
fts/factor_engine/subchain_eval.py — 批量子链质量评估（FTS 标准工作流，2026-08-19 沉淀）

对所有 active 因子批量计算逐品种 IC → 子链画像 → 落库 ``subchain_factor_quality``
质量矩阵（plans/49 §A2），补齐画像覆盖，供 L2 评审质检退化检测（单元粒度）与
L3 子链差异化权重调制消费。

流程（对齐 2026-08-19 专项验证的批量子链评估过程）：
    [0] 公共面板（一次，复用评审质检管道，12 品种）
    [1] 拉取目标因子（默认 status=active）
    [2] 逐因子计算逐品种 IC（复用 ``EnergyQaReviewPipeline._compute_curr_symbol_ic`` 口径）
    [3] ``build_subchain_quality_rows`` → ``SubchainQualityRepository`` 落库（UPSERT 幂等）
    [4] 汇总报告（scope 分布 / 有效子链统计 / 无有效链因子清单）

三门槛护栏经 ``subchain_profile.SubchainProfileConfig`` 参数化（SSOT：
config/settings.yaml → subchain_profile.*；v2.105.0+16 min_chain_ic 0.10→0.02——
2026-08-19 验证：原 0.10 过严，|mean_IC| 0.02~0.08 的有效全链/部分链因子被误判
为无有效链；0.02 下 8 个无有效链因子中 6 个恢复有效，fut_wma_cross 恢复全链）。

无有效链因子处置原则（用户确认）：不自动降级，metadata 标记
``subchain_scope=unknown`` + ``subchain_eval.pending_validation=true``，
交人工/延长窗口进一步验证（宁严勿松但不武断降级）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SubchainEvalConfig(BaseModel):
    """批量子链质量评估配置（全部可配置，Pydantic）。"""

    market: str = "energy"           # 市场（energy/futures）
    days: int = 300                  # 面板回溯窗口（交易日，与评审质检一致）
    status: str = "active"           # 因子状态过滤（active=仅 active；all=全部）
    limit: int = 10000               # 因子拉取上限
    out_dir: str = "reports/energy_chain/{date}/subchain_eval"


def _load_chain_symbols(market: str = "energy") -> dict[str, list[str]]:
    """懒加载子链品种映射（P1 方案：scope resolver 统一加载；futures→sector_map 17 链，
    energy→sub_symbols 四大子链；失败回退 ENERGY_CHAIN_SUB_SYMBOLS 内置）。"""
    try:
        from fts.factor_engine.scope_domain.resolver import resolve_chain_map

        mapped = resolve_chain_map(market)
        if mapped:
            return mapped
    except Exception:  # noqa: BLE001
        pass
    try:
        from fts.factor_engine.portfolio_loop import ENERGY_CHAIN_SUB_SYMBOLS

        return ENERGY_CHAIN_SUB_SYMBOLS
    except Exception:  # noqa: BLE001 — 无子链映射回退空（行生成安全）
        logger.warning("[子链评估] 子链品种映射加载失败，回退空映射")
        return {}


class _FuturesEvalAdapter:
    """futures 批量子链评估面板适配器（复用评审质检逐品种 IC 算法，不复制）。

    EnergyQaReviewPipeline 为 energy 专属（链映射/面板为 energy 14 品种），
    futures 场景以本适配器提供同接口 _prepare_panel/_compute_curr_symbol_ic。
    """

    def __init__(self, days: int) -> None:
        self.days = days
        self._ic_engine: Any = None

    def _prepare_panel(self):
        from fts.cli import _prepare_futures_data

        return _prepare_futures_data(days=self.days)

    def _compute_curr_symbol_ic(self, factor_row: dict[str, Any], panel: dict[str, Any], common_dates: Any):
        from fts.factor_engine.energy_qa_review import EnergyQaReviewPipeline

        if self._ic_engine is None:
            self._ic_engine = EnergyQaReviewPipeline(config=None)
        return self._ic_engine._compute_curr_symbol_ic(factor_row, panel, common_dates)


@dataclass
class FactorEvalResult:
    """单因子子链评估结果。"""

    factor_id: str
    name: str
    n_symbols: int = 0               # 有效逐品种 IC 数
    effective_chains: list[str] = field(default_factory=list)
    scope: Any = "unknown"
    n_rows: int = 0                  # 落库质量矩阵行数（0=无子链映射）
    skipped: bool = False            # True=无逐品种 IC 跳过（区别于评估后无有效链）
    error: str = ""


class SubchainEvalRunner:
    """批量子链质量评估执行器（FTS 标准工作流）。

    Args:
        config: SubchainEvalConfig（None 用默认）
        db_path: 注入测试用隔离库路径（None 走 market 路由）
    """

    def __init__(
        self,
        config: Optional[SubchainEvalConfig] = None,
        db_path: Optional[str | Path] = None,
    ) -> None:
        self.cfg = config or SubchainEvalConfig()
        self.db_path = db_path
        self.project_root = Path(__file__).resolve().parent.parent.parent

    # ── 主入口 ─────────────────────────────────────────

    def run(self, trace_id: Optional[str] = None) -> dict[str, Any]:
        """执行批量子链质量评估（[0]→[4] 全流程）。

        Returns:
            {status, trace_id, factors_total, factors_ok, factors_failed,
             rows_saved, scope_distribution, no_effective_chains}
        """
        trace_id = trace_id or f"fts.subchain_eval_{datetime.now():%Y%m%d%H%M%S}"
        today = datetime.now().strftime("%Y-%m-%d")
        out_dir = self.project_root / self.cfg.out_dir.format(date=today)
        out_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "[子链评估][%s] 启动 trace_id=%s status=%s days=%d",
            self.cfg.market, trace_id, self.cfg.status, self.cfg.days,
        )

        # [0] 公共面板（复用评审质检管道，消除重复加载）
        pipe = self._pipeline()
        panel, common_dates, _fwd_ret = pipe._prepare_panel()
        logger.info("[子链评估][%s] 面板就绪: %d 品种 × %d 交易日", self.cfg.market, len(panel), len(common_dates))

        # [1] 拉取因子
        factors = self._load_factors()
        logger.info("[子链评估][%s] 因子数=%d", self.cfg.market, len(factors))

        # [2][3] 逐因子评估 + 落库
        results, rows_saved = self._evaluate(factors, pipe, panel, common_dates)

        # [4] 汇总报告
        summary = self._write_report(
            results=results,
            rows_saved=rows_saved,
            trace_id=trace_id,
            today=today,
            out_dir=out_dir,
        )
        logger.info(
            "[子链评估][%s] 完成 trace_id=%s factors=%d rows=%d failed=%d",
            self.cfg.market, trace_id, len(results), rows_saved,
            sum(1 for r in results if r.error),
        )
        return summary

    # ── [0] 面板/管道复用 ───────────────────────────────

    def _pipeline(self):
        """面板/管道实例：energy 复用 EnergyQaReviewPipeline；futures 走轻量面板适配器。"""
        from fts.factor_engine.energy_qa_review import EnergyQaReviewConfig, EnergyQaReviewPipeline

        if self.cfg.market == "energy":
            return EnergyQaReviewPipeline(
                config=EnergyQaReviewConfig(days=self.cfg.days),
                db_path=self.db_path,
            )
        return _FuturesEvalAdapter(self.cfg.days)

    # ── [1] 因子加载 ────────────────────────────────────

    def _load_factors(self) -> list[dict[str, Any]]:
        """从因子库拉取目标状态因子（默认 active）。"""
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository(market=self.cfg.market, db_path=self.db_path)
        try:
            status = None if self.cfg.status == "all" else self.cfg.status
            rows = repo.list_factors(
                market=self.cfg.market,
                status=status,
                limit=self.cfg.limit,
                sort_by="sharpe",
                sort_order="desc",
            ) or []
        finally:
            repo.close()
        # 统一构造 _compute_curr_symbol_ic 可消费的 factor_row（params 已由仓储解析为 dict）
        out: list[dict[str, Any]] = []
        for f in rows:
            out.append(
                {
                    "factor_id": f.get("factor_id", ""),
                    "name": f.get("name", ""),
                    "code": f.get("code") or "",
                    "params": f.get("params") or {},
                    "_hist_ic": abs(float(f.get("ic") or 0.0)),
                }
            )
        return out

    # ── [2][3] 逐因子评估 + 落库 ───────────────────────

    def _evaluate(
        self,
        factors: list[dict[str, Any]],
        pipe: Any,
        panel: dict[str, Any],
        common_dates: Any,
    ) -> tuple[list[FactorEvalResult], int]:
        """逐因子计算逐品种 IC → 子链画像 → 落库质量矩阵。"""
        from fts.factor_engine.factor_db.repository import SubchainQualityRepository
        from fts.factor_engine.subchain_profile import (
            SubchainProfileConfig,
            build_subchain_quality_rows,
            compute_subchain_profile,
        )

        cfg = SubchainProfileConfig()  # 三门槛 SSOT（min_chain_ic=0.02，v2.105.0+16）
        chain_symbols = _load_chain_symbols(self.cfg.market)
        qrepo = SubchainQualityRepository(market=self.cfg.market, db_path=self.db_path)
        results: list[FactorEvalResult] = []
        rows_saved = 0
        try:
            for f in factors:
                fid = f.get("factor_id", "")
                name = f.get("name", fid)
                try:
                    sic = pipe._compute_curr_symbol_ic(f, panel, common_dates)
                except Exception as se:  # noqa: BLE001 — 单因子失败不阻断批量
                    logger.warning("[子链评估][%s] 逐品种 IC 计算失败 %s: %s", self.cfg.market, fid, se)
                    results.append(FactorEvalResult(factor_id=fid, name=name, error=str(se)))
                    continue
                if not sic:
                    logger.info("[子链评估][%s] %s 无有效逐品种 IC（跳过）", self.cfg.market, fid)
                    results.append(FactorEvalResult(factor_id=fid, name=name, skipped=True))
                    continue
                # 画像 + 质量矩阵行
                profile = compute_subchain_profile(fid, sic, chain_symbols, cfg)
                rows = build_subchain_quality_rows(
                    fid, self.cfg.market, sic, chain_symbols=chain_symbols, cfg=cfg, source="review"
                )
                n = 0
                if rows:
                    n = qrepo.save_subchain_quality(rows)
                # metadata 更新（scope/评估时间/pending_validation，不改 status）
                self._update_metadata(fid, profile)
                eff_chains = [c for c, st in profile.chain_stats.items() if st.effective]
                results.append(
                    FactorEvalResult(
                        factor_id=fid,
                        name=name,
                        n_symbols=len(sic),
                        effective_chains=eff_chains,
                        scope=profile.subchain_scope,
                        n_rows=n,
                    )
                )
                rows_saved += n
        finally:
            qrepo.close()
        return results, rows_saved

    def _update_metadata(self, factor_id: str, profile: Any) -> None:
        """更新 metadata 子链画像字段（scope/评估时间；无有效链标记 pending_validation）。"""
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository(market=self.cfg.market, db_path=self.db_path)
        try:
            row = repo.get_factor(factor_id)
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
            meta["subchain_ic_profile"] = {
                c: st.model_dump() for c, st in profile.chain_stats.items()
            }
            meta["subchain_scope"] = profile.subchain_scope
            meta["subchain_specific"] = profile.subchain_specific
            meta["subchain_eval"] = {
                "at": datetime.now().isoformat(),
                "source": "batch_eval",
                "effective_chains": [c for c, st in profile.chain_stats.items() if st.effective],
                "pending_validation": profile.subchain_scope == "unknown",
            }
            repo.update_factor(factor_id, {"metadata": meta})
        finally:
            repo.close()

    # ── [4] 报告 ────────────────────────────────────────

    def _write_report(
        self,
        results: list[FactorEvalResult],
        rows_saved: int,
        trace_id: str,
        today: str,
        out_dir: Path,
    ) -> dict[str, Any]:
        """生成汇总报告（JSON 落盘 + 返回 summary）。"""
        scope_dist: dict[str, int] = {}
        no_effective: list[dict[str, Any]] = []
        for r in results:
            scope_key = r.scope if isinstance(r.scope, str) else f"部分链({len(r.effective_chains)})"
            scope_dist[scope_key] = scope_dist.get(scope_key, 0) + 1
            # 无有效链清单仅含「已评估（有 IC）但零有效链」因子（pending_validation 候选）
            if not r.skipped and not r.effective_chains and not r.error:
                no_effective.append({"factor_id": r.factor_id, "name": r.name})

        report = {
            "trace_id": trace_id,
            "date": today,
            "market": self.cfg.market,
            "status_filter": self.cfg.status,
            "factors_total": len(results),
            "factors_failed": sum(1 for r in results if r.error),
            "rows_saved": rows_saved,
            "scope_distribution": scope_dist,
            "no_effective_chains": no_effective,
            "details": [
                {
                    "factor_id": r.factor_id,
                    "name": r.name,
                    "n_symbols": r.n_symbols,
                    "effective_chains": r.effective_chains,
                    "scope": r.scope,
                    "rows": r.n_rows,
                    "error": r.error or None,
                }
                for r in results
            ],
        }
        (out_dir / f"subchain_eval_{today}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "[子链评估][%s] 报告落盘: %s (scope=%s)",
            self.cfg.market, out_dir / f"subchain_eval_{today}.json", scope_dist,
        )
        return report


__all__ = ["SubchainEvalConfig", "SubchainEvalRunner", "FactorEvalResult"]
