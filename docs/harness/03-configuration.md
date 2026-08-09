# FTS 配置管理

> 版本: v2.62.0
> 最后更新: 2026-08-07

---

## 1. 配置层次

FTS 配置采用三级优先级（高→低）：

```
高优先级         环境变量 (FTS_* 前缀)
    ↑           YAML 配置文件 (config/settings.yaml)
    ↑           代码默认值 (FTSConfig dataclass)
低优先级
```

## 2. 配置项清单

| 配置项 | 类型 | 默认值 | 环境变量 | 说明 |
|:-------|:-----|:-------|:---------|:-----|
| `memory_dir` | str | `"memory"` | `FTS_MEMORY_DIR` | 运行时状态持久化目录 |
| `elite_dir` | str | `"memory/knowledge/factors/elite"` | `FTS_ELITE_DIR` | elite 因子存储目录 |
| `default_market` | str | `"futures"` | `FTS_DEFAULT_MARKET` | 默认市场类型 |
| `llm_backend` | str | `""` | `FTS_LLM_BACKEND` | LLM 后端选择（空=自动检测）|
| `evolution_mode` | str | `"hybrid"` | `FTS_EVOLUTION_MODE` | 演化模式: operator(算子主干) / code(代码创新) / hybrid(混合) |
| `max_generations` | int | 10 | — | L2 最大演化代数 |
| `population_size` | int | 20 | — | 种群大小 |
| `micro_trials_per_generation` | int | 50 | — | 每代 optuna 试验数 |
| `max_workers` | int | 4 | `FTS_MAX_WORKERS` | 并行工作数 |
| `meta_loop_interval_hours` | int | 24 | — | L1 Meta-Loop 间隔 |
| `meta_loop_max_tokens` | int | 8000 | — | L1 单次运行 max token |
| `portfolio_max_factors` | int | 20 | — | L3 组合最大因子数 |
| `portfolio_top_n` | int | 5 | — | L3 Top N 输出 |
| `portfolio_decay_days` | int | 90 | — | L3 衰减检验窗口 |
| `log_level` | str | `"INFO"` | `FTS_LOG_LEVEL` | 日志级别 |
| `log_file` | str | `""` | `FTS_LOG_FILE` | 日志文件路径 |
| `stock_neutralization` | bool | `true` | `FTS_STOCK_NEUTRALIZATION` | 股票因子横截面评估是否做行业/市值中性化（v2.57.0） |
| `industry_map_path` | str | `"data/industry_map.json"` | `FTS_INDUSTRY_MAP_PATH` | 行业映射文件路径（JSON，`{symbol: industry_name}`，v2.57.0） |
| `cap_map_path` | str | `""` | `FTS_CAP_MAP_PATH` | 市值映射文件路径（JSON，`{symbol: market_cap}`，可选，v2.57.0） |
| `futures_adjusted` | bool | `true` | `FTS_FUTURES_ADJUSTED` | 期货连续合约 K 线是否默认返回换月复权序列（因子计算用，v2.58.0） |
| `roll_cost_bps` | float | `2.0` | `FTS_ROLL_COST_BPS` | 展期成本系数（基点/次，回测持仓穿越换月日扣除，v2.58.0） |
| `futures_neutralization` | bool | `true` | `FTS_FUTURES_NEUTRALIZATION` | 期货横截面因子评估是否做板块/产业链中性化（GAP-F03，v2.59.0） |
| `backtest_trade_filter` | bool | `true` | `FTS_BACKTEST_TRADE_FILTER` | 回测是否启用涨跌停拦截 + 停牌过滤（GAP-F02，v2.59.0） |
| `futures_limit_pct` | float | `0.08` | `FTS_FUTURES_LIMIT_PCT` | 期货涨跌停判定阈值（单日涨跌幅 ≥ 该值视为涨跌停，GAP-F02，v2.59.0） |
| `force_walkforward` | bool | `true` | `FTS_FORCE_WALKFORWARD` | 因子晋升路径是否强制 WalkForward 冷启动样本外验证（GAP-F08，v2.60.0） |
| `margin_rate_map` | dict | 见默认表 | —（YAML） | 品种保证金率表（{symbol: 保证金率}，未配置品种用默认 0.10，GAP-F09，v2.60.0） |
| `max_margin_usage` | float | `0.80` | `FTS_MAX_MARGIN_USAGE` | 最大保证金占用率（保证金占用/总权益，超过触发强平风险告警，GAP-F09，v2.60.0） |
| `mcp_enabled` | bool | `false` | `FTS_MCP_ENABLED` | 是否启用 Wind/iFinD MCP 增强字段（启用时若未注入 MCP 客户端抛 RuntimeError 显式报错，未启用则明确降级跳过增强字段，GAP-F04，v2.60.0） |
| `mcp_enabled` | bool | `false` | `FTS_MCP_ENABLED` | 是否启用 Wind/iFinD MCP 增强字段（启用时若未注入 MCP 客户端抛 RuntimeError 显式报错，未启用则明确降级跳过增强字段，GAP-F04，v2.60.0） |

## 3. YAML 配置文件

`config/settings.yaml` 示例：

```yaml
default_market: "futures"
llm_backend: "openai"
max_generations: 10
micro_trials_per_generation: 50
portfolio_max_factors: 20
evolution_mode: "hybrid"   # operator(算子主干) / code(代码创新) / hybrid(混合)
```

> **evolution_mode 说明（Phase C.2）**：取值 `operator`（算子主干）/ `code`（代码创新）/ `hybrid`（混合），默认 `hybrid`，支持环境变量 `FTS_EVOLUTION_MODE` 覆盖。本计划仅落地配置字段（`FTSConfig.evolution_mode` + `config/settings.yaml`），`EvolutionLoop` 对演化模式的分支消费在后续「算子演化引擎」计划中实现。

## 4. Verifier 配置（锁定不可修改）

L2 Verifier 默认配置（定义在 `contracts.py` 中，初始化后锁定）：

| 字段 | 默认值 | 说明 |
|:-----|:-------|:-----|
| `min_ic` | 0.03 | 最小 IC |
| `min_icir` | 0.5 | 最小 ICIR |
| `min_sharpe` | 1.5 | 最小夏普 |
| `max_drawdown` | 0.20 | 最大回撤 |
| `min_economic_score` | 3 | 最小经济逻辑达标维度 |
| `min_t_stat` | 3.0 | 最小 t 统计量 |
| `max_fdr` | 0.05 | 最大 FDR |
| `min_oos_ratio` | 0.30 | 最小样本外比例 |
| `max_turnover_monthly` | 0.50 | 最大月度换手率 |

## 5. 因子质量评分卡配置

因子质量评分卡配置定义在 `fts/config/factor_quality_card_config.py` 中，通过 `FactorQualityCardConfig` 数据类管理。支持市场专用调整，通过 `get_futures_config()` 获取期货专用配置。

### 5.1 评分维度权重

| 维度 | 默认权重 | 说明 |
|:-----|:---------|:-----|
| IC 有效性 (ic_score) | 1.0 | 信息系数 |
| 收益性 (sharpe_score) | 1.0 | Sharpe Ratio |
| 稳定性 (stability_score) | 0.8 | WalkForward 验证 |
| 鲁棒性 (robustness_score) | 0.8 | 跨品种/压力测试 |
| 容量 (capacity_score) | 0.6 | 市场容量 |
| 交易性 (tradability_score) | 0.8 | 换手率评估 |
| 多样性 (diversity_score) | 0.5 | 因子相关性 |
| 逻辑性 (logic_score) | 0.8 | 经济逻辑评分 |
| 实时性 (timeliness_score) | 0.4 | 衰减程度 |
| 兼容性 (compatibility_score) | 0.4 | 组合兼容性 |

### 5.2 评分映射阈值（可配置）

评分映射函数（`_map_ic_to_score`、`_map_sharpe_to_score` 等）支持从配置读取阈值，默认值如下。可通过 `factor_quality_card_config.py` 的 `to_factor_quality_card_config()` 方法输出完整配置字典。

| 映射函数 | 阈值参数 | 默认值 |
|:---------|:---------|:-------|
| IC 映射 | `ic_high/mid/low` | 0.08/0.03/0.01 |
| ICIR 映射 | `icir_high/mid/low` | 1.0/0.5/0.1 |
| Sharpe 映射 | `sharpe_high/mid/low` | 2.0/1.0/0.0 |
| Calmar 映射 | `calmar_high/mid/low` | 2.0/1.0/0.0 |
| 衰减映射 | `decay_mid` | 0.3 |
| 容量映射 | `capacity_high/mid/low` | 50M/10M/1M |
| 换手率映射 | `turnover_high/mid/low` | 0.3/0.5/0.8 |
| 相关性映射 | `corr_high/mid/low` | 0.3/0.5/0.7 |
| 覆盖度映射 | `coverage_high/mid/low` | 0.8/0.5/0.2 |

### 5.3 分级阈值

| 等级 | 总分阈值 | 说明 |
|:-----|:---------|:-----|
| A 级 | ≥ 35 | 精英因子，直接晋升 |
| B 级 | [25, 35) | 合格因子，可晋升 |
| C 级 | < 25 | 淘汰因子 |

## 6. Budget 配置

| 配置 | L1 默认值 | L2 默认值 | L3 默认值 |
|:-----|:----------|:----------|:----------|
| 单次 token 上限 | 50K | 200K | 100K |
| 月度 token 上限 | 1.5M | 6M | — |
| 最大演化代数 | — | 50 | — |
| 熔断 token 比例 | 2.0x | 2.0x | — |
| 连续低 IC/质量熔断 | 5 次 | 3 代 | — |
| 失败率熔断 | 95% | 90% | — |
| 单一家族最大精英因子数 | — | 3 | — |

---

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|:-------------|:-----------|:---------|
| `fts/config/settings.py:FTSConfig` | 所有字段有默认值 | `python -c "from fts.config.settings import FTSConfig; assert hasattr(FTSConfig, 'memory_dir')"` |
| `fts/config/settings.py:load_industry_map` | 行业映射 JSON 可被加载（非 dict 根/文件缺失返回空 dict，空白键过滤） | `python -c "from fts.config.settings import load_industry_map; m = load_industry_map('data/industry_map.json'); assert len(m) > 0"` |
| `config/settings.yaml` | YAML 可被 `load_config()` 解析 | `python -c "from fts.config.settings import load_config; cfg = load_config('config/settings.yaml')"` |
| `contracts.py:VerifierConfig` | 默认值与本文档一致 | 手动比对 |
