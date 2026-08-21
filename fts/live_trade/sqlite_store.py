"""
fts.live_trade.sqlite_store — 模拟仓 SQLite 持久化层（D.1 增强，v2.101.0）。

用 Python 标准库 ``sqlite3``（零额外依赖）持久化模拟仓的账户/持仓/成交/权益，
替代此前 ``SimulatedPaperTrader`` 的 ``paper_state.json`` 轻量快照，
提供事务、原子写入与可查询能力。

数据表:
    - ``sim_account``:      单行账户状态（初始资金/现金/峰值权益/上一权益/当日盈亏/累计已实现/上期换手）
    - ``sim_positions``:    持仓明细（symbol 主键）
    - ``sim_fills``:        成交流水（order_id 主键，追加）
    - ``sim_equity_curve``: 逐日盯市权益曲线（date 主键，追加）

FTS 角色边界: 只做模拟核算的状态落盘，真实撮合由下游（FDT）负责。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fts.live_trade.contracts import SimDailyRecord, SimFill, SimPosition

logger = logging.getLogger(__name__)

# 账户状态列（与 _persist_account/_load_account 对齐）
_ACCOUNT_COLS = (
    "initial_cash",
    "cash",
    "peak_equity",
    "last_equity",
    "daily_pnl",
    "realized_pnl_total",
    "last_turnover",
)


class SimSQLiteStore:
    """模拟仓 SQLite 存储：账户/持仓/成交/权益 四表持久化。

    事务保证: 每个写操作在独立事务内提交；``close()`` 关闭连接。
    恢复语义: ``load_*`` 在无数据时返回空值，缺失/损坏零风险不抛出。
    """

    def __init__(self, db_path: str = "memory/portfolio/simulated/sim_state.db") -> None:
        """初始化并建表。

        Args:
            db_path: SQLite 文件路径（默认生产路径须经存储域登记；显式注入豁免）
        """
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        # GAP-150 写路径契约（严格模式）：默认模拟组合库必须登记（显式注入豁免）
        if db_path == "memory/portfolio/simulated/sim_state.db":
            from fts.store import get_storage_registry

            get_storage_registry().warn_unregistered_write(
                db_path, caller="SimSQLiteStore", strict=True
            )
        self._connect()

    # ─── 生命周期 ────────────────────────────────────────

    def _connect(self) -> None:
        """打开连接、开启 WAL、建表（幂等）。"""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        """建 4 张表（IF NOT EXISTS）。"""
        assert self._conn is not None
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sim_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                initial_cash REAL NOT NULL,
                cash REAL NOT NULL,
                peak_equity REAL NOT NULL,
                last_equity REAL NOT NULL,
                daily_pnl REAL NOT NULL,
                realized_pnl_total REAL NOT NULL,
                last_turnover REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sim_positions (
                symbol VARCHAR PRIMARY KEY,
                market VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                multiplier REAL NOT NULL,
                margin_rate REAL NOT NULL,
                opened_at VARCHAR NOT NULL,
                realized_pnl REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sim_fills (
                order_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                quantity REAL NOT NULL,
                fill_price REAL NOT NULL,
                fee REAL NOT NULL,
                slippage_cost REAL NOT NULL,
                timestamp VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sim_equity_curve (
                date VARCHAR PRIMARY KEY,
                equity REAL NOT NULL,
                cash REAL NOT NULL,
                margin_used REAL NOT NULL,
                position_value REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                daily_pnl REAL NOT NULL,
                turnover REAL NOT NULL,
                n_positions INTEGER NOT NULL
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        """关闭连接。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ─── 账户 ────────────────────────────────────────────

    def save_account(self, account: dict[str, Any]) -> None:
        """Upsert 账户状态（单行）。"""
        assert self._conn is not None
        cols = ", ".join(_ACCOUNT_COLS)
        marks = ", ".join("?" for _ in _ACCOUNT_COLS)
        self._conn.execute(
            f"INSERT INTO sim_account (id, {cols}) VALUES (1, {marks}) "
            f"ON CONFLICT(id) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in _ACCOUNT_COLS),
            [float(account.get(c, 0.0)) for c in _ACCOUNT_COLS],
        )
        self._conn.commit()

    def load_account(self) -> Optional[dict[str, Any]]:
        """读取账户状态；无数据返回 None。"""
        assert self._conn is not None
        row = self._conn.execute("SELECT * FROM sim_account WHERE id = 1").fetchone()
        if row is None:
            return None
        return dict(zip(("id",) + _ACCOUNT_COLS, row))

    # ─── 持仓 ────────────────────────────────────────────

    def save_positions(self, positions: dict[str, SimPosition]) -> None:
        """全量替换持仓表。"""
        assert self._conn is not None
        self._conn.execute("DELETE FROM sim_positions")
        for sym, pos in positions.items():
            self._conn.execute(
                "INSERT INTO sim_positions "
                "(symbol, market, direction, quantity, avg_price, multiplier, margin_rate, opened_at, realized_pnl) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    sym,
                    pos.get("market", ""),
                    pos.get("direction", ""),
                    float(pos.get("quantity", 0.0)),
                    float(pos.get("avg_price", 0.0)),
                    float(pos.get("multiplier", 1.0)),
                    float(pos.get("margin_rate", 1.0)),
                    pos.get("opened_at", ""),
                    float(pos.get("realized_pnl", 0.0)),
                ),
            )
        self._conn.commit()

    def load_positions(self) -> dict[str, SimPosition]:
        """读取全部持仓。"""
        assert self._conn is not None
        out: dict[str, SimPosition] = {}
        for row in self._conn.execute(
            "SELECT symbol, market, direction, quantity, avg_price, multiplier, margin_rate, opened_at, realized_pnl "
            "FROM sim_positions"
        ):
            out[row[0]] = SimPosition(
                symbol=row[0],
                market=row[1],
                direction=row[2],
                quantity=row[3],
                avg_price=row[4],
                multiplier=row[5],
                margin_rate=row[6],
                opened_at=row[7],
                realized_pnl=row[8],
            )
        return out

    # ─── 成交 ────────────────────────────────────────────

    def append_fills(self, fills: list[SimFill]) -> None:
        """追加成交流水（order_id 冲突忽略）。"""
        assert self._conn is not None
        for f in fills:
            self._conn.execute(
                "INSERT OR IGNORE INTO sim_fills "
                "(order_id, symbol, side, quantity, fill_price, fee, slippage_cost, timestamp) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    f.get("order_id", ""),
                    f.get("symbol", ""),
                    f.get("side", ""),
                    float(f.get("quantity", 0.0)),
                    float(f.get("fill_price", 0.0)),
                    float(f.get("fee", 0.0)),
                    float(f.get("slippage_cost", 0.0)),
                    f.get("timestamp", ""),
                ),
            )
        self._conn.commit()

    def load_fills(self) -> list[SimFill]:
        """读取全部成交流水。"""
        assert self._conn is not None
        out: list[SimFill] = []
        for row in self._conn.execute(
            "SELECT order_id, symbol, side, quantity, fill_price, fee, slippage_cost, timestamp FROM sim_fills"
        ):
            out.append(
                SimFill(
                    order_id=row[0],
                    symbol=row[1],
                    side=row[2],
                    quantity=row[3],
                    fill_price=row[4],
                    fee=row[5],
                    slippage_cost=row[6],
                    timestamp=row[7],
                )
            )
        return out

    # ─── 权益曲线 ────────────────────────────────────────

    def append_equity(self, record: SimDailyRecord) -> None:
        """追加一条盯市权益记录（date 冲突忽略）。"""
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR IGNORE INTO sim_equity_curve "
            "(date, equity, cash, margin_used, position_value, realized_pnl, unrealized_pnl, daily_pnl, turnover, n_positions) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                record.get("date", ""),
                float(record.get("equity", 0.0)),
                float(record.get("cash", 0.0)),
                float(record.get("margin_used", 0.0)),
                float(record.get("position_value", 0.0)),
                float(record.get("realized_pnl", 0.0)),
                float(record.get("unrealized_pnl", 0.0)),
                float(record.get("daily_pnl", 0.0)),
                float(record.get("turnover", 0.0)),
                int(record.get("n_positions", 0)),
            ),
        )
        self._conn.commit()

    def load_equity_curve(self) -> list[SimDailyRecord]:
        """读取逐日盯市记录（按日期升序）。"""
        assert self._conn is not None
        out: list[SimDailyRecord] = []
        for row in self._conn.execute(
            "SELECT date, equity, cash, margin_used, position_value, realized_pnl, unrealized_pnl, daily_pnl, turnover, n_positions "
            "FROM sim_equity_curve ORDER BY date"
        ):
            out.append(
                SimDailyRecord(
                    date=row[0],
                    equity=row[1],
                    cash=row[2],
                    margin_used=row[3],
                    position_value=row[4],
                    realized_pnl=row[5],
                    unrealized_pnl=row[6],
                    daily_pnl=row[7],
                    turnover=row[8],
                    n_positions=row[9],
                )
            )
        return out

    # ─── 便捷：全量快照 ──────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """返回当前持久化快照（账户/持仓/成交/权益）。"""
        return {
            "account": self.load_account(),
            "positions": self.load_positions(),
            "fills": self.load_fills(),
            "equity_curve": self.load_equity_curve(),
        }


__all__ = ["SimSQLiteStore"]