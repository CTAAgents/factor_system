"""fts/factor_engine/l3_signal_service.py — L3 信号矩阵服务（plans/40 B/D 层）。

把 L3 组合重算中最重的"信号重复重算"收敛为单一 2D 信号矩阵服务：
    - B 层: 统一的 (n_dates, n_stocks, n_factors) 信号矩阵构建（向量化对齐 +
      信号缓存复用），并把相关性/收益矩阵下沉 DuckDB SQL（C++ 向量化）；
    - D 层: 信号矩阵作为一等公民资产持久化到 DuckDB，支持增量重算
      （仅新晋升因子 / 未入库 (code_hash, params) 全量算，存量仅追加新窗口）。

语义零漂移铁律（对齐 plans/38/39 与 _auto_build_factor_returns 现值）:
    - 逐品种执行 FactorExecutor（与现值一致），信号经 `df.index.get_indexer`
      向量化对齐到共同日期（plans/40 A 层，替代 O(n²) list.index）；
    - 前向收益口径: fwd[t] = (close[t+h] - close[t]) / max(close[t], 1e-10)；
    - 相关性: 参考品种时间序列两两 Pearson，缺省值与 np.corrcoef 一致；
    - 依赖缺失（duckdb/因子代码异常）→ 逐品种现值回退，绝不改变下游语义。

DuckDB 连接纪律（E.4 S1）: 写连接一律短生命周期 + filelock 跨进程串行化，
读连接 read_only 短连接；禁止模块级常驻写连接。

版本: v1.0.0（plans/40 B/D 层）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 数据契约 ─────────────────────────────────────────────


@dataclass
class SignalMatrixBundle:
    """2D/3D 信号矩阵构建结果（plans/40 B 层数据契约）。

    Attributes:
        signal_matrix: 因子信号矩阵 (n_dates, n_stocks, n_factors)，NaN=缺失
        forward_returns: 前向收益矩阵 (n_dates, n_stocks)，与 signal_matrix 对齐
        dates: 共同交易日序列（长度 n_dates）
        symbols: 品种列表（长度 n_stocks）
        factor_ids: 因子 ID 列表（长度 n_factors）
        forward_days: 前向持有期
    """

    signal_matrix: np.ndarray
    forward_returns: np.ndarray
    dates: list[Any]
    symbols: list[str]
    factor_ids: list[str]
    forward_days: int = field(default=5)

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.signal_matrix.shape

    def reference_signal_2d(self, ref_symbol: str | None = None) -> np.ndarray:
        """取参考品种的时间序列信号 (n_dates, n_factors)，供相关性/聚类使用。

        Args:
            ref_symbol: 参考品种；None 用第一个有数据的品种

        Returns:
            (n_dates, n_factors) 信号矩阵；无数据返回空数组
        """
        if self.signal_matrix.size == 0:
            return np.array([])
        if ref_symbol is None or ref_symbol not in self.symbols:
            # 选第一个在任意日期有任一因子有效信号的品种
            per_sym_valid = np.isfinite(self.signal_matrix).any(axis=2).sum(axis=0)
            if per_sym_valid.size == 0 or int(per_sym_valid.max()) == 0:
                return np.array([])
            idx = int(np.argmax(per_sym_valid))
        else:
            idx = self.symbols.index(ref_symbol)
        return np.asarray(self.signal_matrix[:, idx, :], dtype=np.float64)


# ─── 向量化对齐（plans/40 A 层）──────────────────────────


def align_signal_to_dates(
    sig: np.ndarray | pd.Series,
    df: pd.DataFrame,
    common_dates: Sequence[Any],
) -> np.ndarray:
    """将信号序列按共同日期向量化对齐（hash 查找，O(n)）。

    语义: 不在 df 索引中的日期留 NaN；越界（sig 短于 df）也留 NaN。
    """
    n_dates = len(common_dates)
    out = np.full(n_dates, np.nan, dtype=np.float64)
    if df is None or len(df) == 0:
        return out
    sig_arr = np.asarray(sig, dtype=np.float64)
    loc = df.index.get_indexer(common_dates)  # -1 表示不在
    valid = (loc >= 0) & (loc < len(sig_arr))
    if np.any(valid):
        out[valid] = sig_arr[loc[valid]]
    return out


# ─── 2D 信号矩阵构建（B 层核心）──────────────────────────


def build_signal_matrix(
    panel: dict[str, pd.DataFrame],
    valid_factors: list[dict[str, Any]],
    factor_codes: dict[str, dict[str, Any]],
    common_dates: Sequence[Any],
    forward_days: int = 5,
    signal_cache: Any = None,
) -> SignalMatrixBundle:
    """一次性构建 2D/3D 信号矩阵（B 层核心）。

    逐因子 × 逐品种执行 FactorExecutor（复用信号缓存），向量化对齐到共同日期，
    前向收益向量化。语义与 `_auto_build_factor_returns` 现值完全一致。

    Args:
        panel: {symbol: DataFrame(OHLCV)} 市场面板
        valid_factors: 含 factor_id 的因子列表
        factor_codes: factor_id → 因子记录（含 code/params）
        common_dates: 目标对齐日期序列
        forward_days: 前向持有期（默认 5）
        signal_cache: 可选信号缓存（plans/40 A 层），跨步骤复用

    Returns:
        SignalMatrixBundle；因子代码不可用/执行失败处留 NaN（与现值一致）
    """
    from .factor_program import FactorExecutor

    stocks = sorted(panel.keys())
    n_dates, n_stocks, n_factors = len(common_dates), len(stocks), len(valid_factors)
    signal_matrix = np.full((n_dates, n_stocks, n_factors), np.nan, dtype=np.float64)

    for j, f in enumerate(valid_factors):
        fdata = factor_codes.get(f["factor_id"])
        if not fdata:
            continue
        try:
            executor = FactorExecutor(fdata, signal_cache=signal_cache)
        except Exception:  # noqa: BLE001 — 编译失败留 NaN（与现值一致）
            continue
        for i, sym in enumerate(stocks):
            df = panel.get(sym)
            if df is None or df.empty:
                continue
            try:
                sig = executor.execute(df, fdata.get("params", {}))
                signal_matrix[:, i, j] = align_signal_to_dates(sig, df, common_dates)
            except Exception:  # noqa: BLE001 — 单品种执行失败留 NaN
                continue

    forward_returns = np.full((n_dates, n_stocks), np.nan, dtype=np.float64)
    for i, sym in enumerate(stocks):
        df = panel.get(sym)
        if df is None or df.empty or "close" not in df.columns:
            continue
        closes = df["close"].to_numpy(dtype=np.float64)
        fwd = np.full(len(closes), np.nan, dtype=np.float64)
        fwd[:-forward_days] = (
            (closes[forward_days:] - closes[:-forward_days]) / np.maximum(closes[:-forward_days], 1e-10)
        )
        forward_returns[:, i] = align_signal_to_dates(fwd, df, common_dates)

    return SignalMatrixBundle(
        signal_matrix=signal_matrix,
        forward_returns=forward_returns,
        dates=list(common_dates),
        symbols=stocks,
        factor_ids=[f["factor_id"] for f in valid_factors],
        forward_days=forward_days,
    )


# ─── DuckDB 相关性矩阵（B 层下沉）────────────────────────


def duckdb_corr_matrix(
    signal_2d: np.ndarray,
    factor_ids: Sequence[str],
    min_valid_points: int = 10,
) -> np.ndarray:
    """用 DuckDB SQL 计算因子时间序列两两 Pearson 相关（C++ 向量化）。

    输入 (n_dates, n_factors) 时间序列矩阵（参考品种），输出 (n, n) 相关矩阵。
    与 np.corrcoef 语义一致：逐对忽略任一列 NaN；有效点数 ≤ min_valid_points
    的对置 NaN。

    Args:
        signal_2d: 时间序列信号矩阵 (n_dates, n_factors)
        factor_ids: 因子 ID 序列（长度 = n_factors）
        min_valid_points: 最少有效点（不足置 NaN）

    Returns:
        corr: (n_factors, n_factors) 相关矩阵（对角 1.0）
    """
    n = len(factor_ids)
    corr = np.full((n, n), np.nan, dtype=np.float64)
    if n == 0:
        return corr
    np.fill_diagonal(corr, 1.0)
    if signal_2d.ndim != 2 or signal_2d.shape[1] != n or signal_2d.shape[0] < 2:
        return corr

    try:
        import duckdb
    except Exception:  # noqa: BLE001 — 依赖缺失回退 numpy
        return _numpy_corr_matrix(signal_2d, min_valid_points)

    df_wide = pd.DataFrame(np.asarray(signal_2d, dtype=np.float64), columns=[str(f) for f in factor_ids])
    # 清理非法列名（factor_id 可能含特殊字符）
    df_wide.columns = [f"c{i}" for i in range(n)]
    try:
        con = duckdb.connect()  # 内存连接（读路径，短生命周期）
        try:
            con.register("sig", df_wide)
            for i in range(n):
                for j in range(i + 1, n):
                    row = con.execute(
                        f'SELECT corr(sig."c{i}", sig."c{j}"), '
                        f'count(*) FILTER (WHERE sig."c{i}" IS NOT NULL AND sig."c{j}" IS NOT NULL) '
                        f"FROM sig"
                    ).fetchone()
                    if row is None or row[0] is None or int(row[1]) <= min_valid_points:
                        continue
                    c = float(row[0])
                    if np.isfinite(c):
                        corr[i, j] = corr[j, i] = c
        finally:
            con.close()
        return corr
    except Exception as e:  # noqa: BLE001 — SQL 异常回退 numpy，零漂移
        logger.debug("[L3-SIGNAL] DuckDB 相关性失败，回退 numpy: %s", e)
        return _numpy_corr_matrix(signal_2d, min_valid_points)


def _numpy_corr_matrix(signal_2d: np.ndarray, min_valid_points: int = 10) -> np.ndarray:
    """numpy 参考实现（与 duckdb_corr_matrix 语义一致）。"""
    n = signal_2d.shape[1]
    corr = np.full((n, n), np.nan, dtype=np.float64)
    np.fill_diagonal(corr, 1.0)
    for i in range(n):
        for j in range(i + 1, n):
            s1, s2 = signal_2d[:, i], signal_2d[:, j]
            valid = ~(np.isnan(s1) | np.isnan(s2))
            if int(valid.sum()) > min_valid_points:
                c = float(np.corrcoef(s1[valid], s2[valid])[0, 1])
                if np.isfinite(c):
                    corr[i, j] = corr[j, i] = c
    return corr


# ─── DuckDB 持久化 + 增量（D 层）─────────────────────────

_L3_SIGNAL_TABLE = "l3_signal_matrix"
_L3_SIGNAL_META_TABLE = "l3_signal_meta"
_DEFAULT_DB_PATH = "data/l3_signal_store.duckdb"


def _connect(db_path: str | Path, read_only: bool = False):
    import duckdb

    path = str(db_path)
    if read_only:
        con = duckdb.connect(path, read_only=True)
    else:
        con = duckdb.connect(path)
        try:
            con.execute("SET lock_configuration = true")
        except Exception:  # noqa: BLE001 — 旧版不支持时静默降级
            pass
    return con


def _init_tables(con) -> None:
    """幂等建表（D 层）。"""
    con.execute(
        f"""CREATE TABLE IF NOT EXISTS {_L3_SIGNAL_META_TABLE} (
            factor_id VARCHAR NOT NULL,
            code_hash VARCHAR NOT NULL,
            params_hash VARCHAR NOT NULL,
            market VARCHAR NOT NULL,
            end_date VARCHAR NOT NULL,
            n_dates BIGINT NOT NULL,
            n_symbols BIGINT NOT NULL,
            updated_at VARCHAR NOT NULL,
            PRIMARY KEY (factor_id, market, end_date)
        )"""
    )
    con.execute(
        f"""CREATE TABLE IF NOT EXISTS {_L3_SIGNAL_TABLE} (
            factor_id VARCHAR NOT NULL,
            market VARCHAR NOT NULL,
            end_date VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            signal DOUBLE[] NOT NULL,
            PRIMARY KEY (factor_id, market, end_date, symbol)
        )"""
    )


def _params_hash(params: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(params or {}, sort_keys=True, default=str).encode()).hexdigest()


def persist_signal_matrix(
    bundle: SignalMatrixBundle,
    factor_code_hashes: dict[str, str],
    market: str,
    end_date: str,
    db_path: str | Path | None = None,
) -> bool:
    """将信号矩阵写入 DuckDB（D 层，短写连接 + filelock）。

    Args:
        bundle: 信号矩阵（因子列序 = bundle.factor_ids）
        factor_code_hashes: factor_id → code_hash（用于增量判定）
        market: 市场标识
        end_date: 数据截止日（YYYY-MM-DD）
        db_path: DuckDB 路径（默认 data/l3_signal_store.duckdb）

    Returns:
        写入成功返回 True
    """
    if bundle.signal_matrix.size == 0 or not bundle.factor_ids:
        return False
    db_path = db_path or _DEFAULT_DB_PATH
    try:
        from fts.store.duckdb_lock import duckdb_write_lock

        with duckdb_write_lock(str(db_path)):
            con = _connect(db_path)
            try:
                _init_tables(con)
                params_hashes = {
                    fid: _params_hash({})
                    for fid in bundle.factor_ids
                }
                updated_at = pd.Timestamp.now().isoformat()
                for j, fid in enumerate(bundle.factor_ids):
                    code_hash = factor_code_hashes.get(fid, "")
                    con.execute(
                        f"DELETE FROM {_L3_SIGNAL_META_TABLE} WHERE factor_id=? AND market=? AND end_date=?",
                        [fid, market, end_date],
                    )
                    con.execute(
                        f"INSERT INTO {_L3_SIGNAL_META_TABLE} VALUES (?,?,?,?,?,?,?,?)",
                        [
                            fid,
                            code_hash,
                            params_hashes[fid],
                            market,
                            end_date,
                            int(bundle.signal_matrix.shape[0]),
                            int(bundle.signal_matrix.shape[1]),
                            updated_at,
                        ],
                    )
                    con.execute(
                        f"DELETE FROM {_L3_SIGNAL_TABLE} WHERE factor_id=? AND market=? AND end_date=?",
                        [fid, market, end_date],
                    )
                    for i, sym in enumerate(bundle.symbols):
                        col = np.asarray(bundle.signal_matrix[:, i, j], dtype=np.float64)
                        con.execute(
                            f"INSERT INTO {_L3_SIGNAL_TABLE} VALUES (?,?,?,?,?)",
                            [fid, market, end_date, sym, col.tolist()],
                        )
            finally:
                con.close()
        logger.info("[L3-SIGNAL] 信号矩阵持久化: %d 因子 × %d 品种 × %d 日 [%s@%s]",
                    len(bundle.factor_ids), len(bundle.symbols), len(bundle.dates), market, end_date)
        return True
    except Exception as e:  # noqa: BLE001 — 持久化失败不阻断主流程（仅日志）
        logger.warning("[L3-SIGNAL] 信号矩阵持久化失败（非致命）: %s", e)
        return False


def load_signal_matrix(
    factor_ids: Sequence[str],
    market: str,
    end_date: str,
    db_path: str | Path | None = None,
) -> Optional[SignalMatrixBundle]:
    """从 DuckDB 读取信号矩阵（D 层，只读短连接）。

    Returns:
        SignalMatrixBundle（symbols 为持久化时的品种全集，含 NaN 缺失列）；
        无记录/失败返回 None
    """
    db_path = db_path or _DEFAULT_DB_PATH
    try:
        con = _connect(db_path, read_only=True)
        try:
            rows = con.execute(
                f"SELECT symbol, factor_id, signal FROM {_L3_SIGNAL_TABLE} "
                f"WHERE market=? AND end_date=? AND factor_id = ANY(select unnest(?::varchar[]))",
                [market, end_date, list(factor_ids)],
            ).fetchall()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("[L3-SIGNAL] 信号矩阵读取失败（非致命）: %s", e)
        return None
    if not rows:
        return None

    # 重建 (n_dates, n_symbols, n_factors)
    meta_rows = None
    try:
        con = _connect(db_path, read_only=True)
        try:
            meta_rows = con.execute(
                f"SELECT factor_id, n_dates, n_symbols FROM {_L3_SIGNAL_META_TABLE} "
                f"WHERE market=? AND end_date=? AND factor_id = ANY(select unnest(?::varchar[]))",
                [market, end_date, list(factor_ids)],
            ).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        meta_rows = None
    n_dates = int(meta_rows[0][1]) if meta_rows else max((len(r[2]) for r in rows), default=0)
    symbols = sorted({r[0] for r in rows})
    factor_ids_loaded = [f for f in factor_ids]
    n_f = len(factor_ids_loaded)
    mat = np.full((n_dates, len(symbols), n_f), np.nan, dtype=np.float64)
    sym_idx = {s: i for i, s in enumerate(symbols)}
    fid_idx = {f: j for j, f in enumerate(factor_ids_loaded)}
    for sym, fid, sig in rows:
        arr = np.asarray(sig, dtype=np.float64)
        if sym in sym_idx and fid in fid_idx:
            mat[: min(n_dates, len(arr)), sym_idx[sym], fid_idx[fid]] = arr[:n_dates]

    # 前向收益（从信号矩阵无法反推，D 层持久化不含；由调用方按需重建）
    fwd = np.full((n_dates, len(symbols)), np.nan, dtype=np.float64)
    return SignalMatrixBundle(
        signal_matrix=mat,
        forward_returns=fwd,
        dates=[],
        symbols=symbols,
        factor_ids=factor_ids_loaded,
    )


def incremental_factor_ids(
    factor_ids: Sequence[str],
    factor_code_hashes: dict[str, str],
    market: str,
    end_date: str,
    db_path: str | Path | None = None,
) -> tuple[list[str], list[str]]:
    """增量判定（D 层）：返回 (需要全量重算的因子, 已入库可复用的因子)。

    规则: 因子不在 meta 表 / 无 code_hash 或 code_hash 与库中不一致 →
    全量重算；其余视为可复用。

    Args:
        factor_ids: 待处理因子
        factor_code_hashes: factor_id → code_hash
        market: 市场
        end_date: 数据截止日
        db_path: DuckDB 路径

    Returns:
        (to_recompute, reusable): 需重算 / 可复用因子 ID 列表
    """
    if not factor_ids:
        return [], []
    db_path = db_path or _DEFAULT_DB_PATH
    stored: dict[str, str] = {}
    try:
        con = _connect(db_path, read_only=True)
        try:
            rows = con.execute(
                f"SELECT factor_id, code_hash FROM {_L3_SIGNAL_META_TABLE} "
                f"WHERE market=? AND end_date=? AND factor_id = ANY(select unnest(?::varchar[]))",
                [market, end_date, list(factor_ids)],
            ).fetchall()
        finally:
            con.close()
        stored = {r[0]: (r[1] or "") for r in rows}
    except Exception:  # noqa: BLE001
        stored = {}
    to_recompute: list[str] = []
    reusable: list[str] = []
    for fid in factor_ids:
        expected = factor_code_hashes.get(fid, "")
        if fid in stored and expected and stored.get(fid) == expected:
            reusable.append(fid)
        else:
            to_recompute.append(fid)
    return to_recompute, reusable


def _code_hash(code: str) -> str:
    """因子代码 SHA-256（增量判定用，与 factor_catalog.code_hash 口径一致）。"""
    import hashlib

    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


def load_or_build_signal_matrix(
    panel: dict[str, pd.DataFrame],
    valid_factors: list[dict[str, Any]],
    factor_codes: dict[str, dict[str, Any]],
    common_dates: Sequence[Any],
    market: str,
    end_date: str,
    db_path: str | Path | None = None,
    forward_days: int = 5,
    signal_cache: Any = None,
    use_store: bool = True,
) -> SignalMatrixBundle:
    """D 层：信号矩阵一等公民读取 + 增量构建（架构级根治）。

    对已入库且 code_hash 一致的因子从 DuckDB 读取（不重算）；仅对新增/变更因子
    全量重算，合并为完整 3D 信号矩阵后回写（增量重算）。语义与
    ``build_signal_matrix`` 完全一致（新算部分逐品种执行 + 向量化对齐）。

    Args:
        panel: {symbol: DataFrame(OHLCV)} 市场面板
        valid_factors: 含 factor_id/code 的因子列表（顺序 = 输出矩阵因子列序）
        factor_codes: factor_id → 因子记录（含 code/params）
        common_dates: 目标对齐日期序列
        market: 市场标识（增量判定维度）
        end_date: 数据截止日（YYYY-MM-DD，增量判定维度）
        db_path: 信号库路径（默认 data/l3_signal_store.duckdb）
        forward_days: 前向持有期（默认 5）
        signal_cache: 可选信号缓存（plans/40 A 层）
        use_store: False 时退化为纯 build_signal_matrix（不读写库）

    Returns:
        SignalMatrixBundle（3D 矩阵 + 前向收益）；因子代码缺失处留 NaN
    """
    stocks = sorted(panel.keys())
    n_dates, n_factors = len(common_dates), len(valid_factors)
    factor_ids = [f["factor_id"] for f in valid_factors]

    if not use_store or not valid_factors:
        return build_signal_matrix(
            panel, valid_factors, factor_codes, common_dates,
            forward_days=forward_days, signal_cache=signal_cache,
        )

    code_hashes = {f["factor_id"]: _code_hash(f.get("code", "")) for f in valid_factors}
    to_recompute, reusable = incremental_factor_ids(factor_ids, code_hashes, market, end_date, db_path)

    # 仅增量重算新/变更因子
    bundle_new: Optional[SignalMatrixBundle] = None
    recompute_factors = [f for f in valid_factors if f["factor_id"] in to_recompute]
    if recompute_factors:
        bundle_new = build_signal_matrix(
            panel, recompute_factors, factor_codes, common_dates,
            forward_days=forward_days, signal_cache=signal_cache,
        )
    loaded: Optional[SignalMatrixBundle] = None
    if reusable:
        loaded = load_signal_matrix(reusable, market, end_date, db_path)

    # 合并
    mat = np.full((n_dates, len(stocks), n_factors), np.nan, dtype=np.float64)
    fid_pos = {f: j for j, f in enumerate(factor_ids)}
    if bundle_new is not None:
        for j, fid in enumerate(bundle_new.factor_ids):
            if fid in fid_pos:
                mat[:, :, fid_pos[fid]] = bundle_new.signal_matrix[:, :, j]
    if loaded is not None:
        for j, fid in enumerate(loaded.factor_ids):
            if fid not in fid_pos:
                continue
            for i, sym in enumerate(loaded.symbols):
                if sym in stocks:
                    mat[:, stocks.index(sym), fid_pos[fid]] = loaded.signal_matrix[:, i, j]

    # 前向收益（始终按当前 panel 重建，保证口径一致）
    fwd = np.full((n_dates, len(stocks)), np.nan, dtype=np.float64)
    for i, sym in enumerate(stocks):
        df = panel.get(sym)
        if df is None or df.empty or "close" not in df.columns:
            continue
        closes = df["close"].to_numpy(dtype=np.float64)
        f = np.full(len(closes), np.nan, dtype=np.float64)
        f[:-forward_days] = (closes[forward_days:] - closes[:-forward_days]) / np.maximum(closes[:-forward_days], 1e-10)
        fwd[:, i] = align_signal_to_dates(f, df, common_dates)

    # 回写新算因子（短写连接 + filelock，失败不阻断）
    if bundle_new is not None and bundle_new.factor_ids:
        persist_signal_matrix(bundle_new, code_hashes, market, end_date, db_path)

    return SignalMatrixBundle(
        signal_matrix=mat,
        forward_returns=fwd,
        dates=list(common_dates),
        symbols=stocks,
        factor_ids=factor_ids,
        forward_days=forward_days,
    )


__all__ = [
    "SignalMatrixBundle",
    "align_signal_to_dates",
    "build_signal_matrix",
    "duckdb_corr_matrix",
    "persist_signal_matrix",
    "load_signal_matrix",
    "incremental_factor_ids",
    "load_or_build_signal_matrix",
]
