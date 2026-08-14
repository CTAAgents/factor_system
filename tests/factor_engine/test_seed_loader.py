"""tests/factor_engine/test_seed_loader.py — SeedLoader YAML 加载器测试。

覆盖范围:
    - YAML 加载正确性（code-based / expression-based / fundamental-based）
    - 双路径一致性（YAML vs 硬编码结果等价）
    - fallback 机制（YAML 缺失时回退到硬编码）
    - 边界条件（空文件 / 格式错误 / 代码异常）
    - 完整性验证（verify_yaml_integrity）

版本: v1.0.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))


# ─── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def seeds_dir() -> Path:
    """返回 seeds 目录路径，确保存在。"""
    from fts.factor_engine.seed_loader import get_seeds_dir

    d = get_seeds_dir()
    assert d.exists(), f"seeds 目录不存在: {d}"
    return d


@pytest.fixture
def sample_code_yaml(tmp_path: Path) -> Path:
    """创建一个示例 code-based YAML 文件。"""
    doc = {
        "version": "1.0",
        "market": "futures",
        "factors": [
            {
                "name": "test_code_factor",
                "description": "测试 code-based 因子",
                "params": {"lookback": 20},
                "input_fields": ["close"],
                "lookback": 25,
                "output_type": "signal",
                "frequency": "daily",
                "economic_logic": {
                    "theory": 4,
                    "behavioral": 3,
                    "microstructure": 3,
                    "institutional": 3,
                    "narrative": "测试因子",
                },
                "code": (
                    "def factor_program(data, params):\n"
                    "    import numpy as np\n"
                    "    close = data['close'].values\n"
                    "    return np.clip(close, -1, 1)\n"
                ),
            }
        ],
    }
    p = tmp_path / "test_code.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True)
    return p


@pytest.fixture
def sample_expression_yaml(tmp_path: Path) -> Path:
    """创建一个示例 expression-based YAML 文件。"""
    doc = {
        "version": "1.0",
        "market": "stock",
        "factors": [
            {
                "name": "test_expr_factor",
                "description": "测试 expression 因子",
                "expression": "rank(close) * scale(volume)",
                "params": {"d": 10},
                "economic_logic": {
                    "theory": 3,
                    "behavioral": 3,
                    "microstructure": 3,
                    "institutional": 3,
                    "narrative": "表达式测试",
                },
            }
        ],
    }
    p = tmp_path / "test_expr.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True)
    return p


@pytest.fixture
def sample_fundamental_yaml(tmp_path: Path) -> Path:
    """创建一个示例 fundamental-based YAML 文件。"""
    doc = {
        "version": "1.0",
        "market": "stock",
        "factors": [
            {
                "name": "test_fund_factor",
                "description": "测试基本面因子",
                "field_defs": "pe = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data['pe_ttm']",
                "field_check": "pe is not None",
                "expression": "np.tanh(1.0 / pe)",
                "input_fields": ["close"],
                "lookback": 10,
                "economic_logic": {
                    "theory": 4,
                    "behavioral": 3,
                    "microstructure": 3,
                    "institutional": 4,
                    "narrative": "基本面测试",
                },
            }
        ],
    }
    p = tmp_path / "test_fund.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True)
    return p


@pytest.fixture
def empty_yaml(tmp_path: Path) -> Path:
    """创建空 YAML 文件。"""
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    return p


@pytest.fixture
def invalid_yaml(tmp_path: Path) -> Path:
    """创建格式错误的 YAML 文件。"""
    p = tmp_path / "invalid.yaml"
    p.write_text("this: is: invalid: yaml: [[[", encoding="utf-8")
    return p


@pytest.fixture
def unknown_type_yaml(tmp_path: Path) -> Path:
    """创建未知因子类型的 YAML 文件。"""
    doc = {
        "version": "1.0",
        "market": "stock",
        "factors": [
            {
                "name": "test_unknown_factor",
                "description": "未知类型",
                "unknown_field": "should fail",
            }
        ],
    }
    p = tmp_path / "unknown.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True)
    return p


# ─── YAML 加载正确性 ──────────────────────────────────────────


class TestYamlLoading:
    """测试 YAML 加载核心功能。"""

    def test_load_code_based_factor(self, sample_code_yaml: Path):
        """code-based 因子可正确加载。"""
        from fts.factor_engine.seed_loader import load_factors_from_yaml

        factors = load_factors_from_yaml(sample_code_yaml)
        assert len(factors) == 1
        fp = factors[0]
        assert fp["name"] == "test_code_factor"
        assert fp["source"] == "seed"
        assert fp["generation"] == 0
        assert "factor_id" in fp
        assert fp["factor_id"].startswith("fct_")
        assert "def factor_program" in fp["code"]
        assert fp["params"]["lookback"] == 20

    def test_load_expression_based_factor(self, sample_expression_yaml: Path):
        """expression-based 因子可正确加载。"""
        from fts.factor_engine.seed_loader import load_factors_from_yaml

        factors = load_factors_from_yaml(sample_expression_yaml)
        assert len(factors) == 1
        fp = factors[0]
        assert fp["name"] == "test_expr_factor"
        assert "def factor_program" in fp["code"]
        assert "rank" in fp["code"]
        assert "scale" in fp["code"]
        assert sorted(fp["signature"]["input_fields"]) == ["close", "volume"]

    def test_load_fundamental_based_factor(self, sample_fundamental_yaml: Path):
        """fundamental-based 因子可正确加载。"""
        from fts.factor_engine.seed_loader import load_factors_from_yaml

        factors = load_factors_from_yaml(sample_fundamental_yaml)
        assert len(factors) == 1
        fp = factors[0]
        assert fp["name"] == "test_fund_factor"
        assert "def factor_program" in fp["code"]
        assert "pe_ttm" in fp["code"]

    def test_factor_has_valid_structure(self, sample_code_yaml: Path):
        """加载的因子必须满足 FactorProgram 契约。"""
        from fts.factor_engine.seed_loader import load_factors_from_yaml

        fp = load_factors_from_yaml(sample_code_yaml)[0]
        assert "factor_id" in fp
        assert "name" in fp
        assert "code" in fp
        assert "params" in fp
        assert "signature" in fp
        assert "economic_logic" in fp
        assert "source" in fp
        assert "generation" in fp

    def test_factor_has_economic_logic(self, sample_code_yaml: Path):
        """经济逻辑四维评分完整。"""
        from fts.factor_engine.seed_loader import load_factors_from_yaml

        fp = load_factors_from_yaml(sample_code_yaml)[0]
        el = fp["economic_logic"]
        assert "theory" in el
        assert "behavioral" in el
        assert "microstructure" in el
        assert "institutional" in el
        assert el["narrative"] == "测试因子"

    def test_factor_code_is_compilable(self, sample_code_yaml: Path):
        """加载的因子代码可通过编译验证。"""
        from fts.factor_engine.seed_loader import load_factors_from_yaml
        from fts.factor_engine.factor_program import validate_factor_code

        fp = load_factors_from_yaml(sample_code_yaml)[0]
        ok, reasons = validate_factor_code(fp["code"])
        assert ok, f"代码编译失败: {reasons}"


# ─── 目录批量加载 ─────────────────────────────────────────────


class TestDirectoryLoading:
    """测试目录批量加载。"""

    def test_load_futures_directory(self, seeds_dir: Path):
        """期货目录可正确加载所有 YAML 文件。"""
        from fts.factor_engine.seed_loader import load_factors_from_dir

        futures_dir = seeds_dir / "futures"
        factors = load_factors_from_dir(futures_dir)
        assert len(factors) == 185

    def test_load_stock_directory_missing(self, seeds_dir: Path):
        """纯期货版 FTS 无股票种子目录，加载返回空。"""
        from fts.factor_engine.seed_loader import load_factors_from_dir

        stock_dir = seeds_dir / "stock"
        factors = load_factors_from_dir(stock_dir)
        assert factors == []

    def test_load_energy_directory(self, seeds_dir: Path):
        """能化专属种子目录（GAP-121）可正确加载全部 YAML 文件。"""
        from fts.factor_engine.seed_loader import load_factors_from_dir

        energy_dir = seeds_dir / "energy"
        factors = load_factors_from_dir(energy_dir)
        assert len(factors) >= 8
        names = {f["name"] for f in factors}
        # 能化种子使用 eng_ / ec_ 前缀（eng_=量价代理，ec_=链传导专属）
        assert all(n.startswith(("eng_", "ec_")) for n in names), f"能化种子前缀异常: {names}"
        # 全部带 energy 市场标记
        assert {f["market"] for f in factors} == {"energy"}

    def test_load_nonexistent_directory(self, tmp_path: Path):
        """不存在的目录返回空列表。"""
        from fts.factor_engine.seed_loader import load_factors_from_dir

        factors = load_factors_from_dir(tmp_path / "nonexistent")
        assert factors == []

    def test_list_yaml_files(self, seeds_dir: Path):
        """YAML 文件列表正确。"""
        from fts.factor_engine.seed_loader import list_yaml_files

        files = list_yaml_files(seeds_dir / "futures")
        assert len(files) == 20
        assert all(f.suffix == ".yaml" for f in files)


# ─── 全量加载 API ────────────────────────────────────────────


class TestLoadAllYamlSeeds:
    """测试全量加载 API。"""

    def test_load_all_seeds(self):
        """加载所有市场种子（185 个，纯期货版仅期货种子）。"""
        from fts.factor_engine.seed_loader import load_all_yaml_seeds

        seeds = load_all_yaml_seeds()
        assert len(seeds) == 185

    def test_load_futures_only(self):
        """仅加载期货种子（185 个）。"""
        from fts.factor_engine.seed_loader import load_all_yaml_seeds

        seeds = load_all_yaml_seeds(market="futures")
        assert len(seeds) == 185

    def test_load_energy_mixed(self):
        """energy 市场混入加载 = 通用期货种子 + 能化专属种子（GAP-121）。"""
        from fts.factor_engine.seed_loader import load_all_yaml_seeds

        seeds = load_all_yaml_seeds(market="energy")
        names = {f["name"] for f in seeds}
        eng_names = {n for n in names if n.startswith(("eng_", "ec_"))}
        fut_names = {n for n in names if n.startswith("fut_")}
        # 两线知识均注入
        assert len(eng_names) >= 8, f"能化专属种子缺失: {len(eng_names)}"
        assert len(fut_names) >= 100, f"通用期货种子缺失: {len(fut_names)}"
        # energy 专属因子 market 标记为 energy
        eng_markets = {f["market"] for f in seeds if f["name"].startswith(("eng_", "ec_"))}
        assert eng_markets == {"energy"}


# ─── 双路径一致性 ────────────────────────────────────────────


class TestDualPathConsistency:
    """测试 YAML 与硬编码路径的一致性。"""

    def test_futures_names_match(self):
        """YAML 期货因子名称覆盖硬编码兜底（YAML 为主路径，硬编码为子集）。"""
        from fts.factor_engine.seed_loader import load_all_yaml_seeds
        from fts.factor_engine.seed_pool import SeedPool

        yaml_names = {f["name"] for f in load_all_yaml_seeds(market="futures")}
        hc_names = {f["name"] for f in SeedPool(market="futures", use_yaml=False).load_all_seeds()}
        assert hc_names.issubset(yaml_names), f"硬编码兜底包含 YAML 主路径未覆盖的因子: {hc_names - yaml_names}"

    def test_futures_count_match(self):
        """YAML 主路径 185 个期货因子，硬编码兜底 81 个。"""
        from fts.factor_engine.seed_loader import load_all_yaml_seeds
        from fts.factor_engine.seed_pool import SeedPool

        yaml_count = len(load_all_yaml_seeds(market="futures"))
        hc_count = len(SeedPool(market="futures", use_yaml=False).load_all_seeds())
        assert yaml_count == 185
        assert yaml_count >= hc_count

    def test_seedpool_yaml_default(self):
        """SeedPool 默认使用 YAML 路径。"""
        from fts.factor_engine.seed_pool import SeedPool

        pool = SeedPool(market="futures")
        seeds = pool.load_all_seeds()
        assert len(seeds) == 185

    def test_seedpool_use_yaml_false(self):
        """SeedPool use_yaml=False 走硬编码路径。"""
        from fts.factor_engine.seed_pool import SeedPool

        pool = SeedPool(market="futures", use_yaml=False)
        seeds = pool.load_all_seeds()
        assert len(seeds) == 81

    def test_seedpool_fallback_works(self, tmp_path: Path):
        """YAML 加载失败时回退到硬编码。"""
        from fts.factor_engine.seed_loader import set_seeds_dir
        from fts.factor_engine.seed_pool import SeedPool

        # 临时设置 seeds 目录为不存在的路径
        set_seeds_dir(tmp_path / "nonexistent")
        try:
            pool = SeedPool(market="futures", use_yaml=True)
            seeds = pool.load_all_seeds()
            # 应该回退到硬编码路径
            assert len(seeds) == 81
        finally:
            # 恢复
            set_seeds_dir(None)

    def test_seedpool_energy_not_stock_branch(self):
        """energy 市场走期货+能化分支，不再误走股票分支（GAP-121 修复）。"""
        from fts.factor_engine.seed_loader import set_seeds_dir
        from fts.factor_engine.seed_pool import SeedPool

        # 临时设置 seeds 目录为不存在的路径 → YAML 路径失效，走硬编码兜底
        set_seeds_dir("__nonexistent_seeds__")
        try:
            pool = SeedPool(market="energy", use_yaml=False)
            seeds = pool.load_all_seeds()
            names = {s["name"] for s in seeds}
            # 不应包含股票专属种子（修复前误走股票分支的标志）
            stock_only = {n for n in names if n.startswith(("alpha_", "qlib_", "gtja_", "fund_val_"))}
            assert not stock_only, f"energy 市场误加载股票种子: {stock_only}"
            # 硬编码兜底加载期货种子（YAML 失效时的保底）
            fut_names = {n for n in names if n.startswith(("fut_", "tsmom_", "vol_"))}
            assert len(fut_names) >= 50, f"硬编码兜底期货种子不足: {len(fut_names)}"
        finally:
            set_seeds_dir(None)


# ─── 边界条件 ────────────────────────────────────────────────


class TestEdgeCases:
    """测试边界条件。"""

    def test_empty_yaml_file(self, empty_yaml: Path):
        """空 YAML 文件返回空列表。"""
        from fts.factor_engine.seed_loader import load_factors_from_yaml

        factors = load_factors_from_yaml(empty_yaml)
        assert factors == []

    def test_invalid_yaml_file(self, invalid_yaml: Path):
        """格式错误的 YAML 抛出异常。"""
        from fts.factor_engine.seed_loader import load_factors_from_yaml

        with pytest.raises(Exception):
            load_factors_from_yaml(invalid_yaml)

    def test_unknown_factor_type(self, unknown_type_yaml: Path):
        """未知因子类型被跳过（不抛异常，仅记录 warning）。"""
        from fts.factor_engine.seed_loader import load_factors_from_yaml

        factors = load_factors_from_yaml(unknown_type_yaml)
        assert len(factors) == 0

    def test_yaml_without_factors_key(self, tmp_path: Path):
        """缺少 factors 键的 YAML 返回空列表。"""
        doc = {"version": "1.0", "market": "stock"}
        p = tmp_path / "no_factors.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(doc, f, allow_unicode=True)

        from fts.factor_engine.seed_loader import load_factors_from_yaml

        factors = load_factors_from_yaml(p)
        assert factors == []

    def test_factor_missing_name(self, tmp_path: Path):
        """缺少 name 的因子被跳过。"""
        doc = {
            "version": "1.0",
            "market": "stock",
            "factors": [
                {
                    "description": "no name",
                    "code": "def factor_program(data, params): return np.zeros(1)",
                }
            ],
        }
        p = tmp_path / "no_name.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(doc, f, allow_unicode=True)

        from fts.factor_engine.seed_loader import load_factors_from_yaml

        factors = load_factors_from_yaml(p)
        assert len(factors) == 0


# ─── 完整性验证 ──────────────────────────────────────────────


class TestIntegrityVerification:
    """测试完整性验证功能。"""

    def test_verify_all_yaml_integrity(self):
        """所有 YAML 文件通过完整性验证（futures 20 文件 185 因子 + energy 5 文件 14 因子）。"""
        from fts.factor_engine.seed_loader import verify_yaml_integrity

        report = verify_yaml_integrity()
        assert report["valid"] is True
        # 期货 20 文件 + 能化专属 5 文件（energy/，GAP-121）
        assert report["total_files"] == 25
        assert report["total_factors"] == 199
        assert len(report["errors"]) == 0

    def test_verify_report_structure(self):
        """验证报告结构正确。"""
        from fts.factor_engine.seed_loader import verify_yaml_integrity

        report = verify_yaml_integrity()
        assert "total_files" in report
        assert "total_factors" in report
        assert "errors" in report
        assert "files" in report
        assert "valid" in report
        assert isinstance(report["files"], list)

    def test_verify_each_file_has_name(self):
        """每个文件中的因子都有 name 字段。"""
        from fts.factor_engine.seed_loader import verify_yaml_integrity

        report = verify_yaml_integrity()
        for file_info in report["files"]:
            assert "errors" not in file_info or "missing 'name'" not in str(file_info.get("errors", []))


# ─── 路径配置 ────────────────────────────────────────────────


class TestPathConfig:
    """测试路径配置功能。"""

    def test_get_seeds_dir_default(self):
        """默认 seeds 目录在项目根目录下。"""
        from fts.factor_engine.seed_loader import get_seeds_dir

        d = get_seeds_dir()
        assert d.name == "seeds"
        assert d.exists()

    def test_set_seeds_dir(self, tmp_path: Path):
        """可设置自定义 seeds 目录。"""
        from fts.factor_engine.seed_loader import get_seeds_dir, set_seeds_dir

        original = get_seeds_dir()
        try:
            set_seeds_dir(tmp_path)
            assert get_seeds_dir() == tmp_path
        finally:
            set_seeds_dir(original)


# ─── 类型检测 ────────────────────────────────────────────────


class TestFactorTypeDetection:
    """测试因子类型自动检测。"""

    def test_detect_code_type(self):
        from fts.factor_engine.seed_loader import _detect_factor_type

        defn = {"code": "def factor_program(data, params): pass"}
        assert _detect_factor_type(defn) == "code"

    def test_detect_expression_type(self):
        from fts.factor_engine.seed_loader import _detect_factor_type

        defn = {"expression": "rank(close)"}
        assert _detect_factor_type(defn) == "expression"

    def test_detect_fundamental_type(self):
        from fts.factor_engine.seed_loader import _detect_factor_type

        defn = {"field_defs": "pe = data['pe']", "expression": "1/pe"}
        assert _detect_factor_type(defn) == "fundamental"

    def test_detect_unknown_type(self):
        from fts.factor_engine.seed_loader import _detect_factor_type

        defn = {"name": "unknown_factor"}
        with pytest.raises(ValueError):
            _detect_factor_type(defn)


# ─── 输入字段推断 ────────────────────────────────────────────


class TestInputFieldEstimation:
    """测试输入字段推断。"""

    def test_estimate_volume_field(self):
        from fts.factor_engine.seed_loader import _estimate_input_fields

        fields = _estimate_input_fields("rank(close) * scale(volume)")
        assert "close" in fields
        assert "volume" in fields

    def test_estimate_no_extra_fields(self):
        from fts.factor_engine.seed_loader import _estimate_input_fields

        fields = _estimate_input_fields("rank(close)")
        assert fields == ["close"]

    def test_estimate_lookback(self):
        from fts.factor_engine.expr_dsl.seed_analyzer import estimate_lookback_static

        lb = estimate_lookback_static("ts_sum(close, 20)")
        assert lb == 20

    def test_estimate_lookback_default(self):
        from fts.factor_engine.expr_dsl.seed_analyzer import estimate_lookback_static

        lb = estimate_lookback_static("rank(close)")
        assert lb == 10


# ─── 期货种子文件名集合 ────────────────────────────────────


class TestFuturesSeedFileSet:
    """测试期货种子文件名集合（_FUTURES_SEED_FILES，按文件名校验加载完整性）。"""

    def test_seed_file_set_contains_known_files(self):
        from fts.factor_engine.seed_loader import _FUTURES_SEED_FILES

        assert "momentum.yaml" in _FUTURES_SEED_FILES
        assert "term_structure.yaml" in _FUTURES_SEED_FILES
        assert "volatility.yaml" in _FUTURES_SEED_FILES

    def test_seed_file_set_excludes_legacy_library_files(self):
        """外部库种子（qlib158/gtja191/wq101）不属期货内置集合。"""
        from fts.factor_engine.seed_loader import _FUTURES_SEED_FILES

        assert "qlib158.yaml" not in _FUTURES_SEED_FILES
        assert "gtja191.yaml" not in _FUTURES_SEED_FILES
        assert "wq101.yaml" not in _FUTURES_SEED_FILES

