"""tests/factor_engine/test_l3_signal_service.py — L3 信号矩阵服务测试（plans/40 B/D 层）。

覆盖:
    - align_signal_to_dates 与逐日 list.index 等价（O(n²) 修复零漂移）
    - build_signal_matrix 与逐品种逐因子直接执行逐值一致
    - duckdb_corr_matrix 与 numpy 参考一致（含 NaN / 有效点阈值）
    - persist/load round-trip 一致
    - incremental_factor_ids 增量判定（新因子 / code_hash 变化 → 重算）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.l3_signal_service import (
    align_signal_to_dates,
    build_signal_matrix,
    duckdb_corr_matrix,
    incremental_factor_ids,
    load_or_build_signal_matrix,
    load_signal_matrix,
    persist_signal_matrix,
)


# ─── 辅助 ────────────────────────────────────────────────


def _mk_factor(fid: str, window: int = 5, seed: int = 0) -> dict:
    """构造简单因子程序（close 的 window 日收益率归一化；ndarray 环境兼容）。"""
    return {
        "factor_id": fid,
        "name": fid,
        "code": (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = np.asarray(data['close'], dtype=float)\n"
            "    w = %d\n"
            "    s = np.zeros_like(close)\n"
            "    if len(close) > w:\n"
            "        s[w:] = (close[w:] - close[:-w]) / np.maximum(np.abs(close[:-w]), 1e-10)\n"
            "    a = np.asarray(s, dtype=float)\n"
            "    a[np.isnan(a)] = 0.0\n"
            "    return a\n" % window
        ),
        "params": {"seed": seed},
    }


def _mk_panel(n_days: int = 60, symbols=("RB", "CU"), seed: int = 7) -> dict[str, pd.DataFrame]:
    """构造合成 OHLCV 面板（品种长度错开，制造缺口）。"""
    rng = np.random.default_rng(seed)
    panel: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols):
        n = n_days - i * 5  # 错位制造缺口
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        df = pd.DataFrame(
            {
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": rng.integers(1000, 5000, n),
                "amount": rng.integers(1_000_000, 5_000_000, n),
            },
            index=dates,
        )
        panel[sym] = df
    return panel


def _reference_align(sig: np.ndarray, df: pd.DataFrame, common_dates: list) -> np.ndarray:
    """逐日 list.index 参考实现（旧路径语义）。"""
    out = np.full(len(common_dates), np.nan)
    for t, d in enumerate(common_dates):
        if d in df.index:
            idx = list(df.index).index(d)
            out[t] = float(sig[idx]) if idx < len(sig) else np.nan
    return out


# ─── A 层：对齐等价性 ────────────────────────────────────


class TestAlignSignalToDates:
    def test_equivalence_with_reference(self):
        panel = _mk_panel(n_days=40, symbols=("RB", "CU"))
        common_dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        for sym, df in panel.items():
            sig = df["close"].pct_change(3).to_numpy(dtype=float)
            new = align_signal_to_dates(sig, df, common_dates)
            ref = _reference_align(sig, df, common_dates)
            assert np.allclose(new, ref, equal_nan=True)

    def test_empty_df(self):
        df = pd.DataFrame({"close": []})
        out = align_signal_to_dates(np.array([]), df, ["2024-01-01"])
        assert np.isnan(out).all()

    def test_short_signal(self):
        # sig 短于 df：越界日期留 NaN
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame({"close": np.arange(10.0)}, index=dates)
        sig = np.array([1.0, 2.0])  # 只有 2 个点
        out = align_signal_to_dates(sig, df, list(dates))
        assert out[0] == 1.0
        assert out[1] == 2.0
        assert np.isnan(out[2:]).all()


# ─── B 层：信号矩阵构建一致性 ────────────────────────────


class TestBuildSignalMatrix:
    def test_matches_per_symbol_execution(self):
        panel = _mk_panel(n_days=40)
        factors = [_mk_factor("f1", window=3), _mk_factor("f2", window=5)]
        factor_codes = {f["factor_id"]: f for f in factors}
        common_dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        bundle = build_signal_matrix(panel, factors, factor_codes, common_dates)

        assert bundle.signal_matrix.shape == (len(common_dates), len(panel), len(factors))
        assert bundle.forward_returns.shape == (len(common_dates), len(panel))
        assert bundle.factor_ids == ["f1", "f2"]
        assert bundle.forward_days == 5

        # 与逐品种逐因子直接执行逐值一致
        from fts.factor_engine.factor_program import FactorExecutor

        stocks = sorted(panel.keys())
        for j, f in enumerate(factors):
            executor = FactorExecutor(f)
            for i, sym in enumerate(stocks):
                sig = executor.execute(panel[sym], f["params"])
                ref = _reference_align(np.asarray(sig, dtype=float), panel[sym], common_dates)
                assert np.allclose(bundle.signal_matrix[:, i, j], ref, equal_nan=True)

    def test_forward_returns_semantics(self):
        panel = _mk_panel(n_days=30, symbols=("RB",))
        factors = [_mk_factor("f1", window=2)]
        factor_codes = {f["factor_id"]: f for f in factors}
        dates = sorted(panel["RB"].index)
        bundle = build_signal_matrix(panel, factors, factor_codes, dates, forward_days=5)
        closes = panel["RB"]["close"].to_numpy(dtype=float)
        fwd = np.full(len(closes), np.nan)
        fwd[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
        assert np.allclose(bundle.forward_returns[:, 0], fwd, equal_nan=True)

    def test_signal_cache_shared(self):
        panel = _mk_panel(n_days=30, symbols=("RB", "CU", "ZN"))
        factors = [_mk_factor("f1", window=2)]
        factor_codes = {f["factor_id"]: f for f in factors}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        from fts.factor_engine.signal_cache import SignalCache

        cache = SignalCache(max_entries=100)
        bundle1 = build_signal_matrix(panel, factors, factor_codes, dates, signal_cache=cache)
        misses = cache.misses
        # 再次构建同一数据 → 全部命中，不新增 miss
        bundle2 = build_signal_matrix(panel, factors, factor_codes, dates, signal_cache=cache)
        assert cache.misses == misses
        assert np.allclose(bundle1.signal_matrix, bundle2.signal_matrix, equal_nan=True)
        assert cache.hits > 0


# ─── B 层：DuckDB 相关性 ────────────────────────────────


class TestDuckDBCorrMatrix:
    def test_matches_numpy_reference(self):
        rng = np.random.default_rng(42)
        mat = rng.normal(0, 1, (100, 4)).astype(np.float64)
        mat[3, 0] = np.nan
        mat[7:12, 2] = np.nan  # 制造缺口
        fids = ["a", "b", "c", "d"]
        got = duckdb_corr_matrix(mat, fids)
        # 与 numpy 参考一致（不触发 SQL 时用 _numpy_corr_matrix 对照）
        from fts.factor_engine.l3_signal_service import _numpy_corr_matrix

        ref = _numpy_corr_matrix(mat, min_valid_points=10)
        assert np.allclose(got, ref, equal_nan=True)

    def test_diagonal_and_threshold(self):
        mat = np.arange(60.0).reshape(30, 2)
        fids = ["a", "b"]
        got = duckdb_corr_matrix(mat, fids, min_valid_points=100)  # 有效点不足 → NaN
        assert got[0, 0] == 1.0
        assert got[1, 1] == 1.0
        assert np.isnan(got[0, 1])

    def test_empty(self):
        got = duckdb_corr_matrix(np.empty((0, 0)), [])
        assert got.shape == (0, 0)


# ─── D 层：持久化 + 增量 ─────────────────────────────────


class TestPersistLoadIncremental:
    def test_roundtrip(self, tmp_path):
        panel = _mk_panel(n_days=30, symbols=("RB", "CU"))
        factors = [_mk_factor("f1", window=2), _mk_factor("f2", window=4)]
        factor_codes = {f["factor_id"]: f for f in factors}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        bundle = build_signal_matrix(panel, factors, factor_codes, dates)
        db = tmp_path / "l3_signal.duckdb"
        hashes = {"f1": "h1", "f2": "h2"}

        ok = persist_signal_matrix(bundle, hashes, "futures", "2026-08-16", db_path=str(db))
        assert ok

        loaded = load_signal_matrix(["f1", "f2"], "futures", "2026-08-16", db_path=str(db))
        assert loaded is not None
        assert loaded.signal_matrix.shape == bundle.signal_matrix.shape
        assert np.allclose(loaded.signal_matrix, bundle.signal_matrix, equal_nan=True)

    def test_incremental_judgment(self, tmp_path):
        panel = _mk_panel(n_days=30, symbols=("RB",))
        factors = [_mk_factor("f1", window=2), _mk_factor("f2", window=4)]
        factor_codes = {f["factor_id"]: f for f in factors}
        dates = sorted(panel["RB"].index)
        bundle = build_signal_matrix(panel, factors, factor_codes, dates)
        db = tmp_path / "l3_signal.duckdb"

        # 初始全量
        to_recomp, reusable = incremental_factor_ids(
            ["f1", "f2"], {"f1": "h1", "f2": "h2"}, "futures", "2026-08-16", db_path=str(db)
        )
        assert set(to_recomp) == {"f1", "f2"}
        assert reusable == []

        persist_signal_matrix(bundle, {"f1": "h1", "f2": "h2"}, "futures", "2026-08-16", db_path=str(db))

        # 同 code_hash → 可复用
        to_recomp, reusable = incremental_factor_ids(
            ["f1", "f2"], {"f1": "h1", "f2": "h2"}, "futures", "2026-08-16", db_path=str(db)
        )
        assert to_recomp == []
        assert set(reusable) == {"f1", "f2"}

        # code_hash 变化（因子被编辑）→ 重算
        to_recomp, reusable = incremental_factor_ids(
            ["f1", "f2"], {"f1": "h1_new", "f2": "h2"}, "futures", "2026-08-16", db_path=str(db)
        )
        assert to_recomp == ["f1"]
        assert reusable == ["f2"]

        # 新因子（未入库）→ 重算
        to_recomp, reusable = incremental_factor_ids(
            ["f3"], {"f3": "h3"}, "futures", "2026-08-16", db_path=str(db)
        )
        assert to_recomp == ["f3"]
        assert reusable == []

    def test_missing_returns_none(self, tmp_path):
        assert load_signal_matrix(["f1"], "futures", "2026-08-16", db_path=str(tmp_path / "none.duckdb")) is None

    # ─── plans/51 A1：params 纳入增量判定 ───────────────────
    def test_params_hash_judgment(self, tmp_path):
        """A1：同 code 改 params → 重算；同 code 同 params → 复用。"""
        from fts.factor_engine.l3_signal_service import _code_hash, _params_hash

        panel = _mk_panel(n_days=30, symbols=("RB",))
        f = _mk_factor("f1", window=2)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(panel["RB"].index)
        db = tmp_path / "l3_signal.duckdb"

        code_hash = _code_hash(f["code"])
        p_hash = _params_hash(f["params"])
        bundle = build_signal_matrix(panel, [f], factor_codes, dates)
        persist_signal_matrix(
            bundle, {"f1": code_hash}, "futures", "2026-08-16",
            db_path=str(db), params_hashes={"f1": p_hash},
        )

        # 同 code 同 params → 复用
        to_recomp, reusable = incremental_factor_ids(
            ["f1"], {"f1": code_hash}, "futures", "2026-08-16", db_path=str(db),
            params_hashes={"f1": p_hash},
        )
        assert to_recomp == []
        assert reusable == ["f1"]

        # 同 code 改 params → 重算（不得静默复用旧参数信号）
        to_recomp, reusable = incremental_factor_ids(
            ["f1"], {"f1": code_hash}, "futures", "2026-08-16", db_path=str(db),
            params_hashes={"f1": _params_hash({"seed": 999})},
        )
        assert to_recomp == ["f1"]
        assert reusable == []

    def test_params_hash_none_backward_compat(self, tmp_path):
        """A1：params_hashes=None 时仅比 code_hash（向后兼容）。"""
        from fts.factor_engine.l3_signal_service import _code_hash, _params_hash

        panel = _mk_panel(n_days=30, symbols=("RB",))
        f = _mk_factor("f1", window=2)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(panel["RB"].index)
        db = tmp_path / "l3_signal.duckdb"
        code_hash = _code_hash(f["code"])
        bundle = build_signal_matrix(panel, [f], factor_codes, dates)
        persist_signal_matrix(
            bundle, {"f1": code_hash}, "futures", "2026-08-16",
            db_path=str(db), params_hashes={"f1": _params_hash(f["params"])},
        )

        # 不传 params_hashes：仅比 code_hash → 复用（与旧调用方行为一致）
        to_recomp, reusable = incremental_factor_ids(
            ["f1"], {"f1": code_hash}, "futures", "2026-08-16", db_path=str(db),
        )
        assert to_recomp == []
        assert reusable == ["f1"]

    # ─── plans/51 A3：load bundle 契约 ─────────────────────
    def test_load_dates_and_fwd_contract(self, tmp_path):
        """A3：load 结果——传 common_dates 回填 dates；forward_returns 全 NaN。"""
        panel = _mk_panel(n_days=30, symbols=("RB",))
        f = _mk_factor("f1", window=2)
        dates = sorted(panel["RB"].index)
        db = tmp_path / "l3_signal.duckdb"
        load_or_build_signal_matrix(
            panel, [f], {f["factor_id"]: f}, dates, "futures", "2026-08-16", db_path=str(db)
        )

        loaded = load_signal_matrix(["f1"], "futures", "2026-08-16", db_path=str(db), common_dates=dates)
        assert loaded is not None
        assert loaded.dates == list(dates)[: loaded.signal_matrix.shape[0]]
        assert np.isnan(loaded.forward_returns).all()

        loaded2 = load_signal_matrix(["f1"], "futures", "2026-08-16", db_path=str(db))
        assert loaded2 is not None
        assert loaded2.dates == []


class TestLoadOrBuildIncremental:
    """D 层：信号矩阵一等公民 + 增量重算。"""

    def test_first_build_matches_full(self, tmp_path):
        panel = _mk_panel(n_days=30, symbols=("RB", "CU"))
        factors = [_mk_factor("f1", window=2), _mk_factor("f2", window=4)]
        factor_codes = {f["factor_id"]: f for f in factors}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        full = build_signal_matrix(panel, factors, factor_codes, dates)
        inc = load_or_build_signal_matrix(
            panel, factors, factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )
        assert inc.signal_matrix.shape == full.signal_matrix.shape
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)
        assert np.allclose(inc.forward_returns, full.forward_returns, equal_nan=True)

    def test_second_build_reuses_store(self, tmp_path):
        panel = _mk_panel(n_days=30, symbols=("RB", "CU"))
        factors = [_mk_factor("f1", window=2), _mk_factor("f2", window=4)]
        factor_codes = {f["factor_id"]: f for f in factors}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        first = load_or_build_signal_matrix(
            panel, factors, factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )
        # 再次构建：全部命中库（增量判定全 reusable），矩阵与首次一致
        second = load_or_build_signal_matrix(
            panel, factors, factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )
        assert np.allclose(first.signal_matrix, second.signal_matrix, equal_nan=True)
        assert np.allclose(first.forward_returns, second.forward_returns, equal_nan=True)
        # meta 中两因子均已入库
        to_recomp, reusable = incremental_factor_ids(
            ["f1", "f2"],
            {f["factor_id"]: __import__("hashlib").sha256((f.get("code", "")).encode()).hexdigest() for f in factors},
            "futures", "2026-08-16", db_path=str(db),
        )
        assert to_recomp == []
        assert set(reusable) == {"f1", "f2"}

    def test_mixed_incremental_only_recomputes_new(self, tmp_path):
        panel = _mk_panel(n_days=30, symbols=("RB", "CU"))
        f1, f2 = _mk_factor("f1", window=2), _mk_factor("f2", window=4)
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        # 先只入库 f1
        load_or_build_signal_matrix(
            panel, [f1], {f1["factor_id"]: f1}, dates, "futures", "2026-08-16", db_path=str(db)
        )
        # 再构建 f1+f2：f1 复用库，f2 全量重算
        factors = [f1, f2]
        factor_codes = {f["factor_id"]: f for f in factors}
        full = build_signal_matrix(panel, factors, factor_codes, dates)
        inc = load_or_build_signal_matrix(
            panel, factors, factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)

    def test_use_store_false_falls_back(self, tmp_path):
        panel = _mk_panel(n_days=30, symbols=("RB",))
        factors = [_mk_factor("f1", window=2)]
        factor_codes = {f["factor_id"]: f for f in factors}
        dates = sorted(panel["RB"].index)
        db = tmp_path / "l3_signal.duckdb"
        inc = load_or_build_signal_matrix(
            panel, factors, factor_codes, dates, "futures", "2026-08-16",
            db_path=str(db), use_store=False,
        )
        full = build_signal_matrix(panel, factors, factor_codes, dates)
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)

    # ─── plans/51 A2：增量合并形状防护 ──────────────────────
    def test_shape_mismatch_recomputes(self, tmp_path):
        """plans/52：窗口推进（同源数据 30→45 日，前缀一致）→ 增量追加，与全量逐位一致。"""
        full_panel = _mk_panel(n_days=100, symbols=("RB", "CU"))
        panel = {s: df.iloc[:30] for s, df in full_panel.items()}
        f = _mk_factor("f1", window=2)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        # 首次构建（30 日窗口入库）
        load_or_build_signal_matrix(
            panel, [f], factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )

        # 数据更新：同 end_date、窗口变长（45 日）→ 前缀一致 → 增量追加（与全量一致）
        panel2 = {s: df.iloc[:45] for s, df in full_panel.items()}
        dates2 = sorted(set.intersection(*[set(df.index) for df in panel2.values()]))
        full = build_signal_matrix(panel2, [f], factor_codes, dates2)
        inc = load_or_build_signal_matrix(
            panel2, [f], factor_codes, dates2, "futures", "2026-08-16", db_path=str(db)
        )
        assert inc.signal_matrix.shape == full.signal_matrix.shape
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)
        assert np.allclose(inc.forward_returns, full.forward_returns, equal_nan=True)

    def test_shape_match_merges_from_store(self, tmp_path):
        """A2：形状一致时直接从库合并（f1 复用库信号）。"""
        panel = _mk_panel(n_days=30, symbols=("RB", "CU"))
        f1, f2 = _mk_factor("f1", window=2), _mk_factor("f2", window=4)
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        load_or_build_signal_matrix(
            panel, [f1], {f1["factor_id"]: f1}, dates, "futures", "2026-08-16", db_path=str(db)
        )
        factors = [f1, f2]
        factor_codes = {f["factor_id"]: f for f in factors}
        full = build_signal_matrix(panel, factors, factor_codes, dates)
        inc = load_or_build_signal_matrix(
            panel, factors, factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)

    # ─── plans/52：增量窗口追加 ─────────────────────────────
    def test_append_window_matches_full(self, tmp_path):
        """plans/52：前缀一致 + 增量日期 → 仅重算新增段，与全量逐位一致。"""
        full_panel = _mk_panel(n_days=100, symbols=("RB", "CU", "ZN"))
        panel = {s: df.iloc[:30] for s, df in full_panel.items()}
        f = _mk_factor("f1", window=3)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        load_or_build_signal_matrix(
            panel, [f], factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )

        # 窗口推进：45 日（前缀 30 日一致 + 增量 15 日）
        panel2 = {s: df.iloc[:45] for s, df in full_panel.items()}
        dates2 = sorted(set.intersection(*[set(df.index) for df in panel2.values()]))
        full = build_signal_matrix(panel2, [f], factor_codes, dates2)
        inc = load_or_build_signal_matrix(
            panel2, [f], factor_codes, dates2, "futures", "2026-08-16", db_path=str(db)
        )
        assert inc.signal_matrix.shape == full.signal_matrix.shape
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)
        assert np.allclose(inc.forward_returns, full.forward_returns, equal_nan=True)

    def test_append_window_rolling_operator(self, tmp_path):
        """plans/52：rolling 窗口算子（window=50）增量追加与全量逐位一致（窗口回退覆盖）。"""
        full_panel = _mk_panel(n_days=200, symbols=("RB", "CU", "ZN"))
        panel = {s: df.iloc[:80] for s, df in full_panel.items()}
        f = _mk_factor("f1", window=50)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        load_or_build_signal_matrix(
            panel, [f], factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )
        panel2 = {s: df.iloc[:100] for s, df in full_panel.items()}
        dates2 = sorted(set.intersection(*[set(df.index) for df in panel2.values()]))
        full = build_signal_matrix(panel2, [f], factor_codes, dates2)
        inc = load_or_build_signal_matrix(
            panel2, [f], factor_codes, dates2, "futures", "2026-08-16", db_path=str(db)
        )
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)

    def test_prefix_mismatch_falls_back_full(self, tmp_path):
        """plans/52：前缀不一致（历史修订）→ 降级全量，结果正确。"""
        panel = _mk_panel(n_days=30, symbols=("RB", "CU", "ZN"))
        f = _mk_factor("f1", window=3)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        load_or_build_signal_matrix(
            panel, [f], factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )

        # 历史修订：同 end_date 但日期序列整体后移（前缀不一致）
        panel2 = _mk_panel(n_days=30, symbols=("RB", "CU", "ZN"))
        for sym, df in panel2.items():
            df.index = pd.date_range("2024-02-01", periods=len(df), freq="D")
        dates2 = sorted(set.intersection(*[set(df.index) for df in panel2.values()]))
        full = build_signal_matrix(panel2, [f], factor_codes, dates2)
        inc = load_or_build_signal_matrix(
            panel2, [f], factor_codes, dates2, "futures", "2026-08-16", db_path=str(db)
        )
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)

    def test_append_verify_fail_falls_back(self, tmp_path, monkeypatch):
        """plans/52：增量对照验证失败 → 该因子降级全量重算（零漂移兜底）。"""
        from fts.factor_engine import l3_signal_service as l3svc

        panel = _mk_panel(n_days=30, symbols=("RB", "CU", "ZN"))
        f = _mk_factor("f1", window=3)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        load_or_build_signal_matrix(
            panel, [f], factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )
        monkeypatch.setattr(l3svc, "_verify_append", lambda *a, **k: False)

        panel2 = _mk_panel(n_days=45, symbols=("RB", "CU", "ZN"))
        dates2 = sorted(set.intersection(*[set(df.index) for df in panel2.values()]))
        full = build_signal_matrix(panel2, [f], factor_codes, dates2)
        inc = load_or_build_signal_matrix(
            panel2, [f], factor_codes, dates2, "futures", "2026-08-16", db_path=str(db)
        )
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)


# ═══════════════════════════════════════════════════════════════
# plans/57 信号契约 v1（schema_version / factor_status / factor_scope）
# ═══════════════════════════════════════════════════════════════


class TestSignalContractV1:
    def test_meta_columns_roundtrip(self, tmp_path):
        """plans/57：meta 三列（schema_version/factor_status/factor_scope）写读一致。"""
        from fts.factor_engine.l3_signal_service import load_signal_meta

        panel = _mk_panel(n_days=30, symbols=("RB",))
        f = _mk_factor("f1", window=2)
        dates = sorted(panel["RB"].index)
        db = tmp_path / "l3_signal.duckdb"
        bundle = build_signal_matrix(panel, [f], {f["factor_id"]: f}, dates)
        persist_signal_matrix(
            bundle,
            {"f1": "h1"},
            "futures",
            "2026-08-16",
            db_path=str(db),
            params_hashes={"f1": "p1"},
            factor_status_map={"f1": "active"},
            factor_scope_map={"f1": {"subchain_scope": ["聚酯链"], "subchain_specific": ["TA0"]}},
            schema_version=1,
        )
        meta = load_signal_meta(["f1"], "futures", "2026-08-16", db_path=str(db))
        assert meta["f1"]["factor_status"] == "active"
        assert meta["f1"]["schema_version"] == 1
        assert meta["f1"]["params_hash"] == "p1"
        assert meta["f1"]["factor_scope"]["subchain_scope"] == ["聚酯链"]
        assert meta["f1"]["factor_scope"]["subchain_specific"] == ["TA0"]

    def test_meta_defaults(self, tmp_path):
        """plans/57：未传 status/scope → 默认 pending / 通用范围 {all, []}。"""
        from fts.factor_engine.l3_signal_service import load_signal_meta

        panel = _mk_panel(n_days=20, symbols=("CU",))
        f = _mk_factor("f1", window=2)
        dates = sorted(panel["CU"].index)
        db = tmp_path / "l3_signal.duckdb"
        bundle = build_signal_matrix(panel, [f], {f["factor_id"]: f}, dates)
        persist_signal_matrix(bundle, {"f1": "h1"}, "futures", "2026-08-16", db_path=str(db))
        meta = load_signal_meta(["f1"], "futures", "2026-08-16", db_path=str(db))
        assert meta["f1"]["factor_status"] == "pending"
        assert meta["f1"]["schema_version"] == 1
        assert meta["f1"]["factor_scope"] == {"subchain_scope": "all", "subchain_specific": []}

    def test_load_signal_meta_missing(self, tmp_path):
        """plans/57：无记录 → {}（RD 侧判定信号缺失 → 降级）。"""
        from fts.factor_engine.l3_signal_service import load_signal_meta

        db = tmp_path / "nope.duckdb"
        assert load_signal_meta(["f1"], "futures", "2026-08-16", db_path=str(db)) == {}


class TestBackfillSignalMatrix:
    def test_backfill_persists_with_meta(self, tmp_path):
        """plans/57 §6.5：历史回填入口构建 + 落库 + meta 状态传播。"""
        from fts.factor_engine.l3_signal_service import (
            _code_hash,
            backfill_signal_matrix,
            load_signal_meta,
        )

        panel = _mk_panel(n_days=40, symbols=("RB", "CU"))
        f = _mk_factor("f1", window=3)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "backfill.duckdb"
        bundle = backfill_signal_matrix(
            panel, [f], factor_codes, dates, "futures", "2026-08-16",
            db_path=str(db), factor_status_map={"f1": "shadow"},
        )
        assert bundle.factor_ids == ["f1"]
        meta = load_signal_meta(["f1"], "futures", "2026-08-16", db_path=str(db))
        assert meta["f1"]["factor_status"] == "shadow"
        # 版本锁定：执行 code 与库中 code_hash 一致 → 无告警且回填成功
        assert meta["f1"]["code_hash"] == _code_hash(f["code"])

    def test_backfill_isolated_workspace(self, tmp_path):
        """plans/57 §6.8.3：回填工作区与生产库隔离（不同 db_path 互不污染）。"""
        from fts.factor_engine.l3_signal_service import backfill_signal_matrix, load_signal_meta

        panel = _mk_panel(n_days=30, symbols=("RB",))
        f = _mk_factor("f1", window=2)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(panel["RB"].index)
        prod = tmp_path / "prod.duckdb"
        work = tmp_path / "workspace.duckdb"
        backfill_signal_matrix(
            panel, [f], factor_codes, dates, "futures", "2026-08-16",
            db_path=str(work), factor_status_map={"f1": "active"},
        )
        # 生产库无记录（隔离生效）
        assert load_signal_meta(["f1"], "futures", "2026-08-16", db_path=str(prod)) == {}
        assert load_signal_meta(["f1"], "futures", "2026-08-16", db_path=str(work))["f1"]["factor_status"] == "active"


class TestVerifyBackfillConsistency:
    def _bundles(self):
        """直接构造含有效信号值的 bundle（不依赖因子执行环境）。"""
        from fts.factor_engine.l3_signal_service import SignalMatrixBundle

        dates = pd.date_range("2024-01-01", periods=20, freq="D")
        mat = np.random.default_rng(0).normal(size=(20, 2, 1))
        return SignalMatrixBundle(
            signal_matrix=mat,
            forward_returns=np.full((20, 2), np.nan),
            dates=list(dates),
            symbols=["RB", "CU"],
            factor_ids=["f1"],
        )

    def test_consistent_overlap(self):
        """plans/57 §6.8.3 ④：同源数据重叠区 max_diff ≤ 1e-8 → consistent。"""
        from fts.factor_engine.l3_signal_service import verify_backfill_consistency

        b1 = self._bundles()
        res = verify_backfill_consistency(b1, b1)
        assert res["consistent"] is True
        assert res["max_diff"] == 0.0

    def test_detect_drift(self):
        """plans/57 §6.8.3 ④：回填/滚动矩阵被改动 → 不一致告警（调用方统一口径）。"""
        from fts.factor_engine.l3_signal_service import verify_backfill_consistency

        b1 = self._bundles()
        b2 = self._bundles()
        b2.signal_matrix[-1, 0, 0] += 0.5  # 注入漂移
        res = verify_backfill_consistency(b1, b2)
        assert res["consistent"] is False
        assert res["max_diff"] > 1e-8
        assert res["n_overlap_dates"] > 0


class TestLoadOrBuildIncrementalLegacy:
    def test_legacy_meta_without_digest(self, tmp_path):
        """plans/52：meta 无 dates_digest（旧库）→ 前缀未知 → 降级全量，结果正确。"""
        import duckdb

        panel = _mk_panel(n_days=30, symbols=("RB", "CU", "ZN"))
        f = _mk_factor("f1", window=3)
        factor_codes = {f["factor_id"]: f}
        dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        db = tmp_path / "l3_signal.duckdb"

        load_or_build_signal_matrix(
            panel, [f], factor_codes, dates, "futures", "2026-08-16", db_path=str(db)
        )
        # 模拟旧库：清空 dates_digest
        con = duckdb.connect(str(db))
        try:
            con.execute("UPDATE l3_signal_meta SET dates_digest = ''")
        finally:
            con.close()

        panel2 = _mk_panel(n_days=45, symbols=("RB", "CU", "ZN"))
        dates2 = sorted(set.intersection(*[set(df.index) for df in panel2.values()]))
        full = build_signal_matrix(panel2, [f], factor_codes, dates2)
        inc = load_or_build_signal_matrix(
            panel2, [f], factor_codes, dates2, "futures", "2026-08-16", db_path=str(db)
        )
        assert np.allclose(inc.signal_matrix, full.signal_matrix, equal_nan=True)
