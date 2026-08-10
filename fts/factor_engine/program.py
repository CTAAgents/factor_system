"""
loop_engine/program.py — L0 人类设定层：program.md 解析器

HARNESS §11-loop-engineering.md §14:
    L0 人类设定层 — 每周 30 分钟写 program.md（市场环境/预算/风险约束）

program.md 是 L0 的"唯一人类输入接口"：
    - 人类每周维护一份 YAML-frontmatter + Markdown 文档
    - 解析器自动提取所有配置字段
    - 熔断恢复后必须人类确认 program.md 中的配置

版本: v1.1.0（与 FTS 同步）
"""
# pylint: disable=too-many-instance-attributes,too-many-locals,too-many-branches,too-many-statements

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .contracts import EVOLUTION_VERSION

logger = __import__("logging").getLogger(__name__)

# ─── 默认 program.md 模板 ─────────────────────────────────

DEFAULT_PROGRAM_MD = """# L0 人类设定 — 每周量化生产计划

> 最后更新: {date} | 版本: {version}
> 维护者: 人类

---

## 市场环境评估

```yaml
market_regime: 震荡偏多
# 可选: 趋势多头 / 趋势空头 / 震荡偏多 / 震荡偏空 / 高波 / 低波
```

## 因子偏好

```yaml
factor_preference:
  priority_1: 动量因子
  priority_2: 质量因子
  avoid: 反转因子
 # 可选优先级: 动量/反转/价值/成长/质量/低波/红利/市值/宏观
```

## Agent LLM 配置

```yaml
agent_llm:
  default: deepseek-chat
  # 各 Agent 可独立配置:
  # bullish_analyst: claude-sonnet-4
  # bearish_analyst: claude-sonnet-4
  # judge: deepseek-chat
```

## Token 预算

```yaml
budget:
  daily_tokens: 50000        # L1 每日感知预算
  nightly_tokens: 200000     # L2 每夜演化预算
  weekly_portfolio: 100000   # L3 每周组合预算
  max_per_factor: 10000      # 单因子最大 token
```

## 风险约束

```yaml
risk_constraints:
  max_drawdown: 0.20
  max_turnover_per_month: 0.50
  min_sharpe: 1.5
  min_economic_logic_score: 3
```

## 熔断恢复确认

- [ ] L1 熔断已审查（原因: ________）
- [ ] L2 熔断已审查（原因: ________）
- [ ] L3 熔断已审查（原因: ________）
- [ ] program.md 已更新
- [ ] 确认恢复运行

---

*此文件由人类维护，每周更新一次。超过 14 天未更新时系统应发出告警。*
"""


@dataclass
class ProgramConfig:
    """解析 program.md 后得到的结构化配置。"""

    market_regime: str = "震荡偏多"
    factor_priority: list[str] = field(default_factory=lambda: ["动量因子", "质量因子"])
    factor_avoid: list[str] = field(default_factory=lambda: ["反转因子"])
    agent_llm_default: str = "deepseek-chat"
    agent_llm_overrides: dict[str, str] = field(default_factory=dict)
    daily_tokens: int = 50000
    nightly_tokens: int = 200000
    weekly_portfolio: int = 100000
    max_per_factor: int = 10000
    max_drawdown: float = 0.20
    max_turnover: float = 0.50
    min_sharpe: float = 1.5
    min_economic_logic_score: int = 3
    circuit_breakers_reviewed: list[str] = field(default_factory=list)
    last_updated: str = ""
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)


def parse_program_md(content: str) -> ProgramConfig:
    """解析 program.md 内容为 ProgramConfig。

    支持从 YAML 代码块中提取配置，非严格解析：
    - 找不到配置项时使用默认值
    - 忽略无法解析的格式错误
    """
    config = ProgramConfig()
    config.last_updated = datetime.now().isoformat()

    # ── 数据驱动解析：定义每个字段的匹配规则 ──────────────
    _str_fields: list[tuple[str, str]] = [
        ("market_regime", r"market_regime:\s*(\S+)"),
    ]
    _int_fields: list[tuple[str, str]] = [
        ("daily_tokens", r"daily_tokens:\s*(\d+)"),
        ("nightly_tokens", r"nightly_tokens:\s*(\d+)"),
        ("weekly_portfolio", r"weekly_portfolio:\s*(\d+)"),
        ("max_per_factor", r"max_per_factor:\s*(\d+)"),
        ("min_economic_logic_score", r"min_economic_logic_score:\s*(\d+)"),
    ]
    _float_fields: list[tuple[str, str]] = [
        ("max_drawdown", r"max_drawdown:\s*([\d.]+)"),
        ("max_turnover", r"max_turnover_per_month:\s*([\d.]+)"),
        ("min_sharpe", r"min_sharpe:\s*([\d.]+)"),
    ]

    for attr, pattern in _str_fields:
        m = re.search(pattern, content)
        if m:
            setattr(config, attr, m.group(1))

    for attr, pattern in _int_fields:
        m = re.search(pattern, content)
        if m:
            setattr(config, attr, int(m.group(1)))

    for attr, pattern in _float_fields:
        m = re.search(pattern, content)
        if m:
            setattr(config, attr, float(m.group(1)))

    # 因子偏好（特殊处理：priority_1 + priority_2 + avoid）
    p1 = re.search(r"priority_1:\s*(\S+)", content)
    if p1:
        config.factor_priority = [p1.group(1)]
    p2 = re.search(r"priority_2:\s*(\S+)", content)
    if p2:
        config.factor_priority.append(p2.group(1))
    av = re.search(r"avoid:\s*(\S+)", content)
    if av:
        config.factor_avoid = [av.group(1)]

    # Agent LLM 默认 + 覆盖
    llm_def = re.search(r"default:\s*(\S+)", content)
    if llm_def:
        config.agent_llm_default = llm_def.group(1)
    for m in re.finditer(r"# (\w+):\s*(\S+)", content):
        agent_name, model = m.group(1), m.group(2)
        if agent_name not in ("必填", "可选"):
            config.agent_llm_overrides[agent_name] = model

    # 熔断确认标记
    for m in re.finditer(r"\[(x|X| )\]\s*(L[123]) 熔断已审查", content):
        checked, level = m.group(1), m.group(2)
        if checked.lower() == "x":
            config.circuit_breakers_reviewed.append(level)

    return config


def load_program(path: str | Path = "memory/program.md") -> ProgramConfig:
    """加载并解析 program.md 文件。"""
    fp = Path(path)
    if not fp.exists():
        return ProgramConfig(is_valid=False, errors=[f"program.md 不存在: {fp}"])
    content = fp.read_text(encoding="utf-8")
    return parse_program_md(content)


def init_program(path: str | Path = "memory/program.md") -> str:
    """初始化默认 program.md 模板。"""
    fp = Path(path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    content = DEFAULT_PROGRAM_MD.format(
        date=datetime.now().strftime("%Y-%m-%d"),
        version=EVOLUTION_VERSION,
    )
    fp.write_text(content, encoding="utf-8")
    logger.info("program.md 已初始化: %s", fp.resolve())
    return str(fp.resolve())


def get_llm_env_overrides(config: ProgramConfig) -> dict[str, str]:
    """从 ProgramConfig 生成 FDT_LLM_<NAME> 环境变量覆盖。"""
    env: dict[str, str] = {}
    for agent, model in config.agent_llm_overrides.items():
        upper = agent.upper()
        env[f"FDT_LLM_{upper}_MODEL"] = model
    return env


__all__ = [
    "DEFAULT_PROGRAM_MD",
    "ProgramConfig",
    "parse_program_md",
    "load_program",
    "init_program",
    "get_llm_env_overrides",
]
