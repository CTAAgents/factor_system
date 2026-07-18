"""Test L0 program.md parser + L0 monitor."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from fts.factor_engine.program import (
    DEFAULT_PROGRAM_MD,
    ProgramConfig,
    parse_program_md,
    load_program,
    init_program,
    get_llm_env_overrides,
)
from fts.factor_engine.monitor import check_loop, check_all, LoopStatus, AllStatus


# ─── Program Tests ────────────────────────────────────────

class TestParseProgramMd:
    """program.md 解析器测试。"""

    def test_parses_default_template(self):
        """默认模板应解析出正确的默认值。"""
        config = parse_program_md(DEFAULT_PROGRAM_MD)
        assert config.market_regime == "震荡偏多"
        assert "低波因子" in config.factor_priority
        assert "趋势动量因子" in config.factor_avoid
        assert config.agent_llm_default == "deepseek-chat"
        assert config.daily_tokens == 50000
        assert config.nightly_tokens == 200000
        assert config.max_drawdown == 0.20
        assert config.min_sharpe == 1.5
        assert config.is_valid

    def test_empty_content_uses_defaults(self):
        """空内容应使用全部默认值。"""
        config = parse_program_md("")
        assert config.market_regime == "震荡偏多"
        assert config.daily_tokens == 50000
        assert config.is_valid

    def test_custom_values_override(self):
        """自定义值应覆盖默认值。"""
        content = """
```yaml
market_regime: 趋势多头
budget:
  daily_tokens: 100000
  nightly_tokens: 500000
risk_constraints:
  min_sharpe: 2.0
  max_drawdown: 0.15
```
"""
        config = parse_program_md(content)
        assert config.market_regime == "趋势多头"
        assert config.daily_tokens == 100000
        assert config.nightly_tokens == 500000
        assert config.min_sharpe == 2.0
        assert config.max_drawdown == 0.15

    def test_parses_circuit_breaker_review(self):
        """应正确解析熔断确认复选框。"""
        content = """
- [x] L1 熔断已审查（原因: 低质量候选）
- [ ] L2 熔断已审查（原因: ________）
- [X] L3 熔断已审查（原因: ________）
"""
        config = parse_program_md(content)
        assert "L1" in config.circuit_breakers_reviewed
        assert "L3" in config.circuit_breakers_reviewed
        assert "L2" not in config.circuit_breakers_reviewed

    def test_parses_agent_llm_overrides(self):
        """应解析逐 Agent LLM 配置覆盖。"""
        content = """
```yaml
agent_llm:
  default: deepseek-chat
  # bullish_analyst: claude-sonnet-4
  # bearish_analyst: claude-sonnet-4
```
"""
        config = parse_program_md(content)
        assert config.agent_llm_default == "deepseek-chat"
        assert config.agent_llm_overrides.get("bullish_analyst") == "claude-sonnet-4"

    def test_get_llm_env_overrides(self):
        """环境变量覆盖应正确生成。"""
        config = ProgramConfig(
            agent_llm_overrides={"bullish_analyst": "claude-sonnet-4"},
        )
        env = get_llm_env_overrides(config)
        assert env["FDT_LLM_BULLISH_ANALYST_MODEL"] == "claude-sonnet-4"


class TestLoadProgram:
    """program.md 文件加载测试。"""

    def test_load_existing_file(self, tmp_path: Path):
        fp = tmp_path / "program.md"
        fp.write_text("```yaml\nmarket_regime: 趋势空头\n```")
        config = load_program(fp)
        assert config.market_regime == "趋势空头"
        assert config.is_valid

    def test_load_nonexistent_file(self, tmp_path: Path):
        """不存在的文件应报错。"""
        config = load_program(tmp_path / "nonexistent.md")
        assert not config.is_valid
        assert len(config.errors) > 0

    def test_init_program_creates_file(self, tmp_path: Path):
        fp = tmp_path / "program.md"
        result = init_program(fp)
        assert fp.exists()
        assert "L0" in fp.read_text(encoding="utf-8")
        assert result == str(fp.resolve())


# ─── Monitor Tests ────────────────────────────────────────

class TestCheckLoop:
    """单层循环状态检查测试。"""

    def test_no_state_file(self, tmp_path: Path):
        """不存在的状态文件应返回 unknown。"""
        status = check_loop("L1", tmp_path / "nonexistent")
        assert status.name == "L1"
        assert status.status == "unknown"
        assert not status.exists

    def test_valid_state(self, tmp_path: Path):
        """有效状态文件应正确解析。"""
        state_dir = tmp_path / "meta_loop"
        state_dir.mkdir(parents=True)
        state = {
            "run_id": "run_test123",
            "status": "running",
            "tokens_consumed": 5000,
            "budget_limit": 50000,
            "version": "8.10.0",
            "last_updated": "2026-07-18T10:00:00",
        }
        (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        status = check_loop("L1", state_dir)
        assert status.exists
        assert status.run_id == "run_test123"
        assert status.status == "running"
        assert status.tokens_consumed == 5000

    def test_circuit_broken_detected(self, tmp_path: Path):
        """熔断状态应正确检测。"""
        state_dir = tmp_path / "evolution"
        state_dir.mkdir(parents=True)
        state = {
            "status": "circuit_broken",
            "last_error": "Token 超限",
            "version": "8.10.0",
            "last_updated": "2026-07-18T10:00:00",
        }
        (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        status = check_loop("L2", state_dir)
        assert status.status == "circuit_broken"
        assert status.last_error == "Token 超限"
        assert not status.healthy


class TestCheckAll:
    """全部循环状态检查测试。"""

    def test_all_empty(self, tmp_path: Path):
        """空 FDT 根目录应全部 unknown。"""
        status = check_all(str(tmp_path))
        assert len(status.loops) == 3
        assert not any(l.exists for l in status.loops)

    def test_partial_state(self, tmp_path: Path):
        """部分循环有状态文件。"""
        # 只有 L2 有状态
        evo_dir = tmp_path / "memory" / "evolution"
        evo_dir.mkdir(parents=True)
        (evo_dir / "state.json").write_text(
            json.dumps({"status": "running", "version": "8.10.0", "last_updated": "2026-07-18T10:00:00"}),
            encoding="utf-8",
        )
        status = check_all(str(tmp_path))
        assert status.loops[1].exists  # L2
        assert status.loops[1].status == "running"
        assert not status.loops[0].exists  # L1
        assert not status.loops[2].exists  # L3

    def test_detects_circuit_broken(self, tmp_path: Path):
        """熔断检测。"""
        for name in ("meta_loop",):
            d = tmp_path / "memory" / name
            d.mkdir(parents=True)
            (d / "state.json").write_text(
                json.dumps({"status": "circuit_broken", "last_error": "测试", "version": "8.10.0", "last_updated": "2026-07-18T10:00:00"}),
                encoding="utf-8",
            )
        # L2+L3 正常
        for name in ("evolution", "portfolio"):
            d = tmp_path / "memory" / name
            d.mkdir(parents=True)
            (d / "state.json").write_text(
                json.dumps({"status": "completed", "version": "8.10.0", "last_updated": "2026-07-18T10:00:00"}),
                encoding="utf-8",
            )
        status = check_all(str(tmp_path))
        assert status.any_circuit_broken

    def test_detects_stale(self, tmp_path: Path):
        """过期状态检测（超过 24h）。"""
        d = tmp_path / "memory" / "evolution"
        d.mkdir(parents=True)
        # 48 小时前
        (d / "state.json").write_text(
            json.dumps({"status": "running", "version": "8.10.0", "last_updated": "2026-07-16T10:00:00"}),
            encoding="utf-8",
        )
        status = check_all(str(tmp_path), max_stale_hours=24)
        assert status.any_stale
