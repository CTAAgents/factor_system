"""
scripts/run_mhf_tqsdk_exec.py — Phase 4 扩展：MHF 信号 → TqSdk 模拟账户执行。

流程：读 signals/latest_signal.json → 主连→主力合约（TqSdk query_quotes 按持仓量）
→ 目标手数（资金×target_pct / 价格×乘数）→ TargetPosTask 逐合约调仓
→ 成交等待 → 持仓/权益快照留痕。

免费路径：.env TQSDK_USERNAME/PASSWORD（天勤账号）+ TqSim 本地撮合。

输出:
    reports/mhf/tqsdk_exec_{timestamp}.json   （结构化留痕）
    reports/mhf/tqsdk_exec_{timestamp}.md     （可读报告）

用法:
    python scripts/run_mhf_tqsdk_exec.py [--signal signals/latest_signal.json]
                                         [--init-balance 1000000]
                                         [--max-positions 8] [--timeout 30]

失败透明：连接/映射/成交任何异常均记录到留痕并退出码非 0。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.live_trade.tqsdk_mhf_executor import (  # noqa: E402
    ExecConfig,
    TqSdkMhfExecutor,
)

REPORTS_DIR = PROJECT_ROOT / "reports" / "mhf"


def _write_outputs(result: dict, timestamp: str) -> tuple[Path, Path]:
    """写入 JSON 留痕与 markdown 报告，返回 (json_path, md_path)。"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"tqsdk_exec_{timestamp}.json"
    md_path = REPORTS_DIR / f"tqsdk_exec_{timestamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# TqSdk 模拟执行留痕",
        "",
        f"- trace_id: `{result.get('trace_id', '')}`",
        f"- signal_id: `{result.get('signal_id', '')}`",
        f"- bar_time: `{result.get('bar_time', '')}`",
        f"- 状态: {'成功' if result.get('ok') else '失败/跳过'}",
        "",
        "## 信号方向",
        "",
    ]
    for sym, direction in (result.get("signals") or {}).items():
        lines.append(f"- {sym}: {'多' if direction > 0 else '空'}")
    lines += ["", "## 目标仓位", ""]
    for sym, t in (result.get("targets") or {}).items():
        lines.append(
            f"- {sym}: {t['contract']} 方向={'多' if t['direction'] > 0 else '空'} "
            f"手数={t['lots']} 参考价={t['price']:.2f} 乘数={t['multiplier']}"
        )
    if result.get("skipped"):
        lines += ["", "## 跳过品种", ""]
        lines.append(", ".join(result["skipped"]))
    lines += ["", "## 成交后持仓", ""]
    for contract, pos in (result.get("fills") or {}).items():
        lines.append(
            f"- {contract}: 多 {pos['volume_long']:.0f} 手 / 空 {pos['volume_short']:.0f} 手"
            f"（开多价 {pos['open_price_long']:.2f} / 开空价 {pos['open_price_short']:.2f}）"
        )
    eq = result.get("equity") or {}
    lines += ["", "## 账户权益", ""]
    if eq:
        lines.append(f"- 总权益: {eq.get('balance', 0.0):,.2f}")
        lines.append(f"- 可用: {eq.get('available', 0.0):,.2f}")
        lines.append(f"- 持仓盈亏: {eq.get('position_profit', 0.0):,.2f}")
        lines.append(f"- 平仓盈亏: {eq.get('close_profit', 0.0):,.2f}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="MHF 信号 → TqSdk 模拟账户执行")
    parser.add_argument("--signal", type=str, default="signals/latest_signal.json",
                        help="信号 JSON 路径")
    parser.add_argument("--init-balance", type=float, default=1_000_000.0)
    parser.add_argument("--max-positions", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30.0, help="成交等待超时（秒）")
    parser.add_argument("--backtest-start", type=str, default="",
                        help="回放撮合起始（ISO，如 2026-08-13T14:30:00）；缺省为实时模式")
    parser.add_argument("--backtest-end", type=str, default="",
                        help="回放撮合结束（ISO，如 2026-08-13T14:45:00）")
    args = parser.parse_args()

    signal_path = Path(args.signal)
    if not signal_path.is_absolute():
        signal_path = PROJECT_ROOT / signal_path
    payload = json.loads(signal_path.read_text(encoding="utf-8"))

    backtest_window: tuple[str, str] | None = None
    if args.backtest_start and args.backtest_end:
        backtest_window = (args.backtest_start, args.backtest_end)
    cfg = ExecConfig(
        init_balance=args.init_balance,
        max_positions=args.max_positions,
        timeout_seconds=args.timeout,
        backtest_window=backtest_window,
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    executor = TqSdkMhfExecutor(cfg, trace_id=f"fts.mhf.exec_{timestamp}")
    result = executor.run_once(payload)
    json_path, md_path = _write_outputs(result, timestamp)

    status = "OK" if result.get("ok") else "FAIL"
    n_targets = len(result.get("targets") or {})
    equity = (result.get("equity") or {}).get("balance", 0.0)
    print(f"[{status}] trace_id={result.get('trace_id')} targets={n_targets} equity={equity:.2f}")
    print(f"json: {json_path}")
    print(f"md:   {md_path}")
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
