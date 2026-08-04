# FTS 系统架构文档

> 版本: v2.0.0
> 最后更新: 2026-08-04

---

## 1. 项目概述

FTS（Factor Intelligence System，因子智能系统）是一个独立的因子策略系统，专注于因子推演、策略组建与交易信号产出。数据层基于腾讯自选股 MCP (akshare) 提供 A 股/ETF/期货行情数据，FTS **本身包含自洽的数据源适配层**，无外部数据项目依赖。

### 项目边界

| 职责 | 归属 |
|:-----|:-----|
| 行情数据获取（A 股/ETF OHLCV） | **FTS（通过 MCP/akshare 接入腾讯/东方财富 API）** |
| 行情数据获取（期货 OHLCV） | **FTS（通过 DuckDB kline_cache + AKShare futures_zh_daily_sina）** |
| 因子推演（挖掘/演化/评估） | **FTS 核心能力** |
| 多因子策略组建 | **FTS 核心能力** |
| 交易信号产出 | **FTS 核心能力** |
| 循环调度与状态管理 | **FTS 核心能力** |
| 健康监控与 HTTP 指标 | **FTS 核心能力** |

## 2. 分层架构

FTS 采用 5 层分层架构，从高层的人类设定到底层的组合执行：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          入口层 (Entry Layer)                           │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ cli.py       │  │ scheduler/       │  │ monitor/                 │  │
│  │ 统一命令行入口  │  │ 定时任务调度       │  │ 系统健康监控 + HTTP 端点  │  │
│  └──────┬───────┘  └────────┬─────────┘  └───────────┬──────────────┘  │
└─────────┼───────────────────┼─────────────────────────┼────────────────┘
          │                   │                         │
          ▼                   ▼                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    L0 人类设定层 (Human Configuration)                   │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ program.py (Program.md)                                         │   │
│  │ 人类通过 Program.md 文件设定因子演化的目标、约束、市场偏好、       │   │
│  │ 风险偏好等最高层级指令。L1/L2/L3 均受 program.md 约束。          │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
└─────────────────────────────┼──────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│    L1 Meta-Loop (元循环 — 知识感知与市场监控层)                          │
│                                                                         │
│  meta_loop.py                       experience_chain.py                │
│  - BootstrappingChain（市场知识补给）  - 经验链存储                       │
│  - DebateQualityAnalyzer（辩论质量分析）                                 │
│  - FactorPoolManager（因子池管理）                                      │
│  - L1Verifier（L1 锁定协议）                                           │
│  - MetaStateManager（状态管理）                                         │
│                                                                         │
│  职责: 每日知识补给 → 种子因子注入 → 市场语境感知 → 演化方向指引        │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │ 注入种子因子 + 演化方向
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  L2 Evolution Loop (演化循环 — 因子核心演化层)                           │
│                                                                         │
│  parent_selection  →   macro_evolution    →   micro_evolution    →   evaluation_chain        │
│  (UCT 树搜索)           (LLM 改逻辑 + 失败模式聚类)  (optuna 调参)          (三级评估链)              │
│                                                                         │
│  evolution_loop.py — L2 主循环协调器                                   │
│  seed_pool.py — 种子池（482 个因子：9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面/另类/宏观 + L1 注入接口）
│  ├── seed_data/               # 种子因子定义库
│  │   ├── wq101.py             # 101 个 WorldQuant Alpha 因子
│  │   ├── qlib158.py           # 158 个 Qlib 因子
│  │   ├── gtja191.py           # 191 个国泰君安 Alpha 因子
│  │   ├── fundamental_seeds.py # 23 个基本面/另类/宏观因子
│   │   ├── alpha_ops.py        # 公共操作库
│   │   └── loader.py           # 动态加载器：因子定义 → FactorProgram（含基本面支持）
│   │   └── __init__.py          # 统一导出入口                    │
│  factor_program.py — 因子程序（图灵完备代码 + 安全沙箱）                  │
│  verifier.py — Verifier 锁定协议                                       │
│  state.py — 演化状态管理 + trace_id 全链路                              │
│                                                                         │
│  职责: 夜间批量演化 → UCT 父因子选择 → LLM 逻辑改造(含失败模式聚类) → optuna 参数优化 → 三级评估 → elite │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │ elite 因子
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  L3 Portfolio Loop (组合循环 — 组合构建与信号产出层)                     │
│                                                                         │
│  portfolio_loop.py                                                     │
│  - PortfolioManager（组合管理器）                                       │
│  - orthogonalize_factors（因子正交化）                                  │
│  - decay_test（衰减检验）                                              │
│  - build_combo（构建组合）                                             │
│  - synthesize_signals（信号合成）                                      │
│  - generate_agent_proposals（Agent 提案生成）                          │
│  - load_elite_factors（加载 elite 因子）                               │
│  - L3Verifier（L3 锁定协议）                                           │
│                                                                         │
│  职责: 组合构建 → 正交化 → 衰减检验 → 信号合成                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 层间交互

- **L0 → L1**: Program.md 设定 L1 的搜索空间、预算、市场偏好
- **L1 → L2**: 注入种子因子 + 演化方向指引（通过 seed_pool.inject()）
- **L2 → L3**: 产出 elite 因子（写入 memory/knowledge/factors/elite/）

---

## 3. 模块结构

```
fts/
├── fts/__init__.py                 # 包入口 + 版本号 v1.7.0
├── cli.py                      # 统一命令行入口
├── data.py                     # 数据层（MCP + 期货 + 期货基本面统一入口）
├── data_mcp.py                 # MCP 数据适配层（akshare 腾讯/东方财富）
├── data_futures.py             # 期货数据适配层（DuckDB kline_cache + AKShare）
├── data_futures_fundamental.py # 期货基本面数据（库存/仓单/基差 — AKShare）
├── llm.py                      # LLM 客户端（OpenAI/Anthropic/Mock）
├── config/                     # 配置系统
│   └── settings.py             # FTSConfig + load_config()
├── core/                       # 核心契约层
│   ├── contracts.py            # TypedDict 契约 re-export
│   ├── atomic.py               # 原子文件操作
│   └── enums.py                # 枚举定义
├── factor_engine/              # 因子引擎（核心模块）
│   ├── __init__.py             # 模块入口 + 版本号 v1.1.0
│   ├── contracts.py            # 完整 TypedDict 契约（L1+L2+L3）
│   ├── evolution_loop.py       # L2 主循环
│   ├── meta_loop.py            # L1 元循环
│   ├── portfolio_loop.py       # L3 组合循环
│   ├── macro_evolution.py      # LLM 宏观演化
│   ├── micro_evolution.py      # optuna 微观调参
│   ├── evaluation_chain.py     # 三级评估链
│   ├── experience_chain.py     # 经验链存储
│   ├── seed_data/              # 种子因子定义（WQ101 + Qlib158 + GTJA191）
│   │   ├── __init__.py
│   │   ├── wq101.py            # 101 个 WorldQuant Alpha 因子
│   │   ├── qlib158.py          # 158 个 Qlib 因子
│   │   ├── gtja191.py          # 191 个国泰君安 Alpha 因子
│   │   ├── alpha_ops.py        # 公共操作库
│   │   └── loader.py           # 动态加载器
│   ├── seed_pool.py            # 种子池（482 个因子，含 WQ101 + Qlib158 + GTJA191 + 基本面外部种子）
│   ├── factor_program.py       # 因子程序（安全沙箱）
│   ├── verifier.py             # Verifier 锁定协议
│   ├── state.py                # 演化状态管理
│   ├── program.py              # L0 人类设定（Program.md）
│   ├── walk_forward.py         # 走航验证
│   ├── cost_model.py           # 交易成本模型
│   ├── regime.py               # 市场制度检测
│   ├── stress_test.py          # 压力测试
│   ├── ablation.py             # 输入敏感性消融实验（Phase A 逻辑审查）
│   ├── shap_analyzer.py        # SHAP 局部可解释性分析（Phase B 逻辑审查）
│   ├── robustness.py           # 鲁棒性审查（Phase B 逻辑审查）
│   ├── causal_validator.py     # 因果结构审查（Phase C 逻辑审查）
│   └── monitor.py              # 循环监控
├── pipeline/                   # 因子推演管线
│   ├── base.py                 # FactorPipeline 抽象基类
│   └── factor_combiner.py      # 因子组合器
├── strategies/                 # 策略层
│   ├── base_v2.py              # BaseStrategyV2
│   ├── multi_factor_strategy.py# 多因子策略
│   └── strategy_evolution.py   # 策略进化（动态因子权重/市场制度自适应/多周期信号融合）
└── monitor/                    # 健康监控
    ├── __init__.py             # 状态报告函数
    ├── http_server.py          # HTTP 监控端点
    ├── elite_tracker.py        # Elite 因子追踪
    └── logic_monitor.py        # 逻辑监控仪表盘（Phase C 逻辑审查）
├── scheduler/                   # 调度层
│   ├── __init__.py             # 模块入口 + 导出
│   ├── engine.py               # SchedulerEngine（APScheduler 包装器）
│   ├── tasks.py                # TaskRegistry + TaskSpec + 注册默认任务
│   └── jobs.py                 # 任务工作函数（L1/L2/L3/信号管道/健康检查）
```

---

## 4. 数据流

### 全局数据流

```
MCP/akshare (腾讯自选股/东方财富 API)     DuckDB kline_cache (期货)
    │                                          │
    │ OHLCV K 线数据 (A 股 / ETF)              │ OHLCV 日线 (期货连续合约)
    │                                          │
    ▼                                          ▼
FTS (因子推演) — 支持 A 股/ETF/期货横截面因子演化
    │
    │ 因子引擎 → 策略组建 → 交易信号
    ▼
下游系统（信号消费方）
```

### 期货数据流

```
AKShare futures_zh_daily_sina
    │
    │ scripts/download_futures.py（断点续传）
    ▼
DuckDB kline_cache (data/fts_history.duckdb)
    │
    │ FuturesDataProvider._from_kline_cache()     ← 优先级1
    │ AKShare 即时获取（降级）                       ← 优先级2
    │ 合成数据（降级）                                ← 优先级3
    ▼
FTSDataProvider.get_futures_ohlcv() / get_futures_panel()
    │
    │ --universe futures
    ▼
EvolutionLoop（期货横截面因子演化，跨品种因子计算）
    │
    ▼
scripts/futures_signal_pipeline.py（横截面信号管道，方向校正 = 截面 IC 法，因子加权 = Ridge 回归 L2 正则化，Market Regime 检测 = RegimeAwareSelector）
    │
    ▼
reports/{date}/futures_signals_{date}.md
```

**common_dates 语义（v1.7.1）**：
- `get_futures_panel()` 返回的 `common_dates` 由「全品种日期交集」改为「多数对齐」：
  取至少 `max(2, 品种数//2)` 个品种共有的日期。
- 原因：全交集在 76 个商品期货（FUTURES_SUBSET）下会因个别停更品种
  （WH0/JR0/RI0/LR0 数据止于 2022-2023）将交集清空，导致横截面方向校正
  （截面 IC 法）静默失效，全部因子 flip=1.0。
- 方向校正按日期定位（`df.index.get_loc`）而非位置索引，避免品种间
  日期错位污染 IC 计算。
- 信号管道剔除数据陈旧品种：最新交易日早于共同日期末端（如已停更的
  WH0/JR0/RI0/LR0）的品种不参与横截面排名，防止陈旧价格混入当前信号。
- 信号报告输出品种中文名称（FUTURES_SYMBOL_NAMES 映射）与主力合约代码
  （get_dominant_contracts() 按 contract_kline 最新交易日最大成交量判定）。
- L3 定时任务（20:00）触发信号管道时使用 `--universe all` 全量商品池。

**因子加权方法（v1.7.3 — Ridge 回归）**：
- 基于 Shen & Xiu 的弱信号理论：当因子信号普遍较弱时，L2 正则化（Ridge）优于 L1 选择（Lasso/硬阈值）。
- 使用全部精英因子（不按 IC 过滤），以 Ridge 回归学习差异化权重：
  强因子自动获得高权重，弱因子获得接近零的权重但不被丢弃。
- 这替代了 v1.7.2 的 IC>0.3 硬过滤 + 等权合成。
- 实现：`_compute_ridge_weights()` 在 `scripts/futures_signal_pipeline.py`。

**Market Regime 检测（v1.8.1）**：
- 信号管道在数据加载后、信号计算前，调用 `RegimeAwareSelector.detect()` 检测当前市场制度。
- 检测方法：从品种面板构建市场综合 OHLCV（取所有品种 close 截面均值），计算 MA20 斜率、ATR/价格、量比、收益自相关，分层判定制度类型。
- 制度类型：bull（趋势上涨）/ bear（趋势下跌）/ high_vol（高波动）/ low_vol（低波动）/ oscillate（震荡）。
- 报告输出：制度名称 + 置信度 + 特征值（趋势强度/波动率/量比/市场广度）+ Regime 调整后的交易建议。
- 趋势友好（bull/bear）→ 优先做空/做多增量最强的品种，可放大仓位；震荡（oscillate）→ 反向操作；高波动（high_vol）→ 缩小仓位，只做增量绝对值 > 0.15 的品种。
- 实现：`_build_composite_ohlcv()` 构建市场综合 OHLCV，`RegimeAwareSelector` 在 `fts/factor_engine/regime.py`。

### FTS 内部数据流

```
Program.md (L0 人类设定)
    │
    ▼
L1 Meta-Loop ──→ 知识补给 + 种子注入 ──→ seed_pool.py
    │                                       │
    │                                       ▼
    │                              L2 Evolution Loop
    │                              ├── macro_evolution (LLM 改逻辑)
    │                              ├── micro_evolution (optuna 调参)
    │                              ├── evaluation_chain (三级评估)
    │                              └── verifier (锁定)
    │                                       │
    │                                       ▼
    │                              elite 因子 (JSON)
    │                                       │
    │                                       ▼
    └──────────────────────→ L3 Portfolio Loop
                              ├── 正交化
                              ├── 衰减检验
                              ├── 组合构建
                              └── 信号合成
```

---

## 5. 关键契约

### TraceID 全链路

`trace_id` 必须贯穿所有模块、文档和日志。生成规则：

```python
# fts.factor_engine.state.generate_trace_id()
trace_id = f"{prefix}_{8hex}_{timestamp}"
```

所有 CLI 子命令在启动时生成 `trace_id`，通过参数或全局变量传递到各层循环。

### Verifier 锁定协议

Verifier 是 FTS 的核心安全机制，锁定后不可逆：

- **L1 Verifier**: 控制 L1 种子注入和知识补给
- **L2 Verifier**: 控制 L2 因子演化流程
- **L3 Verifier**: 控制 L3 组合构建和信号产出
- 锁定后只能读取，无法修改配置

### Program.md 约定

人类通过 `Program.md` 文件设定 FTS 的最高层级指令：

- ProgramConfig: 目标、约束、市场偏好、风险偏好
- `parse_program_md()`: 解析 Program.md → ProgramConfig
- `load_program()`: 加载并验证 Program 配置

---

## 6. 各层循环运行时间

| 循环 | 触发时间 | 频率 | 职责 |
|:-----|:---------|:-----|:-----|
| L1 Meta-Loop | 08:30 | 每日 | 知识补给 + 种子注入 |
| L2 Evolution Loop | 23:00 | 每日 | 夜间因子演化 |
| L3 Portfolio Loop | 20:00 | 每日 | 组合构建 + 正交化 + 信号合成 |
| 期货信号管道 | 20:30 | 每日 | 横截面信号报告（全量因子 Ridge 回归加权） |
| Health Check | 每 10 分钟 | 高频 | 状态监控 |

---

## 7. 技术栈

- **语言**: Python 3.10+
- **核心依赖**: numpy, pandas, pyyaml
- **演化依赖（可选）**: optuna (evolution extra)
- **LLM 依赖（可选）**: openai, anthropic (llm extra)
- **数据依赖（可选）**: akshare >= 1.18.64 (mcp extra)
- **期货数据（可选）**: duckdb >= 0.8.0, akshare >= 1.18.64
- **测试**: pytest 7.4+, pytest-cov 4.1+
- **打包**: setuptools, pyproject.toml

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `seed_pool.py` → 种子池 482 因子（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面）+ 期货 12 家族 50+ 子因子；`data_fundamental.py` → FundamentalProvider 基本面数据层；`data_futures.py` → FuturesDataProvider 期货数据层（82 品种 FUTURES_SUBSET + 59 个品种 DuckDB 缓存 + AKShare 降级，`get_futures_panel()` common_dates 多数对齐 ≥ 品种数//2，FUTURES_SYMBOL_NAMES 名称映射，get_dominant_contracts() 主力合约判定）；`data_futures_fundamental.py` → FuturesFundamentalProvider 期货基本面数据（库存/仓单/基差）；`scheduler/` → 调度层（5 个 APScheduler 定时任务：L1:08:30 / L2:23:00 / L3:20:00 / 信号管道:20:30 / 健康检查:每10m）；`scripts/futures_signal_pipeline.py` → 横截面信号管道（方向校正 = 截面 IC 法，因子加权 = Ridge 回归 L2 正则化，Market Regime 检测 = RegimeAwareSelector 分层判定，`_build_composite_ohlcv()` 构建市场综合 OHLCV，按日期定位，`--universe all` 全量商品池，输出品种名称/主力合约 + Regime 调整交易建议）；`fts/factor_engine/regime.py` → RegimeAwareSelector 市场制度感知（5 种制度：bull/bear/high_vol/low_vol/oscillate，MA20 斜率 + ATR/价格 + 量比 + 收益自相关）；`strategies/strategy_evolution.py` → 策略进化（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion） |
| 可验证断言 | 种子池总数 = 482；期货数据层支持 82 个连续合约品种，数据源优先级 3 级（DuckDB → AKShare → 合成）；common_dates 多数对齐（WH0 等停更品种不清空交集）；方向校正按日期定位；信号管道因子加权 = Ridge 回归（全量因子，L2 正则化）；主力合约判定 = contract_kline 最新交易日最大成交量；调度器注册 5 个任务；信号管道集成 Market Regime 检测（5 种制度分层判定，输出 Regime 调整交易建议）；策略进化模块包含 3 种策略（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion） |
| 检验方式 | `python -c "from fts.scheduler.tasks import list_tasks; assert len(list_tasks()) == 5"` |
