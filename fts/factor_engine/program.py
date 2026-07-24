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
  priority_1: 低波因子
  priority_2: 期限结构因子
  avoid: 趋势动量因子
 # 可选优先级: 动量/反转/波动率/持仓量/基差/期限结构/低波/宏观
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
    factor_priority: list[str] = field(default_factory=lambda: ["低波因子", "期限结构因子"])
    factor_avoid: list[str] = field(default_factory=lambda: ["趋势动量因子"])
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

    errors: list[str] = []

    # 解析市场环境
    regime_match = re.search(r"market_regime:\s*(\S+)", content)
    if regime_match:
        config.market_regime = regime_match.group(1)

    # 解析因子偏好
    priority_match = re.search(r"priority_1:\s*(\S+)", content)
    if priority_match:
        config.factor_priority = [priority_match.group(1)]
    priority_2 = re.search(r"priority_2:\s*(\S+)", content)
    if priority_2:
        config.factor_priority.append(priority_2.group(1))
    avoid_match = re.search(r"avoid:\s*(\S+)", content)
    if avoid_match:
        config.factor_avoid = [avoid_match.group(1)]

    # 解析 Agent LLM
    llm_default = re.search(r"default:\s*(\S+)", content)
    if llm_default:
        config.agent_llm_default = llm_default.group(1)

    # 解析各 Agent 覆盖
    for m in re.finditer(r"# (\w+):\s*(\S+)", content):
        agent_name, model = m.group(1), m.group(2)
        if agent_name not in ("必填", "可选"):
            config.agent_llm_overrides[agent_name] = model

    # 解析预算
    daily = re.search(r"daily_tokens:\s*(\d+)", content)
    if daily:
        config.daily_tokens = int(daily.group(1))
    nightly = re.search(r"nightly_tokens:\s*(\d+)", content)
    if nightly:
        config.nightly_tokens = int(nightly.group(1))
    weekly = re.search(r"weekly_portfolio:\s*(\d+)", content)
    if weekly:
        config.weekly_portfolio = int(weekly.group(1))
    max_pt = re.search(r"max_per_factor:\s*(\d+)", content)
    if max_pt:
        config.max_per_factor = int(max_pt.group(1))

    # 解析风险约束
    dd = re.search(r"max_drawdown:\s*([\d.]+)", content)
    if dd:
        config.max_drawdown = float(dd.group(1))
    turn = re.search(r"max_turnover_per_month:\s*([\d.]+)", content)
    if turn:
        config.max_turnover = float(turn.group(1))
    sharpe = re.search(r"min_sharpe:\s*([\d.]+)", content)
    if sharpe:
        config.min_sharpe = float(sharpe.group(1))
    econ = re.search(r"min_economic_logic_score:\s*(\d+)", content)
    if econ:
        config.min_economic_logic_score = int(econ.group(1))

    # 解析熔断确认
    for m in re.finditer(r"\[(x|X| )\]\s*(L[123]) 熔断已审查", content):
        checked, level = m.group(1), m.group(2)
        if checked.lower() == "x":
            config.circuit_breakers_reviewed.append(level)

    config.is_valid = len(errors) == 0
    config.errors = errors
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
