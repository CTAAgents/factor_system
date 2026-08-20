# FTS 运维与版本管理

> 版本: v3.1.0+4
> 最后更新: 2026-08-20

---

## 1. 版本历史

> 版本号 = 里程碑版本 + build 段（SemVer build 段制，v2.103.0 修订，见 §6 版本升级流程）。日常开发变更 bump build 段（如 v2.103.0+1），里程碑发布 bump 正式版本（如 v2.104.0）。所有变更均在下方版本历史记录。
> 以下为最近 20 次发布记录，更早开发明细见 §7 历史版本归档说明（git 历史完整保留）。

| 版本 | 日期 | 说明 |
|:-----|:-----|:-----|
| **v3.1.0+4** | **2026-08-20** | **Harness 精简压缩：docs/harness 顶层 12 篇 md 由 ~1.2MB 压缩至 ~118KB（只留现状基线）；历史计划/设计/验收/评审 123 文件归档至 docs/archive/ 并生成索引；CLAUDE.md 141→117 行、AGENTS.md 178→119 行；verify_doc_consistency/update_doc_versions 移除 plans/design 扫描范围、06-testing 断言 4020+→8469；12 处代码 docstring 引用 docs/harness/{plans,design}→docs/archive** |** |
| **v3.1.0+3** | **2026-08-20** | **换月日历根治（plans/60 阶段A落地）：主链路统一消费 QuantData continuous_daily 复权序列——① get_ohlcv QUANTDATA 来源跳过 RollCalendar 二次复权（消除双重复权）；② aggregator 缓存命中保留真实 source（QUANTDATA 已复权 vs TDX 未复权回退）；③ 日线 cache_max_age_days 30→1（QuantData 每日刷新真实消费，不再永久命中旧缓存）；④ _write_cache 幂等覆盖（先删后插，QuantData 覆盖旧 TDX 行）；⑤ fetch_ohlcv 透传 adj_factor。验证：全 88 品种复权序列与 QuantData 0 偏差、346 因子全量信号重算（1655×19×346，中位有效率93.7%）、active 86 因子符号同向率 65.1%；523 测试全绿** |** |
| **v3.1.0+2** | **2026-08-20** | **RollCalendar backfill_days 默认值 5→20：切换日缺价回溯窗口扩大，全库缺价救回率 63%→78%（跳过 127→76，另救回 51 个超5日窗口事件）；文档 04-resilience/06-testing 同步；test_roll_calendar 17 passed** |** |
| **v3.1.0+1** | **2026-08-20** | **fix(RollCalendar): 切换日缺价回溯取价——切换日两合约任一价格缺失（真实单日数据缺口）时向前回溯最近共同交易日（默认5交易日窗口，可配置 backfill_days）取收盘价计算 adj_ratio，避免换月事件被跳过致复权跳空未消除；回溯窗口内仍无共同价格才降级跳过。全库验证：此前跳过的 NI/MA/IF 换月事件全部保留，83 品种换月日历完整。测试 +1（test_missing_prices_backfills_common_day 回溯取价）+1（test_missing_prices_backfill_window_exhausted_skips 窗口耗尽仍跳过），test_roll_calendar 17 passed** |** |
| **v3.1.0** | **2026-08-20** | **发布里程碑 v3.1.0：评审质检体系优化（plans/59 八项全部完成，GAP-161~168 关闭）——Regime 条件化门槛/FDR 折扣/特异观察期/口径收敛/数据门禁/人审SLA/参数稳健区/流动性环境 + 124 新用例，全量回归 8454 passed** |** |
| **v3.0.0+25** | **2026-08-20** | **plans/59 OPT-08 容量/交易性评分流动性环境动态化（GAP-168）：新增 liquidity_env 缩放纯函数 + FactorQualityCard.evaluate liquidity_scale 接入 + 13 测试；plans/59 八项优化全部完成** |** |
| **v3.0.0+24** | **2026-08-20** | **plans/59 OPT-07 参数稳健区动态化（GAP-167）：新增 param_robustness 网格扰动/鲁棒区检测纯函数 + Q3/F3/月度复检三接入点 + 19 测试** |** |
| **v3.0.0+23** | **2026-08-20** | **plans/59 OPT-05 数据质量-评审联动（GAP-165）+ OPT-06 人审 SLA 自动降级（GAP-166）：新增 data_gate/review_sla 纯函数 + review_inplace 数据门禁 + enforce_review_sla 全程机审 + 27 测试** |** |
| **v3.0.0+22** | **2026-08-20** | **plans/59 OPT-04 IC 口径一致性校验（GAP-164）：新增 ic_consistency 纯函数 + review_inplace 接入（漂移转人审）+ 12 测试** |** |
| **v3.0.0+21** | **2026-08-20** | **plans/59 OPT-03 特异因子观察期与 OOS 前瞻复核（GAP-163）：新增 specific_observe 观察期/贝叶斯收缩/OOS 复核纯函数 + 晋升落库标记 + review_specific_observations 复核入口 + 20 测试** |** |
| **v3.0.0+20** | **2026-08-20** | **plans/59 OPT-02 跨运行累积 FDR 折扣（GAP-162）：新增 fdr_discount 折扣纯函数 + evolution_promote 多重检验门接入 + 14 测试** |** |
| **v3.0.0+19** | **2026-08-20** | **docs: 08-gap-analysis 全量瘦身校正——删除 v2.104.0 前关闭的早期差距条目与第 3 节差距详情、重编号冲突（GAP-152/153→176/177、GAP-154~158→169~173、L系列 161/162→174/175）、统一总览口径并跑通 verify_doc_consistency** |** |
| **v3.0.0+18** | **2026-08-20** | **plans/59 OPT-01 Regime 条件化门槛（GAP-161）：新增 regime_thresholds 乘数查表 + AutoReviewPolicy/IR 分类门槛/月度M2/F5 四接入点 + 19 测试** |** |
| **v3.0.0+17** | **2026-08-20** | **fix: SC0 贴水采集脚本骨架（scripts/collect_specific_fields.py，manual csv/json 导入→parquet 幂等 upsert，placeholder 报告位）+ 预存失败 29 项修复——① pandas3 read-only：factor_returns.max_abs_correlation / portfolio_walk_forward._max_corr 改 to_numpy(copy=True)（连带修复 portfolio_loop measured 7 项）；② v3.0.0+1 市场反转断言：test_promote_success/test_default_database_path/tqsdk 默认解耦；③ v2.105.0+16 energy 训练池 12→14（含橡胶）；④ GAP-160 holdout_panel 最小装配补齐（g11/g4 5 处）；⑤ GAP-149 状态枚举 decaying→degraded；⑥ regime_features 常量品种相关 NaN→0；⑦ l3_signal_service 因子代码 ndarray 兼容；测试 +5（collect_specific_fields）** |** |
| **v3.0.0+16** | **2026-08-20** | **feat(GAP-161/162): 演化生成端品种级通道 + 品种特有数据源真实接入——GAP-161 评估链 _detect_symbol_candidates 探测（跨子期稳定单品种，宁漏标不误标）+ 晋升 Verifier 品种级放行（symbol_candidates 豁免 IC/ICIR 稀释）+ 落库 scope 升级 kind=symbol；GAP-162 specific_fields 真实 parquet 加载通道（date 对齐注入，缓存缺失降级不阻断）+ SC0.sc_freight_premium 首个启用字段 + settings specific_fields_cache_dir；测试 +4（探测 2/真实注入 1/缺失降级 1）** |** |
| **v3.0.0+15** | **2026-08-20** | **feat(P3): 品种特有数据源通道框架——config/specific_fields.yaml 注册表（SC0/AU0/EC0 占位条目）+ scope_domain/specific_fields.py 加载器（enabled 过滤/注入降级不阻断）+ settings specific_fields 配置（默认关）+ llm.py symbol_focus 品种级知识聚焦 prompt 块 + storage_landscape 登记 specific_fields 域（planned）** |** |
| **v3.0.0+14** | **2026-08-20** | **feat(P2): 品种级特异因子通道——evaluate_symbol_scope 品种域评估 + symbol_scope_guard 真伪鉴别护栏（样本窗/跨子期/显著性，宁漏标不误标）+ 晋升 scope_pending 过渡保护（无画像全链低质转人审不误杀）+ 评审过渡保护 + Q10 品种级分支（护栏通过才判品种特异）+ 信号契约 v2（factor_scope 支持 kind=symbol/evidence，schema_version=2 幂等，v1 兼容）+ test_scope_contract_v2 6 用例** |** |
| **v3.0.0+13** | **2026-08-20** | **feat(P1): 特异因子 futures 全链对齐——GAP-144 子链放行去 energy 限制（futures sector_map 17 链生效，域内 IC 优先于 effective 子链画像）+ L1 注入按 futures 17 链分批（chain_focus_batches 统一加载，原仅 energy）+ subchain_eval 扩展 futures（_FuturesEvalAdapter 复用逐品种 IC 算法）；meta_loop 测试改 futures 分批断言** |** |
| **v3.0.0+12** | **2026-08-20** | **feat(P0): scope 域评估模块——特异因子全流程适配第一步：新建 fts/factor_engine/scope_domain（types/resolver/evaluator/guard/hooks，域内 IC/Sharpe/子期一致/显著性护栏），评估链产出 domain_stats + 晋升落库 metadata.scope_domain + 评审域内门禁（AutoReviewPolicy domain_stats 入参）+ energy 子链 YAML 化（workflows.energy.sub_symbols 替换 ENERGY_CHAIN_SUB_SYMBOLS 硬编码）；开关 FTS_SCOPE_DOMAIN_ENABLED 默认开启可回退；test_scope_domain 19 用例** |** |

## 2. 安装方式

### 基础安装

```bash
# 从项目根目录安装
pip install .

# 带可选依赖
pip install .[evolution]    # 带 optuna 演化支持
pip install .[llm]          # 带 LLM 支持
pip install .[mcp]         # 带 MCP 数据支持（akshare 腾讯/东方财富）
pip install .[dev]          # 带开发工具（pytest）
pip install .[evolution,llm,mcp,dev]  # 全部
```

### 核心依赖

| 依赖 | 版本要求 | 用途 |
|:-----|:---------|:-----|
| numpy | >=1.24 | 数值计算 |
| pandas | >=2.0 | 数据处理 |
| pyyaml | >=6.0 | YAML 配置读取 |
| duckdb | — | 行情库/因子资产库权威存储 |

### 可选依赖

| extra | 依赖 | 用途 |
|:------|:-----|:-----|
| evolution | optuna>=3.0 | 贝叶斯调参 |
| llm | openai>=1.0, anthropic>=0.20 | LLM 因子演化 |
| data | datacore | Data-Core 数据接入 |
| dev | pytest>=7.4, pytest-cov>=4.1 | 测试工具 |

---

## 3. CLI 入口

### 统一入口

```bash
python -m fts.cli <command> [options]
```

### 子命令列表

| 子命令 | 选项 | 说明 |
|:-------|:-----|:-----|
| `version` | — | 打印版本号 |
| `monitor` | `--json` | 检查所有循环健康状态 |
| `evolution run` | `--max-generations N`, `--universe {single,csi300,futures}`, `--max-stocks N` | 启动 L2 因子演化主循环（支持单标/沪深300/期货横截面） |
| `meta-loop run` | — | 启动 L1 Meta-Loop |
| `portfolio run` | — | 启动 L3 组合构建 |
| `scheduler run` | — | 启动调度器后台运行（APScheduler） |
| `scheduler list` | — | 列出所有已注册定时任务 |
| `factor list` | `--elite-dir PATH` | 列出 elite 因子 |
| `factor show <factor_id>` | `--elite-dir PATH` | 查看单个因子详情 |

### 使用示例

```bash
python -m fts.cli version
python -m fts.cli monitor                # 健康检查
python -m fts.cli monitor --json         # JSON 格式输出
python -m fts.cli evolution run --max-generations 20
python -m fts.cli factor list
python -m fts.cli factor show factor_abc123
```

---

## 4. 状态检查

### 健康监控命令

```bash
python -m fts.cli monitor
```

### 监控指标

| 指标 | 说明 | 告警阈值 |
|:-----|:-----|:---------|
| healthy | 整体健康状态 | False = 告警 |
| circuit_broken | 熔断状态 | True = 紧急 |
| stale | 超过 24h 未更新 | True = 告警 |
| age_hours | 距上次运行小时数 | >24h = stale |
| tokens_consumed | Token 消耗 | 按 budget 阈值 |
| status | 运行/暂停/完成/熔断 | circuit_broken = 紧急 |

### 状态文件位置

各循环的状态持久化到 `memory/` 目录（v2.101.0 起按市场隔离）：

| 循环 | 状态文件 |
|:-----|:---------|
| L1 Meta-Loop | `memory/meta_loop/{market}/state.json`（market ∈ futures/stock） |
| L2 Evolution Loop | `memory/evolution/{market}/state.json` |
| L3 Portfolio Loop | `memory/portfolio/{market}/state.json` |

---

## 5. 定时任务调度器

### 启动方式

```bash
# 后台运行（APScheduler）
python -m fts.cli scheduler run

# 列出所有已注册任务
python -m fts.cli scheduler list
```

### 调度承载

v3.0.0 起（plans/57 双系统切分），FTS 因子生产的 L1/L2/评审/监控定时任务由 **TRAE Schedule 自动化**承载，全部面向全期货（默认市场 futures，84 品种/17 产业链），统一走 `fts.scheduler.jobs` 通用 job：`l1_meta_loop_job()` / `l2_seed_promotion_job()` / `l2_evolution_weekday|weekend_job()` / `l2_review_job()` / `import_external_factors_job()`。APScheduler 保留为本地后备执行器（`scheduler run` / `scheduler list`），注册任务为全期货口径。

### 依赖与降级

调度器依赖 APScheduler；未安装时 `SchedulerEngine.start()` 静默返回 False，所有任务不执行，系统正常运行。

---

## 6. 版本升级流程

### SemVer build 段制（v2.103.0+）

**版本号 = 里程碑版本 + build 段：`x.y.z[+N]`**。日常开发 bump build 段（反映每次变更），可交付发布 bump 正式版本（patch/minor/major）。两者统一走 bump 脚本：

```bash
# 1. 日常修改（GAP 实现 / bugfix / 测试补充 / 文档同步 / 数据修复）→ build 段 +1
python scripts/bump_version.py --build --message "本次修改的变更说明"
#    自动完成：pyproject.toml version → README 徽章 → 07-operations.md 追加条目 → 同步全部文档版本头

# 2. 里程碑发布（可交付：晋级里程碑完成 + 全量回归通过）→ patch/minor/major（build 清零）
python scripts/bump_version.py --type patch --message "本次发布的变更说明"
python scripts/bump_version.py --type minor --message "本次发布的变更说明"

# 3. 验证一致性
python scripts/bump_version.py --check     # 单日护栏（仅里程碑）：今日已里程碑 bump 则 exit 1
python scripts/verify_doc_consistency.py
```

**单日护栏（仅约束里程碑 bump）**：同一天最多做一次里程碑 bump（`--type`）。并发会话同日冲突时，后到者将变更**追加到当日已有版本条目**，不产生新版本号；确需新里程碑版本才使用 `--force`（不推荐）。**build bump（`--build`）不受护栏约束**，可随时多次执行。

**禁止手工修改 pyproject.toml 版本号**——统一通过 `scripts/bump_version.py` 执行，保证 07-operations.md、README 徽章与文档版本头同步。

### 版本号变更规则

| 变更类型 | 示例 | 条件 |
|:---------|:-----|:-----|
| MAJOR | v3.0.0 | 重大架构变更 / 不兼容接口变更 |
| MINOR | v2.104.0 | 新功能 / 晋级里程碑完成 |
| PATCH | v2.103.1 | 发布后修复（bugfix） |
| BUILD | v2.103.0+1 → +2 → +3 | 日常开发（GAP 实现 / 测试 / 文档 / 数据修复），build 段 +1，里程碑版本不变 |

> 注意：日常开发（文档更新、测试补充、内部重构、数据修复）不触发正式版本号变更，仅 bump build 段（`--build`）。

### 版本号统一管理规范（v2.22.0+）

FTS 从 v2.22.0 起使用 **单一真实源（Single Source of Truth）** 管理版本号：

1. **唯一源头**：`pyproject.toml` 中的 `version` 字段
2. **代码动态读取**：`fts/__init__.py` 通过 `tomllib`/`tomli` 从 `pyproject.toml` 读取，避免手动同步
3. **文档自动同步**：`scripts/update_doc_versions.py` 自动扫描并更新所有 Harness 文档的版本头
4. **CI 检查**：`scripts/verify_doc_consistency.py` 内置版本号一致性检查，commit 前自动执行

**操作流程**：

```bash
python scripts/update_doc_versions.py --apply      # 同步文档版本号
python scripts/verify_doc_consistency.py           # 验证一致性
python scripts/verify_doc_consistency.py --fix-versions  # 自动修复版本号不一致
```

---

## 7. 历史版本归档

> v2.103.0 之前按「每 GAP/会话 bump」记录的 127 条开发明细（v0.2.0 → v2.101.0 前）已不再维护，完整内容由 git 历史保留，可按需 `git log` 回溯。
> 自 v2.103.0 起版本历史统一记录于 §1（日常开发 bump build 段、里程碑发布 bump 正式版本），本段不再增长。

---

## 一致性元数据

| 字段 | 值 |
|:-----|:---|
| 代码→文档映射 | `scripts/bump_version.py` → 07-operations.md §1 版本历史表（表头分隔行 `|:` + 条目格式 `| **v..** | **date** |`）；`fts/cli.py` → §3 CLI 入口 |
| 可验证断言 | §1 版本历史表存在表头分隔行；`fts/__init__.py` 存在 `__version__` |
| 检验方式 | `python scripts/bump_version.py --peek`（可解析版本）；`python scripts/verify_doc_consistency.py`（版本号一致性 PASS） |
