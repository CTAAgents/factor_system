"""
tests/test_config_settings.py — FTS config.settings 模块全面测试。

覆盖目标: 85%+（当前 64%）
覆盖缺失场景:
  1. load_config 显式 config_path → YAML
  2. load_config 通过 FTS_CONFIG_FILE 环境变量
  3. pyyaml 不可用时回退到 json
  4. _apply_env_overrides: bool / int / float / str / private skip
  5. get_config() 全局单例初始化
  6. 配置文件不存在（静默回退）
  7. 配置文件格式错误（静默回退）
  8. FTS_ 环境变量不匹配任何字段（忽略）
  9. load_config(None) 无 env var → 默认值
  10. YAML 缺少字段 → 保留默认值
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fts.config.settings import (
    DEFAULT_ELITE_DIR,
    DEFAULT_MEMORY_DIR,
    FTSConfig,
    _apply_dict,
    _apply_env_overrides,
    get_config,
    load_config,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_global_config():
    """每个测试前重置全局配置单例，避免测试间污染。"""
    import fts.config.settings as _s

    _s._default_config = None
    yield
    _s._default_config = None


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    """创建示例 YAML 配置文件。"""
    p = tmp_path / "config.yaml"
    p.write_text(
        "max_generations: 50\n"
        "population_size: 100\n"
        "log_level: DEBUG\n"
        "meta_loop_interval_hours: 12\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_json(tmp_path: Path) -> Path:
    """创建示例 JSON 配置文件（用于 yaml 不可用时的回退测试）。"""
    p = tmp_path / "config.json"
    data = {"max_generations": 30, "population_size": 50, "log_level": "WARN"}
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ═══════════════════════════════════════════════════════════
# FTSConfig dataclass 默认值
# ═══════════════════════════════════════════════════════════

class TestFTSConfigDefaults:
    """FTSConfig dataclass 默认值正确性。"""

    def test_default_values(self):
        """所有字段默认值正确。"""
        cfg = FTSConfig()
        assert cfg.memory_dir == DEFAULT_MEMORY_DIR
        assert cfg.elite_dir == DEFAULT_ELITE_DIR
        assert cfg.max_generations == 10
        assert cfg.population_size == 20
        assert cfg.micro_trials_per_generation == 50
        assert cfg.max_workers == 4
        assert cfg.meta_loop_interval_hours == 24
        assert cfg.meta_loop_max_tokens == 8000
        assert cfg.portfolio_max_factors == 20
        assert cfg.portfolio_top_n == 5
        assert cfg.portfolio_decay_days == 90
        assert cfg.log_level == "INFO"
        assert cfg.log_file == ""
        assert cfg.llm_backend == ""


# ═══════════════════════════════════════════════════════════
# load_config()
# ═══════════════════════════════════════════════════════════

class TestLoadConfig:
    """load_config() 配置加载路径全覆盖。"""

    # ── 场景 1: 显式 config_path → YAML ──

    def test_with_explicit_yaml_path(self, sample_yaml):
        """显式 config_path → 从 YAML 成功加载。"""
        cfg = load_config(str(sample_yaml))
        assert cfg.max_generations == 50
        assert cfg.population_size == 100
        assert cfg.log_level == "DEBUG"
        assert cfg.meta_loop_interval_hours == 12

    # ── 场景 10: YAML 缺少字段 → 保留默认值 ──

    def test_missing_field_keeps_default(self, tmp_path):
        """YAML 只覆盖部分字段 → 未覆盖字段保留默认值。"""
        p = tmp_path / "partial.yaml"
        p.write_text("max_generations: 99\n", encoding="utf-8")
        cfg = load_config(str(p))
        assert cfg.max_generations == 99
        assert cfg.population_size == 20  # 默认值
        assert cfg.log_level == "INFO"  # 默认值

    # ── 场景 2: 通过 FTS_CONFIG_FILE 环境变量 ──

    def test_with_env_var_config_file(self, sample_yaml, monkeypatch):
        """FTS_CONFIG_FILE 环境变量 → 加载对应配置。"""
        monkeypatch.setenv("FTS_CONFIG_FILE", str(sample_yaml))
        cfg = load_config()
        assert cfg.max_generations == 50
        assert cfg.population_size == 100

    def test_env_var_config_file_precedence(self, sample_yaml, tmp_path, monkeypatch):
        """FTS_CONFIG_FILE 在 config_path=None 时生效；显式传参优先级更高。"""
        other = tmp_path / "other.yaml"
        other.write_text("max_generations: 1\n", encoding="utf-8")
        monkeypatch.setenv("FTS_CONFIG_FILE", str(sample_yaml))
        cfg = load_config()  # None → 使用 FTS_CONFIG_FILE
        assert cfg.max_generations == 50
        cfg2 = load_config(str(other))  # 显式 → 覆盖 env var
        assert cfg2.max_generations == 1

    # ── 场景 3: pyyaml 不可用 → 回退到 json ──

    def test_yaml_import_error_fallback_to_json(self, sample_json):
        """pyyaml 不可用 → 回退到 json.loads。"""
        import builtins

        original_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named yaml")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", _mock_import):
            cfg = load_config(str(sample_json))
            assert cfg.max_generations == 30
            assert cfg.population_size == 50
            assert cfg.log_level == "WARN"

    # ── 场景 6: 配置文件不存在 → 静默回退 ──

    def test_config_path_not_exist(self):
        """不存在的配置文件路径 → 静默回退到默认值。"""
        cfg = load_config("/nonexistent/path/config.yaml")
        assert cfg.max_generations == 10
        assert cfg.population_size == 20

    def test_env_var_config_file_not_exist(self, monkeypatch):
        """FTS_CONFIG_FILE 指向不存在的文件 → 静默回退。"""
        monkeypatch.setenv("FTS_CONFIG_FILE", "/nonexistent/config.yaml")
        cfg = load_config()
        assert cfg.max_generations == 10

    # ── 场景 7: 配置文件格式错误 → 静默回退 ──

    def test_malformed_yaml_silent_fallback(self, tmp_path):
        """YAML 语法错误 → 静默回退到默认值。"""
        p = tmp_path / "bad.yaml"
        p.write_text("{invalid: [yaml structure", encoding="utf-8")
        cfg = load_config(str(p))
        assert cfg.max_generations == 10
        assert cfg.population_size == 20

    def test_malformed_json_when_yaml_unavailable(self, tmp_path):
        """pyyaml 不可用时 JSON 格式错误 → 静默回退。"""
        p = tmp_path / "bad.json"
        p.write_text("{invalid json}", encoding="utf-8")

        import builtins

        original_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named yaml")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", _mock_import):
            cfg = load_config(str(p))
            assert cfg.max_generations == 10
            assert cfg.population_size == 20

    # ── 场景 9: config_path=None 且无环境变量 → 返回默认值 ──

    def test_no_config_path_no_env_var_returns_defaults(self):
        """config_path=None 且 FTS_CONFIG_FILE 未设置 → 全部默认值。"""
        cfg = load_config()
        assert cfg.max_generations == 10
        assert cfg.population_size == 20
        assert cfg.log_level == "INFO"
        assert cfg.memory_dir == DEFAULT_MEMORY_DIR

    # ── 环境变量覆盖（在 load_config 内部自动触发） ──

    def test_env_override_in_load_config(self, monkeypatch):
        """load_config 时环境变量覆盖生效。"""
        monkeypatch.setenv("FTS_MAX_GENERATIONS", "100")
        monkeypatch.setenv("FTS_LOG_LEVEL", "ERROR")
        cfg = load_config()
        assert cfg.max_generations == 100
        assert cfg.log_level == "ERROR"

    def test_env_override_over_yaml(self, sample_yaml, monkeypatch):
        """环境变量优先级高于 YAML 配置。"""
        monkeypatch.setenv("FTS_MAX_GENERATIONS", "999")
        cfg = load_config(str(sample_yaml))
        assert cfg.max_generations == 999  # env var 覆盖 YAML
        assert cfg.population_size == 100  # 来自 YAML

    # ── 场景 8: FTS_ 环境变量不匹配任何字段 → 忽略 ──

    def test_env_var_not_matching_any_field(self, monkeypatch):
        """FTS_ 前缀但不匹配任何字段 → 静默忽略，不抛异常。"""
        monkeypatch.setenv("FTS_NONEXISTENT_FIELD", "should_be_ignored")
        monkeypatch.setenv("FTS_ALSO_INVALID", "42")
        cfg = load_config()
        assert cfg.max_generations == 10
        assert cfg.population_size == 20


# ═══════════════════════════════════════════════════════════
# _apply_env_overrides() 环境变量覆盖
# ═══════════════════════════════════════════════════════════

class TestApplyEnvOverrides:
    """_apply_env_overrides() 各分支全覆盖。"""

    # ── 场景 4a: 布尔类型 ──

    def test_bool_branch_with_enabled_field(self, monkeypatch):
        """_apply_env_overrides bool 分支 — 使用包含 bool 属性的对象。"""
        class _Cfg:
            verbose = False
            dry_run = True

        cfg = _Cfg()
        monkeypatch.setenv("FTS_VERBOSE", "true")
        _apply_env_overrides(cfg)
        assert cfg.verbose is True
        assert cfg.dry_run is True  # 无对应 env var，不变

    @pytest.mark.parametrize("raw,expected", [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("0", False),
        ("false", False),
    ])
    def test_bool_branch_parametrized(self, monkeypatch, raw, expected):
        """_apply_env_overrides bool 分支参数化覆盖。"""
        class _Cfg:
            active = False

        cfg = _Cfg()
        monkeypatch.setenv("FTS_ACTIVE", raw)
        _apply_env_overrides(cfg)
        assert cfg.active is expected

    # ── 场景 4b: 整数类型 ──

    def test_int_env_override(self, monkeypatch):
        """整数字段环境变量覆盖。"""
        cfg = FTSConfig()
        monkeypatch.setenv("FTS_MAX_GENERATIONS", "42")
        _apply_env_overrides(cfg)
        assert cfg.max_generations == 42
        assert isinstance(cfg.max_generations, int)

    # ── 场景 4c: 浮点类型 ──

    def test_float_env_override(self, monkeypatch):
        """浮点字段环境变量覆盖。"""
        class _Cfg:
            threshold = 0.5

        cfg = _Cfg()
        monkeypatch.setenv("FTS_THRESHOLD", "0.85")
        _apply_env_overrides(cfg)
        assert cfg.threshold == 0.85
        assert isinstance(cfg.threshold, float)

    # ── 场景 4d: 字符串类型 ──

    def test_string_env_override(self, monkeypatch):
        """字符串字段环境变量覆盖。"""
        cfg = FTSConfig()
        monkeypatch.setenv("FTS_LOG_LEVEL", "CRITICAL")
        _apply_env_overrides(cfg)
        assert cfg.log_level == "CRITICAL"
        assert isinstance(cfg.log_level, str)

    # ── 场景 4e: 私有属性跳过 ──

    def test_private_attribute_skip(self, monkeypatch):
        """以下划线开头的属性不被 env var 覆盖。"""
        class _Cfg:
            _secret = "original"
            name = "hello"

        cfg = _Cfg()
        monkeypatch.setenv("FTS__SECRET", "leaked")
        _apply_env_overrides(cfg)
        assert cfg._secret == "original"  # 未被覆盖
        assert cfg.name == "hello"


# ═══════════════════════════════════════════════════════════
# _apply_dict()
# ═══════════════════════════════════════════════════════════

class TestApplyDict:
    """_apply_dict() 字典应用到配置。"""

    def test_apply_valid_keys(self):
        """有效字段被正确应用。"""
        cfg = FTSConfig()
        _apply_dict(cfg, {"max_generations": 99, "log_level": "TRACE"})
        assert cfg.max_generations == 99
        assert cfg.log_level == "TRACE"

    def test_apply_unknown_key_ignored(self):
        """字典中存在未知字段 → 静默忽略。"""
        cfg = FTSConfig()
        _apply_dict(cfg, {"nonexistent_key": 42})
        # 不抛异常，字段不变
        assert not hasattr(cfg, "nonexistent_key")

    def test_apply_none_value_skipped(self):
        """值为 None 的字段被跳过。"""
        cfg = FTSConfig()
        cfg.max_generations = 50
        _apply_dict(cfg, {"max_generations": None, "log_level": "ERROR"})
        # max_generations 因值为 None 被跳过
        assert cfg.max_generations == 50
        assert cfg.log_level == "ERROR"


# ═══════════════════════════════════════════════════════════
# get_config() 全局单例
# ═══════════════════════════════════════════════════════════

class TestGetConfig:
    """get_config() 全局单例初始化。"""

    def test_get_config_returns_instance(self):
        """首次调用返回 FTSConfig 实例。"""
        cfg = get_config()
        assert isinstance(cfg, FTSConfig)
        assert cfg.max_generations == 10

    def test_get_config_singleton(self):
        """多次调用返回同一实例。"""
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_get_config_mutation_persists(self):
        """通过 get_config 修改的状态在后续调用中保持。"""
        cfg1 = get_config()
        cfg1.max_generations = 999
        cfg2 = get_config()
        assert cfg2.max_generations == 999

    def test_get_config_returns_new_after_reset(self):
        """手动重置 _default_config 后 get_config 返回新实例。"""
        import fts.config.settings as _s

        cfg1 = get_config()
        cfg1.max_generations = 888
        _s._default_config = None  # 模拟重置
        cfg2 = get_config()
        assert cfg2 is not cfg1
        assert cfg2.max_generations == 10


# ═══════════════════════════════════════════════════════════
# 集成场景: 多优先级叠加
# ═══════════════════════════════════════════════════════════

class TestConfigPriority:
    """配置优先级叠加验证（环境变量 > YAML > 默认值）。"""

    def test_priority_env_over_yaml_over_default(self, sample_yaml, monkeypatch):
        """环境变量 > YAML > 默认值 优先级链。"""
        monkeypatch.setenv("FTS_MAX_GENERATIONS", "77")  # 最高优先级
        # YAML 中 population_size=100, 无 env var → 使用 YAML 值
        # meta_loop_interval_hours 在 YAML 中 =12, 无 env var → YAML 值
        # micro_trials_per_generation 无 YAML 无 env var → 默认值 50
        cfg = load_config(str(sample_yaml))
        assert cfg.max_generations == 77  # env var
        assert cfg.population_size == 100  # YAML
        assert cfg.meta_loop_interval_hours == 12  # YAML
        assert cfg.micro_trials_per_generation == 50  # 默认值
