# FTS 运维与版本管理

> 版本: v2.2.0
> 最后更新: 2026-08-04

---

## 1. 版本历史

| 版本 | 日期 | 说明 |
|:-----|:-----|:-----|
| **v2.2.0** | 2026-08-04 | Phase B 因子泛化优化 — 品种分层训练 + 精英因子全量重验证：新增 `FUTURES_SECTOR_MAP` 产业链分类映射（7 类覆盖所有期货品种）；新增 `FUTURES_STRATIFIED_SUBSET` 分层训练集（19 品种覆盖 7 大产业链）；L2 演化循环使用分层训练集排除盲测品种；新增 `scripts/futures_factor_revalidation.py` 精英因子全量重验证脚本（自动计算全量品种截面 IC、自动降级 CRITICAL 退化因子、输出验证报告到 reports/）；首次重验证自动降级 2 个退化因子（fut_basis_momentum_g1, fut_basis_momentum）；19 个差距全部关闭 |
| **v2.1.0** | 2026-08-04 | Phase A 因子泛化优化 — 盲测品种池 + 单品种 IC 追踪 + 品种级权重分配：新增 `FUTURES_HOLDOUT` 盲测品种池（6 品种覆盖各产业链），L2 演化训练排除盲测品种；新增 `_compute_holdout_validation()` 盲测验证报告（盲测 IC vs 训练 IC 对比 + 保持率警告）；新增 `_compute_per_variety_ic_matrix()` 品种-因子 IC 矩阵（18 因子 × 25+ 品种）；新增 `_compute_per_variety_weights()` 品种级权重分配（全局 Ridge 权重 × 品种 IC 自适应调整）；修改 `_compute_composite_scores()` 支持品种级权重参数；报告新增「品种-因子有效性矩阵 (IC)」章节含 3 个子表；控制台输出品种级 vs 全局排名一致性 Spearman ρ；17 个差距全部关闭 |
| **v2.0.0** | 2026-08-04 | Phase C 逻辑审查 — 因果结构审查 + 持续监控仪表盘：新增因果验证器（causal_validator.py）通过自然实验事件验证因子预测的因果意义（6 个预定义事件，3σ 异常检测，方向一致性校验）；新增逻辑监控仪表盘（logic_monitor.py）覆盖因子行为漂移检测（与动量/均值回归基准的相关性）、极端预测占比报警（>2σ 占比超 5%）、换月日信号异常检测（3σ 阈值）；新增 6 个自然实验事件定义（A 股 4 个 + 期货 2 个）；新增 ~40 个测试用例，1850+ 测试全绿；pyproject.toml 中 fts.__version__ 同步至 v2.0.0 |
| **v1.9.0** | 2026-08-03 | Phase A 演化优化 — UCT 父因子选择 + 失败模式聚类：父因子选择从轮询改为 UCT 树搜索，宏观演化引入失败模式聚类分析注入 LLM prompt，新增 32 个测试用例（test_uct_selection.py 10 + test_failure_pattern.py 22） |
| **v1.8.1** | 2026-08-03 | Market Regime 集成到信号管道：新增 `_build_composite_ohlcv()` 从品种面板构建市场综合 OHLCV，管道调用 `RegimeAwareSelector.detect()` 检测当前市场制度（5 种：bull/bear/high_vol/low_vol/oscillate），控制台输出制度名称+置信度+特征值，报告新增「市场制度」章节含 Regime 调整后的交易建议（趋势友好→放大仓位、震荡→反向操作、高波动→缩小仓位+增量绝对值>0.15）；版本号 1.8.0→1.8.1 |
| **v1.8.0** | 2026-08-03 | 信号管道 v5 多空双向 + 信号增量：管道升级为多空双向排名（按信号强度绝对值排序），新增信号增量追踪（较昨日变化判断趋势加速/衰竭），信号快照 JSON 持久化 + JSONL 历史追加，L3 Portfolio Loop 自动触发信号管道（全量 82 品种），README 拆分股票/期货种子因子；版本号 1.7.3→1.8.0 |
| **v1.7.3** | 2026-08-03 | 信号管道 Ridge 回归加权：`_compute_ridge_weights` 基于 L2 正则化学习因子权重（替代 IC>0.3 硬过滤+等权），弱因子保留不丢弃；新增 21 个测试用例（`tests/test_futures_signal_pipeline.py`）；调度任务修复（`futures_signal_pipeline` 默认注册）；基本面数据测试适配 FundamentalProvider API 变更 |
| **v1.7.2** | 2026-08-03 | 信号管道全量商品池：L3/定时任务改为 `--universe all`（82 品种 FUTURES_SUBSET 剔除金融期货，剔除停更/陈旧品种后 72 品种参与评分）；报告输出品种中文名称（FUTURES_SYMBOL_NAMES）、主力合约代码（get_dominant_contracts：contract_kline 最新交易日最大成交量 + AKShare futures_zh_realtime 持仓量 fallback）、盘中实时价（AKShare 分时最新 close，覆盖 72/72）；新增 8 测试用例（名称映射 + 主力合约判定 + AKShare fallback） |
| **v1.7.1** | 2026-08-03 | 期货全量信号修复：`get_futures_panel()` common_dates 改为多数对齐（≥ 品种数//2），修复 WH0/JR0/RI0/LR0 停更品种清空交集导致方向校正失效的问题；信号管道方向校正改为按日期定位（df.index.get_loc）；管道新增 `--universe all` 支持全量 76 商品期货（FUTURES_SUBSET 剔除金融期货）；信号管道剔除停更/陈旧品种（最新交易日早于共同日期末端）；Elite 因子 MA 计算修复（np.convolve mode='same' 尾部边界 bug → rolling mean）；新增 12 测试用例（test_data_futures_panel.py） |
| **v1.6.0** | 2026-08-03 | 期货自治循环：L1/L2/L3 全自动调度（APScheduler 定时任务）+ 期货基本面数据接入（库存/仓单/基差）+ 信号管道定时任务 + 5 个注册任务（L1:08:30 / L2:23:00 / L3:20:00 / 信号管道:20:30 / 健康检查:每10m）+ 期货全量种子因子库（12 大因子家族 50+ 子因子）+ 期货因子演化脚本 + 顶级因子过滤（IC>0.3）接入信号管道 + 信号报告输出到 reports/{date}/ |
| **v0.1.0** | 2026-07-18 | 从 FDT 剥离，完成 Phase 1-7，220 测试全绿 |
| **v1.5.1** | 2026-08-03 | 期货组合构建与信号生成：L3 PortfolioLoop 构建组合策略（组合夏普 5.43），新增 scripts/futures_signal_pipeline.py 期货横截面信号管道（66 期货 Elite 因子），生成 25 核心品种信号报告（含 Top 20 排名与因子贡献分析） |
| **v1.5.0** | 2026-08-03 | 期货数据接入：新增 FuturesDataProvider（DuckDB kline_cache + AKShare futures_zh_daily_sina），FTSDataProvider 集成 get_futures_ohlcv/get_futures_panel，CLI 扩展 --universe futures 支持期货横截面因子演化，新增 scripts/download_futures.py 断点续传下载脚本，82 个期货品种（25 核心 + 57 全量），3 级数据降级（DuckDB → AKShare → 合成） |
| **v1.4.0** | 2026-08-03 | 基本面/另类/宏观因子加入种子池（482 种子）；新增 FundamentalProvider 数据层 + 23 个基本面种子因子（估值/质量/成长/市值/换手率/宏观/另类复合）；seed_data 新增 fundamental_seeds.py；loader 支持基本面种子加载；1502 测试全绿，99% 覆盖率 |
| **v1.3.2** | 2026-08-03 | 代码审核提升：消除 `_evaluate_and_promote_seeds` 重复横截面逻辑，提取 3 个公共 Mock fixture（`mock_trial`/`mock_optuna_study`/`mock_evolve_micro`）；1432 测试全绿，99% 覆盖率，47/47 模块 100% 覆盖率 |
| **v1.3.1** | 2026-08-03 | 代码审核提升：重构 `parse_program_md` 为数据驱动解析（76→48 行），提取 `_evaluate_cross_section` 方法（178→155 行），拆分 Eager Test；1432 测试全绿，99% 覆盖率，46/47 模块 100% 覆盖率 |
| **v1.3.0** | 2026-08-03 | 国泰君安 191 因子加入种子池（459 种子）；seed_data 新增 gtja191.py；loader 支持 gtja191 批量加载；工程测试全覆盖：1431 测试全绿，46/47 模块 100% 覆盖率，仅余 1 空白行未覆盖 |
| **v1.2.0** | 2026-08-02 | 种子因子集成：世坤 101 因子 + Qlib 158 因子加入种子池（268 种子）；seed_data 目录统一管理；熔断修复（种子评估不计入计数器）；纯多头回测策略；1325 测试全绿，99% 覆盖率 |
| **v1.1.0** | 2026-07-24 | MCP 数据源迁移：Data-Core → akshare(腾讯/东方财富)；移除 6 个期货专用种子因子；CLI 移除 `--universe futures`；默认市场改为 stock；1231 测试全绿 |
| **v1.0.0** | 2026-07-19 | 本地原生部署：进程守护/热重载/HTTP 监控/Windows 服务/CI/CD/E2E 测试/部署文档、1231 测试全绿 |
| **v0.4.0** | 2026-07-19 | EliteFactorTracker、AutoRetireManager、WalkForwardOptimizer、EvaluationChain 走航集成、1104 测试全绿 |
| **v0.3.0** | 2026-07-19 | Data-Core 集成适配、FDT 残留清除、覆盖率提升至 96%、969 测试全绿、原子持久化 |
| **v0.2.0** | 2026-07-18 | CLI 引擎真实调用、Config+memory 目录、Scheduler 引擎、覆盖率提升至 89%、778 测试全绿 |

### 版本号位置

FTS 项目版本号定义在两个位置，变更时必须同步更新：

| 文件 | 字段 |
|:-----|:-----|
| `fts/__init__.py` | `__version__ = "2.2.0"` |
| `pyproject.toml` | `version = "2.2.0"` |

异常引擎内部版本号位于 `fts/factor_engine/__init__.py` 的 `EVOLUTION_VERSION`（当前 v1.1.0），与 FTS 项目版本同步。

---

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

或通过注册的脚本命令：

```bash
fts <command> [options]
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
# 查看版本
python -m fts.cli version

# 健康检查
python -m fts.cli monitor
python -m fts.cli monitor --json    # JSON 格式输出

# 因子演化
python -m fts.cli evolution run --max-generations 20

# 因子管理
python -m fts.cli factor list
python -m fts.cli factor show factor_abc123
```

---

## 4. 状态检查

### 健康监控命令

```bash
python -m fts.cli monitor
```

输出示例：

```
=== FTS System Status ===
Overall healthy : YES
Checked at      : 2026-07-18T10:30:00
FTS version     : 1.1.0
Circuit broken  : NO
Stale (>24h)    : NO
Tokens today    : 0

=== Loop Status ===
[OK]   L1  | status=running          | run_id=run_1658136000_a1b2c3     | age=0.0h
[OK]   L2  | status=completed        | run_id=run_1658136000_d4e5f6     | age=0.0h
[OK]   L3  | status=completed        | run_id=run_1658136000_g7h8i9     | age=0.0h
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

各循环的状态持久化到 `memory/` 目录：

| 循环 | 状态文件 |
|:-----|:---------|
| L1 Meta-Loop | `memory/meta_loop/state.json` |
| L2 Evolution Loop | `memory/evolution/state.json` |
| L3 Portfolio Loop | `memory/portfolio/state.json` |

---

## 5. 定时任务调度器

### 启动方式

```bash
# 后台运行（APScheduler）
python -m fts.cli scheduler run

# 列出所有已注册任务
python -m fts.cli scheduler list
```

### 注册任务清单

| 任务名 | cron 表达式 | 时间 | 说明 |
|:-------|:------------|:-----|:-----|
| `l1_meta_loop` | `30 8 * * *` | 每日 08:30 | L1 Meta-Loop：知识补给 + Bootstrapping + 种子注入 |
| `l2_evolution_loop` | `0 23 * * *` | 每日 23:00 | L2 Evolution Loop：夜间因子演化（LLM + optuna + 横截面） |
| `l3_portfolio_loop` | `0 20 * * *` | 每日 20:00 | L3 Portfolio Loop：组合构建 + 正交化 + 衰减检验 + 信号合成 |
| `futures_signal_pipeline` | `30 20 * * *` | 每日 20:30 | 期货信号管道：独立生成横截面信号报告 |
| `health_check` | `*/10 * * * *` | 每 10 分钟 | 健康检查：监控所有循环状态 |

### 依赖

调度器依赖 APScheduler：

```bash
pip install apscheduler
```

### 降级策略

如果 APScheduler 未安装，`SchedulerEngine.start()` 静默返回 False，所有任务不执行，系统正常运行。

---

## 6. 版本升级流程

### 常规升级步骤

1. **更新版本号**
   - 修改 `fts/__init__.py` 中的 `__version__`
   - 修改 `pyproject.toml` 中的 `version`

2. **更新文档**
   - 在本文件（`07-operations.md`）版本历史中添加新版本记录
   - 如有架构变更，更新 `01-architecture.md`
   - 如有测试变更，更新 `06-testing.md`
   - 如有差距关闭，更新 `08-gap-analysis.md`

3. **同步 README.md**
   - 更新版本徽章
   - 更新测试数和覆盖率
   - 同步 API 使用示例、模块列表、文档链接
   - 确认 13 项 commit 检查清单第 12 项（README 同步）通过

4. **运行测试**
   ```bash
   python -m pytest tests/ --cov=fts --cov-report=term-missing
   ```
   确认全部通过

5. **提交并打标签**
   ```bash
   git tag v0.2.0
   ```

### 版本号变更规则

| 变更类型 | 示例 | 条件 |
|:---------|:-----|:-----|
| MAJOR | v1.0.0 | 重大架构变更 |
| MINOR | v0.2.0 | 功能新增 / 阶段完成 |
| PATCH | v0.1.1 | bug 修复 / 文档更新 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/__init__.py` __version__ = "2.1.0"；`pyproject.toml` version = "2.1.0" |
| 可验证断言 | 版本号 v2.1.0 在 fts/__init__.py 和 pyproject.toml 中一致 |
| 检验方式 | `python -c "import fts; assert fts.__version__ == '2.1.0'"` |
