#!/usr/bin/env python3
"""AutoResearch 式因子演化通道对比实验（脚本草稿，v0.1 — 待 review 后执行）。

=====================================================================
研究问题
=====================================================================
因子挖掘场景中，三种"变异器"产出的因子在质量、多样性与样本外保持率上
孰优孰劣：
  - A 组  AutoResearch 式：macro 通道（LLM 语义变异 + 成功模式注入）
  - B 组  经典 GA 式    ：operator 通道（UCT 父选择 + 确定性算子变异）
  - C 组  FTS 生产混合  ：batch 四通道轮换（macro/gp/deep/transformer/operator）

设计要点（对应《用 AutoResearch 的框架挖因子》§3）:
  * 唯一变量 = 变异器（覆盖 _batch_generate_one 的 method_hint）
  * 控制变量 = 评估链 / FactorAuditor 7 项审计 / 晋升门槛 / 预算 / 随机种子
  * 三组各自独立 memory_dir + elite_dir + factor_db_path（隔离，防组间污染，
    对齐 GAP-030 测试隔离经验）

用法:
  # 全部三组（默认）
  python scripts/ar_factor_experiment.py --rounds 30 --batch-size 5
  # 只跑 A 组（LLM 变异）试运行，先验证链路
  python scripts/ar_factor_experiment.py --only a --rounds 2 --batch-size 2
  # 只打印计划，不执行
  python scripts/ar_factor_experiment.py --dry-run

产出:
  data/experiments-ar_a-{run_id}.json   A 组实验日志（experiment_log）
  data/experiments-ar_b-{run_id}.json   B 组
  data/experiments-ar_c-{run_id}.json   C 组
  reports/futures/{date}/ar_channel_comparison.md  三组对比报告
=====================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("ar_experiment")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 固定随机种子：抽样/降噪/模型训练全固定，保证三组可复现、组间可比较
DEFAULT_SEED = 42


# ─────────────────────────────────────────────────────────────────────
# 变异器覆盖：A/B 组固定单通道（复用 _batch_generate_one 的 seed 递增逻辑）
# ─────────────────────────────────────────────────────────────────────
def _fixed_method_generator(loop: Any, method_hint: str):
    """构造固定 method_hint 的 batch 生成回调（对齐 evolution_loop._batch_generate_one）。

    覆盖后 BatchMiner 仍走并行粗筛 → _process_candidate 完整准入链，
    唯一变化是每次生成都走指定演化通道。
    """

    def _gen(parent: Any, generation: int, trace_id: str) -> Optional[dict[str, Any]]:
        seed = loop.batch_random_seed + loop._batch_idx
        loop._batch_idx = loop._batch_idx + 1
        evolved = loop._evolve_one(
            parent,
            generation,
            trace_id,
            method_hint=method_hint,
            seed=seed,
        )
        if evolved is None:
            return None
        factor, method, summary, tokens = evolved
        return {
            "factor": factor,
            "parent_id": parent.get("factor_id", "?"),
            "method": method,
            "summary": summary,
            "tokens": tokens,
            "prefilter_ok": False,
            "prefilter_reason": "",
            "prefilter_ic": 0.0,
        }

    return _gen


# ─────────────────────────────────────────────────────────────────────
# 单组运行
# ─────────────────────────────────────────────────────────────────────
def run_group(
    tag: str,
    method_hint: Optional[str],
    *,
    rounds: int,
    batch_size: int,
    seed: int,
) -> Optional[Path]:
    """运行一组演化实验，返回 experiment_log 路径。

    Args:
        tag: 组标签（a/b/c）
        method_hint: None = 生产混合（C 组）；"macro"/"operator" = 单通道（A/B 组）
        rounds: 最大演化代数
        batch_size: 每代候选数（预算 = rounds * batch_size）
        seed: 随机种子
    """
    from fts.cli import _prepare_futures_data, _relaxed_futures_audit_config, _relaxed_futures_quality_config
    from fts.config.settings import get_config
    from fts.factor_engine import EvolutionLoop, SeedPool, get_default_llm_client

    cfg = get_config()
    trace_id = f"fts.ar_experiment.{tag}.{int(time.time())}"
    logger.info("[%s] trace_id=%s rounds=%d batch_size=%d seed=%d",
                tag, trace_id, rounds, batch_size, seed)

    # 1) 数据：期货横截面，700 日（GAP-073：勿超 750，否则 WalkForward 0 窗口）
    panel, common_dates, fwd_ret = _prepare_futures_data(days=700, max_symbols=0)
    first_sym = list(panel.keys())[0]
    logger.info("[%s] panel symbols=%d common_dates=%d", tag, len(panel), len(common_dates))

    # 2) 隔离目录（三组互不污染）
    base = cfg.memory_dir + f"/evolution/futures_ar_{tag}"
    elite_dir = str(Path(cfg.get_elite_dir("futures")).parent / f"futures_elite_ar_{tag}")

    # 3) 构造 L2 演化循环（同 CLI 生产路径，仅隔离目录）
    np.random.seed(seed)
    random.seed(seed)
    loop = EvolutionLoop(
        data=panel[first_sym],
        forward_returns=fwd_ret,
        elite_dir=elite_dir,
        memory_dir=base,
        llm_client=get_default_llm_client(),
        seed_pool=SeedPool(market="futures"),
        n_trials_micro=min(rounds * 3, 30),
        cross_section_data=panel,
        cross_section_dates=common_dates,
        market="futures",
        quality_card_config=_relaxed_futures_quality_config(),
        audit_config=_relaxed_futures_audit_config(),
    )
    loop.batch_random_seed = seed

    # 4) 变异器覆盖（A/B 固定单通道；C 保持生产轮换）
    if method_hint is not None:
        loop._batch_generate_one = _fixed_method_generator(loop, method_hint)  # type: ignore[method-assign]

    # 5) 主循环
    result = loop.run(max_generation=rounds)
    logger.info("[%s] status=%s promoted=%d/%d 熔断=%s",
                tag, result.status, result.total_factors_promoted,
                result.total_factors_evaluated, result.circuit_breaker_reason)

    # 6) 定位实验日志（experiment_log.py 输出 data/experiments-{run_id}.json）
    logs = sorted((_ROOT / "data").glob("experiments-*.json"))
    if not logs:
        logger.warning("[%s] 未找到 experiment_log", tag)
        return None
    return logs[-1]


# ─────────────────────────────────────────────────────────────────────
# 汇总对比
# ─────────────────────────────────────────────────────────────────────
def build_report(group_logs: dict[str, Path], out_md: Path) -> None:
    """从三组 experiment_log 提取晋升率/IC/方法分布，输出对比报告。"""
    summary: dict[str, Any] = {}
    for tag, p in group_logs.items():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            s = data.get("summary", {})
            by_method = s.get("by_method", {})
            summary[tag] = {
                "total_evaluated": s.get("total_evaluated"),
                "total_promoted": s.get("total_promoted"),
                "promote_rate": s.get("promote_rate"),
                "by_method": {k: v.get("rate") for k, v in by_method.items()},
                "log": str(p),
            }
        except Exception as e:  # noqa: BLE001
            summary[tag] = {"error": str(e)}

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        "# AutoResearch 式因子演化通道对比实验\n\n"
        "| 组 | 变异器 | 评估数 | 晋升数 | 晋升率 | by_method 晋升率 |\n"
        "|:--|:--|--:|--:|--:|:--|\n"
        + "\n".join(
            f"| {tag} | {v.get('variant', tag)} | {v.get('total_evaluated', '-')} | "
            f"{v.get('total_promoted', '-')} | {v.get('promote_rate', '-')} | "
            f"{v.get('by_method', {})} |"
            for tag, v in summary.items()
        )
        + "\n\n> OOS 保持率与跨标的：取各组 experiment_log 中 audit walkforward / "
          "cross_symbol / symbol_holdout 输出人工复核。\n",
        encoding="utf-8",
    )
    logger.info("对比报告已写入: %s", out_md)


# ─────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="AutoResearch 式因子演化通道对比实验（草稿）")
    ap.add_argument("--rounds", type=int, default=30, help="最大演化代数（默认 30）")
    ap.add_argument("--batch-size", type=int, default=5, help="每代候选数（默认 5；预算=rounds*batch-size）")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子（默认 42）")
    ap.add_argument("--only", choices=["a", "b", "c"], default=None, help="只跑指定组（默认全部三组）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不执行")
    ap.add_argument("-v", "--verbose", action="store_true", help="INFO 日志")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # 三组定义：A=macro（AutoResearch 式）/ B=operator（经典 GA）/ C=生产混合
    groups: dict[str, dict[str, Any]] = {
        "a": {"variant": "macro (LLM 语义变异 + 模式注入)", "method_hint": "macro"},
        "b": {"variant": "operator (UCT + 确定性算子变异)", "method_hint": "operator"},
        "c": {"variant": "生产混合 (idx%4 轮换)", "method_hint": None},
    }
    if args.only:
        groups = {args.only: groups[args.only]}

    if args.dry_run:
        for tag, meta in groups.items():
            logger.info("[%s] %s | rounds=%d batch=%d | 预算=%d 候选",
                        tag, meta["variant"], args.rounds, args.batch_size,
                        args.rounds * args.batch_size)
        return 0

    group_logs: dict[str, Path] = {}
    for tag, meta in groups.items():
        log = run_group(
            tag,
            meta["method_hint"],
            rounds=args.rounds,
            batch_size=args.batch_size,
            seed=args.seed,
        )
        if log is not None:
            group_logs[tag] = log

    if group_logs:
        from datetime import date as _date

        report = _ROOT / "reports" / "futures" / str(_date.today()) / "ar_channel_comparison.md"
        build_report(group_logs, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
