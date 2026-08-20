"""
tests/factor_engine/test_scope_contract_v2.py — 信号契约 v2 扩展测试（P2 方案）

覆盖：factor_scope 支持 kind=symbol（品种级）+ guard_passed 证据 + schema_version=2
幂等写入与读回；v1 兼容（subchain_scope/subchain_specific 仍可直通）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.l3_signal_service import load_signal_meta, persist_signal_matrix


def _mk_panel(n_days: int = 30, symbols: tuple[str, ...] = ("RB", "CU")):
    idx = pd.date_range("2025-06-01", periods=n_days, freq="B")
    panel = {}
    for i, s in enumerate(symbols):
        rng = np.random.default_rng(i)
        close = 100 + np.cumsum(rng.normal(0, 1, n_days))
        panel[s] = pd.DataFrame({"close": close, "volume": 1000}, index=idx)
    return panel


def _bundle(n_days: int = 30):
    from fts.factor_engine.l3_signal_service import (
        build_signal_matrix,
    )

    panel = _mk_panel(n_days=n_days)
    factors = [
        {"factor_id": "f1", "name": "a", "code": "x", "params": {"window": 2}},
        {"factor_id": "f2", "name": "b", "code": "y", "params": {"window": 4}},
    ]
    factor_codes = {f["factor_id"]: f for f in factors}
    dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
    return build_signal_matrix(panel, factors, factor_codes, dates)


def test_scope_v2_symbol_roundtrip(tmp_path):
    """契约 v2：factor_scope 支持 kind=symbol + evidence；schema_version=2 幂等读写。"""
    bundle = _bundle()
    db = tmp_path / "l3_signal.duckdb"
    scope_map = {
        "f1": {"kind": "symbol", "symbols": ["RB0"], "evidence": {"guard_passed": True}},
        "f2": {"subchain_scope": "all", "subchain_specific": []},
    }
    ok = persist_signal_matrix(
        bundle,
        {"f1": "h1", "f2": "h2"},
        "futures",
        "2026-08-16",
        db_path=str(db),
        factor_scope_map=scope_map,
        schema_version=2,
    )
    assert ok

    meta = load_signal_meta(["f1", "f2"], "futures", "2026-08-16", db_path=str(db))
    assert meta is not None and "f1" in meta
    scope1 = meta["f1"].get("factor_scope") or {}
    assert scope1.get("kind") == "symbol"
    assert scope1.get("symbols") == ["RB0"]
    assert (scope1.get("evidence") or {}).get("guard_passed") is True
    assert meta["f1"].get("schema_version") == 2
    # v1 直通兼容
    scope2 = meta["f2"].get("factor_scope") or {}
    assert scope2.get("subchain_scope") == "all"


def test_scope_v1_default_when_no_map(tmp_path):
    """无 factor_scope_map → 落通用 v1 语义（all/[]）。"""
    bundle = _bundle()
    db = tmp_path / "l3_signal.duckdb"
    persist_signal_matrix(
        bundle,
        {"f1": "h1", "f2": "h2"},
        "futures",
        "2026-08-16",
        db_path=str(db),
        schema_version=2,
    )
    meta = load_signal_meta(["f1"], "futures", "2026-08-16", db_path=str(db))
    scope = (meta or {}).get("f1", {}).get("factor_scope") or {}
    assert scope.get("subchain_scope") == "all"
    assert scope.get("subchain_specific") == []
