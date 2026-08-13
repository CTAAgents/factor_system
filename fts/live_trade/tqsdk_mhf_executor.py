"""
fts/live_trade/tqsdk_mhf_executor.py — TqSdk 模拟执行器（plans/33 Phase 4 扩展）

将 MHF 信号（signals/latest_signal.json）落地到 TqSdk 模拟账户执行：
信号 → 主连映射 → 目标仓位 → TargetPosTask 调仓 → 成交留痕 → 快照。

免费路径：使用现有天勤账号（.env TQSDK_USERNAME/PASSWORD）+ TqSim 本地撮合。

HARNESS §trace_id: 每次执行生成独立 trace_id。
HARNESS §降级/熔断: 连接/映射/成交异常均捕获记录，不中断外部流程。
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .contracts import contract_multiplier

logger = logging.getLogger(__name__)


def _finite(value: Any) -> float:
    """数值兜底：None/NaN/Inf → 0.0，保证 JSON 可序列化。"""
    try:
        f = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def is_trading_time(now: datetime) -> bool:
    """期货交易时段判断（跨品种保守并集，Asia/Shanghai 本地时间）。

    日盘 09:00-15:00；夜盘 21:00-次日 02:30。其余时段跳过执行，
    避免非交易时段无谓连接 TqSdk 与拒单噪音。
    """
    minute = now.hour * 60 + now.minute
    day_open = 9 * 60              # 09:00
    day_close = 15 * 60            # 15:00
    night_open = 21 * 60           # 21:00
    night_close = 2 * 60 + 30      # 次日 02:30
    return day_open <= minute < day_close or minute >= night_open or minute < night_close


@dataclass
class ExecConfig:
    """TqSdk 模拟执行配置。"""

    init_balance: float = 1_000_000.0   # 模拟账户初始资金
    max_positions: int = 8              # 最大持仓品种数（对齐 MhfRiskConfig）
    target_pct: float = 0.0625          # 单品种目标仓位占比（1/8）
    timeout_seconds: float = 30.0       # 成交等待超时兜底
    backtest_window: tuple[str, str] | None = None  # (start, end) ISO 时间；回放撮合模式
    tq_user: str = ""
    tq_pass: str = ""


def parse_signal_directions(payload: dict[str, Any], max_positions: int) -> dict[str, int]:
    """从信号 payload 提取 direction≠0 品种，按 max_positions 截取。

    Args:
        payload: FactorSignal 契约 payload（signals{品种:{direction,score,...}}）
        max_positions: 最大持仓品种数

    Returns:
        {FTS品种: direction}
    """
    sigs = payload.get("signals") or {}
    active = {
        sym: int(data["direction"])
        for sym, data in sigs.items()
        if data.get("direction", 0) != 0
    }
    items = list(active.items())[:max_positions]
    return dict(items)


def target_lots(direction: int, price: float, multiplier: float, per_symbol_cash: float) -> int:
    """目标手数：floor(单品种资金 / (价格×乘数))，方向非 0 时至少 1 手。

    Args:
        direction: 信号方向（1 多 / -1 空 / 0 平）
        price: 最新价
        multiplier: 合约乘数
        per_symbol_cash: 单品种目标资金

    Returns:
        目标手数（非负整数）
    """
    if direction == 0 or price <= 0 or multiplier <= 0 or per_symbol_cash <= 0:
        return 0
    lots = int(per_symbol_cash / (price * multiplier))
    return max(lots, 1)


def select_underlying(quote_map: dict[str, Any], tq_map: dict[str, str]) -> dict[str, str]:
    """从主连 quote 提取主力合约（underlying_symbol）。

    Args:
        quote_map: 主连代码 -> quote dict（含 underlying_symbol）
        tq_map: FTS品种代码 -> TqSdk 主连代码（如 {"AG0": "KQ.m@SHFE.ag"}）

    Returns:
        {FTS品种: 具体合约 symbol（如 SHFE.ag2610）}
    """
    out: dict[str, str] = {}
    for fts_sym, kq in tq_map.items():
        q = quote_map.get(kq)
        if q and q.get("underlying_symbol"):
            out[fts_sym] = str(q["underlying_symbol"])
    return out


class TqSdkMhfExecutor:
    """MHF 信号 → TqSdk 模拟账户执行器（一次性执行模式）。"""

    def __init__(self, config: ExecConfig | None = None, trace_id: str = "") -> None:
        self._cfg = config or ExecConfig()
        self._trace_id = trace_id or f"fts.mhf.exec_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        self._api: Any = None
        self._contracts: list[str] = []

    # ── 供测试注入 ──────────────────────────────────────────
    def _build_api(self) -> Any:
        """构建 TqApi（TqSim 模拟账户；backtest_window 时启用回放撮合）。"""
        import tqsdk  # noqa: F401
        from tqsdk import TqAuth, TqSim

        user = self._cfg.tq_user or os.environ.get("TQSDK_USERNAME", "")
        pwd = self._cfg.tq_pass or os.environ.get("TQSDK_PASSWORD", "")
        if not (user and pwd):
            raise RuntimeError("未配置 TQSDK_USERNAME/TQSDK_PASSWORD")
        kwargs: dict[str, Any] = {
            "auth": TqAuth(user, pwd),
            "account": TqSim(init_balance=self._cfg.init_balance),
        }
        if self._cfg.backtest_window:
            from datetime import datetime as _dt
            from tqsdk import TqBacktest

            start_s, end_s = self._cfg.backtest_window
            kwargs["backtest"] = TqBacktest(
                start_dt=_dt.fromisoformat(start_s), end_dt=_dt.fromisoformat(end_s)
            )
        return tqsdk.TqApi(**kwargs)

    def _symbol_map(self) -> dict[str, str]:
        """FTS 品种代码 → TqSdk 主连代码映射。"""
        from fts.data_sources.tqsdk_source import _SYMBOL_MAP

        return _SYMBOL_MAP

    def _resolve_main_contracts(self, tq_map: dict[str, str]) -> dict[str, str]:
        """主连 → 主力合约：订阅主连 quote 并等待 underlying_symbol 就绪。

        Args:
            tq_map: {FTS品种: 主连代码}

        Returns:
            {FTS品种: 主力具体合约}
        """
        quote_map: dict[str, Any] = {}
        for kq in tq_map.values():
            quote_map[kq] = self._api.get_quote(kq)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if all(q.get("underlying_symbol") for q in quote_map.values()):
                break
            if not self._api.wait_update(deadline=deadline):
                break
        return select_underlying(quote_map, tq_map)

    # ── 核心流程 ────────────────────────────────────────────
    def run_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        """信号 → 模拟执行 → 留痕快照。

        Args:
            payload: FactorSignal 契约 payload

        Returns:
            留痕 dict（targets/fills/positions/equity/ok）
        """
        result: dict[str, Any] = {
            "trace_id": self._trace_id,
            "signal_id": payload.get("signal_id"),
            "bar_time": payload.get("bar_time"),
            "ok": False,
        }
        signals = parse_signal_directions(payload, self._cfg.max_positions)
        result["signals"] = signals
        if not signals:
            logger.warning("[%s] 无有效信号，跳过执行", self._trace_id)
            return result

        try:
            self._api = self._build_api()
            # 主连 → 主力合约（读主连 quote 的 underlying_symbol）
            tq_map = {s: self._symbol_map()[s] for s in signals if s in self._symbol_map()}
            contracts = self._resolve_main_contracts(tq_map)

            targets: dict[str, dict[str, Any]] = {}
            skipped: list[str] = []
            for sym, direction in signals.items():
                contract = contracts.get(sym)
                if not contract:
                    skipped.append(sym)
                    continue
                price = self._quote_price(contract)
                if price is None:
                    skipped.append(sym)
                    continue
                mult = contract_multiplier(sym)
                lots = target_lots(direction, price, mult, self._cfg.init_balance * self._cfg.target_pct)
                if lots == 0:
                    skipped.append(sym)
                    continue
                targets[sym] = {
                    "contract": contract,
                    "direction": direction,
                    "lots": lots,
                    "price": price,
                    "multiplier": mult,
                }
            result["targets"] = targets
            result["skipped"] = skipped
            if not targets:
                logger.warning("[%s] 无可执行合约（%s），跳过", self._trace_id, skipped)
                return result

            self._execute(targets)
            result["fills"] = self._snapshot_positions()
            result["equity"] = self._snapshot_equity()
            result["ok"] = True
            logger.info(
                "[%s] 模拟执行完成: %d 品种，equity=%.2f",
                self._trace_id, len(targets), result["equity"]["balance"],
            )
            return result
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] 模拟执行失败: %s", self._trace_id, e)
            return result
        finally:
            self._close()

    # ── 内部辅助 ────────────────────────────────────────────
    def _quote_price(self, contract: str) -> float | None:
        """取具体合约最新价；数据未就绪时驱动 wait_update（带 deadline 防阻塞）。"""
        q = self._api.get_quote(contract)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            last = float(q.get("last_price") or 0.0)
            if last > 0:
                return last
            if not self._api.wait_update(deadline=deadline):
                break
        last = float(q.get("last_price") or 0.0)
        return last if last > 0 else None

    def _execute(self, targets: dict[str, dict[str, Any]]) -> None:
        """逐合约 TargetPosTask 调仓并等待成交（TqSdk 3.x 为单合约单例）。"""
        from tqsdk import TargetPosTask

        self._contracts = [t["contract"] for t in targets.values()]
        volume_map = {
            t["contract"]: t["lots"] * (1 if t["direction"] > 0 else -1)
            for t in targets.values()
        }
        tasks = {c: TargetPosTask(self._api, c) for c in self._contracts}
        for contract, task in tasks.items():
            task.set_target_volume(volume_map[contract])
        deadline = time.time() + self._cfg.timeout_seconds
        while time.time() < deadline:
            if all(self._position_reached(c, volume_map[c]) for c in self._contracts):
                break
            if not self._api.wait_update(deadline=deadline):
                break

    def _position_reached(self, contract: str, target: int) -> bool:
        """目标持仓是否到位（多 target>0 / 空 target<0 / 平 0）。"""
        pos = self._api.get_position(contract)
        cur = float(pos.get("volume_long") or 0.0) - float(pos.get("volume_short") or 0.0)
        return cur == target

    def _snapshot_positions(self) -> dict[str, dict[str, Any]]:
        """持仓快照（含目标对比）；NaN/Inf 归一为 0.0 保证 JSON 可序列化。"""
        out: dict[str, dict[str, Any]] = {}
        for contract in self._contracts:
            pos = self._api.get_position(contract)
            out[contract] = {
                "volume_long": _finite(pos.get("volume_long")),
                "volume_short": _finite(pos.get("volume_short")),
                "open_price_long": _finite(pos.get("open_price_long")),
                "open_price_short": _finite(pos.get("open_price_short")),
            }
        return out

    def _snapshot_equity(self) -> dict[str, float]:
        """账户权益快照。"""
        acc = self._api.get_account()
        return {
            "balance": float(acc.get("balance") or 0.0),
            "available": float(acc.get("available") or 0.0),
            "position_profit": float(acc.get("position_profit") or 0.0),
            "close_profit": float(acc.get("close_profit") or 0.0),
        }

    def _close(self) -> None:
        """关闭连接（保留持仓，模拟账户不注销）。"""
        if self._api is not None:
            try:
                self._api.close()
            except Exception:  # noqa: BLE001
                pass
            self._api = None
