# FTS — Factor Intelligence System

> **因子智能系统** — AI 原生的量化因子发现、评估、组合与演化引擎

[![Tests](https://img.shields.io/badge/tests-7830%20passing-blue)](#)
[![Version](https://img.shields.io/badge/version-2.104.0%2B113-blue)](#)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](#)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](#)

> **⚠️ 合规提示 / Compliance Notice**
>
> FTS 以 **Apache License 2.0** 开源，仅用于研究与教育用途，**不构成任何投资建议**。
> 本发行版**不包含** WorldQuant 101 Alphas、GTJA 191 Alphas 等第三方专有因子的实现或复现。
> 使用前请阅读 [免责声明](DISCLAIMER.md) 与 [开源合规指南](COMPLIANCE.md)，并查看 [NOTICE](NOTICE) 中的第三方声明。

---

## 概述

FTS 是一个 AI 原生的量化因子智能系统，通过三层进化循环实现因子的自动化发现、评估、组合与演化：

- **L1 Meta-Loop** — 每日市场感知与知识补给
- **L2 Evolution Loop** — 夜间因子自动演化（LLM 改逻辑 + Optuna 调参）
- **L3 Portfolio Loop** — 组合构建与信号产出

支持期货品种的横截面因子演化（股票市场已剥离至独立项目 fts-stock，v0.0.1，2026-08），内置 6 类因子强制审计、50 分制质量评分卡、APScheduler 全自动定时调度、HTTP 监控端点与 Web UI 仪表盘。

---

## 快速开始

```bash
# 安装
pip install -e .

# 查看版本
fts version

# 查看系统健康状态
fts monitor

# 运行 L1 Meta-Loop（市场感知 + 知识补给）
fts meta-loop run

# 运行 L2 因子演化
fts evolution run --max-generations 10

# 运行 L3 组合构建
fts portfolio run --universe futures

# 列出因子
fts factor list --market futures

# 查看因子血缘
fts factor lineage fct_xxxxxxxx

# 启动 Web UI 仪表盘
fts ui --port 9100

# 列出调度任务
fts scheduler list
```

---

## CLI 命令参考

### 版本与监控

| 命令 | 说明 |
|------|------|
| `fts version` | 打印版本号、引擎版本和配置路径 |
| `fts monitor [--json]` | 检查所有循环健康状态，支持 JSON 输出 |

### L1 Meta-Loop

| 命令 | 说明 |
|------|------|
| `fts meta-loop run [--market {futures,energy}]` | 启动 L1 市场感知与知识补给；`--market energy` 走能源链独立输出，知识输入混入通用期货种子（seeds/futures）+ 能化专属种子（seeds/energy）双线（GAP-121） |

### L2 Evolution Loop

| 命令 | 说明 |
|------|------|
| `fts evolution run [options]` | 启动 L2 因子演化 |

L2 演化参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--max-generations` | 10 | 最大演化代数 |
| `--symbol` | `000001` | 单标的演化的代码 |
| `--universe` | `futures` | 演化市场（`futures`） |
| `--max-stocks` | 0 | 横截面模式最大标的数（0 = 使用全部品种） |
| `--chain` | 空 | 产业链专属工作流：`energy`（能源链专属训练链 + 独立因子库路由，GAP-121） |
| `--symbols` | 空 | 显式品种列表（逗号分隔，如 `SC0,FU0,LU0`；覆盖 `--chain` 默认链品种） |

### 能源产业链专属工作流（GAP-121）

独立于通用期货工作流的链级闭环（因子挖掘 → 质检 → 信号组合 → 综合得分），
以 12 个化工品种为训练链、其余化工产业链 8 品种为链外盲测池，
因子库/精英目录/报告独立隔离。品种池与产业链分类单一事实源：
`config/futures_universe.yaml`（训练池/泛化范围/盲测池派生，改 YAML 即自动换池，
`fts/data_futures` 内置默认兜底，v2.104.0+38）：

```bash
# 0) 数据深度补全（LU0/PR0/PL0 等新上市品种，AKShare 全历史 → kline_cache）
python scripts/sync_energy_chain_depth.py            # 全品种；--symbols LU0,PR0 指定
python scripts/sync_energy_chain_depth.py --dry-run  # 只报告不写库

# 1) 因子挖掘（12 化工品种演化，落 data/factor_catalog_energy.duckdb）
fts evolution run --chain energy --max-generations 10

# 2) 因子质检（12 品种全链质检）
python scripts/verify_qa_workflow.py --chain energy --days 300

# 3) 信号组合 + 综合得分（12 训练 + 8 化工盲测，链外盲测 IC 对比 + 化工链分层泛化）
python scripts/futures_signal_pipeline.py --chain energy --days 120
# 输出: reports/energy_chain/{date}/（信号快照 + 交易建议 + 盲测泛化对比 + 分层）
```

训练链/盲测池再优化（2026-08-15）：扩池 9→12 品种（能源3 SC/FU/BU + 聚酯3 PX/TA/PF +
油化工3 L/PP/PG + 煤化工3 MA/UR/SA，四大化工子链各 3 降相关性），盲测池自动重算为
其余化工链 8 品种；`ENERGY_CHAIN_MIN_TRAIN_ROWS=300` 深度阈值 +
`check_energy_chain_depth()` 审计（排除 SYNTHETIC 合成脏数据）。

### L3 Portfolio Loop

| 命令 | 说明 |
|------|------|
| `fts portfolio run [options]` | 启动 L3 组合构建 |

L3 参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--universe` | `futures` | 组合市场（`futures`） |
| `--synthesis-mode` | 自动 | 信号合成模式（`elastic_net` / `adaptive` / `sharpe_weight` / `equal_weight` / `ml_ensemble` / `optimizer`） |
| `--optimizer-mode` | `risk_parity` | optimizer 目标（`risk_parity` / `mvo`，GAP-L303） |
| `--returns-matrix` | 无 | 因子收益矩阵 CSV 路径（optimizer 模式与组合实测化需要，可选） |
| --force-recompute | 关闭 | 强制全量重算组合权重（GAP-072，默认按 l3_weight_recompute_cadence 判定：daily 每日重算，weekly 仅周五重算） |

### 因子管理

| 命令 | 说明 |
|------|------|
| `fts factor list [--market] [--cluster] [--min-ic] [--min-sharpe] [--diverse] [--json]` | 列出 elite 因子，支持信号聚类簇筛选和多样性选择 |
| `fts factor show <factor_id>` | 查看单个因子详情 |
| `fts factor stats [--market] [--json]` | 因子聚类分布统计（信号相关性分组） |
| `fts factor lineage <factor_id>` | 查询因子演化血缘 |
| `fts factor seeds [--market]` | 列出种子因子 |

### 调度器

| 命令 | 说明 |
|------|------|
| `fts scheduler run` | 启动定时调度器（后台运行） |
| `fts scheduler list` | 列出所有已注册的定时任务 |

### 回测流水线（B.2）

| 命令 | 说明 |
|------|------|
| `fts backtest run <factor_id> [--days] [--capital]` | 单个因子回测 |
| `fts backtest batch [--grade] [--limit] [--days]` | 批量回测 + 对比排名 |
| `fts backtest compare <id1> <id2>` | 两个因子对比回测 |

### 特征工程（C.1）

| 命令 | 说明 |
|------|------|
| `fts feature list [--category]` | 列出特征算子（50 算子 / 7 类） |
| `fts feature analyze <factor_id>` | 特征重要性分析（置换重要性） |
| `fts gp evolve [--generations] [--population]` | GP 演化 |

### 反馈闭环（C.3）

| 命令 | 说明 |
|------|------|
| `fts feedback trigger` | 触发反馈检查（Live 偏离 + 定期评估） |
| `fts feedback process` | 处理待处理反馈事件（归因 + 方向调整） |
| `fts feedback report [--month]` | 生成月度效果报告 |
| `fts feedback stats` | 反馈闭环统计 |

### Web UI

| 命令 | 说明 |
|------|------|
| `fts ui [--host] [--port]` | 启动 Web UI 仪表盘（默认 `127.0.0.1:9100`） |

WorkFlow UI（CTA 手册端到端工作流，v2.104.0+25）：
- 访问 `http://127.0.0.1:9100/workflow` 打开 WorkFlow 看板（React SPA，`web/workflow_ui/dist` 构建产物）
- 工作流页：11 阶段 + 质检闭环节点流（数据基建→因子库挖掘→预处理→IC/IR→Regime→合成→调仓→风控→样本外→仿真→爬坡→质检）、端到端/单动作真实执行、批次历史、阶段日志与 JSON 产物查看
- 质检看板页：QA 7 状态分布 + 观察/暂停预警清单
- API：`GET /api/workflow/stages|runs|runs/{id}|qa/board`、`POST /api/workflow/runs|runs/{id}/run_all|runs/{id}/stage/{s}/action/{a}/run`
- 前端开发：`cd web/workflow_ui && npm run dev`（代理 `/api` → 9100）；构建：`npm run build`

### 信号桥接（Phase 25，v2.39.0）

| 命令 | 说明 |
|------|------|
| `fts bridge serve [--host] [--port]` | 启动 REST 信号服务（默认 `127.0.0.1:8765`，POST /signal 接收 FactorSignal） |
| `fts bridge publish [--protocol] [--input]` | 发布信号到目标协议（`json` / `redis` / `rest`） |
| `fts bridge status [--protocol]` | 查看信号桥接状态（最近信号 / 可用性） |

---

## API 使用

FTS 核心模块可通过 Python API 直接调用：

```python
from fts.factor_engine import EvolutionLoop, PortfolioLoop, MetaLoop
from fts import get_config

# 获取配置
config = get_config()

# L2 因子演化
evo = EvolutionLoop()
result = evo.run(max_generations=10, universe="futures")
print(result.status, result.total_factors_evaluated)

# L3 组合构建
portfolio = PortfolioLoop()
result = portfolio.run(universe="futures", synthesis_mode="sharpe_weight")
print(result.combo_sharpe, result.n_factors_retained)

# L1 Meta-Loop
meta = MetaLoop()
result = meta.run(market="futures")

# 因子查询
from fts.factor_engine import get_default_seed_pool
pool = get_default_seed_pool()
factors = pool.list_factors(market="futures")
```

### 调度器 API

```python
from fts.scheduler import SchedulerEngine, list_tasks

# 查看任务列表
tasks = list_tasks()
for t in tasks:
    print(f"{t.name}: {t.cron_expression}")

# 启动调度器
engine = SchedulerEngine()
engine.start()
```

### 监控 API

```python
from fts.monitor import check_all_status, format_status_report

report = check_all_status()
print(format_status_report(report))
# JSON 输出
from fts.monitor import status_report_to_json
print(status_report_to_json(report))
```

---

## 系统特性

### CTA 手册 WorkFlow 端到端工作流（v2.104.0+25）

按《期货CTA多因子策略标准化作业手册》(v1.3) 编排 11 阶段 + 因子质检闭环，UI 一键驱动 FTS 真实执行：
- **阶段编排**：`fts/workflow/stages.py` 定义 12 节点（数据基建→因子库挖掘→预处理与正交化→单因子 IC/IR→Regime 识别→多因子合成→信号转仓位与风险平价→组合风控→滚动样本外回测→仿真柜台联调→实盘分阶段上线→因子质检闭环），动作映射 `fts cli` 真实命令
- **执行器**：`fts/workflow/executor.py` 单阶段后台线程 subprocess + 端到端顺序推进、失败即停、超时熔断、JSON 产物解析、批次状态按阶段记录汇总同步
- **状态持久化**：`fts/workflow/store.py` SQLite WAL（`data/workflow.db`，workflow_runs + stage_runs 双表，崩溃可回放）
- **前端**：`web/workflow_ui/` React18 + Vite SPA（`fts ui` → `http://127.0.0.1:9100/workflow`）

### 三层进化循环

| 循环 | 调度时间 | 核心职责 |
|------|----------|----------|
| L1 Meta-Loop | 工作日每日 07:59 | 市场感知、知识补给、Bootstrapping、Debate 分析 |
| L2 Evolution | 工作日每日 00:00 | LLM 宏观改逻辑 + Optuna 微观调参、三级评估链、质量评分 |
| L3 Portfolio | 工作日每日 19:00 | 因子筛选、正交化、信号合成、Verifier 校验（GAP-072 与信号管道解绑，equal_weight 权重每日重算） |
| 期货信号管道 | 工作日每日 20:00 | 横截面信号报告（v2.105.0 起因子选择与基础权重由 L3 组合提供，仅信号计算 + Regime 档位缩放权重调整） |

### 6 类因子强制审计

每轮 L2 演化自动执行以下审计，任一未通过则熔断：

| 审计项 | 说明 |
|--------|------|
| 因果检验 | 自然事件因果结构验证 |
| 样本外验证 | Walk-Forward 滚动检验 |
| 跨品种验证 | 不同市场横截面泛化 |
| 压力测试 | 极端行情鲁棒性 |
| 多重检验 | Family-Wise Error Rate 控制 |
| 数据窥探 | 过拟合检测 |

### 50 分制质量评分卡

| 维度 | 分值 | 说明 |
|------|------|------|
| IC 稳定性 | 10 | IC 均值/标准差/衰减 |
| Sharpe 比率 | 8 | 年化夏普及回撤 |
| 换手率 | 6 | 月度换手率 |
| 容量 | 5 | 流动性冲击估算 |
| 频率 | 5 | 信号频率适配 |
| 覆盖率 | 5 | 标的覆盖比例 |
| 鲁棒性 | 6 | 消融/扰动敏感性 |
| 经济逻辑 | 5 | 可解释性评分 |

### 信号合成模式

| 模式 | 适用场景 | 算法 |
|------|----------|------|
| `equal_weight` | 期货（默认） | 等权合成（enable_pca=True 时用 P2 PCA 权重） |
| `sharpe_weight` | 期货（备用） | 因子夏普比率加权 |
| `elastic_net` | 期货（备用） | L1+L2 正则化学习权重 |
| `adaptive` | 通用（v2.56.0） | Sharpe 基权重 + Regime 自适应 style 维度（FactorStyle）+ RegimeSmoother 平滑 |

### Market Regime 自适应

系统实时检测 5 种市场状态并自动调整策略参数：

`bull` / `bear` / `high_vol` / `low_vol` / `oscillate`

- **FactorStyle 维度**（v2.56.0）：`REGIME_STYLE_MULTIPLIERS`（momentum/value/defensive 等 15 风格 × 5 制度）
- **Style 维度调整**：按 FactorStyle 倍率调整，clamp 到 [0.5, 1.5]×base
- **RegimeSmoother**：Regime 切换时权重指数平滑（默认 alpha=0.5, min_days=2）
- **机构级优化（plans/28）**：多周期 HMM 后验概率 `regime_probs` → 概率混合权重（`probability_mix`，关闭/无 probs 回退硬查表）→ RegimeSmoother 不对称切换（`de_risk_alpha`/`re_risk_alpha`）→ 置信度熵标定 `exposure_scale` 仓位缩放（`confidence_scale`，Step 2.5 计算、组合整体缩放）→ BIC 状态数选择（防翻转）→ 制度样本外有效性验证（`validate_regime` CLI）→ `fts_regime_*` 观测指标（/metrics 审计）

### 断路器保护

L2/L3 内置多层熔断机制：

- **低 IC 熔断** — 连续 5 代 IC < 0.005 时暂停演化
- **失败率熔断** — 因子淘汰率超过阈值时触发
- **Token 熔断** — LLM Token 耗尽时降级为纯参数演化
- **数据熔断** — 数据源不可用时自动切换合成数据

---

## 项目结构

```
fts/                          # 核心源码（86 个 Python 文件）
├── config/                   # 配置系统（YAML + 环境变量 + 默认值）
├── core/                     # 核心契约（enums + TypedDict）
├── factor_engine/            # 因子引擎（L1/L2/L3 + 审计 + 评分卡）
│   ├── audit.py              # 6 类因子强制审计
│   ├── factor_quality_card.py# 50 分制质量评分卡
│   ├── evolution_loop.py     # L2 演化主循环（operator/code/hybrid/batch 四模式）
│   ├── batch_mining.py       # 批量挖掘漏斗（GAP-I201：批量生成 + 并行粗筛，v2.65.0）
│   ├── portfolio_loop.py     # L3 组合构建
│   ├── meta_loop.py          # L1 Meta-Loop
│   ├── factor_db/            # 因子数据库（DuckDB）
│   ├── seed_data/            # 种子因子库
│   └── ...                   # 其他引擎模块
├── pipeline/                 # 因子推演管线
├── strategies/               # 策略层
├── scheduler/                # APScheduler 定时调度
├── monitor/                  # 健康监控 + HTTP 端点 + Web UI
├── ml/                       # ML 模型层（LightGBM/XGBoost/Ensemble，v2.38.0）
├── bridge/                   # VNPY 信号桥接层（JSON/Redis/REST，v2.38.0）
├── data.py                   # 数据层统一入口
├── data_futures.py           # 期货数据（DuckDB + AKShare）
├── live_trade/               # 实盘执行链路（orders/stop_orders/intervention/gateway）+ 模拟仓（D.1：contracts/simulated_portfolio/simulated_engine/sqlite_store；D.2：book/matching tick 盘口撮合 + 组合级风控 + PARTIAL/限价单/集合竞价，回放+纸面+SQLite 持久化）
├── llm.py                    # LLM 客户端（OpenAI/Anthropic/Mock）
└── cli.py                    # CLI 统一入口

tests/                        # 120+ 个测试文件，5327+ 个测试用例（含机构级权重学习 28 用例 + L3 调度期货路径 1 用例 + 批量挖掘漏斗 21 用例 + GAP-X01/X02/X03 修复 6 用例 + GAP-L308/L309 收尾 26 用例 + GAP-S09~S12 收尾 27 用例 + GAP-I301/I205 收尾 14 用例 + GAP-I206 收尾 10 用例 + GAP-I206 正交化闭环 10 用例 + GAP-I204 多目标适应度 7 用例 + GAP-L401 corr/cross_section_rank 算子 4 用例 + GAP-F13 漂移告警闭环 9 用例 + GAP-F10 种子库去重 13 用例 + GAP-F15 极值扰动 10 用例 + GAP-054 数据驱动动态池 11 用例 + GAP-055 盲测池机构标准 9 用例 + GAP-F16 覆盖率补齐 341 用例 + GAP-070 兜底家族豁免 2 用例 + v2.98.1 L3 期货路径市场 OHLCV 自动构建 3 用例 + v2.98.2 GAP-071 L2 质检性能 19 用例（test_signal_cache 14 + 走航口径 3 + 审计复用 2） + v2.98.3 股票 L3 早间调度+信号管道联动 3 用例 + v2.100.0 GAP-074 算子演化多样性修复 4 用例 + v2.100.1 L1 感知层样本按市场区分 6 用例（股票→CSI300 成分股 / 期货 13 品种原样） + v2.101.0 GAP-068 多频叠加 22 用例 + GAP-069 持仓拥挤度 15 用例 + GAP-075 跨标的稳健性检查 15 用例（标的留出验证 + cross_symbol 激活） + GAP-076 信号管道截面标准化 10 用例（normalize_signal_matrix 8 + 权重快照 normalize 2） + GAP-078 TQ 探活进程级重试 6 用例（探活缓存/瞬时重试/冷却/恢复） + D.1 模拟仓 18 用例（test_simulated_portfolio.py：开/加/减/平/反手、盯市、风控/干预拦截、因子归因、回放引擎、合约乘数/市场推断、SQLite 存取/恢复/PaperTrader 持久化） + D.2 模拟交易进阶 64 用例（test_book_matching.py 26：tick 盘口撮合/部分成交/限价单/集合竞价；test_neutralization.py 14：行业/市值中性化；test_portfolio_metrics.py 17：组合级风控三级预警；test_calibrate_book_vs_bps.py 7：book vs bps 标定） + D.2 偏差 b Regime 自适应接线 19 用例（test_signal_common.py 17：行业/风格面板聚合构造 6、权重 style 倍率调整 6、全链路集成 1、快照 regime 字段 2、后缀键行业/市值对齐回归 2；test_config_settings.py 2：stock_signal_regime 默认与 env 覆盖） + GAP-083 pre_settle 零依赖派生 18 用例（test_aggregator.py _derive_pre_settle 7 + 缓存接入点 1：settle.shift(1)/0·NaN 回退 close/已有值不覆盖/倒序输入自适应排序；test_backfill_futures_hold.py TestDerivePreSettle 10：跨行派生/有效不覆盖/dry-run/settle 不推进/双格式/异常不阻断/CLI） + GAP-090 存储域注册表 13 用例（tests/store/test_storage_registry.py：YAML 契约加载/路由/契约校验/legacy·planned 血缘约束/env 覆盖/降级） + plans/29 P1 迁移脚本 17 用例（tests/scripts/test_migrate_elite_json_to_catalog.py：差量补齐+status 映射/幂等/sync 漂移同步/dry-run/verify-only/孤儿/市场路由） + plans/29 P2 状态存储与迁移 19 用例（tests/store/test_state_db.py 11：UPSERT/历史追加/快照/持久化读回；tests/scripts/test_migrate_state_to_duckdb.py 8：glob 发现去重/迁移对账/幂等/痕迹归档） + plans/29 P3-A 信号缓存 Parquet 化 5 用例（test_factor_optimizer.py TestFactorSignalCacheParquet：put 写 parquet/磁盘重开读回/checksum 篡改判 miss/.npy 回退重建/clear 双格式清理） + plans/29 P3-B 行情库冷热归档 7 用例（tests/scripts/test_archive_history_cold.py：年份统计/min_year/dry-run/归档-verify 闭环/幂等/不一致检测） + plans/29 P4 读路径切换测试适配（状态类五文件注入临时 StateKVStore fixture 隔离 SSOT：test_evolution_loop/test_meta_loop/test_portfolio_loop/test_stock_pipeline/test_futures_pipeline；test_dynamic_pool.py _EmptySSOT mock 使 JSON 兼容路径可测；test_cli_extra.py factor list 目录模式用例改模拟 DuckDB 不可用回退——DuckDB SSOT 优先、JSON 仅回退，见 07 版本历史） + GAP-088 期货宏观注入端闭环 11 用例（test_macro_panel_injection.py 8：面板级 helper 多标的 5 列注入+跨标的共享只拉一次/发布滞后/字段缺失/单标的失败不阻断/拉取失败降级/cli 横截面演化接线 2；test_futures_signal_pipeline_macro.py 3：信号管道默认开/关闭不调用/异常降级） + plans/28 Regime 机构级 12 用例（test_regime_calibration 3 + test_regime_model_selection 2 + test_regime_validation 3 + test_prometheus_metrics T10 4） + E.3 S2 L4 状态库 SQLite 化 9 用例（tests/store/test_state_db.py 11→14：WAL 生效/写连接存活外部只读不阻塞/upsert 原子回滚/seq 单调/8 线程并发写串行；tests/scripts/test_migrate_state_to_sqlite.py 6：迁移闭环/seq 接续/幂等保护/force 覆盖/源库锁占用降级/源缺失） + E.4 S1 L2/L3 连接生命周期根治 4 用例（tests/store/test_duckdb_lock.py：跨进程写锁互斥/超时/锁文件生命周期/不可重入；受影响模块回归 653 passed——store + data_futures + data_sources 全 + factor_db；L2/L3 写连接短生命周期 + filelock 跨进程互斥 + 读路径 read_only，见 07 版本历史） + v2.103.0+5 L3 权重学习代码加载 SSOT 对齐 4 用例（test_portfolio_loop.py TestBuildFactorCodeMap：内存 code 优先/DuckDB 补拉/JSON 快照兜底/全缺空映射；elastic_net/ml_ensemble 因子代码加载对齐 DuckDB，实测 5 因子全命中、Elastic Net 500 截面回归日不再回退 sharpe_weight）））（注：其中 v2.98.3 股票 L3 早间调度、v2.100.1 股票→CSI300 成分股、test_stock_pipeline、test_neutralization 等股票历史测试记录均已随股票管线剥离至 fts-stock，2026-08）） + v2.103.0+20 新晋级精英因子观察期默认关闭 2 用例（test_evolution_l1_merge.py：默认晋升记录无 shadow_pool / FTS_EVOLUTION_SHADOW_OBSERVE=1 恢复观察 5 交易日；test_portfolio_loop.py TestShadowPool 晋升观察期写入用例改显式 shadow_observe=True 适配新默认语义） + v2.103.0+28 演化失败率熔断默认禁用 + elite_tracker B 级观察期默认关闭 1 用例（test_elite_tracker.py test_b_grade_enters_observing 拆为默认 active + observe_tracker 显式开启；report/get_by_status 断言适配默认 active；cli FTS_EVOLUTION_CB_FAILURE_RATE 默认 1.0） + v2.104.0+13 Verifier 换手成本敏感净收益校验 5 用例（test_verifier.py：成本覆盖放行/成本不足拦截/极端换手净夏普拦截/开关回退绝对硬剔/cost_adjusted 明细字段，GAP-114） + v2.104.0+14 熔断预算传播修复 3 用例（test_evolution_loop.py：重绑传播至 UctSelector/100% 失败率禁用态不熔断/重绑保留熔断态按新阈值触发 + owner 动态读取一致性，GAP-115） + v2.104.0+50 plans/36 因子选择综合评分 15 用例（test_portfolio_loop.py 227→237：综合评分 5 + quality_weight 定权 3 + 滚动 OOS 2；test_factor_clustering.py 43→48：score_map 代表排序 5，聚类阈值参数化 + 簇内 top-N） + v2.104.0+52/53 横截面评估全矩阵化 30 用例（test_panel_vector.py：预对齐面板口径/向量化 IC vs 逐日 spearmanr 对照（NaN 缺口·常数·低覆盖率·随机回归）/完整流水线对照/接入开关 on·off 评估产出一致 + 中性化路径/面板化执行引擎（逐列等价·内部缺口回退·代码因子回退·算子因子接入对照），plans/37） + v2.104.0+67 CAP 数量安全阀与 OOS 校正评分 10 用例（test_portfolio_loop.py 245→255：composite use_oos_ic 3 + TestCapSafetyValve 4 + 聚类先行·安全阀集成 3，GAP-125） + v2.104.0+68 load_elite_factors JSON 兜底 icir 提取 3 用例（test_portfolio_loop.py TestLoadEliteFactors：bt 提取/顶层回退/缺失默认 0.0） + v2.104.0+70 plans/41 L1 知识注入增强 17 用例（test_web_search_extractor.py 8：无 llm 空/暂停空/搜索→LLM 提取/prompt 去标签/搜索失败空/管道注册/max_factors 8/prompt chain_focus；test_meta_loop.py +9：energy 四子链分批/小配额合计守恒/异常回退单批/实时链知识面板注入/面板失败降级/LLM 分批调用携带 chain_focus/futures 单批/预算上调断言，WebSearchExtractor 动态源 + web_collector 感知 + 子链分批 bootstrap，见 07 版本历史） + v2.104.0+71 L1 提取器 max_factors 配置化 3 用例（test_web_search_extractor.py 8→10：默认 20/显式覆盖 30/FTSConfig 注入 35/天软静态感知源不参与；test_config_settings.py +1：默认 20/env 覆盖 30；新增 FTSConfig.l1_extractor_max_factors 配置项——研报/论文/宏观/WebSearch LLM 源统一配额、天软静态感知源不参与，见 07 版本历史） + v2.104.0+88 L3 权重重算因子 L2 评审硬过滤 6 用例（test_portfolio_loop.py TestLoadEliteDuckdb +2：评审驳回剔除/未评审剔除；TestFilterReviewApproved +4：approved 保留/rejected 剔除/unreviewed 剔除/空输入——load_elite_factors 仅放行 factor_reviews.decision=approved 因子参与组合权重重算，rejected/未评审硬剔除，见 07 版本历史） + v2.104.0+111 plans/48 Regime 分层方向 Gate 36 用例（test_regime_gate.py 26：子链方向 Gate 判定/暴露映射分段边界/防双重惩罚/盲测 default + test_portfolio_loop_adaptive +9：因子 subchain_scope 子链 regime 路由/全局回退/概率混合/收益来源族画像 + test_subchain_weight +1：与 plans/47 幅度层调制正交串联——方向层 Gate 管"能否/方向"、幅度层调制管"怎么赚/赚多少"，先方向后幅度，见 07 版本历史） + v2.104.0+112 plans/49 因子×子链质量矩阵 46 用例（test_subchain_quality_store.py 13：质量矩阵建表/UPSERT 幂等/时序查询/latest/recent/build_subchain_quality_rows——t=inf→None 序列化 + test_qa_subchain.py 19：Q10/F6 两级判定（外层跨产业链方向一致+内层子链特异 t 检验护栏+反向子链 avoid）/机审单链特异放行/准入三级权重 + test_lifecycle_subchain.py 14：单元粒度退化全分支（全部失效→degrade/部分链→scope_shrink/单链特异唯一链→degrade）/scope 收缩传导 47 调制矩阵闭环——评审质检与生命周期张量化适配 47/48，GAP-137，见 07 版本历史）
scripts/                      # 工具脚本
config/                       # 项目配置
memory/                       # 运行时持久化（自动创建）
```

---

## 可选依赖

| Extra | 功能 | 安装 |
|-------|------|------|
| `evolution` | Optuna 贝叶斯调参 | `pip install -e ".[evolution]"` |
| `llm` | LLM 客户端 | `pip install -e ".[llm]"` |
| `mcp` | MCP 数据源（AKShare） | `pip install -e ".[mcp]"` |
| `portfolio` | 组合构建（scikit-learn） | `pip install -e ".[portfolio]"` |
| `ml` | ML 模型层（LightGBM/XGBoost） | `pip install -e ".[ml]"` |
| `bridge` | 信号桥接（redis-py） | `pip install -e ".[bridge]"` |
| `regime` | 市场制度检测（hmmlearn/statsmodels） | `pip install -e ".[regime]"` |
| `monitor` | 监控服务（FastAPI/uvicorn） | `pip install -e ".[monitor]"` |
| `data` | 数据层工具（requests/tqdm/pyarrow） | `pip install -e ".[data]"` |
| `dev` | 开发工具 | `pip install -e ".[dev]"` |
| 全部 | 安装所有可选依赖 | `pip install -e ".[all,dev]"` 或 `pip install -r requirements.txt` |

---

## 配置

系统支持三种配置方式，按优先级从高到低：

1. **环境变量** — `FTS_MEMORY_DIR`、`FTS_LOG_LEVEL` 等
2. **YAML 配置** — `config/settings.yaml`
3. **默认值** — 代码内建默认值

```bash
# 环境变量示例
export FTS_MEMORY_DIR=./memory
export FTS_LOG_LEVEL=INFO
```

---

## 运行测试

```bash
# 运行全部测试
python -m pytest tests/ --no-cov --tb=short

# 运行指定模块
python -m pytest tests/factor_engine/ -q

# 查看覆盖率
python -m pytest tests/ --cov=fts --cov-report=term-missing

# 因子质检工作流程端到端验证（CTA 手册第六章）
# 真实数据优先，--synthetic 用合成面板（CI/无网络，确定性）
python scripts/verify_qa_workflow.py --days 300
python scripts/verify_qa_workflow.py --days 150 --synthetic
```

---

## 许可证

本项目基于 **Apache License 2.0** 开源，仅供研究与教育用途，不构成任何投资建议。详见 [LICENSE](LICENSE)。

- [NOTICE](NOTICE) — 版权与第三方声明（Qlib / WorldQuant / GTJA）
- [免责声明](DISCLAIMER.md) — 风险、责任与用户义务声明
- [开源合规指南](COMPLIANCE.md) — 包含项、排除项、种子库构建与第三方数据使用指引