"""
fts.factor_engine.imports.import_pipeline — 外部因子常态化导入管道（v2.105.0+32，GAP-126 扩展）。

背景
----
scripts/extract_* 六个脚本（academic/broker/mc/tinysoft/vnpy/wind）为一次性离线产物
（seeds/futures/*.yaml），无准入评估、无血缘、无调度。本管道将其升级为常态化导入：

 ① 候选加载   — 6 个 YAML（seed_loader 加载），source=extract_<family>
 ② 字段权威校验 — validate_field_availability：L2 缺失字段（fundamental 9 字段）禁依赖，
                 防空谈因子（权威性原则：可消费数据仅限 QuantData 可得字段）
 ③ 去重/幂等   — factor_catalog code_hash + l1_injected 已有 candidate_id 比对
 ④ 注入       — 写 l1_injected/<cand_id>.json + factor_pool.json 登记（status=pending）
                 → L2 种子评估链（run_seed_stage）消费，Q1-Q10 准入 + admission 分级
                 由既有链保证，不重复实现评估
 ⑤ 血缘与报告 — trace_id 贯穿，每源统计落 memory/logs/imports/YYYY-MM-DD.log

调度: 月度 cron（import_external_factors_job），幂等可重入。
默认 dry-run（apply=False 只统计），apply=True 才落注入文件。

版本: v1.0.0
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ...data_sources.quantdata_provider import validate_field_availability

logger = logging.getLogger(__name__)

# 六个 extract_* 脚本的 YAML 产物（source 家族 → 相对路径）
EXTERNAL_SEED_SOURCES: dict[str, str] = {
    "academic_papers": "seeds/futures/academic_papers.yaml",
    "broker_reports": "seeds/futures/broker_reports.yaml",
    "mc_cta": "seeds/futures/mc_cta.yaml",
    "tinysoft": "seeds/futures/tinysoft.yaml",
    "vnpy_cta": "seeds/futures/vnpy_cta.yaml",
    "wind_cta": "seeds/futures/wind_cta.yaml",
}

DEFAULT_INJECT_DIR = Path("memory/knowledge/factors/l1_injected")
DEFAULT_POOL_PATH = Path("memory/knowledge/factors/factor_pool.json")
IMPORT_LOG_DIR = Path("memory/logs/imports")


def _make_candidate_id(name: str, code: str) -> str:
    """确定性候选 ID：cand_<md5(name+code)[:8]>（幂等去重键）。"""
    digest = hashlib.md5(f"{name}|{code}".encode("utf-8")).hexdigest()[:8]
    return f"cand_{digest}"


class ExternalFactorImportRunner:
    """外部因子常态化导入管道。

    Args:
        market: 目标市场（futures，默认）
        inject_dir: L1 注入目录（默认 memory/knowledge/factors/l1_injected）
        factor_pool_path: factor_pool.json 路径
        trace_id: 全链路 trace_id（默认自动生成）
        dry_run: 仅统计不写入（默认 True 安全；apply=True 或 dry_run=False 时注入）
    """

    def __init__(
        self,
        market: str = "futures",
        inject_dir: Optional[Path] = None,
        factor_pool_path: Optional[Path] = None,
        trace_id: Optional[str] = None,
        dry_run: bool = True,
    ) -> None:
        from ..state import generate_trace_id

        self.market = market
        self.inject_dir = Path(inject_dir or DEFAULT_INJECT_DIR)
        self.factor_pool_path = Path(factor_pool_path or DEFAULT_POOL_PATH)
        self.trace_id = trace_id or f"fts.import.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        self.dry_run = dry_run
        self._trace_id_fn = generate_trace_id

    # ─── ① 候选加载 ──

    def _load_yaml_candidates(
        self, source: str, yaml_path: Path
    ) -> list[dict[str, Any]]:
        """加载单个 YAML 种子文件 → SeedCandidate 字典列表（source=extract_<family>）。"""
        from ..seed_loader import load_factors_from_yaml

        if not yaml_path.exists():
            logger.warning("[import] YAML 缺失: %s", yaml_path)
            return []
        try:
            programs = load_factors_from_yaml(str(yaml_path), trace_id=self.trace_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[import] YAML 加载失败 [%s]: %s", yaml_path, e)
            return []
        candidates: list[dict[str, Any]] = []
        for fp in programs:
            signature = fp.get("signature") or {}
            candidates.append(
                {
                    "candidate_id": _make_candidate_id(fp.get("name", ""), fp.get("code", "")),
                    "name": fp.get("name", ""),
                    "code": fp.get("code", ""),
                    "params": fp.get("params", {}),
                    "signature": signature,
                    "economic_logic": fp.get("economic_logic", {}),
                    "source": f"extract_{source}",
                    "market": self.market,
                    "trace_id": self.trace_id,
                    "created_at": datetime.now().isoformat(),
                }
            )
        return candidates

    # ─── ② 字段权威校验 ──

    @staticmethod
    def _field_authority(candidate: dict[str, Any]) -> dict[str, list[str]]:
        signature = candidate.get("signature") or {}
        fields = list(signature.get("input_fields") or [])
        return validate_field_availability(fields)

    # ─── ③ 去重 ──

    def _existing_ids(self) -> set[str]:
        """已存在 candidate_id（l1_injected 目录 + factor_pool.json）。"""
        existing: set[str] = set()
        if self.inject_dir.exists():
            existing.update(p.stem for p in self.inject_dir.glob("cand_*.json"))
        pool = self._load_pool()
        existing.update(
            f.get("factor_id") for f in pool.get("factors", []) if f.get("factor_id")
        )
        return existing

    def _load_pool(self) -> dict[str, Any]:
        if self.factor_pool_path.exists():
            try:
                with open(self.factor_pool_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"version": "1", "updated_at": "", "factors": [], "total_count": 0, "pending_count": 0}

    # ─── ④ 注入 ──

    def _persist_candidate(self, candidate: dict[str, Any]) -> None:
        """写 l1_injected/<cand_id>.json + factor_pool.json 登记（复用 L1 存储契约）。"""
        from ..meta_loop import FactorPoolEntry, FactorPoolManager

        self.inject_dir.mkdir(parents=True, exist_ok=True)
        cand = dict(candidate)
        cand["injected_to_l2"] = True
        cand["injected_at"] = datetime.now().isoformat()
        cand_file = self.inject_dir / f"{cand['candidate_id']}.json"
        with open(cand_file, "w", encoding="utf-8") as f:
            json.dump(cand, f, ensure_ascii=False, indent=2, default=str)

        entry = FactorPoolEntry(
            factor_id=cand["candidate_id"],
            name=cand.get("name", ""),
            source=cand.get("source", "extract_external"),
            parent_topic=None,
            debate_round_ref=None,
            debate_gap=None,
            economic_logic=cand.get("economic_logic", {}),
            priority=2,  # 外部成熟因子种子，中等优先级
            status="pending",
            market=self.market,
            trace_id=self.trace_id,
            created_at=cand.get("created_at", datetime.now().isoformat()),
            updated_at=datetime.now().isoformat(),
        )
        manager = FactorPoolManager(factor_pool_path=str(self.factor_pool_path))
        manager.add_entry(entry)

    # ─── 主流程 ──

    def run(
        self,
        sources: Optional[list[str]] = None,
        apply: Optional[bool] = None,
    ) -> dict[str, Any]:
        """执行导入管道。

        Args:
            sources: 家族子集（None=全部 6 源）
            apply: 覆盖 dry_run 开关（True=注入，False=仅统计）

        Returns:
            {"trace_id", "sources": {family: {total, field_blocked, duplicate, injected, failed}},
             "summary": {...}}
        """
        do_apply = (not self.dry_run) if apply is None else apply
        target = {k: v for k, v in EXTERNAL_SEED_SOURCES.items() if sources is None or k in sources}

        per_source: dict[str, dict[str, Any]] = {}
        totals = {"total": 0, "field_blocked": 0, "duplicate": 0, "injected": 0, "failed": 0}

        for family, rel in target.items():
            yaml_path = Path(rel)
            cands = self._load_yaml_candidates(family, yaml_path)
            stats = {"total": len(cands), "field_blocked": 0, "duplicate": 0, "injected": 0, "failed": 0}
            existing = self._existing_ids()

            for cand in cands:
                totals["total"] += 1
                authority = self._field_authority(cand)
                if authority["missing"] or authority["unknown"]:
                    # 防空谈因子：L2 缺失字段（fundamental 类）或未登记字段 → 拒绝
                    stats["field_blocked"] += 1
                    totals["field_blocked"] += 1
                    logger.info(
                        "[import] %s 字段权威校验拦截: missing=%s unknown=%s",
                        cand["name"], authority["missing"], authority["unknown"],
                    )
                    continue
                if cand["candidate_id"] in existing:
                    stats["duplicate"] += 1
                    totals["duplicate"] += 1
                    continue
                if do_apply:
                    try:
                        self._persist_candidate(cand)
                        stats["injected"] += 1
                        totals["injected"] += 1
                    except Exception as e:  # noqa: BLE001
                        logger.error("[import] 注入失败 [%s]: %s", cand["name"], e)
                        stats["failed"] += 1
                        totals["failed"] += 1
                else:
                    stats["injected"] += 1  # dry-run：可注入计数
                    totals["injected"] += 1

            per_source[family] = stats

        report = {
            "trace_id": self.trace_id,
            "mode": "apply" if do_apply else "dry-run",
            "sources": per_source,
            "summary": dict(totals),
            "run_at": datetime.now().isoformat(),
        }
        self._write_report(report)
        return report

    def _write_report(self, report: dict[str, Any]) -> None:
        """血缘报告：memory/logs/imports/YYYY-MM-DD.log（JSONL 追加）。"""
        try:
            IMPORT_LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_file = IMPORT_LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(report, ensure_ascii=False, default=str) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.warning("[import] 报告写入失败: %s", e)


__all__ = [
    "ExternalFactorImportRunner",
    "EXTERNAL_SEED_SOURCES",
    "DEFAULT_INJECT_DIR",
    "DEFAULT_POOL_PATH",
    "_make_candidate_id",
]
