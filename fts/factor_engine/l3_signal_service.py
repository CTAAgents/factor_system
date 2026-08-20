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
    out: np.ndarray = np.full(n_dates, np.nan, dtype=np.float64)
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
    signal_matrix: np.ndarray = np.full((n_dates, n_stocks, n_factors), np.nan, dtype=np.float64)

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

    forward_returns: np.ndarray = np.full((n_dates, n_stocks), np.nan, dtype=np.float64)
    for i, sym in enumerate(stocks):
        df = panel.get(sym)
        if df is None or df.empty or "close" not in df.columns:
            continue
        closes = df["close"].to_numpy(dtype=np.float64)
        fwd: np.ndarray = np.full(len(closes), np.nan, dtype=np.float64)
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

    保留状态（plans/51 B4 豁免）：**未接入生产**——本实现逐对执行一次 SQL
    查询（Python 循环 + 每对一次往返），而参考品种因子数通常 <100，消费方
    ``portfolio_loop._compute_signal_correlations`` / ``factor_clustering``
    沿用 numpy ``corrcoef`` 单次调用更优。本函数保留为备用路径（依赖缺失/超大
    相关矩阵场景），与 numpy 语义逐位一致（``test_matches_numpy_reference``）。

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
    corr: np.ndarray = np.full((n, n), np.nan, dtype=np.float64)
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
    corr: np.ndarray = np.full((n, n), np.nan, dtype=np.float64)
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
# plans/52：增量窗口追加的执行回退长度——旧窗口尾部回退段覆盖滚动窗口算子
# 的历史回溯需求（DSL/feature_ops 最大支持窗口上限的保守值；无法覆盖的超长
# 窗口因子由抽样对照验证兜底自动降级全量）
_W_RECALL = 500


def _default_db_path() -> str:
    """默认信号库路径：配置优先（plans/51 B2 存储域登记后路径可配），缺失回退硬编码。

    离线/测试环境 get_config 可能失败，回退 ``_DEFAULT_DB_PATH`` 保证可运行。
    """
    try:
        from fts.config.settings import get_config

        p = get_config().l3_signal_store_db
        if p:
            return p
    except Exception:  # noqa: BLE001 — 配置读取失败回退硬编码
        pass
    return _DEFAULT_DB_PATH


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
            dates_digest VARCHAR NOT NULL DEFAULT '',
            PRIMARY KEY (factor_id, market, end_date)
        )"""
    )
    # plans/52：存量库迁移补 dates_digest 列（增量窗口追加前缀判定用）
    try:
        con.execute(
            f"ALTER TABLE {_L3_SIGNAL_META_TABLE} "
            "ADD COLUMN IF NOT EXISTS dates_digest VARCHAR NOT NULL DEFAULT ''"
        )
    except Exception:  # noqa: BLE001 — 旧版本不支持 IF NOT EXISTS 时忽略（列可能已存在）
        pass
    # plans/57 信号契约 v1：追加 schema_version / factor_status / factor_scope 三列（幂等迁移）。
    # DuckDB 1.5.x 不支持 ADD COLUMN 带约束（NOT NULL/DEFAULT），故用无约束 ADD；
    # 写入侧恒提供全量值、读取侧 NULL 回退默认（load_signal_meta），语义等价默认列。
    for _ddl in (
        f"ALTER TABLE {_L3_SIGNAL_META_TABLE} ADD COLUMN IF NOT EXISTS schema_version INTEGER",
        f"ALTER TABLE {_L3_SIGNAL_META_TABLE} ADD COLUMN IF NOT EXISTS factor_status VARCHAR",
        f"ALTER TABLE {_L3_SIGNAL_META_TABLE} ADD COLUMN IF NOT EXISTS factor_scope JSON",
    ):
        try:
            con.execute(_ddl)
        except Exception:  # noqa: BLE001 — 旧版本不支持时忽略（列可能已存在）
            pass
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


def _dates_digest(dates: Sequence[Any]) -> str:
    """日期序列指纹（plans/52 前缀追加判定）：blake2 摘要，空序列返回空串。

    增量窗口追加需判定"库中旧窗口日期 = 当前 common_dates 前缀"，对日期序列
    求指纹做 O(1) 比对（替代逐日比对）。空序列返回空串（与"未记录"兼容）。
    """
    if not dates:
        return ""
    import hashlib

    h = hashlib.blake2b(digest_size=16)
    for d in dates:
        h.update(str(d).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def persist_signal_matrix(
    bundle: SignalMatrixBundle,
    factor_code_hashes: dict[str, str],
    market: str,
    end_date: str,
    db_path: str | Path | None = None,
    params_hashes: Optional[dict[str, str]] = None,
    factor_status_map: Optional[dict[str, str]] = None,
    factor_scope_map: Optional[dict[str, dict[str, Any]]] = None,
    schema_version: int = 1,
) -> bool:
    """将信号矩阵写入 DuckDB（D 层，短写连接 + filelock）。

    Args:
        bundle: 信号矩阵（因子列序 = bundle.factor_ids）
        factor_code_hashes: factor_id → code_hash（用于增量判定）
        market: 市场标识
        end_date: 数据截止日（YYYY-MM-DD）
        db_path: DuckDB 路径（默认 data/l3_signal_store.duckdb）
        params_hashes: factor_id → params_hash（真实参数哈希，plans/51 A1；
            None 时回退空参数哈希——仅限测试/兼容路径，主流程必须传真值）
        factor_status_map: factor_id → factor_status（active/degraded/shadow/retired，
            plans/57 契约 v1 状态传播；None 落默认 pending）
        factor_scope_map: factor_id → scope 定义（plans/57 契约 v1：{"subchain_scope":
            str|list, "subchain_specific": list}；P2 契约 v2 扩展：{"kind": "all"|"chain"|
            "symbol", "chains": [...], "symbols": [...], "evidence": {...}}——JSON 列直通，
            写入方按因子 metadata.scope_domain 构造；RD 不识别 v2 时按 schema_version 降级）
        schema_version: 契约版本（FTS 侧契约变更时递增，RD 校验不兼容即降级；P2 起支持 2）

    Returns:
        写入成功返回 True
    """
    if bundle.signal_matrix.size == 0 or not bundle.factor_ids:
        return False
    # GAP-150 写路径契约（严格模式）：默认信号库必须登记（显式注入豁免）
    if db_path is None:
        from fts.store import get_storage_registry

        get_storage_registry().warn_unregistered_write(
            _default_db_path(), caller="L3SignalStore", strict=True
        )
    db_path = db_path or _default_db_path()
    try:
        from fts.store.duckdb_lock import duckdb_write_lock

        with duckdb_write_lock(str(db_path)):
            con = _connect(db_path)
            try:
                _init_tables(con)
                params_hashes = params_hashes or {}
                stored_params_hashes = {
                    fid: (params_hashes.get(fid) or _params_hash({}))
                    for fid in bundle.factor_ids
                }
                status_map = factor_status_map or {}
                scope_map = factor_scope_map or {}
                updated_at = pd.Timestamp.now().isoformat()
                dates_digest = _dates_digest(list(bundle.dates))  # plans/52 前缀判定指纹
                for j, fid in enumerate(bundle.factor_ids):
                    code_hash = factor_code_hashes.get(fid, "")
                    con.execute(
                        f"DELETE FROM {_L3_SIGNAL_META_TABLE} WHERE factor_id=? AND market=? AND end_date=?",
                        [fid, market, end_date],
                    )
                    con.execute(
                        f"INSERT INTO {_L3_SIGNAL_META_TABLE} "
                        "(factor_id, code_hash, params_hash, market, end_date, n_dates, n_symbols, "
                        "updated_at, dates_digest, schema_version, factor_status, factor_scope) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,CAST(? AS JSON))",
                        [
                            fid,
                            code_hash,
                            stored_params_hashes[fid],
                            market,
                            end_date,
                            int(bundle.signal_matrix.shape[0]),
                            int(bundle.signal_matrix.shape[1]),
                            updated_at,
                            dates_digest,
                            int(schema_version),
                            status_map.get(fid, "pending"),
                            json.dumps(
                                scope_map.get(fid) or {"subchain_scope": "all", "subchain_specific": []},
                                ensure_ascii=False,
                            ),
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
    common_dates: Optional[Sequence[Any]] = None,
) -> Optional[SignalMatrixBundle]:
    """从 DuckDB 读取信号矩阵（D 层，只读短连接）。

    契约说明（plans/51 A3）:
        - signal_matrix 完整（symbols 为持久化时的品种全集，含 NaN 缺失列）；
        - forward_returns 全 NaN —— D 层持久化不含前向收益，需调用方按 panel 重建
          （参考 load_or_build_signal_matrix 的合并重建逻辑），不可直接消费；
        - dates 仅在传入 common_dates 时回填；forward_days 取默认 5，持久化时不
          含该元数据，调用方需自行确认。

    Args:
        factor_ids: 待读取因子
        market: 市场标识
        end_date: 数据截止日
        db_path: DuckDB 路径
        common_dates: 目标日期序列（回填 bundle.dates；行数与库中不一致时以库中
            n_dates 为准截断/填充 NaN）

    Returns:
        SignalMatrixBundle；无记录/失败返回 None
    """
    db_path = db_path or _default_db_path()
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
    mat: np.ndarray = np.full((n_dates, len(symbols), n_f), np.nan, dtype=np.float64)
    sym_idx = {s: i for i, s in enumerate(symbols)}
    fid_idx = {f: j for j, f in enumerate(factor_ids_loaded)}
    for sym, fid, sig in rows:
        arr = np.asarray(sig, dtype=np.float64)
        if sym in sym_idx and fid in fid_idx:
            mat[: min(n_dates, len(arr)), sym_idx[sym], fid_idx[fid]] = arr[:n_dates]

    # 前向收益（从信号矩阵无法反推，D 层持久化不含；由调用方按需重建）
    fwd: np.ndarray = np.full((n_dates, len(symbols)), np.nan, dtype=np.float64)
    dates_out: list[Any] = []
    if common_dates is not None:
        dates_out = list(common_dates)[:n_dates]
    return SignalMatrixBundle(
        signal_matrix=mat,
        forward_returns=fwd,
        dates=dates_out,
        symbols=symbols,
        factor_ids=factor_ids_loaded,
    )


def load_signal_meta(
    factor_ids: Sequence[str],
    market: str,
    end_date: str,
    db_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """读取信号矩阵 meta（D 层，只读短连接）——plans/57 契约 v1。

    返回 {factor_id: {code_hash, params_hash, schema_version, factor_status,
    factor_scope, dates_digest, n_dates, n_symbols, updated_at}}；
    无记录/失败返回 {}（RD 侧据此判定信号缺失 → 降级）。

    Args:
        factor_ids: 待读取因子
        market: 市场标识
        end_date: 数据截止日
        db_path: DuckDB 路径
    """
    if not factor_ids:
        return {}
    db_path = db_path or _default_db_path()
    out: dict[str, dict[str, Any]] = {}
    try:
        con = _connect(db_path, read_only=True)
        try:
            rows = con.execute(
                f"SELECT factor_id, code_hash, params_hash, schema_version, factor_status, "
                f"factor_scope, dates_digest, n_dates, n_symbols, updated_at "
                f"FROM {_L3_SIGNAL_META_TABLE} "
                f"WHERE market=? AND end_date=? AND factor_id = ANY(select unnest(?::varchar[]))",
                [market, end_date, list(factor_ids)],
            ).fetchall()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("[L3-SIGNAL] meta 读取失败（非致命）: %s", e)
        return out
    for r in rows:
        fid, scope = r[0], r[5]
        try:
            scope = json.loads(scope) if isinstance(scope, str) else scope
        except Exception:  # noqa: BLE001 — 历史脏数据降级为通用范围
            scope = {"subchain_scope": "all", "subchain_specific": []}
        out[fid] = {
            "code_hash": r[1],
            "params_hash": r[2],
            "schema_version": int(r[3] or 1),
            "factor_status": r[4] or "pending",
            "factor_scope": scope,
            "dates_digest": r[6] or "",
            "n_dates": int(r[7] or 0),
            "n_symbols": int(r[8] or 0),
            "updated_at": r[9],
        }
    return out


def backfill_signal_matrix(
    panel: dict[str, pd.DataFrame],
    valid_factors: list[dict[str, Any]],
    factor_codes: dict[str, dict[str, Any]],
    common_dates: Sequence[Any],
    market: str,
    end_date: str,
    db_path: str | Path | None = None,
    forward_days: int = 5,
    signal_cache: Any = None,
    factor_status_map: Optional[dict[str, str]] = None,
    factor_scope_map: Optional[dict[str, dict[str, Any]]] = None,
    schema_version: int = 1,
) -> SignalMatrixBundle:
    """历史回填模式（plans/57 §6.5 / §6.8.3，复用 build_signal_matrix 核心）。

    输入 QuantData 全历史面板 + (date_range, factor_ids)，按当前因子代码/参数
    全量回算并持久化到指定 db_path（回填工作区，不污染生产信号库）。

    版本锁定（§6.8.3 ①）：回填执行 code 与 factor_codes 内 code_hash 比对，
    不一致（因子已演化变更）→ 日志告警并用当前 code 重算（重审本就是重新评估）；
    code_hash+params_hash 双哈希保证回填版本与实盘一致。

    Args:
        panel: {symbol: DataFrame(OHLCV)} 全历史面板（QuantData continuous_daily）
        valid_factors: 含 factor_id/code 的因子列表
        factor_codes: factor_id → 因子记录（含 code/params/code_hash）
        common_dates: 目标对齐日期序列（date_range 内全部交易日）
        market: 市场标识
        end_date: 数据截止日（YYYY-MM-DD，date_range 上界）
        db_path: 回填落库路径（默认 _default_db_path()——调用方应传工作区路径隔离）
        forward_days: 前向持有期
        signal_cache: 可选信号缓存
        factor_status_map / factor_scope_map: 契约 meta（plans/57 状态传播）
        schema_version: 契约版本

    Returns:
        SignalMatrixBundle（已持久化到 db_path）
    """
    # 版本锁定：执行 code 与库中 code_hash 比对，不一致告警（仍用当前 code 重算）
    for f in valid_factors:
        fid = f.get("factor_id")
        rec = factor_codes.get(fid) if fid else None
        if rec is None:
            continue
        code = f.get("code") or rec.get("code", "")
        known_hash = rec.get("code_hash") or _code_hash(rec.get("code", ""))
        if _code_hash(code) != known_hash:
            logger.warning(
                "[L3-SIGNAL] 回填版本锁定: 因子 %s 执行 code 与库中 code_hash 不一致，"
                "用当前 code 重算（重审口径）", fid,
            )
    bundle = build_signal_matrix(
        panel, valid_factors, factor_codes, common_dates,
        forward_days=forward_days, signal_cache=signal_cache,
    )
    code_hashes = {f["factor_id"]: _code_hash(f.get("code", "")) for f in valid_factors}
    params_hashes = {f["factor_id"]: _params_hash(f.get("params", {})) for f in valid_factors}
    persist_signal_matrix(
        bundle, code_hashes, market, end_date, db_path,
        params_hashes=params_hashes,
        factor_status_map=factor_status_map,
        factor_scope_map=factor_scope_map,
        schema_version=schema_version,
    )
    return bundle


def verify_backfill_consistency(
    backfill_bundle: SignalMatrixBundle,
    rolling_bundle: SignalMatrixBundle,
    atol: float = 1e-8,
) -> dict[str, Any]:
    """回填矩阵 vs 存量滚动矩阵重叠区一致性校验（plans/57 §6.8.3 ④）。

    按重叠 (dates, symbols, factor_ids) 切片逐因子比对最大绝对差；
    不一致（max_diff > atol）→ 以回填为准由调用方统一口径（此处仅报告）。

    Args:
        backfill_bundle: 历史回填矩阵（310 日窗）
        rolling_bundle: 存量滚动矩阵（300/500 日窗，重叠区 = 回填尾部）
        atol: 容差（默认 1e-8）

    Returns:
        {"consistent": bool, "max_diff": float, "per_factor": {fid: max_diff},
         "n_overlap_dates": int, "n_overlap_symbols": int}
    """
    d_b, d_r = set(map(str, backfill_bundle.dates)), set(map(str, rolling_bundle.dates))
    overlap_dates = sorted(d_b & d_r)
    overlap_syms = sorted(set(backfill_bundle.symbols) & set(rolling_bundle.symbols))
    per_factor: dict[str, float] = {}
    for fid in backfill_bundle.factor_ids:
        if fid not in rolling_bundle.factor_ids:
            continue
        jb, jr = backfill_bundle.factor_ids.index(fid), rolling_bundle.factor_ids.index(fid)
        sb, sr = backfill_bundle.symbols, rolling_bundle.symbols
        b_idx = {s: i for i, s in enumerate(sb)}
        r_idx = {s: i for i, s in enumerate(sr)}
        db_idx = {str(d): i for i, d in enumerate(backfill_bundle.dates)}
        dr_idx = {str(d): i for i, d in enumerate(rolling_bundle.dates)}
        max_diff = 0.0
        for d in overlap_dates:
            for s in overlap_syms:
                v_b = backfill_bundle.signal_matrix[db_idx[d], b_idx[s], jb]
                v_r = rolling_bundle.signal_matrix[dr_idx[d], r_idx[s], jr]
                if np.isfinite(v_b) and np.isfinite(v_r):
                    max_diff = max(max_diff, abs(float(v_b) - float(v_r)))
        per_factor[fid] = max_diff
    max_diff = max(per_factor.values()) if per_factor else 0.0
    return {
        "consistent": max_diff <= atol,
        "max_diff": max_diff,
        "per_factor": per_factor,
        "n_overlap_dates": len(overlap_dates),
        "n_overlap_symbols": len(overlap_syms),
    }


def incremental_factor_ids(
    factor_ids: Sequence[str],
    factor_code_hashes: dict[str, str],
    market: str,
    end_date: str,
    db_path: str | Path | None = None,
    params_hashes: Optional[dict[str, str]] = None,
) -> tuple[list[str], list[str]]:
    """增量判定（D 层）：返回 (需要全量重算的因子, 已入库可复用的因子)。

    规则（plans/51 A1 修订）: 因子不在 meta 表 / 无 code_hash 或 code_hash 与库中
    不一致 → 全量重算；params_hashes 非 None 时同时校验 params_hash——不一致同样
    重算（同 factor_id 改参数后不得静默复用旧信号，与 SignalCache key 含 params
    的口径一致）。params_hashes 为 None 时仅比对 code_hash（向后兼容）。

    Args:
        factor_ids: 待处理因子
        factor_code_hashes: factor_id → code_hash
        market: 市场
        end_date: 数据截止日
        db_path: DuckDB 路径
        params_hashes: factor_id → params_hash（plans/51 A1；None=仅比 code_hash）

    Returns:
        (to_recompute, reusable): 需重算 / 可复用因子 ID 列表
    """
    if not factor_ids:
        return [], []
    db_path = db_path or _default_db_path()
    stored: dict[str, tuple[str, str]] = {}
    try:
        con = _connect(db_path, read_only=True)
        try:
            rows = con.execute(
                f"SELECT factor_id, code_hash, params_hash FROM {_L3_SIGNAL_META_TABLE} "
                f"WHERE market=? AND end_date=? AND factor_id = ANY(select unnest(?::varchar[]))",
                [market, end_date, list(factor_ids)],
            ).fetchall()
        finally:
            con.close()
        stored = {r[0]: ((r[1] or ""), (r[2] or "")) for r in rows}
    except Exception:  # noqa: BLE001
        stored = {}
    to_recompute: list[str] = []
    reusable: list[str] = []
    for fid in factor_ids:
        expected_code = factor_code_hashes.get(fid, "")
        expected_params = (params_hashes or {}).get(fid, "")
        if fid in stored and expected_code:
            s_code, s_params = stored[fid]
            same_code = s_code == expected_code
            same_params = (params_hashes is None) or (
                bool(expected_params) and s_params == expected_params
            )
            if same_code and same_params:
                reusable.append(fid)
                continue
        to_recompute.append(fid)
    return to_recompute, reusable


def _code_hash(code: str) -> str:
    """因子代码 SHA-256（增量判定用，与 factor_catalog.code_hash 口径一致）。"""
    import hashlib

    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


# ─── 增量窗口追加（plans/52 GAP-139）────────────────────


def _classify_reusable(
    factor_ids: Sequence[str],
    market: str,
    end_date: str,
    common_dates: Sequence[Any],
    db_path: str | Path | None = None,
    append_enabled: bool = True,
) -> tuple[list[str], dict[str, int], list[str]]:
    """按前缀一致性细分可复用因子（plans/52）。

    Returns:
        (direct_reuse, append_plan, fallback_ids):
            direct_reuse — 窗口未变（旧窗 = 当前窗口），直接读库复用；
            append_plan — {factor_id: n_old}，前缀一致且有增量日期，走增量窗口追加；
            fallback_ids — 前缀不一致 / 元数据缺失（旧库无 digest），降级全量重算（A2 兜底）。
    """
    if not factor_ids:
        return [], {}, []
    db_path = db_path or _default_db_path()
    meta: dict[str, tuple[int, str]] = {}
    try:
        con = _connect(db_path, read_only=True)
        try:
            rows = con.execute(
                f"SELECT factor_id, n_dates, dates_digest FROM {_L3_SIGNAL_META_TABLE} "
                f"WHERE market=? AND end_date=? AND factor_id = ANY(select unnest(?::varchar[]))",
                [market, end_date, list(factor_ids)],
            ).fetchall()
        finally:
            con.close()
        meta = {r[0]: (int(r[1] or 0), (r[2] or "")) for r in rows}
    except Exception:  # noqa: BLE001 — 查询失败按"前缀未知"降级全量
        meta = {}
    n_total = len(common_dates)
    direct: list[str] = []
    append_plan: dict[str, int] = {}
    fallback: list[str] = []
    for fid in factor_ids:
        m = meta.get(fid)
        if m is None:
            fallback.append(fid)
            continue
        n_old, old_digest = m
        if n_old <= 0 or n_old > n_total or not old_digest:
            # 元数据异常 / digest 缺失（旧库未写）→ 前缀未知 → 全量（安全兼容）
            fallback.append(fid)
            continue
        if old_digest != _dates_digest(common_dates[:n_old]):
            fallback.append(fid)  # 前缀不一致（历史修订/窗口变化）→ 全量
        elif n_total > n_old and append_enabled:
            append_plan[fid] = n_old
        else:
            direct.append(fid)
    return direct, append_plan, fallback


def _verify_append(
    panel: dict[str, pd.DataFrame],
    fdata: dict[str, Any],
    executor: Any,
    common_dates: Sequence[Any],
    stocks: Sequence[str],
    full_signal: np.ndarray,
    n_old: int,
) -> bool:
    """抽样对照验证（plans/52 零漂移兜底）。

    抽 2 个品种做全量执行，在新增日期段与增量拼接结果逐位比对。因子代码对同一
    因子所有品种共享窗口语义 → 抽样验证通过即代表回退段覆盖充分，全品种增量可信；
    任一不一致 → 调用方降级该因子全量重算。
    """
    n_total = len(common_dates)
    if n_old >= n_total:
        return True
    sample_syms = [s for s in panel if s in stocks][:2]
    if not sample_syms:
        return True  # 无可验证品种 → 保守放行（无增量信号可验证）
    new_dates = list(common_dates[n_old:])
    stock_idx = {s: i for i, s in enumerate(stocks)}
    try:
        for sym in sample_syms:
            df = panel.get(sym)
            if df is None or df.empty:
                continue
            sig = executor.execute(df, fdata.get("params", {}))
            sig_arr = np.asarray(sig, dtype=np.float64)
            loc = df.index.get_indexer(new_dates)
            col = stock_idx[sym]
            for t, _d in enumerate(new_dates):
                i = int(loc[t])
                if i < 0 or i >= len(sig_arr):
                    continue
                exp = float(sig_arr[i])
                got = float(full_signal[n_old + t, col])
                if not (np.isnan(exp) and np.isnan(got)):
                    if np.isnan(exp) or np.isnan(got) or not np.isclose(exp, got, rtol=1e-10, atol=1e-12):
                        return False
    except Exception:  # noqa: BLE001 — 验证执行异常 → 保守降级全量
        return False
    return True


def _append_window_signals(
    panel: dict[str, pd.DataFrame],
    append_factors: list[dict[str, Any]],
    factor_codes: dict[str, dict[str, Any]],
    common_dates: Sequence[Any],
    append_plan: dict[str, int],
    loaded: Optional[SignalMatrixBundle],
    signal_cache: Any = None,
    recall: int = _W_RECALL,
) -> tuple[dict[str, np.ndarray], list[str]]:
    """增量窗口追加执行（plans/52 GAP-139）。

    对每个可增量因子：旧窗口信号（前 n_old 行）取自已加载的 ``loaded``，新段信号
    用"旧窗口尾部回退 + 新增交易日"切片执行后截取——滚动窗口算子在增量段前缀的
    历史回溯由回退段提供，与全量执行逐位一致。抽样对照验证不过 → 该因子并入
    ``verify_fail`` 由调用方降级全量重算（零漂移）。

    Returns:
        (append_signals, verify_fail): {factor_id: (n_total, n_stocks) 完整新窗信号矩阵},
        验证失败需全量重算的因子 ID 列表
    """
    from .factor_program import FactorExecutor

    n_total = len(common_dates)
    if loaded is None or not append_factors:
        return {}, [f["factor_id"] for f in append_factors]
    stocks = list(loaded.symbols)
    stock_idx = {s: i for i, s in enumerate(stocks)}
    fid_loaded = {fid: j for j, fid in enumerate(loaded.factor_ids)}
    exec_start_by_fid: dict[str, int] = {}
    for f in append_factors:
        fid = f["factor_id"]
        n_old = append_plan.get(fid, 0)
        exec_start_by_fid[fid] = max(0, n_old - recall)

    append_signals: dict[str, np.ndarray] = {}
    verify_fail: list[str] = []
    for f in append_factors:
        fid = f["factor_id"]
        n_old = append_plan.get(fid, 0)
        if n_old <= 0 or n_old >= n_total:
            verify_fail.append(fid)
            continue
        fdata = factor_codes.get(fid)
        if not fdata or fid not in fid_loaded:
            verify_fail.append(fid)
            continue
        try:
            executor = FactorExecutor(fdata, signal_cache=signal_cache)
        except Exception:  # noqa: BLE001 — 编译失败留 NaN（与现值一致）
            verify_fail.append(fid)
            continue
        exec_start = exec_start_by_fid[fid]
        exec_dates = list(common_dates[exec_start:])
        full: np.ndarray = np.full((n_total, len(stocks)), np.nan, dtype=np.float64)
        # 旧窗口段（前 n_old 行）从 loaded 取（防御 loaded 行数 < n_old 时截断）
        old_col = loaded.signal_matrix[:, :, fid_loaded[fid]]
        full[: min(n_old, old_col.shape[0])] = old_col[:n_old]
        # 新段执行：回退切片数据执行 → 截取新增日期输出
        for sym, df in panel.items():
            col = stock_idx.get(sym)
            if col is None or df is None or df.empty:
                continue
            try:
                df_exec = df.loc[df.index.isin(exec_dates)]
                if df_exec.empty:
                    continue
                sig = executor.execute(df_exec, fdata.get("params", {}))
                sig_arr = np.asarray(sig, dtype=np.float64)
                loc = df_exec.index.get_indexer(common_dates)
                for t in range(n_old, n_total):
                    i = int(loc[t])
                    if 0 <= i < len(sig_arr):
                        full[t, col] = sig_arr[i]
            except Exception:  # noqa: BLE001 — 单品种执行失败留 NaN（与现值一致）
                continue
        # 抽样对照验证（零漂移兜底）
        if _verify_append(panel, fdata, executor, common_dates, stocks, full, n_old):
            append_signals[fid] = full
        else:
            logger.warning(
                "[L3-SIGNAL] 增量窗口追加对照验证失败（factor=%s n_old=%d），降级全量重算",
                fid,
                n_old,
            )
            verify_fail.append(fid)
    return append_signals, verify_fail


def _persist_factor_bundle(
    signal_2d: np.ndarray,
    fid: str,
    symbols: Sequence[str],
    dates: Sequence[Any],
    code_hash: str,
    params_hash: str,
    market: str,
    end_date: str,
    db_path: str | Path | None = None,
    factor_status: str = "pending",
    factor_scope: Optional[dict[str, Any]] = None,
    schema_version: int = 1,
) -> bool:
    """单因子完整窗信号回写（plans/52：增量追加后更新库中整窗 + meta digest）。

    plans/57 契约 v1：factor_status/factor_scope/schema_version 一并落 meta。
    """
    n_dates = signal_2d.shape[0]
    bundle = SignalMatrixBundle(
        signal_matrix=signal_2d.reshape(n_dates, signal_2d.shape[1], 1),
        forward_returns=np.full((n_dates, signal_2d.shape[1]), np.nan, dtype=np.float64),
        dates=list(dates),
        symbols=list(symbols),
        factor_ids=[fid],
    )
    return persist_signal_matrix(
        bundle,
        {fid: code_hash},
        market,
        end_date,
        db_path,
        params_hashes={fid: params_hash},
        factor_status_map={fid: factor_status},
        factor_scope_map={fid: factor_scope} if factor_scope is not None else None,
        schema_version=schema_version,
    )


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

    对已入库且 (code_hash, params_hash) 均一致的因子从 DuckDB 读取（不重算）；
    仅对新增/变更/前缀不符因子全量重算（plans/51 A1：params 纳入增量判定；
    A2：行数与面板不一致/读取失败降级重算不静默错位）；前缀一致且有增量日期的
    因子走**增量窗口追加**（plans/52：仅重算新增交易日 + 窗口回退段，抽样对照
    验证不过自动全量，零漂移），合并为完整 3D 信号矩阵后回写。语义与
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
    params_hashes = {f["factor_id"]: _params_hash(f.get("params", {})) for f in valid_factors}
    to_recompute, reusable = incremental_factor_ids(
        factor_ids, code_hashes, market, end_date, db_path, params_hashes=params_hashes
    )

    # plans/52：增量窗口追加开关（读配置，缺失默认开启；抽样对照验证兜底零漂移）
    append_enabled = True
    try:
        from fts.config.settings import get_config as _cfg_append

        append_enabled = bool(getattr(_cfg_append(), "l3_signal_store_append_window", True))
    except Exception:  # noqa: BLE001 — 配置读取失败默认开启
        append_enabled = True

    # 可复用因子细分：直读 / 增量窗口追加 / 降级全量（plans/52 前缀判定）
    direct_reuse, append_plan, fallback = _classify_reusable(
        reusable, market, end_date, common_dates, db_path, append_enabled=append_enabled
    )
    if fallback:
        logger.info(
            "[L3-SIGNAL] %d 个可复用因子前缀不一致/元数据缺失，降级全量重算（plans/52 前缀判定）",
            len(fallback),
        )
    recompute_ids = set(to_recompute) | set(fallback)

    # 读库：direct 因子（窗口未变，行数应 == n_dates）+ append 因子（旧窗，行数 = n_old）分开读
    loaded: Optional[SignalMatrixBundle] = None
    if direct_reuse:
        loaded = load_signal_matrix(direct_reuse, market, end_date, db_path, common_dates=common_dates)
        if loaded is not None and loaded.signal_matrix.shape[0] != n_dates:
            logger.warning(
                "[L3-SIGNAL] 信号库行数 %d != 当前面板 %d 行（market=%s end_date=%s），"
                "降级重算 %d 个因子",
                loaded.signal_matrix.shape[0], n_dates, market, end_date, len(direct_reuse),
            )
            recompute_ids |= set(direct_reuse)
            loaded = None
        elif loaded is None:
            logger.warning(
                "[L3-SIGNAL] 信号库读取失败（market=%s end_date=%s），降级重算 %d 个可复用因子",
                market, end_date, len(direct_reuse),
            )
            recompute_ids |= set(direct_reuse)
    loaded_append: Optional[SignalMatrixBundle] = None
    if append_plan:
        loaded_append = load_signal_matrix(
            list(append_plan.keys()), market, end_date, db_path, common_dates=common_dates
        )
        if loaded_append is None:
            logger.warning(
                "[L3-SIGNAL] 信号库读取失败（增量追加因子，market=%s end_date=%s），降级重算 %d 个",
                market, end_date, len(append_plan),
            )
            recompute_ids |= set(append_plan.keys())

    # 增量窗口追加执行（仅重算新增交易日 + 回退段；对照验证不过 → 并入全量）
    append_signals: dict[str, np.ndarray] = {}
    if append_plan and loaded_append is not None:
        append_factors = [f for f in valid_factors if f["factor_id"] in append_plan]
        append_signals, append_fail = _append_window_signals(
            panel, append_factors, factor_codes, common_dates, append_plan,
            loaded_append, signal_cache=signal_cache,
        )
        if append_fail:
            recompute_ids |= set(append_fail)
        if append_signals:
            logger.info(
                "[L3-SIGNAL] 增量窗口追加完成: %d 个因子仅重算新增交易日（plans/52）",
                len(append_signals),
            )

    # 全量重算（新/变更/前缀不符/验证失败）
    bundle_new: Optional[SignalMatrixBundle] = None
    recompute_factors = [f for f in valid_factors if f["factor_id"] in recompute_ids]
    if recompute_factors:
        bundle_new = build_signal_matrix(
            panel, recompute_factors, factor_codes, common_dates,
            forward_days=forward_days, signal_cache=signal_cache,
        )

    # 合并
    mat: np.ndarray = np.full((n_dates, len(stocks), n_factors), np.nan, dtype=np.float64)
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
    # 增量追加信号列序对齐（append_signals 列序 = loaded_append.symbols）
    if append_signals and loaded_append is not None:
        col_map = {s: i for i, s in enumerate(loaded_append.symbols)}
        for fid, sig_full in append_signals.items():
            if fid not in fid_pos:
                continue
            for ci, sym in enumerate(stocks):
                li = col_map.get(sym)
                if li is not None:
                    mat[:, ci, fid_pos[fid]] = sig_full[:, li]

    # 前向收益（始终按当前 panel 重建，保证口径一致）
    fwd: np.ndarray = np.full((n_dates, len(stocks)), np.nan, dtype=np.float64)
    for i, sym in enumerate(stocks):
        df = panel.get(sym)
        if df is None or df.empty or "close" not in df.columns:
            continue
        closes = df["close"].to_numpy(dtype=np.float64)
        f: np.ndarray = np.full(len(closes), np.nan, dtype=np.float64)
        f[:-forward_days] = (closes[forward_days:] - closes[:-forward_days]) / np.maximum(closes[:-forward_days], 1e-10)
        fwd[:, i] = align_signal_to_dates(f, df, common_dates)

    # 回写（短写连接 + filelock，失败不阻断）：全量重算因子 + 增量追加因子（整窗 + meta digest）
    if bundle_new is not None and bundle_new.factor_ids:
        persist_signal_matrix(
            bundle_new, code_hashes, market, end_date, db_path, params_hashes=params_hashes
        )
    if append_signals and loaded_append is not None:
        for fid, sig_full in append_signals.items():
            _persist_factor_bundle(
                sig_full, fid, loaded_append.symbols, common_dates,
                code_hashes.get(fid, ""), params_hashes.get(fid, ""),
                market, end_date, db_path,
            )

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
    "load_signal_meta",
    "backfill_signal_matrix",
    "verify_backfill_consistency",
    "incremental_factor_ids",
    "load_or_build_signal_matrix",
]
