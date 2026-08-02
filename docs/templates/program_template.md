# Program.md 模板说明

## 文件位置

Program.md 是 FTS 的 **L0 人类设定层** 输入文件，默认路径为：

```
memory/program.md
```

可通过 `load_program(path)` 参数指定自定义路径。

## 作用

- **唯一的人类输入接口** — 人类通过此文件设定每周的量化生产计划
- **L0 顶层监督** — 设定市场环境判断、因子偏好、LLM 配置、预算和风险约束
- **熔断恢复确认** — 系统熔断后必须由人类在此文件中确认才能恢复运行
- **超过 14 天未更新**时系统会发出告警

## 解析方式

系统使用 `fts/factor_engine/program.py` 中的正则表达式从 YAML 代码块中提取配置，非严格解析：
- 找不到配置项时使用默认值
- 忽略无法解析的格式错误

## 配置项清单

| 配置项 | 类型 | 默认值 | 说明 |
|:-------|:-----|:--------|:-----|
| `market_regime` | str | 震荡偏多 | 市场环境判断。建议使用 Wind AIFin `market_regime_switch_skill` 获取：进攻→趋势多头，防守→趋势空头，震荡→震荡偏多/偏空，切换→震荡偏空 |
| `factor_preference.priority_1` | str | 动量因子 | 优先使用的因子类型 |
| `factor_preference.priority_2` | str | 质量因子 | 次优先使用的因子类型 |
| `factor_preference.avoid` | str | 反转因子 | 应避免的因子类型 |
| `agent_llm.default` | str | deepseek-chat | 默认 LLM 模型 |
| `agent_llm.*` | str | — | 各 Agent 独立 LLM 配置（可选） |
| `budget.daily_tokens` | int | 50000 | L1 每日感知预算 |
| `budget.nightly_tokens` | int | 200000 | L2 每夜演化预算 |
| `budget.weekly_portfolio` | int | 100000 | L3 每周组合预算 |
| `budget.max_per_factor` | int | 10000 | 单因子最大 token |
| `risk_constraints.max_drawdown` | float | 0.20 | 最大回撤限制 |
| `risk_constraints.max_turnover_per_month` | float | 0.50 | 月最大换手率 |
| `risk_constraints.min_sharpe` | float | 1.5 | 最小夏普比率 |
| `risk_constraints.min_economic_logic_score` | int | 3 | 最小经济逻辑评分 |

## 完整模板

```markdown
# L0 人类设定 — 每周量化生产计划

> 最后更新: {date} | 版本: {version}
> 维护者: 人类

---

## 市场环境评估

```yaml
market_regime: 震荡偏多
# 可选: 趋势多头 / 趋势空头 / 震荡偏多 / 震荡偏空 / 高波 / 低波
# 获取方式: 在 TRAE 中运行 market_regime_switch_skill
#   → 将输出中的"当前市场档位"映射为此字段
#   进攻→趋势多头 / 防守→趋势空头 / 震荡→震荡偏多/偏空 / 切换→震荡偏空
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
```

## 相关代码

- 解析器: `fts/factor_engine/program.py`
- 测试: `tests/factor_engine/test_program.py`
- 解析后的配置类型: `ProgramConfig` (dataclass)

## 获取 market_regime 的操作步骤

1. 打开 TRAE IDE
2. 使用 `market_regime_switch_skill`（Wind AIFin 插件市场状态判档技能）
3. 技能输出包含 **"当前市场档位"** 字段，取值：进攻 / 防守 / 震荡 / 切换
4. 按以下映射填入 `market_regime`：

   | Skill 输出 | market_regime 值 |
   |:-----------|:-----------------|
   | 进攻 | 趋势多头 |
   | 防守 | 趋势空头 |
   | 震荡 | 震荡偏多 或 震荡偏空（根据个人判断） |
   | 切换 | 震荡偏空 |

5. 同时参考技能输出的 **"仓位与风格含义"** 部分，更新 `factor_preference` 的优先级