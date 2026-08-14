"""
tests/scripts/test_gap_threshold_calibration.py — G4/G11 阈值校准脚本口径测试。

覆盖:
    - 日换手反推口径：库内 turnover_monthly = turnover_daily × 42（G11 信号翻转率口径），
      反推日换手须除以 42（回归防护：曾误用 /21 导致日换手高估 2 倍，2026-08-14 修复）
    - _calibrate_catalog / _calibrate_evaluations 分布与候选阈值通过率
"""

from __future__ import annotations

import duckdb
import pytest

from scripts.gap_threshold_calibration import (
    TURNOVER_DAILY_TO_MONTHLY,
    _calibrate_catalog,
    _calibrate_evaluations,
)


def _mk_catalog_conn(rows: list[tuple]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE factor_catalog (
            factor_id VARCHAR PRIMARY KEY, name VARCHAR, code TEXT,
            params JSON, signature JSON, economic_logic JSON, source VARCHAR,
            parent_id VARCHAR, generation INTEGER, trace_id VARCHAR,
            sharpe DOUBLE, ic DOUBLE, icir DOUBLE, max_drawdown DOUBLE,
            turnover_monthly DOUBLE, decay_6m DOUBLE, status VARCHAR,
            status_updated_at TIMESTAMP, consecutive_ic_negative_months INTEGER,
            consecutive_sharpe_drop_months INTEGER, last_incremental_eval_at TIMESTAMP,
            decay_rate_3m DOUBLE, decay_rate_6m DOUBLE, market VARCHAR,
            family VARCHAR, created_at TIMESTAMP, updated_at TIMESTAMP,
            is_elite BOOLEAN, metadata JSON, style_tags JSON
        )
        """
    )
    for (fid, tm, icir, ic, is_elite) in rows:
        conn.execute(
            "INSERT INTO factor_catalog (factor_id, name, code, market, family, turnover_monthly,"
            " icir, ic, decay_6m, sharpe, status, is_elite)"
            " VALUES (?, ?, 'x', 'futures', 'other', ?, ?, ?, 0.05, 1.0, 'active', ?)",
            [fid, fid, tm, icir, ic, is_elite],
        )
    return conn


class TestDailyTurnoverScale:
    def test_conversion_constant_is_42(self) -> None:
        """换算系数必须是 42（G11：日换手 = mean(|Δsign|)/2，月度 = 日 × 42）。"""
        assert TURNOVER_DAILY_TO_MONTHLY == 42

    def test_catalog_daily_turnover_uses_42(self) -> None:
        """turnover_monthly=42 → 日换手 P50=1.0（而非 21 口径的 2.0）。"""
        conn = _mk_catalog_conn([("f1", 42.0, 0.5, 0.1, False)])
        out = _calibrate_catalog(conn)
        assert out["daily_turnover"]["percentiles"]["p50"] == pytest.approx(1.0)

    def test_catalog_elite_daily_turnover_uses_42(self) -> None:
        """elite 因子 turnover_monthly=84 → elite 日换手 P50=2.0。"""
        conn = _mk_catalog_conn([("f1", 84.0, 0.5, 0.1, True)])
        out = _calibrate_catalog(conn)
        assert out["daily_turnover_elite"]["percentiles"]["p50"] == pytest.approx(2.0)

    def test_evaluations_daily_turnover_uses_42(self) -> None:
        """factor_evaluations.level_1_turnover=21 → 日换手 P50=0.5。"""
        conn = duckdb.connect()
        conn.execute(
            "CREATE TABLE factor_evaluations (factor_id VARCHAR, level_1_ic DOUBLE,"
            " level_1_icir DOUBLE, level_1_turnover DOUBLE, overall_passed BOOLEAN)"
        )
        conn.execute(
            "INSERT INTO factor_evaluations VALUES ('f1', 0.1, 0.5, 21.0, TRUE)"
        )
        out = _calibrate_evaluations(conn)
        assert out["daily_turnover"]["percentiles"]["p50"] == pytest.approx(0.5)


class TestCandidatePassRate:
    def test_turnover_candidate_le_threshold(self) -> None:
        """日换手 ≤0.20 通过率：0.1/0.4 两个因子 → 1/2 = 50%。"""
        conn = _mk_catalog_conn(
            [
                ("f1", 0.1 * TURNOVER_DAILY_TO_MONTHLY, 0.5, 0.1, False),
                ("f2", 0.4 * TURNOVER_DAILY_TO_MONTHLY, 0.5, 0.1, False),
            ]
        )
        out = _calibrate_catalog(conn)
        assert out["turnover_candidates"]["0.2"]["passed"] == 1
        assert out["turnover_candidates"]["0.2"]["rate"] == pytest.approx(0.5)

    def test_icir_candidate_ge_threshold(self) -> None:
        """|ICIR| ≥0.30 通过率：0.2/0.5 两个因子 → 1/2 = 50%。"""
        conn = _mk_catalog_conn(
            [("f1", 1.0, 0.2, 0.1, False), ("f2", 1.0, 0.5, 0.1, False)]
        )
        out = _calibrate_catalog(conn)
        assert out["icir_candidates"]["0.3"]["passed"] == 1
        assert out["icir_candidates"]["0.3"]["rate"] == pytest.approx(0.5)

    def test_candidate_ignores_zero_turnover(self) -> None:
        """turnover_monthly=0（缺省）因子不污染非零分布；le 口径下 0 计为通过。"""
        conn = _mk_catalog_conn(
            [("f1", 0.0, 0.5, 0.1, False), ("f2", 0.15 * 42.0, 0.5, 0.1, False)]
        )
        out = _calibrate_catalog(conn)
        assert out["daily_turnover"]["n"] == 2
        assert out["turnover_candidates"]["0.2"]["rate"] == pytest.approx(1.0)
