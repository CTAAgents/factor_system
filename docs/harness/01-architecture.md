# FTS 系统架构文档

> 版本: v0.3.0
> 最后更新: 2026-07-24

---

## 1. 项目概述

FTS（Factor Intelligence System，因子智能系统）是一个独立的因子策略系统，专注于因子推演、策略组建与交易信号产出。数据层基于腾讯自选股 MCP (akshare) 提供 A 股和 ETF 行情数据，FTS **本身包含自洽的数据源适配层**，无外部数据项目依赖。

### 项目边界

| 职责 | 归属 |
|:-----|:-----|
| 行情数据获取（A 股/ETF OHLCV） | **FTS（通过 MCP/akshare 接入腾讯/东方财富 API）** |
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
│  macro_evolution    →   micro_evolution    →   evaluation_chain        │
│  (LLM 改逻辑)           (optuna 调参)          (三级评估链)              │
│                                                                         │
│  evolution_loop.py — L2 主循环协调器                                   │
│  seed_pool.py — 种子池（9 个内置因子 + L1 注入接口）                    │
│  factor_program.py — 因子程序（图灵完备代码 + 安全沙箱）                  │
│  verifier.py — Verifier 锁定协议                                       │
│  state.py — 演化状态管理 + trace_id 全链路                              │
│                                                                         │
│  职责: 夜间批量演化 → LLM 逻辑改造 → optuna 参数优化 → 三级评估 → elite │
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
├── __init__.py                 # 包入口 + 版本号 v1.1.0
├── cli.py                      # 统一命令行入口
├── data.py                     # 数据层（MCP 统一入口）
├── data_mcp.py                 # MCP 数据适配层（akshare 腾讯/东方财富）
├── llm.py                      # LLM 客户端（OpenAI/Anthropic/Mock）
├── config/                     # 配置系统
│   └── settings.py             # FTSConfig + load_config()
├── core/                       # 核心契约层
│   ├── contracts.py            # TypedDict 契约 re-export
│   ├── atomic.py               # 原子文件操作
│   └── enums.py                # 枚举定义
├── factor_engine/              # 因子引擎（核心模块）
│   ├── __init__.py             # 模块入口 + 版本号 v8.10.0
│   ├── contracts.py            # 完整 TypedDict 契约（L1+L2+L3）
│   ├── evolution_loop.py       # L2 主循环
│   ├── meta_loop.py            # L1 元循环
│   ├── portfolio_loop.py       # L3 组合循环
│   ├── macro_evolution.py      # LLM 宏观演化
│   ├── micro_evolution.py      # optuna 微观调参
│   ├── evaluation_chain.py     # 三级评估链
│   ├── experience_chain.py     # 经验链存储
│   ├── seed_pool.py            # 种子池（9 个内置因子）
│   ├── factor_program.py       # 因子程序（安全沙箱）
│   ├── verifier.py             # Verifier 锁定协议
│   ├── state.py                # 演化状态管理
│   ├── program.py              # L0 人类设定（Program.md）
│   ├── walk_forward.py         # 走航验证
│   ├── cost_model.py           # 交易成本模型
│   ├── regime.py               # 市场制度检测
│   ├── stress_test.py          # 压力测试
│   └── monitor.py              # 循环监控
├── pipeline/                   # 因子推演管线
│   ├── base.py                 # FactorPipeline 抽象基类
│   └── factor_combiner.py      # 因子组合器
├── strategies/                 # 策略层
│   ├── base_v2.py              # BaseStrategyV2
│   └── multi_factor_strategy.py# 多因子策略
└── monitor/                    # 健康监控
    ├── __init__.py             # 状态报告函数
    ├── http_server.py          # HTTP 监控端点
    └── elite_tracker.py        # Elite 因子追踪
```

---

## 4. 数据流

### 全局数据流

```
MCP/akshare (腾讯自选股/东方财富 API)
    │
    │ pip install fts[mcp] (akshare >=1.18.64)
    │ OHLCV K 线数据 (A 股 / ETF)
    ▼
FTS (因子推演)
    │
    │ 因子引擎 → 策略组建 → 交易信号
    ▼
下游系统（信号消费方）
```

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
| L1 Meta-Loop | 09:00 | 每日 | 知识补给 + 种子注入 |
| L2 Evolution Loop | 23:00 | 每日 | 夜间因子演化 |
| L3 Portfolio Loop | 06:00 (周一) | 每周 | 组合构建 + 信号合成 |
| Health Check | 每 10 分钟 | 高频 | 状态监控 |

---

## 7. 技术栈

- **语言**: Python 3.10+
- **核心依赖**: numpy, pandas, pyyaml
- **演化依赖（可选）**: optuna (evolution extra)
- **LLM 依赖（可选）**: openai, anthropic (llm extra)
- **数据依赖（可选）**: akshare >= 1.18.64 (mcp extra)
- **测试**: pytest 7.4+, pytest-cov 4.1+
- **打包**: setuptools, pyproject.toml
