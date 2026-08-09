# FTS — Factor Intelligence System

> **因子智能系统** — AI 原生的量化因子发现、评估、组合与演化引擎

[![Tests](https://img.shields.io/badge/tests-4020%2B%20passing-blue)](#)
[![Version](https://img.shields.io/badge/version-2.54.0-blue)](#)
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

支持 A 股、ETF 和期货品种的横截面因子演化，内置 6 类因子强制审计、50 分制质量评分卡、APScheduler 全自动定时调度、HTTP 监控端点与 Web UI 仪表盘。

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
| `fts meta-loop run [--market {stock,futures}]` | 启动 L1 市场感知与知识补给 |

### L2 Evolution Loop

| 命令 | 说明 |
|------|------|
| `fts evolution run [options]` | 启动 L2 因子演化 |

L2 演化参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--max-generations` | 10 | 最大演化代数 |
| `--symbol` | `000001` | 单标的演化的代码 |
| `--universe` | `futures` | 演化市场（`futures` / `stock` / `csi300` / `single`） |
| `--max-stocks` | 0 | 横截面演化最大标的数 |

### L3 Portfolio Loop

| 命令 | 说明 |
|------|------|
| `fts portfolio run [options]` | 启动 L3 组合构建 |

L3 参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--universe` | `stock` | 组合市场（`stock` / `futures`） |
| `--synthesis-mode` | 自动 | 信号合成模式（`sharpe_weight` / `elastic_net` / `equal_weight` / `ml_ensemble`） |

### 因子管理

| 命令 | 说明 |
|------|------|
| `fts factor list [--market] [--family] [--min-ic] [--min-sharpe] [--diverse] [--json]` | 列出 elite 因子，支持筛选和多样性选择 |
| `fts factor show <factor_id>` | 查看单个因子详情 |
| `fts factor stats [--market] [--json]` | 因子家族分布统计 |
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
| `fts backtest batch --family <family>` | 批量回测 + Sharpe 排名 |
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

### 三层进化循环

| 循环 | 调度时间 | 核心职责 |
|------|----------|----------|
| L1 Meta-Loop | 每日 08:30 | 市场感知、知识补给、Bootstrapping、Debate 分析 |
| L2 Evolution | 每日 23:00 | LLM 宏观改逻辑 + Optuna 微观调参、三级评估链、质量评分 |
| L3 Portfolio | 每日 20:00 | 因子筛选、正交化、信号合成、Verifier 校验 |

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
| `sharpe_weight` | 期货（默认） | 因子夏普比率加权 |
| `elastic_net` | 股票（默认） | L1+L2 正则化学习权重 |
| `equal_weight` | 通用 | 等权合成 |

### Market Regime 自适应

系统实时检测 5 种市场状态并自动调整策略参数：

`bull` / `bear` / `high_vol` / `low_vol` / `oscillate`

### 断路器保护

L2/L3 内置多层熔断机制：

- **低 IC 熔断** — 连续 5 代 IC < 0.005 时暂停演化
- **失败率熔断** — 因子淘汰率超过阈值时触发
- **Token 熔断** — LLM Token 耗尽时降级为纯参数演化
- **数据熔断** — 数据源不可用时自动切换合成数据

---

## 项目结构

```
fts/                          # 核心源码（84 个 Python 文件）
├── config/                   # 配置系统（YAML + 环境变量 + 默认值）
├── core/                     # 核心契约（enums + TypedDict）
├── factor_engine/            # 因子引擎（L1/L2/L3 + 审计 + 评分卡）
│   ├── audit.py              # 6 类因子强制审计
│   ├── factor_quality_card.py# 50 分制质量评分卡
│   ├── evolution_loop.py     # L2 演化主循环
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
├── data_mcp.py               # MCP 数据适配层
├── llm.py                    # LLM 客户端（OpenAI/Anthropic/Mock）
└── cli.py                    # CLI 统一入口

tests/                        # 116+ 个测试文件，3985+ 个测试用例（含高IC筛查 25 用例）
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
```

---

## 许可证

本项目基于 **Apache License 2.0** 开源，仅供研究与教育用途，不构成任何投资建议。详见 [LICENSE](LICENSE)。

- [NOTICE](NOTICE) — 版权与第三方声明（Qlib / WorldQuant / GTJA）
- [免责声明](DISCLAIMER.md) — 风险、责任与用户义务声明
- [开源合规指南](COMPLIANCE.md) — 包含项、排除项、种子库构建与第三方数据使用指引
