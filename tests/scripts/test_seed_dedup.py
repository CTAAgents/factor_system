"""GAP-F10 种子库去重校验测试。

覆盖:
    - 去重校验 collect 纯函数（YAML/内嵌内部重复检测、交叉重叠报告）
    - verify_seed_dedup.py CLI 门禁退出码
"""

from __future__ import annotations

import sys
from pathlib import Path


_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from scripts.verify_seed_dedup import collect  # noqa: E402


def _fp(name: str) -> dict:
    return {"name": name, "factor_id": f"fct_{name}"}


def _yaml(name: str, src: str = "a.yaml") -> tuple[str, dict]:
    return (src, _fp(name))


# ─── collect 纯函数 ────────────────────────────────────────


class TestSeedDedupCollect:
    def test_no_duplicates(self):
        embedded = [_fp("fut_a"), _fp("fut_b")]
        yaml_items = [_yaml("fut_c"), _yaml("fut_d")]
        r = collect(embedded, yaml_items)
        assert r["exact_duplicates"] == {}
        assert r["embedded_duplicates"] == {}
        assert r["cross_overlap"] == []
        assert r["total_unique_names"] == 4

    def test_yaml_internal_duplicate(self):
        embedded = [_fp("fut_a")]
        yaml_items = [_yaml("fut_b"), _yaml("fut_b", "b.yaml")]
        r = collect(embedded, yaml_items)
        assert "fut_b" in r["exact_duplicates"]
        assert set(r["exact_duplicates"]["fut_b"]) == {"a.yaml", "b.yaml"}

    def test_embedded_internal_duplicate(self):
        embedded = [_fp("fut_a"), _fp("fut_a")]
        yaml_items = [_yaml("fut_b")]
        r = collect(embedded, yaml_items)
        assert "fut_a" in r["embedded_duplicates"]

    def test_cross_overlap_reported_not_blocked(self):
        embedded = [_fp("fut_a"), _fp("fut_b")]
        yaml_items = [_yaml("fut_a"), _yaml("fut_c")]
        r = collect(embedded, yaml_items)
        # 交叉重叠进 cross_overlap，但不计入内部重复
        assert r["cross_overlap"] == ["fut_a"]
        assert r["exact_duplicates"] == {}
        assert r["embedded_duplicates"] == {}
        assert r["embedded_only"] == ["fut_b"]
        assert r["yaml_only"] == ["fut_c"]

    def test_empty_sources(self):
        r = collect([], [])
        assert r["total_embedded"] == 0
        assert r["total_yaml"] == 0
        assert r["exact_duplicates"] == {}
        assert r["embedded_duplicates"] == {}


# ─── CLI 门禁退出码 ────────────────────────────────────────


class TestSeedDedupCli:
    def test_main_exit_zero_when_no_dup(self, monkeypatch):
        import scripts.verify_seed_dedup as mod

        monkeypatch.setattr(mod, "load_embedded_seeds", lambda: [_fp("fut_a")])
        monkeypatch.setattr(mod, "load_yaml_seeds", lambda path: [_yaml("fut_b")])
        assert mod.main(argv=[]) == 0

    def test_main_exit_one_when_yaml_dup(self, monkeypatch):
        import scripts.verify_seed_dedup as mod

        monkeypatch.setattr(mod, "load_embedded_seeds", lambda: [_fp("fut_a")])
        monkeypatch.setattr(
            mod,
            "load_yaml_seeds",
            lambda path: [_yaml("fut_b"), _yaml("fut_b", "b.yaml")],
        )
        assert mod.main(argv=[]) == 1

    def test_main_exit_zero_with_cross_overlap(self, monkeypatch):
        # 内嵌 vs YAML 交叉重叠属兜底源冗余（21-plan 不重写种子），不拦截
        import scripts.verify_seed_dedup as mod

        monkeypatch.setattr(mod, "load_embedded_seeds", lambda: [_fp("fut_a")])
        monkeypatch.setattr(mod, "load_yaml_seeds", lambda path: [_yaml("fut_a"), _yaml("fut_c")])
        assert mod.main(argv=[]) == 0

    def test_json_output(self, monkeypatch, capsys):
        import scripts.verify_seed_dedup as mod

        monkeypatch.setattr(mod, "load_embedded_seeds", lambda: [_fp("fut_a")])
        monkeypatch.setattr(mod, "load_yaml_seeds", lambda path: [_yaml("fut_b")])
        assert mod.main(argv=["--json"]) == 0
        out = capsys.readouterr().out
        assert '"exact_duplicates"' in out
