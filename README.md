# FTS — Factor Intelligence System

> **因子智能系统** — AI 原生的量化因子发现、评估、组合与演化引擎

[![Tests](https://img.shields.io/badge/tests-778%20passing-brightgreen)](#)
[![Coverage](https://img.shields.io/badge/coverage-89%25-yellowgreen)](#)
[![Version](https://img.shields.io/badge/version-0.2.0-blue)](#)

---

## 概述

FTS 是一个 AI 原生的量化因子智能系统，实现三层进化循环：

- **L1 Meta-Loop** — 每日市场感知与知识补给（Web 感知 + Bootstrapping + debate 分析）
- **L2 Evolution Loop** — 夜间因子自动演化（LLM 宏观改逻辑 + optuna 微观调参）
- **L3 Portfolio Loop** — 组合构建与信号产出（正交化 + 衰减检验 + 加权融合）

项目定位：**数据层（Data-Core）← FTS（因子智能）← 决策层（FDT）**

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

# L2 因子演化
fts evolution run --max-generations 3

# L3 组合构建
fts portfolio run

# 查看 elite 因子
fts factor list

# 调度器任务列表
fts scheduler list
```

可选依赖：

| Extra | 功能 | 安装 |
|-------|------|------|
| `evolution` | optuna 贝叶斯调参 | `pip install -e ".[evolution]"` |
| `llm` | LLM 客户端（openai/anthropic） | `pip install -e ".[llm]"` |
| `data` | Data-Core 集成 | `pip install -e ".[data]"` |
| `dev` | 开发工具（pytest/pytest-cov） | `pip install -e ".[dev]"` |
| 全部 | 安装所有可选依赖 | `pip install -e ".[evolution,llm,data,dev]"` |

## 项目结构

```
fts/                          # 核心源码（~3,400 语句）
├── config/                   # 配置系统（YAML + 环境变量 + 默认值）
├── core/                     # 核心契约（enums + TypedDict 重导出）
├── factor_engine/            # 因子引擎（L1/L2/L3 三层循环）
├── pipeline/                 # 因子推演管线
├── strategies/               # 策略层（base_v2 + multi_factor）
├── scheduler/                # 调度层（TaskRegistry + APScheduler 引擎）
├── data.py                   # Data-Core 集成层
├── llm.py                    # LLM 客户端统一接口（OpenAI/Anthropic/Mock）
├── cli.py                    # 统一命令行入口
└── monitor.py                # 健康监控

tests/                        # 778 个测试，全部通过
├── factor_engine/            # 因子引擎测试（13 文件）
├── pipeline/                 # 管线测试（2 文件）
├── scheduler/                # 调度测试
├── strategies/               # 策略测试（2 文件）
├── core/                     # 核心契约测试（2 文件）
├── test_cli.py               # CLI 集成测试
├── test_llm.py               # LLM 客户端测试
└── test_monitor.py           # 监控测试

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
├── archive/                  # 已完成/过时文档归档
│   └── PLAN_v2.2.md          # 初始构建计划（已完成）
└── harness/                  # HARNESS 工程文档（活文档）
    ├── 01-architecture.md    # 系统架构
    ├── 02-lifecycle.md       # 开发生命周期
    ├── 06-testing.md         # 测试策略与覆盖率
    ├── 07-operations.md      # 版本管理与运维
    ├── 08-gap-analysis.md    # 差距管理
    └── 09-advancement-plan.md# 晋级计划
```

## 架构概览

```
Data-Core（数据基础设施）
    ↑ UnifiedDataProvider.get(symbol, DataType, params)
FTS（因子智能系统）
    ↑ SignalPayload + FactorCombo
FDT（期货交易决策系统）
```

### 三层循环

| 循环 | 调度 | 职责 |
|------|------|------|
| L1 Meta-Loop | 每日 09:00 | 市场感知、知识补给、Bootstrapping、debate 分析 |
| L2 Evolution | 每日 23:00 | 因子演化（LLM 改逻辑 + optuna 调参）、三级评估链 |
| L3 Portfolio | 每周一 06:00 | 组合构建、正交化、衰减检验、信号输出 |

## 工程指标

| 指标 | 值 |
|------|:---:|
| **版本** | v0.2.0 |
| **测试通过数** | 778 / 778（100%）|
| **测试覆盖率** | 89%（27 个模块 ≥90%）|
| **代码行数** | ~3,400 语句 |
| **文件数** | 77 个源码 + 测试文件 |

## 依赖关系

- **Data-Core**（上游）：数据采集与加工（含 LLM 情绪打分），FTS 通过 `UnifiedDataProvider` 消费
- **FDT**（下游）：消费 FTS 产出的因子信号进行交易决策

## 许可证

MIT License

## 相关项目

- [Data-Core](https://github.com/CTAAgents/data-core) — 量化数据基础设施
- [FDT](https://github.com/CTAAgents/FDT) — 期货交易决策系统
