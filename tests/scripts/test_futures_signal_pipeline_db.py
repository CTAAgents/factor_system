"""tests/scripts/test_futures_signal_pipeline_db.py — 期货信号管道 DuckDB 因子资产库读取测试（GAP-097）。

覆盖:
  - load_futures_elite_factors_from_db: 基础加载（market/is_elite/status 过滤 + 字段构造 +
    metadata.evaluation 复用 + metadata 缺失时顶层评估列构造 + code 空跳过 + IC 阈值过滤）
  - 两层去重（代码哈希 + 回测结果 stat）
  - DB 空库/文件缺失 → 返回 []（触发 JSON 快照回退）
  - _load_signal_factors: DuckDB 优先 + JSON 降级回退
"""

import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from futures_signal_pipeline import (  # noqa: E402
    _load_signal_factors,
    load_futures_elite_factors_from_db,
)


# ─── Helpers ────────────────────────────────────────────────────────────


def _seed_db(db_path: Path, factors: list[dict]) -> None:
    """写入测试因子到隔离 DuckDB（FactorRepository 不存在时自动建表）。"""
    from fts.factor_engine.factor_db.repository import FactorRepository

    with FactorRepository(db_path=db_path, market="futures") as repo:
        for f in factors:
            repo.create_factor(f)


def _factor(
    name: str,
    code: str,
    *,
    ic: float,
    sharpe: float,
    t_stat: float = 1.0,
    market: str = "futures",
    status: str = "active",
    is_elite: bool = True,
    with_eval: bool = True,
) -> dict:
    """构造最小因子记录（metadata.evaluation 与 JSON 快照同构）。"""
    data: dict = {
        "factor_id": f"fct_{name}",
        "name": name,
        "code": code,
        "params": {"window": 5},
        "market": market,
        "status": status,
        "is_elite": is_elite,
        "ic": ic,
        "sharpe": sharpe,
        "icir": ic / 2.0,
    }
    if with_eval:
        data["metadata"] = {
            "evaluation": {
                "factor_id": f"fct_{name}",
                "level_1_backtest": {
                    "ic": ic,
                    "icir": ic / 2.0,
                    "sharpe": sharpe,
                    "t_stat": t_stat,
                    "turnover_monthly": 0.0,
                    "max_drawdown": 0.05,
                },
            }
        }
    return data


def _simple_code(name: str) -> str:
    return f"def factor_program(data, params):\n    import numpy as np\n    c = data['close'].values\n    return np.tanh(np.diff(c, prepend=c[0]) * {hash(name) % 100 + 1})"


# ─── load_futures_elite_factors_from_db ─────────────────────────────────


def test_load_from_db_basic(tmp_path):
    """基础加载：market=active+elite 过滤、字段构造、metadata.evaluation 复用。"""
    db = tmp_path / "test.duckdb"
    _seed_db(db, [
        _factor("fut_a", _simple_code("a"), ic=0.40, sharpe=10.0, t_stat=12.5),
        _factor("fut_b", _simple_code("b"), ic=0.35, sharpe=8.0, t_stat=9.0),
    ])
    factors = load_futures_elite_factors_from_db(ic_threshold=0, db_path=db)
    assert len(factors) == 2
    names = {f["name"] for f in factors}
    assert names == {"fut_a", "fut_b"}
    f0 = next(f for f in factors if f["name"] == "fut_a")
    assert f0["code"] == _simple_code("a")
    assert f0["params"] == {"window": 5}
    # metadata.evaluation 复用：t_stat 保留
    assert f0["evaluation"]["level_1_backtest"]["t_stat"] == 12.5
    assert f0["evaluation"]["level_1_backtest"]["ic"] == 0.40


def test_load_from_db_filters(tmp_path):
    """market/is_elite/status 过滤：stock、非 elite、非 active 因子均不加载。"""
    db = tmp_path / "test.duckdb"
    _seed_db(db, [
        _factor("fut_ok", _simple_code("ok"), ic=0.40, sharpe=10.0),
        _factor("fut_stock", _simple_code("stk"), ic=0.40, sharpe=10.0, market="stock"),
        _factor("fut_not_elite", _simple_code("ne"), ic=0.40, sharpe=10.0, is_elite=False),
        _factor("fut_archived", _simple_code("ar"), ic=0.40, sharpe=10.0, status="archived"),
    ])
    factors = load_futures_elite_factors_from_db(ic_threshold=0, db_path=db)
    assert [f["name"] for f in factors] == ["fut_ok"]


def test_load_from_db_build_evaluation_from_columns(tmp_path):
    """metadata 无 evaluation 时，用 factor_catalog 顶层评估列构造 level_1_backtest。"""
    db = tmp_path / "test.duckdb"
    _seed_db(db, [_factor("fut_plain", _simple_code("p"), ic=0.42, sharpe=9.5, with_eval=False)])
    factors = load_futures_elite_factors_from_db(ic_threshold=0, db_path=db)
    assert len(factors) == 1
    bt = factors[0]["evaluation"]["level_1_backtest"]
    assert bt["ic"] == 0.42
    assert bt["sharpe"] == 9.5
    assert bt["icir"] == 0.21


def test_load_from_db_ic_threshold(tmp_path):
    """IC 阈值过滤：|ic| < 阈值的因子跳过。"""
    db = tmp_path / "test.duckdb"
    _seed_db(db, [
        _factor("fut_hi", _simple_code("hi"), ic=0.45, sharpe=10.0),
        _factor("fut_lo", _simple_code("lo"), ic=0.15, sharpe=10.0),
    ])
    factors = load_futures_elite_factors_from_db(ic_threshold=0.3, db_path=db)
    assert [f["name"] for f in factors] == ["fut_hi"]


def test_load_from_db_dedup_code(tmp_path):
    """代码哈希去重：相同 code 只保留第一个（DB 按 sharpe desc 排序 → 保留高 sharpe 版本）。"""
    db = tmp_path / "test.duckdb"
    code = _simple_code("dup")
    _seed_db(db, [
        _factor("fut_dup1", code, ic=0.40, sharpe=10.0, t_stat=9.0),
        _factor("fut_dup2", code, ic=0.41, sharpe=10.5, t_stat=9.5),
    ])
    factors = load_futures_elite_factors_from_db(ic_threshold=0, db_path=db)
    assert [f["name"] for f in factors] == ["fut_dup2"]


def test_load_from_db_dedup_stat(tmp_path):
    """回测结果去重：相同 (ic, sharpe, t_stat) 视为同一因子逻辑。"""
    db = tmp_path / "test.duckdb"
    _seed_db(db, [
        _factor("fut_s1", _simple_code("s1"), ic=0.40, sharpe=10.0, t_stat=9.0),
        _factor("fut_s2", _simple_code("s2"), ic=0.40, sharpe=10.0, t_stat=9.0),
    ])
    factors = load_futures_elite_factors_from_db(ic_threshold=0, db_path=db)
    assert [f["name"] for f in factors] == ["fut_s1"]


def test_load_from_db_empty_returns_empty(tmp_path):
    """空库返回 []（触发 JSON 回退）。"""
    db = tmp_path / "empty.duckdb"
    _seed_db(db, [])
    assert load_futures_elite_factors_from_db(ic_threshold=0, db_path=db) == []


def test_load_from_db_missing_db_returns_empty(tmp_path):
    """库文件不存在返回 []（FactorRepository 建空表，不抛异常）。"""
    db = tmp_path / "nope.duckdb"
    assert load_futures_elite_factors_from_db(ic_threshold=0, db_path=db) == []


def test_load_from_db_bad_db_returns_empty(tmp_path):
    """库损坏/连接失败返回 []（异常兜底，不阻断主路径）。"""
    db = tmp_path / "bad.duckdb"
    db.write_text("not a duckdb file", encoding="utf-8")
    assert load_futures_elite_factors_from_db(ic_threshold=0, db_path=db) == []


# ─── _load_signal_factors（DB 优先 + JSON 回退）────────────────────────


def test_load_signal_factors_db_priority(tmp_path):
    """DB 有因子时优先使用 DB 结果（不落 JSON 回退）。"""
    db = tmp_path / "pri.duckdb"
    _seed_db(db, [_factor("fut_pri", _simple_code("pri"), ic=0.40, sharpe=10.0)])
    with patch(
        "futures_signal_pipeline.load_futures_elite_factors_from_db",
        side_effect=lambda ic_threshold=0, db_path=None: load_futures_elite_factors_from_db(
            ic_threshold=ic_threshold, db_path=db
        ),
    ), patch("futures_signal_pipeline.load_futures_elite_factors") as mock_json:
        factors = _load_signal_factors(ic_threshold=0)
    assert [f["name"] for f in factors] == ["fut_pri"]
    mock_json.assert_not_called()


def test_load_signal_factors_json_fallback(tmp_path):
    """DB 为空时回退 JSON 快照目录加载。"""
    with patch("futures_signal_pipeline.load_futures_elite_factors_from_db", return_value=[]):
        with patch("futures_signal_pipeline.load_futures_elite_factors", return_value=[{"name": "json_only"}]) as mock_json:
            factors = _load_signal_factors(ic_threshold=0)
    assert [f["name"] for f in factors] == ["json_only"]
    mock_json.assert_called_once_with(ic_threshold=0)
