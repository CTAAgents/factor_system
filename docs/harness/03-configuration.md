# FTS 配置管理

> 版本: v3.1.0+7
> 最后更新: 2026-08-20

---

## 1. 配置层次

FTS 配置采用三级优先级（高→低）：

```
高优先级         环境变量 (FTS_* 前缀)
    ↑           YAML 配置文件 (config/settings.yaml)
    ↑           代码默认值 (FTSConfig dataclass)
低优先级
```

配置权威源头：`fts/config/settings.py`（`FTSConfig` dataclass，全字段带默认值）+ `config/settings.yaml`（YAML 覆盖）+ 环境变量（`FTS_*` 覆盖）。**禁止硬编码业务参数/阈值/约束**，一律经 Pydantic V2 配置模型管理。

---

## 2. 核心配置组（当前生效）

> 完整配置项清单以 `fts/config/settings.py` `FTSConfig` 为权威（含每项默认值与 `FTS_*` 环境变量），此处列当前生效的关键配置组。

### 2.1 市场与数据

| 配置项 | 默认值 | 说明 |
|:-------|:-------|:-----|
| `default_market` | `"futures"` | 全局市场开关（v3.0.0+1 反转回 futures，plans/57 双系统切分后 FTS 因子生产默认面向全部期货 84 品种/17 产业链）；调度任务门控/CLI 默认/仓储路由均跟随 |
| `memory_dir` | `"memory"` | 运行时状态持久化目录 |
| 存储域契约路径 | `docs/harness/_data/storage_landscape.yaml` | `FTS_STORAGE_LANDSCAPE_PATH`（StorageRegistry 加载，13 域） |
| 写路径严格模式 | `"1"` | `FTS_STORAGE_WRITE_STRICT`：FactorRepository 默认路径未登记 storage_landscape 抛 ValueError 阻断；`"0"` 回退告警 |
| `elite_dir` | `"memory/knowledge/factors/futures_elite"` | elite 因子存储目录（默认对齐期货） |

### 2.2 演化引擎（L2）

| 配置项 | 默认值 | 说明 |
|:-------|:-------|:-----|
| `evolution_mode` | `"operator_first"` | 演化模式：operator / operator_first（算子优先，LLM/GP 兜底）/ code / hybrid / batch |
| `max_generations` | 10 | L2 最大演化代数 |
| 演化 CLI 失败率熔断阈值 | 1.0 | `FTS_EVOLUTION_CB_FAILURE_RATE`（1.0=禁用失败率熔断，夜间演化默认跑满世代数；保留 token 与连续低 IC 熔断兜底） |
| `micro_staged_evolution` | true | 微观演化两阶段漏斗（粗筛快速淘汰 + 精筛自适应 trials，GAP-I205） |
| `l2_elite_corr_threshold` | 0.9 | L2 准入去冗余相关性阈值（GAP-I206） |
| `l2_orthogonal_basis_enabled` | true | 多因子正交基底（Gram-Schmidt，GAP-I206 补充） |
| `structure_cluster_quota_enabled` | true | 结构性聚类配额（`structure_cluster_max`=15 / `corr_threshold`=0.85，GAP-077；v2.104.0+25 起 max_per_family 家族配额已删除） |
| `batch_size` / `batch_max_candidates` | 20 / 5 | 批量挖掘漏斗（GAP-I201） |
| `executor_backend` | `"thread"` | 批量粗筛执行器后端：thread/process/dask/ray（GAP-I502，dask/ray 缺依赖降级 process） |

### 2.3 评审质检（plans/59，GAP-161~168）

| 配置项 | 默认值 | 说明 |
|:-------|:-------|:-----|
| `review_mode` | `"auto"` | 审查模式：auto=机审优先 / manual=纯人审（C8-2） |
| `regime_thresholds_enabled` | false | Regime 条件化门槛总开关（OPT-01，开启后机审/IR/月度复检按市场制度乘数调整） |
| `fdr_discount_enabled` | false | 跨运行累积 FDR 折扣总开关（OPT-02，`p_eff = p × discount^retries`） |
| `specific_observe_enabled` | true | 特异因子观察期总开关（OPT-03，20 天观察期满复核固化/撤销/顺延） |
| `data_gate_enabled` | true | 数据质量-评审联动门禁（OPT-05，数据严重异常暂不 approved） |
| `review_sla_enabled` | true | 人审 SLA 自动处置（OPT-06，超 5 天降权 50% / 超 10 天退 L2 冷却池） |
| `param_robust_enabled` | true | 参数稳健区动态化（OPT-07，网格扰动鲁棒区占比，fragile 窄峰一票否决） |
| `liquidity_env_enabled` | true | 容量/交易性评分流动性环境动态化（OPT-08，移仓窗口/价差比缩放） |
| `l3.subchain_quality.enabled` | false | 子链张量化退化检测灰度开关（v2.105.0 起默认 true 已生效；退化检测走单元粒度） |

### 2.4 L3 组合侧（v3.0.0 起已登记退役，配置保留兼容）

| 配置项 | 默认值 | 说明 |
|:-------|:-------|:-----|
| `l3_turnover_penalty` | 0.0 | 组合目标函数换手惩罚系数 λ（GAP-I303） |
| `l3_weight_recompute_cadence` | `"daily"` | L3 权重重算频率（v2.105.0 起仅作用于 PortfolioLoop L3 侧） |
| `l3_turnover_budget_enabled` | false | G3 换手预算分配开关（关闭，由粘性约束 + 换手惩罚双通道兜底） |
| `l3_g1_enabled` | true | G1 同向敞口惩罚开关（`exposure_final = exposure_scale × aligned_scale`） |
| `cross_section_panel_vector` | true | 横截面评估全矩阵化（IC 矩阵化 + 算子原生向量化，plans/37+39） |

### 2.5 数据源与缓存

| 配置项 | 默认值 | 说明 |
|:-------|:-------|:-----|
| `FTS_QUANTDATA_HOME` | — | QuantData 库路径（quantdata_provider 只读短连接直读） |
| 日线 `cache_max_age_days` | 1 | QuantData 每日刷新真实消费（v3.1.0+3 30→1） |
| `tqsdk_sources_enabled` | false | 天勤源显式 opt-in 门控（v3.0.0+2；默认不挂载，主链路 QuantData 无感） |
| `futures_enhance_enabled` | false | 增强源开关（已启用天勤增强基础上追加 IFindSDKSource） |
| `factor_turnover_daily_max` | 0.45 | G11 日换手硬剔除阈值（信号翻转率口径，P95 校准拦 top ~5%） |

### 2.6 L1 知识补给

| 配置项 | 默认值 | 说明 |
|:-------|:-------|:-----|
| `l1_bulk_enabled` | true | 批量三层管线「采集→粗筛→LLM 深读」开关（plans/44） |
| `l1_extractor_max_factors` | 20 | L1 提取器单次 LLM 最大因子数 |
| `l1_embedding_threshold` | 0.30 | 相关性粗筛阈值 |
| `l1_dedup_threshold` | 0.90 | 语义去重阈值 |
| `l1_knowledge_deepread_max` | 60 | 深读子集上限（篇/天，token 预算约束） |
| `l1_l2_backlog_days` | 7 | L1→L2 积压 warning 阈值（天） |

### 2.7 G11 日换手口径说明（信号翻转率）

G11 的"日换手"衡量因子信号本身的变号频率，**不是**期货合约换手率：`turnover_daily = mean(|Δsign(信号)|)/2`，值域 [0,1]（0=从不翻转，1=每日全仓反转）。期货主系统 `factor_turnover_daily_max` 默认 0.45（83 个 active 期货因子真实分布 P95=0.456，仅拦 top ~5% 天天翻仓的极端抖动因子）。

---

## 3. YAML 配置文件

### 3.1 settings.yaml（示例）

```yaml
default_market: "futures"
llm_backend: "openai"
max_generations: 10
evolution_mode: "operator_first"
batch_size: 20
```

### 3.2 品种池/产业链配置（config/futures_universe.yaml，v2.104.0+38）

品种池与产业链分类单一事实源（SSOT），驱动 `fts.data_futures` 全部池常量。加载优先级：**`config/futures_universe.yaml` > 内置默认**（YAML 缺失/损坏/校验失败时回退内置默认并告警）。

```yaml
universe:            # FUTURES_SUBSET（按交易所分组，展平后 82 品种）
core_subset: [...]   # FUTURES_CORE_SUBSET（25）
holdout: [...]       # FUTURES_HOLDOUT（15 盲测池，GAP-055）
stratified_subset: [...]       # FUTURES_STRATIFIED_SUBSET（19 分层训练集）
sector_map: {...}    # FUTURES_SECTOR_MAP 主体（17 产业链）
workflows:
  energy:
    chain_symbols: [...]        # ENERGY_CHAIN_SYMBOLS（12）
    chemical_sectors: [...]     # 泛化范围
    market: energy              # 因子库路由标记
    min_train_rows: 300
    min_holdout_rows: 250
```

校验规则（任一失败即回退内置默认）：universe 无重复、各池 ⊆ universe、盲测池 ∩ 分层训练集 = ∅、泛化范围子链名存在于 sector_map。energy 盲测池自动派生 = 泛化范围全部成员 − 训练池。

---

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
| `max_turnover_monthly` | 5.0 | 最大月度换手率（次/月 = turnover_daily×42）；`turnover_cost_net=True` 时为**成本敏感净收益校验触发线**（换手超线改判成本后净夏普，不再硬剔） |
| `one_side_cost_rate` | 0.0005 | 期货单边往返成本率（5bps，可经 `FTS_COST_*` 实证标定值覆盖） |
| `turnover_cost_net` | True | 成本敏感净收益校验开关（GAP-114 方案 A） |

> 与日换手硬剔除 `factor_turnover_daily_max=0.45` 分层：外层拦极端、内层 verifier 对中高换手因子做成本覆盖判定。

---

## 5. 因子质量评分卡配置

定义在 `fts/config/factor_quality_card_config.py`（`FactorQualityCardConfig`），支持市场专用调整（`get_futures_config()`）。

### 5.1 评分维度权重

| 维度 | 权重 | 维度 | 权重 |
|:-----|:-----|:-----|:-----|
| IC 有效性 | 1.0 | 多样性 | 0.5 |
| 收益性 | 1.0 | 逻辑性 | 0.8 |
| 稳定性 | 0.8 | 实时性 | 0.4 |
| 鲁棒性 | 0.8 | 兼容性 | 0.4 |
| 容量 / 交易性 | 0.6 / 0.8 | | |

### 5.2 分级阈值

| 等级 | 总分阈值 | 说明 |
|:-----|:---------|:-----|
| A 级 | ≥ 35 | 精英因子，直接晋升 |
| B 级 | [25, 35) | 合格因子，可晋升 |
| C 级 | < 25 | 淘汰因子 |

---

## 6. Budget 配置

| 配置 | L1 | L2 | L3 |
|:-----|:---|:---|:---|
| 单次 token 上限 | 60K | 200K | 100K |
| 月度 token 上限 | 1.5M | 6M | — |
| 最大演化代数 | — | 50 | — |
| 熔断 token 比例 | 2.0x | 2.0x | — |
| 连续低 IC/质量熔断 | 5 次 | 3 代 | — |
| 失败率熔断 | 95% | 90% | — |
| 单结构簇最大精英因子数 | — | 15 | — |

## 6.1 因子审计阈值（FactorAuditConfig，模块级 dataclass）

| 配置 | 默认值 | 说明 |
|:-----|:-------|:-----|
| `min_cross_symbol_ratio` | 0.8 | cross_symbol 主防线：≥80% 品种 IC 为正 |
| `min_mean_ic` / `ratio_floor` | 0.05 / 0.6 | 软门控A：平均 IC 强度 + 符号比例下限 |
| `binomial_alpha` | 0.05 | 软门控C：二项检验显著性水平 |
| `min_sector_coverage` | 5 | 软门控D（v3.0.0+7）：板块级覆盖率下限（7 大板块 ≥5 有正 IC 代表） |
| `bonferroni_alpha` / `fdr_alpha` | 0.05 | 多重检验校正显著性 |
| `lookback_max_lag` | 5 | 数据窥探检验最大滞后阶数 |
| `stress_max_drawdown` | 0.40 | 压力场景最大回撤上限 |
| `min_oos_pass_ratio` | 0.5 | OOS 最小窗口通过率 |

## 6.2 标的留出验证（SymbolHoldoutConfig）

| 配置 | 默认值 | 说明 |
|:-----|:-------|:-----|
| `use_blind_pool` | true | 盲测池模式（v3.0.0+7 GAP-160）：holdout 用 FUTURES_HOLDOUT 15 品种；false 回退训练池内分层留出 |
| `min_ic_retention` | 0.5 | IC 保持率下限 |
| `min_train_ic` | 0.05 | 训练集 \|IC\| 下限（弱信号判定不可靠 → skipped） |
| `holdout_ratio` | 0.2 | 训练池内留出模式的分层留出比例 |
| `min_holdout_symbols` | 5 | 留出集最少标的数 |

---

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|:-------------|:-----------|:---------|
| `fts/config/settings.py:FTSConfig` | 所有字段有默认值 | `python -c "from fts.config.settings import FTSConfig; assert hasattr(FTSConfig, 'memory_dir')"` |
| `config/settings.yaml` | YAML 可被 `load_config()` 解析 | `python -c "from fts.config.settings import load_config; cfg = load_config('config/settings.yaml')"` |
| `config/futures_universe.yaml` | 品种池可加载且与内置默认等价 | `python -c "import fts.data_futures as df; assert len(df.FUTURES_SUBSET)==82"` |
| `contracts.py:VerifierConfig` | 默认值与本文档一致 | 手动比对 |
| `fts/store/registry.py` | 存储域注册表契约 | `python -c "from fts.store import StorageRegistry; assert StorageRegistry().validate_contract() == []"` |
