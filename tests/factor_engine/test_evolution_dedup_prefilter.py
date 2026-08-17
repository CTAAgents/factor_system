"""
tests/factor_engine/test_evolution_dedup_prefilter.py — 生成端去重前置测试（Step 1.35）

覆盖 ``EvolutionLoop._is_generated_duplicate`` / ``_build_seen_expression_norms``：
- elite 池既有表达式 → 新后代命中拦截
- 本 run 已生成/已评估表达式 → 同批重复拦截
- 表达式规范化（空白/换行压缩）等价判定
- 非重复表达式放行并并入已见集合
- 空 elite 目录 / 无 code 因子 / 损坏 JSON 降级安全
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from fts.factor_engine.evolution_loop import EvolutionLoop
from fts.factor_engine.evolution_promote import normalize_expression


def _make_loop(elite_dir, sample_ohlcv, forward_returns) -> EvolutionLoop:
    """构造最小 EvolutionLoop（llm_client 用 MagicMock，不依赖外部 fixture）。"""
    from fts.factor_engine.verifier import FactorVerifier

    return EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=elite_dir,
        llm_client=MagicMock(),
        verifier=FactorVerifier(),
    )


def _write_elite(elite_dir, factor_id: str, name: str, code: str) -> None:
    """写入一个 elite 快照 JSON（模拟既有精英因子）。"""
    elite_dir.mkdir(parents=True, exist_ok=True)
    (elite_dir / f"{factor_id}.json").write_text(
        json.dumps({"factor_id": factor_id, "name": name, "code": code}),
        encoding="utf-8",
    )


def _factor(fid: str, name: str, code: str) -> dict:
    return {"factor_id": fid, "name": name, "code": code}


# ─── _is_generated_duplicate ───────────────────────────────


def test_duplicate_hit_existing_elite(sample_ohlcv, forward_returns, tmp_elite_dir):
    """与既有 elite 表达式相同 → 拦截（True）。"""
    _write_elite(tmp_elite_dir, "fct_old", "old_factor", "def f(data,p):\n    return data['close']")
    loop = _make_loop(tmp_elite_dir, sample_ohlcv, forward_returns)
    new = _factor("fct_new", "new_factor", "def f(data,p):\n    return data['close']")  # 同表达式（换行差异）
    assert loop._is_generated_duplicate(new) is True


def test_duplicate_normalized_whitespace(sample_ohlcv, forward_returns, tmp_elite_dir):
    """空白/换行差异经 normalize 后等价 → 拦截。"""
    _write_elite(tmp_elite_dir, "fct_a", "a", "def f(data,p): return data['close']  +  1")
    loop = _make_loop(tmp_elite_dir, sample_ohlcv, forward_returns)
    new = _factor("fct_b", "b", "def f(data,p): return data['close'] + 1")
    assert loop._is_generated_duplicate(new) is True


def test_duplicate_within_same_run(sample_ohlcv, forward_returns, tmp_elite_dir):
    """同 run 内首见放行、再遇拦截（防同批重复）。"""
    loop = _make_loop(tmp_elite_dir, sample_ohlcv, forward_returns)
    code = "def f(data,p): return data['close'] * 2"
    first = _factor("fct_1", "one", code)
    assert loop._is_generated_duplicate(first) is False  # 首见：放行并入集
    second = _factor("fct_2", "two", code)
    assert loop._is_generated_duplicate(second) is True  # 再遇：拦截


def test_non_duplicate_passes(sample_ohlcv, forward_returns, tmp_elite_dir):
    """不同表达式 → 放行（False）。"""
    _write_elite(tmp_elite_dir, "fct_old", "old", "def f(data,p): return data['close']")
    loop = _make_loop(tmp_elite_dir, sample_ohlcv, forward_returns)
    new = _factor("fct_new", "new", "def f(data,p): return data['volume']")
    assert loop._is_generated_duplicate(new) is False


def test_missing_code_passes(sample_ohlcv, forward_returns, tmp_elite_dir):
    """无 code 因子不判定重复（放行）。"""
    _write_elite(tmp_elite_dir, "fct_old", "old", "def f(data,p): return data['close']")
    loop = _make_loop(tmp_elite_dir, sample_ohlcv, forward_returns)
    assert loop._is_generated_duplicate({"factor_id": "x", "name": "y"}) is False


def test_empty_elite_dir_passes(sample_ohlcv, forward_returns, tmp_elite_dir):
    """空 elite 目录：首见放行。"""
    loop = _make_loop(tmp_elite_dir, sample_ohlcv, forward_returns)
    assert loop._is_generated_duplicate(_factor("f", "f", "def f(data,p): return 1")) is False


# ─── _build_seen_expression_norms ──────────────────────────


def test_build_norms_skips_aux_files(sample_ohlcv, forward_returns, tmp_elite_dir):
    """辅助文件（_ 前缀）与无 code 文件被跳过。"""
    tmp_elite_dir.mkdir(parents=True, exist_ok=True)
    (tmp_elite_dir / "_l2_seed_correlation_index.json").write_text('{"correlations": []}', encoding="utf-8")
    _write_elite(tmp_elite_dir, "fct_1", "one", "def f(data,p):\n    return data['close']")
    _write_elite(tmp_elite_dir, "fct_2", "two", "def f(data,p): return data['volume']")
    (tmp_elite_dir / "broken.json").write_text("{not json", encoding="utf-8")
    loop = _make_loop(tmp_elite_dir, sample_ohlcv, forward_returns)
    norms = loop._build_seen_expression_norms()
    assert len(norms) == 2
    assert normalize_expression("def f(data,p): return data['close']") in norms


def test_build_norms_nonexistent_dir(sample_ohlcv, forward_returns, tmp_elite_dir):
    """目录不存在 → 空集（不抛异常）。"""
    loop = _make_loop(tmp_elite_dir / "nope", sample_ohlcv, forward_returns)
    assert loop._build_seen_expression_norms() == set()


def test_cache_lazy_and_reused(sample_ohlcv, forward_returns, tmp_elite_dir):
    """_seen_expression_norms 懒加载且首次检查后缓存（多次调用不重扫）。"""
    _write_elite(tmp_elite_dir, "fct_old", "old", "def f(data,p): return data['close']")
    loop = _make_loop(tmp_elite_dir, sample_ohlcv, forward_returns)
    assert loop._seen_expression_norms is None
    assert loop._is_generated_duplicate(_factor("n", "n", "def f(data,p): return data['close']")) is True
    assert loop._seen_expression_norms is not None  # 已缓存
    assert len(loop._seen_expression_norms) == 1  # 不重复并入


# ─── normalize_expression 复用（GAP-135 既有函数契约） ─────


def test_normalize_expression_contract():
    """规范化压缩空白/换行（与 GAP-135 晋升端一致）。"""
    assert normalize_expression("def f( a ):\n return   b  ") == "def f( a ): return b"
