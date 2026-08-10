"""tests/factor_engine/test_factor_screener.py — FactorScreener（B.2 Stage 1）筛选器测试。

覆盖:
    screen(): 内存列表入口（等级/总分/状态/风格/limit 全条件组合）
    screen(): 仓库入口（factors=None → _load_from_repo 各降级分支）
    静态方法: _pass_grade / _pass_total_score / _pass_status / _pass_style
    _load_from_repo: 仓库初始化失败 / 查询异常降级
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fts.factor_engine.factor_screener import FactorScreener


# ─── 辅助 fixture ─────────────────────────────────────────


def _make_factor(
    grade: str = "B",
    total_score: float = 60.0,
    status: str = "active",
    style_tags=None,
    **extra,
) -> dict:
    f = {
        "factor_id": f"fct_{grade}_{total_score}",
        "name": f"factor_{grade}_{total_score}",
        "grade": grade,
        "total_score": total_score,
        "status": status,
    }
    if style_tags is not None:
        f["style_tags"] = style_tags
    f.update(extra)
    return f


@pytest.fixture
def sample_factors() -> list[dict]:
    """含 A/B/C 各等级因子的样本列表。"""
    return [
        _make_factor(grade="A", total_score=80.0, status="active", style_tags=["momentum"]),
        _make_factor(grade="B", total_score=60.0, status="active", style_tags=["value"]),
        _make_factor(grade="B", total_score=45.0, status="observing", style_tags=["carry"]),
        _make_factor(grade="C", total_score=70.0, status="retired", style_tags=["momentum"]),
        _make_factor(grade="c", total_score=55.0, status="active", style_tags=["value", "low_vol"]),
    ]


# ─── screen() 内存列表入口 ────────────────────────────────


class TestScreenInMemory:
    def test_no_filter_returns_all(self, sample_factors):
        """默认 min_grade='B' 会剔除 C 级因子。"""
        result = FactorScreener().screen(factors=sample_factors)
        assert len(result) == 3  # A + 2×B，C/c 被默认 B 级门槛剔除

    def test_grade_filter_only(self, sample_factors):
        result = FactorScreener().screen(factors=sample_factors, min_grade="B")
        assert all(f["grade"].upper() in ("A", "B") for f in result)
        # 小写 grade="c" 应被剔除（默认 C 级门槛 B）
        assert all(f["grade"].upper() != "C" for f in result)

    def test_grade_a_only(self, sample_factors):
        result = FactorScreener().screen(factors=sample_factors, min_grade="A")
        assert all(f["grade"] == "A" for f in result)

    def test_min_total_score_filter(self, sample_factors):
        result = FactorScreener().screen(factors=sample_factors, min_total_score=50.0)
        assert all(f["total_score"] >= 50.0 for f in result)

    def test_status_filter(self, sample_factors):
        result = FactorScreener().screen(factors=sample_factors, status=["active"])
        assert all(f["status"] == "active" for f in result)

    def test_style_filter(self, sample_factors):
        result = FactorScreener().screen(factors=sample_factors, style_filter=["momentum"])
        assert all("momentum" in (f.get("style_tags") or []) for f in result)

    def test_combined_filters(self, sample_factors):
        result = FactorScreener().screen(
            factors=sample_factors,
            min_grade="B",
            min_total_score=50.0,
            status=["active", "observing"],
            style_filter=["value", "momentum"],
        )
        assert len(result) == 2  # A/momentum/active 与 B/value/active 均命中
        assert all(f["grade"] in ("A", "B") for f in result)

    def test_limit_truncates(self, sample_factors):
        result = FactorScreener().screen(factors=sample_factors, limit=2)
        assert len(result) == 2

    def test_limit_zero_ignored(self, sample_factors):
        result = FactorScreener().screen(factors=sample_factors, limit=0)
        assert len(result) == 3  # 默认 B 级门槛过滤后为 3

    def test_empty_factors_returns_empty(self):
        result = FactorScreener().screen(factors=[])
        assert result == []

    def test_no_match_returns_empty(self, sample_factors):
        result = FactorScreener().screen(factors=sample_factors, min_grade="A", status=["retired"])
        assert result == []

    def test_unknown_min_grade_defaults_to_b(self, sample_factors):
        """未知等级字符串回退默认 3（B）。"""
        result = FactorScreener().screen(factors=sample_factors, min_grade="Z")
        assert all(f["grade"].upper() in ("A", "B") for f in result)


# ─── 静态筛选方法 ─────────────────────────────────────────


class TestStaticFilters:
    def test_pass_grade_default_c(self):
        """缺省 grade 视为 C（等级分 2），min_grade=B(3) 时不通过。"""
        assert FactorScreener._pass_grade({"factor_id": "x"}, 3) is False

    def test_pass_grade_unknown_defaults_c(self):
        assert FactorScreener._pass_grade({"grade": "ZZ"}, 2) is True  # C(2) >= C(2)

    def test_pass_grade_case_insensitive(self):
        assert FactorScreener._pass_grade({"grade": "a"}, 3) is True

    def test_pass_total_score_none_threshold(self):
        assert FactorScreener._pass_total_score({"total_score": 10}, None) is True

    def test_pass_total_score_legacy_quality_score(self):
        """total_score 缺失时回退 quality_score。"""
        assert FactorScreener._pass_total_score({"quality_score": 55.0}, 50.0) is True

    def test_pass_total_score_missing_returns_false(self):
        assert FactorScreener._pass_total_score({"name": "x"}, 50.0) is False

    def test_pass_total_score_below_threshold(self):
        assert FactorScreener._pass_total_score({"total_score": 30.0}, 50.0) is False

    def test_pass_status_no_filter(self):
        assert FactorScreener._pass_status({"status": "anything"}, None) is True
        assert FactorScreener._pass_status({"status": "anything"}, []) is True

    def test_pass_status_hit(self):
        assert FactorScreener._pass_status({"status": "active"}, ["active"]) is True

    def test_pass_status_miss(self):
        assert FactorScreener._pass_status({"status": "retired"}, ["active"]) is False

    def test_pass_status_missing(self):
        assert FactorScreener._pass_status({"name": "x"}, ["active"]) is False

    def test_pass_style_no_filter(self):
        assert FactorScreener._pass_style({"name": "x"}, None) is True
        assert FactorScreener._pass_style({"name": "x"}, []) is True

    def test_pass_style_list_tags(self):
        assert FactorScreener._pass_style({"style_tags": ["momentum"]}, ["momentum"]) is True

    def test_pass_style_str_tags(self):
        assert FactorScreener._pass_style({"style_tags": "value"}, ["value"]) is True

    def test_pass_style_legacy_style_field(self):
        assert FactorScreener._pass_style({"style": ["carry"]}, ["carry"]) is True

    def test_pass_style_miss(self):
        assert FactorScreener._pass_style({"style_tags": ["value"]}, ["momentum"]) is False

    def test_pass_style_no_tags(self):
        assert FactorScreener._pass_style({"name": "x"}, ["momentum"]) is False


# ─── 仓库入口（factors=None）──────────────────────────────


class TestScreenFromRepo:
    def test_repo_query_success(self):
        """仓库查询成功返回列表。"""
        repo = MagicMock()
        repo.get_eligible.return_value = [_make_factor(grade="A")]
        screener = FactorScreener(repo=repo, market="futures")
        result = screener.screen(factors=None)
        assert len(result) == 1
        repo.get_eligible.assert_called_once_with(market="futures", require_elite=True)

    def test_repo_query_returns_none(self):
        """仓库返回 None 应降级为空列表。"""
        repo = MagicMock()
        repo.get_eligible.return_value = None
        screener = FactorScreener(repo=repo)
        assert screener.screen(factors=None) == []

    def test_repo_query_exception_returns_empty(self):
        """仓库查询抛异常应降级为空列表。"""
        repo = MagicMock()
        repo.get_eligible.side_effect = RuntimeError("duckdb down")
        screener = FactorScreener(repo=repo)
        assert screener.screen(factors=None) == []

    def test_repo_none_auto_init(self):
        """repo 为 None 时自动实例化 FactorRepository。"""
        with patch("fts.factor_engine.factor_db.repository.FactorRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_eligible.return_value = [_make_factor(grade="A")]
            mock_repo_cls.return_value = mock_repo
            screener = FactorScreener()
            result = screener.screen(factors=None)
            assert len(result) == 1
            mock_repo_cls.assert_called_once()

    def test_repo_none_auto_init_failure(self):
        """自动实例化仓库失败应返回空列表。"""
        with patch("fts.factor_engine.factor_db.repository.FactorRepository", side_effect=ImportError("no repo")):
            screener = FactorScreener()
            assert screener.screen(factors=None) == []

    def test_load_from_repo_caches_repo(self):
        """_load_from_repo 成功后仓库被缓存复用。"""
        repo = MagicMock()
        repo.get_eligible.return_value = []
        screener = FactorScreener(repo=repo)
        screener._load_from_repo()
        assert screener._repo is repo
