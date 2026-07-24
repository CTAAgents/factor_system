# FTS 配置管理

> 版本: v1.1.0
> 最后更新: 2026-07-24

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
| `default_market` | str | `"stock"` | `FTS_DEFAULT_MARKET` | 默认市场类型 |
| `llm_backend` | str | `""` | `FTS_LLM_BACKEND` | LLM 后端选择（空=自动检测）|
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

## 3. YAML 配置文件

`config/settings.yaml` 示例：

```yaml
default_market: "stock"
llm_backend: "openai"
max_generations: 10
micro_trials_per_generation: 50
portfolio_max_factors: 20
```

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

## 5. Budget 配置

| 配置 | L1 默认值 | L2 默认值 | L3 默认值 |
|:-----|:----------|:----------|:----------|
| 单次 token 上限 | 50K | 200K | 100K |
| 月度 token 上限 | 1.5M | 6M | — |
| 最大演化代数 | — | 50 | — |
| 熔断 token 比例 | 2.0x | 2.0x | — |
| 连续低 IC/质量熔断 | 5 次 | 3 代 | — |
| 失败率熔断 | 95% | 90% | — |

---

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|:-------------|:-----------|:---------|
| `fts/config/settings.py:FTSConfig` | 所有字段有默认值 | `python -c "from fts.config.settings import FTSConfig; assert hasattr(FTSConfig, 'memory_dir')"` |
| `config/settings.yaml` | YAML 可被 `load_config()` 解析 | `python -c "from fts.config.settings import load_config; cfg = load_config('config/settings.yaml')"` |
| `contracts.py:VerifierConfig` | 默认值与本文档一致 | 手动比对 |
