"""
RHI 全局 Harness 自进化 — FTS 适配版。

FTS 部署方式：
  python scripts/rhi_global_setup.py init     # 初始化 RHI 自进化
  python scripts/rhi_global_setup.py step     # 执行一轮优化
  python scripts/rhi_global_setup.py status   # 查看状态

原理：
  将项目的 CLAUDE.md 作为 Harness prompt，每次 step 比较当前版本
  与上一版本的输出质量评分，决定是否保留更新。

评分维度（按 FTS 实际规则微调）：
  memory_coverage (0.30) — memory 存储体系 + D:\\Knowledge 知识库引用
  rule_completeness (0.30) — 13 项检查清单 + 反模式(AP) + verify_doc_consistency
  consistency (0.20) — docs/harness 文档体系 + trace_id 全链路 + 差距管理
  clarity (0.20) — CLAUDE.md 本体行数(<500 最佳, <800 可接受) + 版本号纪律
  FTS 为双文件规范体系（CLAUDE.md @AGENTS.md）：规则检测合并两者；
  行数仅统计 CLAUDE.md 本体（AGENTS.md 为长文档，不参与 clarity 度量）。

参考：
  RHI: Recursive Harness Self-Improvement, arXiv:2607.15524
  MemoHarness: Agent Harnesses That Learn from Experience, arXiv:2607.14159
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

STARTER_KIT = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("rhi-setup")

# ─── 评分函数 ───

def _score_claude(claude_path: Path) -> dict:
    """FTS 适配版四维评分。

    FTS 为双文件规范体系（CLAUDE.md @AGENTS.md），规则检测合并两者；
    clarity 的行数仅统计 CLAUDE.md 本体。
    """
    if not claude_path.exists():
        return {"score": 0.0, "breakdown": {}}
    content = claude_path.read_text(encoding="utf-8")
    # 排除 RHI 自述章节，避免评分关键词自指污染（"描述即命中"导致度量失真）
    rhi_marker = "## RHI 递归 Harness 自进化"
    if rhi_marker in content:
        content = content.split(rhi_marker, 1)[0]
    # FTS 双文件规范：CLAUDE.md 引用 @AGENTS.md，规则检测合并两者
    agents_path = claude_path.parent / "AGENTS.md"
    if agents_path.exists():
        content = content + "\n" + agents_path.read_text(encoding="utf-8")

    scores = {}
    # D1+D5 memory_coverage (0.30) — memory 存储体系 + D:\Knowledge 知识库引用
    has_memory = "memory" in content.lower()
    has_knowledge = ("D:\\Knowledge" in content) or ("knowledge" in content.lower())
    scores["memory_coverage"] = 1.0 if (has_memory and has_knowledge) else (0.5 if (has_memory or has_knowledge) else 0.0)
    # D6 rule_completeness (0.30) — 13 项检查清单 + 反模式(AP) + verify_doc_consistency
    has_check = any(k in content for k in ["13 项", "13项", "检查清单", "checklist"])
    has_anti = any(k in content for k in ["反模式", "anti-pattern", "AP01"])
    has_doc_verify = "verify_doc_consistency" in content
    n_rule = sum([has_check, has_anti, has_doc_verify])
    scores["rule_completeness"] = {3: 1.0, 2: 0.7, 1: 0.4}.get(n_rule, 0.0)
    # D4 consistency (0.20) — docs/harness 文档体系 + trace_id 全链路 + 差距管理
    has_harness = "docs/harness" in content
    has_trace = "trace_id" in content
    has_gap = "08-gap-analysis" in content
    n_cons = sum([has_harness, has_trace, has_gap])
    scores["consistency"] = {3: 1.0, 2: 0.7, 1: 0.4}.get(n_cons, 0.0)
    # D6 clarity (0.20) — FTS 适配: 深定制项目行数阈值放宽 + 版本号纪律
    lines = len(claude_path.read_text(encoding="utf-8").splitlines())
    has_version = ("bump_version" in content) or ("版本号" in content)
    clarity = 1.0 if lines < 500 else (0.5 if lines < 800 else 0.2)
    scores["clarity"] = clarity if has_version else clarity * 0.5
    weights = {"memory_coverage": 0.30, "rule_completeness": 0.30, "consistency": 0.20, "clarity": 0.20}
    total = sum(scores[k] * weights[k] for k in weights)
    return {"score": round(total, 4), "breakdown": scores}

def _improvement_rate(prefs: list) -> float:
    if not prefs:
        return 0.0
    improves = sum(1 for p in prefs if p.get("preference") == "improve")
    return improves / len(prefs)

# ─── 子命令 ───

def cmd_deploy(args: argparse.Namespace) -> int:
    """将 RHI 部署到指定项目。"""
    if args.project:
        target = Path(args.project).resolve()
    else:
        target = Path.cwd()
    rhi_dir = target / ".rhi"
    rhi_dir.mkdir(parents=True, exist_ok=True)

    # 复制本脚本到目标项目
    script_dst = target / "scripts" / "rhi_global_setup.py"
    if not target.joinpath("scripts").exists():
        target.joinpath("scripts").mkdir(parents=True, exist_ok=True)
    if not script_dst.exists():
        shutil.copy2(__file__, script_dst)
        logger.info(f"[deploy] 已复制 rhi_global_setup.py 到 {script_dst}")

    # 初始化历史
    history_file = rhi_dir / "history.json"
    if not history_file.exists():
        init_history = {"versions": [], "preferences": [], "improvement_rate": 0.0, "best_version": 0, "converged": False}
        history_file.write_text(json.dumps(init_history, indent=2), encoding="utf-8")
        logger.info(f"[deploy] 已创建 {history_file}")

    print(f"\n=== ✅ RHI 已部署到 {target} ===")
    print(f"  用法: python scripts/rhi_global_setup.py step")
    print(f"        python scripts/rhi_global_setup.py status")
    return 0

def cmd_init(args: argparse.Namespace) -> int:
    """初始化当前项目的 RHI（首版本快照）。"""
    root = Path(args.project).resolve() if args.project else Path.cwd()
    claude = root / "CLAUDE.md"
    if not claude.exists():
        logger.error(f"CLAUDE.md 不存在: {claude}")
        return 1
    rhi_dir = root / ".rhi"
    rhi_dir.mkdir(parents=True, exist_ok=True)
    history_file = rhi_dir / "history.json"
    history = {"versions": [], "preferences": [], "improvement_rate": 0.0, "best_version": 0, "converged": False}
    score = _score_claude(claude)
    history["versions"].append({
        "version": 0, "timestamp": datetime.now().isoformat(),
        "score": score["score"], "breakdown": score["breakdown"],
        "content_length": len(claude.read_text(encoding="utf-8")),
    })
    history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"\n=== ✅ RHI 初始化完成 ===")
    print(f"  项目: {root}")
    print(f"  首版评分: {score['score']:.3f}")
    return 0

def cmd_step(args: argparse.Namespace) -> int:
    """执行一轮 RHI 自改进。"""
    root = Path(args.project).resolve() if args.project else Path.cwd()
    claude = root / "CLAUDE.md"
    if not claude.exists():
        logger.error(f"CLAUDE.md 不存在: {claude}")
        return 1
    rhi_dir = root / ".rhi"
    history_file = rhi_dir / "history.json"
    if not history_file.exists():
        logger.error(f"未初始化，请先运行 init")
        return 1
    history = json.loads(history_file.read_text(encoding="utf-8"))
    max_iters = args.max_iters or 5
    iter_num = len(history.get("versions", []))
    if iter_num >= max_iters:
        history["converged"] = True
        history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")
        print(f"  已达最大轮次 {max_iters}，已收敛")
        return 0
    score = _score_claude(claude)
    versions = history.get("versions", [])
    prefs = history.get("preferences", [])
    if versions:
        prev = versions[-1]
        delta = score["score"] - prev.get("score", 0)
        pref = "improve" if delta > 0.02 else ("regress" if delta < -0.02 else "tie")
        prefs.append({"iteration": iter_num, "preference": pref, "score_current": score["score"],
                       "score_previous": prev.get("score", 0), "rationale": f"delta={delta:+.3f}"})
    else:
        prefs.append({"iteration": 0, "preference": "tie", "score_current": score["score"], "score_previous": 0.0, "rationale": "首轮"})
    versions.append({"version": iter_num, "timestamp": datetime.now().isoformat(), "score": score["score"],
                      "breakdown": score["breakdown"], "content_length": len(claude.read_text(encoding="utf-8"))})
    s_i = _improvement_rate(prefs)
    history["versions"] = versions
    history["preferences"] = prefs
    history["improvement_rate"] = s_i
    history["best_version"] = max(range(len(versions)), key=lambda i: versions[i].get("score", 0))
    if s_i < 0.3 and len(prefs) >= 2:
        history["converged"] = True
    elif iter_num >= max_iters - 1:
        history["converged"] = True
    history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")
    icon = {"improve": "✅", "regress": "❌", "tie": "➡️"}.get(prefs[-1]["preference"], "➡️")
    print(f"\n=== {'🔄 RHI' if not history['converged'] else '🎯 收敛'} ===")
    print(f"  轮次: #{iter_num}  {icon} {prefs[-1]['preference']}")
    print(f"  评分: {prefs[-1]['score_current']:.3f} (vs {prefs[-1]['score_previous']:.3f})")
    return 0

def cmd_status(args: argparse.Namespace) -> int:
    """查看当前项目的 RHI 状态。"""
    root = Path(args.project).resolve() if args.project else Path.cwd()
    claude = root / "CLAUDE.md"
    rhi_dir = root / ".rhi"
    history_file = rhi_dir / "history.json"
    score = _score_claude(claude) if claude.exists() else {"score": 0.0}
    history = json.loads(history_file.read_text(encoding="utf-8")) if history_file.exists() else {}
    versions = history.get("versions", [])
    prefs = history.get("preferences", [])
    print(f"\n=== 📊 RHI 状态 ===")
    print(f"  项目: {root}")
    print(f"  评分: {score['score']:.3f}")
    print(f"  版本: {len(versions)} | 迭代: {len(prefs)} | 改进率: {history.get('improvement_rate', 0):.3f}")
    print(f"  收敛: {'✅' if history.get('converged') else '⏳'}")
    if versions:
        best = max(versions, key=lambda v: v.get("score", 0))
        print(f"  最优: v{best.get('version', '?')} ({best.get('score', 0):.3f})")
    return 0


# ─── CLI ───

def main() -> int:
    parser = argparse.ArgumentParser(description="RHI 全局 Harness 自进化 — FTS 适配版")
    parser.add_argument("command", nargs="?", default="status", choices=["init", "step", "status", "deploy"])
    parser.add_argument("--project", "-p", help="目标项目目录（默认 CWD）")
    parser.add_argument("--max-iters", "-n", type=int, default=5, help="最大迭代轮次")
    args = parser.parse_args()
    cmds = {"deploy": cmd_deploy, "init": cmd_init, "step": cmd_step, "status": cmd_status}
    return cmds[args.command](args)

if __name__ == "__main__":
    sys.exit(main())
