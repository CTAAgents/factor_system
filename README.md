# FTS — Factor Intelligence System

> **因子智能系统** — AI 原生的量化因子发现、评估、组合与演化引擎

[![Tests](https://img.shields.io/badge/tests-1735%20passing-brightgreen)](#)
[![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen)](#)
[![Version](https://img.shields.io/badge/version-2.2.0-blue)](#)

---

## 概述

FTS 是一个 AI 原生的量化因子智能系统，实现三层进化循环：

- **L1 Meta-Loop** — 每日市场感知与知识补给（Web 感知 + Bootstrapping + debate 分析）
- **L2 Evolution Loop** — 夜间因子自动演化（LLM 宏观改逻辑 + optuna 微观调参）
- **L3 Portfolio Loop** — 组合构建与信号产出（正交化 + 衰减检验 + 加权融合）

项目定位：**MCP/akshare（腾讯/东方财富数据源）← FTS（因子智能 → 交易信号）**

支持 A 股、ETF 和 82 个期货品种（25 核心 + 57 全量）的横截面因子演化，支持期货基本面数据（库存/仓单/基差）注入，支持 APScheduler 全自动 L1/L2/L3 定时调度，支持五层逻辑审查框架（消融实验 + 场景测试 + SHAP 分析 + 鲁棒性审查 + 因果验证）。

## 快速开始

```bash
# 安装
pip install -e .

# 查看版本与配置
fts version

# 运行测试
python -m pytest tests/ --no-cov --tb=short

# 查看监控状态
fts monitor

# L1 Meta-Loop（市场感知）
fts meta-loop run

# L2 因子演化（期货默认）
fts evolution run --max-generations 10

# L2 横截面演化（沪深300）
fts evolution run --universe csi300 --max-stocks 20

# L3 组合构建（期货）
fts portfolio run --universe futures --synthesis-mode sharpe_weight

# L3 组合构建（股票）
fts portfolio run --universe stock --synthesis-mode elastic_net

# 查看 elite 因子
fts factor list --market futures

# 查看种子因子
fts factor seeds --market futures

# 调度器任务列表
fts scheduler list

# 启动 Web UI 仪表盘
fts ui --port 9100
```

可选依赖：

| Extra | 功能 | 安装 |
|-------|------|------|
| `evolution` | optuna 贝叶斯调参 | `pip install -e ".[evolution]"` |
| `llm` | LLM 客户端（openai/anthropic） | `pip install -e ".[llm]"` |
| `mcp` | MCP 数据源（akshare 腾讯/东方财富） | `pip install -e ".[mcp]"` |
| `portfolio` | 组合构建（scikit-learn） | `pip install -e ".[portfolio]"` |
| `dev` | 开发工具（pytest/pytest-cov） | `pip install -e ".[dev]"` |
| 全部 | 安装所有可选依赖 | `pip install -e ".[evolution,llm,mcp,portfolio,dev]"` |

## 项目结构

```
fts/                          # 核心源码（65 个 Python 文件）
├── config/                   # 配置系统（YAML + 环境变量 + 默认值）
├── core/                     # 核心契约（enums + TypedDict 重导出）
├── factor_engine/            # 因子引擎（L1/L2/L3 三层循环 + 五层逻辑审查）
│   ├── ablation.py           # 输入敏感性消融实验（5 种模式）
│   ├── causal_validator.py   # 因果结构审查（自然实验事件验证）
│   ├── robustness.py         # 鲁棒性审查（对抗样本/缺失值/分布外）
│   ├── shap_analyzer.py      # SHAP 局部可解释性分析
│   ├── regime.py             # Market Regime 检测（5 种状态）
│   ├── seed_data/            # 种子因子定义库
│   │   ├── wq101.py          # 101 个 WorldQuant Alpha 因子
│   │   ├── qlib158.py        # 158 个 Qlib 因子
│   │   ├── gtja191.py        # 191 个国泰君安 Alpha 因子
│   │   ├── fundamental_seeds.py  # 23 个基本面/另类/宏观因子
│   │   └── loader.py         # 动态加载器
│   └── seed_data_futures_full.py  # 期货种子因子（57 个，13大因子家族）
├── pipeline/                 # 因子推演管线（因子组合与融合）
├── strategies/               # 策略层（base_v2 + multi_factor + strategy_evolution）
├── scheduler/                # 调度层（TaskRegistry + APScheduler 引擎）
├── monitor/                  # 健康监控 + HTTP 端点 + 逻辑监控仪表盘
├── data.py                   # 数据层（MCP 统一入口 + 基本面注入）
├── data_fundamental.py       # 基本面数据层（估值/财务/宏观字段注入）
├── data_futures.py           # 期货数据层（DuckDB + AKShare）
├── data_mcp.py               # MCP 数据适配层（akshare 腾讯/东方财富）
├── llm.py                    # LLM 客户端统一接口（OpenAI/Anthropic/Mock）
└── cli.py                    # 统一命令行入口

tests/                        # 63 个测试文件，1735+ 全部通过
├── factor_engine/            # 因子引擎测试（24 文件，含消融/因果/鲁棒性/SHAP）
├── scenarios/                # 宏观行为场景测试（23 个场景定义 + 验证器）
├── monitor/                  # 监控测试（逻辑监控 + 精英追踪）
├── pipeline/                 # 管线测试（2 文件）
├── scheduler/                # 调度测试（4 文件）
├── strategies/               # 策略测试（3 文件）
├── core/                     # 核心契约测试（3 文件）
├── test_cli.py               # CLI 集成测试
├── test_data_futures_panel.py# 期货面板测试
└── test_futures_signal_pipeline.py # 信号管道测试

scripts/                      # 工具脚本（17 个）
├── futures_signal_pipeline.py    # 期货横截面信号管道
├── futures_factor_revalidation.py# 精英因子全量重验证
├── download_futures.py           # 期货数据断点续传下载
├── verify_doc_consistency.py     # 文档一致性校验
└── daily_signal_pipeline.py      # 每日信号管道

config/                       # 项目级配置文件
├── settings.yaml             # YAML 配置示例
└── .gitignore

memory/                       # 运行时持久化（自动创建）
├── evolution/                # L2 演化状态
├── meta_loop/                # L1 元循环状态
├── portfolio/                # L3 组合状态
└── knowledge/factors/        # 因子知识库
    ├── elite/                # 精英因子
    └── l1_injected/          # L1 注入因子

docs/                         # 项目文档
├── production_plan.md        # 生产就绪实施计划
├── CODE_WIKI.md              # 代码 Wiki
├── execution_modes_flowchart.md  # 执行模式流程图
├── business_flow.md          # 业务流程图
├── harness/                  # HARNESS 工程文档（活文档）
│   ├── 01-architecture.md    # 系统架构
│   ├── 02-lifecycle.md       # 开发生命周期
│   ├── 04-resilience.md      # 韧性设计
│   ├── 06-testing.md         # 测试策略与覆盖率
│   ├── 07-operations.md      # 版本管理与运维
│   ├── 08-gap-analysis.md    # 差距管理
│   ├── 09-advancement-plan.md# 晋级计划
│   └── 11-logic-review-plan.md# 五层逻辑审查实施计划
└── agents/                   # 角色职责文档
    └── fts-agent.md          # FTS Agent 职责定义
```

## 架构概览

```
MCP/akshare（腾讯自选股/东方财富 API）
    ↓ OHLCV K 线数据 + VWAP + 基本面
FTS（因子智能系统）
    ↓ 交易信号（多空双向）
下游消费系统（FDT 交易决策 / 手动执行）
```

### 三层循环

| 循环 | 调度 | 职责 |
|------|------|------|
| L1 Meta-Loop | 每日 08:30 | 市场感知、知识补给、Bootstrapping、debate 分析 |
| L2 Evolution | 每日 23:00 | 因子演化（LLM 改逻辑 + optuna 调参）、三级评估链、UCT 父因子选择 |
| L3 Portfolio | 每周一 06:00 | 组合构建、正交化、衰减检验、信号输出（Ridge 回归加权） |

### 五层逻辑审查框架

| 层级 | 模块 | 职责 |
|------|------|------|
| L1 输入敏感性 | `ablation.py` | 5 种消融模式（volume_zero/vwap_replace/time_shuffle/noise_inject/feature_permute） |
| L2 宏观行为 | `scenarios/` | 23 个典型市场场景验证（趋势/震荡/危机/政策/季节性） |
| L3 局部可解释 | `shap_analyzer.py` | SHAP 极端样本归因（top-5 贡献特征） |
| L4 鲁棒性 | `robustness.py` | 对抗样本/缺失值/分布外测试 |
| L5 因果结构 | `causal_validator.py` | 自然实验事件验证（6 个预定义事件，3σ 异常检测） |

持续监控：`logic_monitor.py` — 因子行为漂移检测 + 极端预测占比报警 + 换月日信号异常检测

### 演化模式

| 模式 | 命令 | 说明 |
|------|------|------|
| 期货演化（默认） | `fts evolution run` | 25 核心期货品种横截面因子演化 |
| 单标演化 | `fts evolution run --universe single --symbol 000001` | 单只股票的因子演化 |
| 横截面演化 | `fts evolution run --universe csi300 --max-stocks 20` | 沪深 300 成分股横截面因子演化 |

### 信号合成模式

| 模式 | 适用市场 | 说明 |
|------|----------|------|
| `sharpe_weight` | 期货（默认） | 按因子夏普比率加权 |
| `elastic_net` | 股票（默认） | L2 正则化学习因子权重 |
| `equal_weight` | 通用 | 等权合成 |

## 工程指标

| 指标 | 值 |
|------|:---:|
| **版本** | v2.2.0 |
| **测试通过数** | 1735+ / 1748（99.3%）|
| **测试覆盖率** | 99%（46/47 模块 100%，1 模块 73% 需 MCP 网络环境）|
| **源码文件数** | 65 个 Python 文件 |
| **测试文件数** | 63 个 Python 文件 |
| **脚本文件数** | 17 个工具脚本 |
| **股票种子因子** | 482（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面/另类/宏观） |
| **期货种子因子** | 12 大因子家族 50+ 子因子（期限结构/动量/波动率/量价/持仓/截面动量/基差/资金流/高频/情绪/拥挤度/期权PCR） |
| **期货品种池** | 82 个（25 核心 + 57 全量），支持 7 大产业链分层训练 |
| **Market Regime** | 5 种状态（bull/bear/high_vol/low_vol/oscillate） |
| **逻辑审查场景** | 23 个宏观行为场景 + 6 个自然实验事件 |

## 依赖关系

- **MCP/akshare**（数据源）：腾讯自选股/东方财富 API，提供 A 股和 ETF 行情数据
- **DuckDB**（期货数据）：存储期货连续合约 K 线数据（kline_cache + contract_kline）
- **AKShare**（期货数据）：提供期货实时行情和基本面数据

## 许可证

MIT License

## 相关项目

- **FDT** — Factor Decision Tree（因子决策树，下游交易执行系统）
- **Data-Core** — 数据采集加工系统（已集成到 FTS 内部）
