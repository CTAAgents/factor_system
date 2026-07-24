# FTS — 因子智能系统 用户手册

*文档版本：1.1.0 | 最后更新：2026-07-24*

---

## 目录

1. [系统概述](#1-系统概述)
2. [安装与配置](#2-安装与配置)
3. [命令行参考](#3-命令行参考)
4. [三层循环架构](#4-三层循环架构)
5. [因子演化流程](#5-因子演化流程)
6. [调度器](#6-调度器)
7. [Web UI 仪表盘](#7-web-ui-仪表盘)
8. [配置参考](#8-配置参考)
9. [数据源](#9-数据源)
10. [环境变量](#10-环境变量)
11. [种子因子](#11-种子因子)
12. [故障排除](#12-故障排除)
13. [工程指标](#13-工程指标)

---

## 1. 系统概述

FTS（Factor Intelligence System）是一个 AI 原生的量化因子智能系统，实现从数据获取到交易信号产出的全流程自动化。

**数据流：**

```
腾讯自选股 HTTP API (qt.gtimg.cn) / akshare
    ↓ OHLCV K线数据
FTS 因子智能系统（三层循环）
    ↓ 交易信号
下游消费系统 / 策略层
```

**核心能力：**

- L1 Meta-Loop：每日市场感知、知识补给、因子 Bootstrapping
- L2 Evolution Loop：夜间因子自动演化（LLM 宏观改逻辑 + optuna 微观调参）
- L3 Portfolio Loop：组合构建、正交化、衰减检验、信号输出
- 仅支持 **A 股和 ETF** 因子演化（期货因子已移除）

---

## 2. 安装与配置

### 2.1 环境要求

- Python >= 3.10
- 操作系统：Windows / Linux / macOS

### 2.2 安装

```bash
# 克隆仓库
git clone https://github.com/CTAAgents/factor_system.git
cd factor_system

# 基础安装（推荐）
pip install -e .

# 完整安装（含所有可选依赖）
pip install -e ".[evolution,llm,mcp,dev]"
```

### 2.3 可选依赖

| Extra | 功能 | 安装命令 |
|-------|------|----------|
| `evolution` | optuna 贝叶斯调参 | `pip install -e ".[evolution]"` |
| `llm` | LLM 客户端（openai/anthropic） | `pip install -e ".[llm]"` |
| `mcp` | MCP 数据源（akshare 腾讯/东方财富） | `pip install -e ".[mcp]"` |
| `dev` | 开发工具（pytest/pytest-cov） | `pip install -e ".[dev]"` |

### 2.4 验证安装

```bash
fts version
# 输出示例：
# FTS version: 1.1.0
# Factor engine version: 1.1.0
```

---

## 3. 命令行参考

### 3.1 全局命令

```
fts version             查看版本号和配置路径
fts monitor             查看系统监控状态
fts ui                  启动 Web UI 仪表盘（默认 http://127.0.0.1:9100）
fts --help              查看帮助
```

### 3.2 因子演化

```bash
# 单标演化（默认标的 000001 平安银行，最大 10 代）
fts evolution run

# 指定标的和代数
fts evolution run --symbol 510300 --max-generations 5

# 横截面演化（沪深300成分股）
fts evolution run --universe csi300 --max-stocks 20

# 选项说明
# --max-generations    最大演化代数（默认 10）
# --symbol             演化目标品种代码（默认 000001）
# --universe           股票池类型: single（单标）/ csi300（横截面）
# --max-stocks         横截面模式最大标的数（默认 50）
```

### 3.3 L1 Meta-Loop

```bash
# 运行市场感知 + Bootstrapping
fts meta-loop run
```

### 3.4 L3 组合构建

```bash
# 加载 elite 因子 → 正交化 → 衰减检验 → 信号输出
fts portfolio run
```

### 3.5 因子管理

```bash
# 列出所有 elite 因子
fts factor list

# 查看单个因子详情
fts factor show <factor_id>
```

### 3.6 调度器

```bash
# 列出所有已注册的定时任务
fts scheduler list

# 启动调度器后台运行
fts scheduler run
```

### 3.7 Web UI

```bash
# 启动仪表盘（默认端口 9100）
fts ui

# 自定义端口
fts ui --port 8080

# 局域网访问
fts ui --host 0.0.0.0
```

---

## 4. 三层循环架构

### 4.1 循环概览

| 循环 | 调度时间 | 职责 |
|------|----------|------|
| L1 Meta-Loop | 每日 09:00 | 市场感知、Web 知识补给、Bootstrapping、Debate 分析 |
| L2 Evolution Loop | 每日 23:00 | 因子演化（LLM 改逻辑 + optuna 调参）、三级评估链 |
| L3 Portfolio Loop | 每周一 06:00 | 组合构建、正交化、衰减检验、信号输出 |

### 4.2 L1 Meta-Loop 流程

```
1. 市场感知（Web 收集） → 市场摘要
2. Bootstrapping → 生成候选因子
3. Debate 分析 → 识别知识缺口
4. 候选因子注入 L2 种子池
```

### 4.3 L2 Evolution Loop 流程

```
for generation in 1..MAX_GEN:
    ├─ 选择父因子（种子池轮询）
    ├─ Step 1: Macro Evolution（LLM 改逻辑）
    │   - LLM 分析父因子 + 经验链教训
    │   - 输出新因子代码和经济逻辑
    ├─ Step 2: Micro Evolution（optuna 调参）
    │   - optuna 搜索最优参数
    │   - 100 trials（默认）
    ├─ Step 3: 三级评估链
    │   ├─ Level 1: 回测验证（IC>0.03 / 夏普>1.5 / 单调性 / 样本外≥30%）
    │   ├─ Level 2: 经济逻辑评分（四维 ≥ 3/4）
    │   └─ Level 3: 多重检验（Bonferroni + FDR）
    ├─ Step 4: Verifier 判定
    ├─ Step 5: 经验链记录
    └─ Step 6: 状态持久化

熔断条件（任一触发即停止）:
    - Token 消耗超标（2x 预算）
    - 连续 3 代 IC < 0.01
    - 失败率 > 90%
```

### 4.4 L3 Portfolio Loop 流程

```
1. 加载 elite 因子
2. 信号合成（等权 / 因子加权）
3. 因子正交化（最大相关系数 < 0.7）
4. 衰减检验（最大衰减率 < 30%）
5. 组合构建 + 夏普计算
6. Verifier 判定
7. 信号输出
```

---

## 5. 因子演化流程

### 5.1 单标模式

```bash
# 以沪深300ETF（510300）为例
fts evolution run --symbol 510300 --max-generations 5
```

单标模式使用**时序评估**：

- **IC**：信号与未来收益的 Spearman 秩相关
- **单调性**：分 10 组，组序与组收益的 Spearman 秩相关 ≥ 0.5（p<0.05）
- **多空收益**：基于信号分位数构建时变权重（非恒定收益）

### 5.2 横截面模式

```bash
# 沪深300 成分股横截面演化
fts evolution run --universe csi300 --max-stocks 20
```

横截面模式使用**截面评估**：

- **IC**：每期截面 Spearman IC 的时序均值
- **多空收益**：每期做多 top 20%，做空 bottom 20%
- **单调性**：自动标记为 True（横截面评估通过）

### 5.3 评估链阈值

| 级别 | 指标 | 阈值 |
|------|------|------|
| Level 1 | IC | > 0.03 |
| Level 1 | 夏普比率 | > 1.5 |
| Level 1 | 单调性 | Spearman ≥ 0.5 (p<0.05) |
| Level 2 | 经济逻辑维度 | ≥ 3/4 维度达标（每维≥3分） |
| Level 3 | Bonferroni 校正 p 值 | < 0.01 |
| Level 3 | 调整后 t 统计量 | > 2.0 |

### 5.4 熔断机制

- **Token 熔断**：`tokens > limit × circuit_breaker_token_ratio (2.0)`
- **低 IC 熔断**：连续 3 代 IC < 0.01
- **失败率熔断**：`(evaluated - promoted) / evaluated > 90%`（evaluated ≥ 10 时触发）

---

## 6. 调度器

### 6.1 默认任务

| 任务名 | Cron | 说明 |
|--------|------|------|
| `health_check` | `*/10 * * * *` | 每 10 分钟健康检查 |
| `l1_meta_loop` | `0 9 * * *` | 每日 09:00 L1 知识补给 |
| `l2_evolution_loop` | `0 23 * * *` | 每日 23:00 L2 因子演化 |
| `l3_portfolio_loop` | `0 6 * * 1` | 每周一 06:00 L3 组合构建 |

### 6.2 调度器命令

```bash
# 查看任务列表
fts scheduler list

# 启动调度器
fts scheduler run
```

---

## 7. Web UI 仪表盘

### 7.1 启动

```bash
fts ui
# 浏览器访问: http://127.0.0.1:9100
```

### 7.2 仪表盘内容

- **4 张指标卡**：系统健康、FTS 版本、今日 Token 消耗、Elite 因子数
- **3 循环状态卡**：L1/L2/L3 独立卡片（状态、run_id、更新时间、Token、错误信息）
- **Elite 因子表**：因子 ID、名称、代数、IC、夏普、来源
- **自动刷新**：每 10 秒轮询一次

### 7.3 API 端点

| 端点 | 返回 |
|------|------|
| `GET /` | 仪表盘 HTML |
| `GET /api/status` | 系统状态 JSON（循环详情、因子计数） |
| `GET /api/factors` | Elite 因子列表 JSON |
| `GET /health` | 健康检查 JSON |

---

## 8. 配置参考

### 8.1 配置文件

`config/settings.yaml`：

```yaml
# ── 数据配置 ──
default_market: "stock"

# ── LLM 配置 ──
llm_backend: "openai"

# ── 演化配置 ──
max_generations: 10
population_size: 20
micro_trials_per_generation: 50

# ── L1 Meta-Loop ──
meta_loop_interval_hours: 24
meta_loop_max_tokens: 8000

# ── L3 Portfolio ──
portfolio_max_factors: 20
portfolio_top_n: 5
portfolio_decay_days: 90

# ── 日志 ──
log_level: "INFO"
log_file: "logs/fts.log"
```

### 8.2 配置加载优先级

1. **环境变量**（`FTS_*` 前缀，最高优先级）
2. **YAML 配置文件**（`config/settings.yaml`）
3. **代码默认值**（最低优先级）

### 8.3 环境变量覆盖

所有配置项均可通过 `FTS_<大写名称>` 环境变量覆盖：

```bash
# 示例
set FTS_LOG_LEVEL=DEBUG
set FTS_MAX_WORKERS=8
set FTS_MEMORY_DIR=D:\data\fts_memory
```

---

## 9. 数据源

### 9.1 腾讯自选股 API（默认）

- **协议**：HTTP REST（qt.gtimg.cn / web.ifzq.gtimg.cn）
- **数据内容**：A 股和 ETF 日线 OHLCV
- **Python 依赖**：无额外依赖（标准库 httpx）
- **代码适配**：`fts/data_mcp.py` — `MCPDataProvider`
- **统一入口**：`fts/data.py` — `FTSDataProvider`

### 9.2 使用方式

```python
from fts.data_mcp import MCPDataProvider

provider = MCPDataProvider()
df = provider.get_ohlcv("510300", days=250)  # 沪深300ETF
print(df.head())
```

### 9.3 数据降级

网络不可用时自动降级到**合成数据**，确保系统在离线环境也可测试运行。

---

## 10. 环境变量

### 10.1 LLM 配置

存储在 `.env` 文件（已加入 `.gitignore`，不会提交）：

```ini
# FTS 环境变量配置
OPENAI_API_KEY=sk-your-api-key-here
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

### 10.2 FTS 专有环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FTS_MEMORY_DIR` | 运行时持久化目录 | `memory` |
| `FTS_ELITE_DIR` | 精英因子存储目录 | `memory/knowledge/factors/elite` |
| `FTS_CONFIG_FILE` | YAML 配置文件路径 | `config/settings.yaml` |
| `FTS_DEFAULT_MARKET` | 默认市场 | `stock` |
| `FTS_LLM_BACKEND` | LLM 后端 | `""`（自动检测） |
| `FTS_LOG_LEVEL` | 日志级别 | `INFO` |
| `FTS_LOG_FILE` | 日志文件路径 | `""`（仅控制台） |
| `FTS_MAX_WORKERS` | 最大并行数 | `4` |

### 10.3 启动脚本

使用 `start_fts.ps1`（PowerShell）自动加载 `.env` 文件：

```powershell
.\start_fts.ps1
```

---

## 11. 种子因子

系统内置 9 个种子因子，作为演化初始池：

| 因子名 | 类型 | 说明 |
|--------|------|------|
| `momentum` | 动量 | 过去 N 日收益率，捕捉趋势延续 |
| `volatility_reversion` | 波动率回归 | 布林带宽度偏离，均值回归信号 |
| `volume_confirmation` | 量价确认 | 价格突破 + 成交量确认 |
| `seasonality` | 季节效应 | 月度日历效应（如月初效应） |
| `fundamental_momentum` | 基本面动量 | 基于可计算的基本面指标趋势 |
| `sentiment` | 情绪 | 基于价格反转的情绪代理 |
| `liquidity` | 流动性 | 换手率和交易量变化 |
| `tail_risk` | 尾部风险 | 极端收益后的反转效应 |
| `sector_rotation` | 板块轮动 | 基于板块相对强度的轮动 |

查看所有种子因子：

```python
from fts.factor_engine.seed_pool import SeedPool
pool = SeedPool()
print(pool.list_names())
print(f"种子数: {pool.count()}")
```

---

## 12. 故障排除

### 12.1 100% 失败率熔断

**症状**：`失败率熔断: 100.00% > 90.00%`

**原因**：
- 数据不足或数据质量问题
- 评估链阈值过高（IC>0.03 / Sharpe>1.5）
- 种子因子与标的不适配

**排查**：
```bash
# 1. 检查数据是否正常加载
python -c "from fts.data_mcp import MCPDataProvider; df = MCPDataProvider().get_ohlcv('510300', days=250); print(df.shape)"

# 2. 查看评估失败的详细原因
fts monitor --json

# 3. 如果使用 MockLLMClient，考虑配置真实 LLM
# 检查 .env 文件中的 OPENAI_API_KEY 是否已设置
```

**修复**：
- 确认 `.env` 文件包含正确的 API Key
- 运行 `.\start_fts.ps1` 再试
- 或手动设置环境变量：`$env:OPENAI_API_KEY="sk-xxx"`

### 12.2 状态文件残留

**症状**：连续运行多次后立即熔断（generations=0）

**原因**：状态文件累计了前次运行的 `evaluated` 计数

**修复**：
```bash
# 清除演化状态（重新开始）
Remove-Item -Recurse -Force memory/evolution/state.json
```

### 12.3 CLI 命令找不到

**症状**：`fts: The term 'fts' is not recognized`

**原因**：未安装包或 PATH 未更新

**修复**：
```bash
# 重新安装
pip install -e .

# 或使用模块模式运行
python -m fts.cli version
```

### 12.4 数据源连接失败

**症状**：数据获取超时或返回空

**原因**：网络问题或腾讯 API 限制

**修复**：系统自动降级到合成数据，不影响正常运行。如需真实数据，检查网络连接。

### 12.5 LLM 返回 Mock

**症状**：日志显示 `LLM backend: MockLLMClient`

**原因**：未配置 LLM API Key

**修复**：
```bash
# 设置 DeepSeek API（推荐）
$env:OPENAI_API_KEY="sk-你的key"
$env:OPENAI_BASE_URL="https://api.deepseek.com/v1"
$env:OPENAI_MODEL="deepseek-chat"

# 或使用 start_fts.ps1 自动加载
.\start_fts.ps1
```

---

## 13. 工程指标

| 指标 | 值 |
|------|:---:|
| **版本** | v1.1.0 |
| **测试通过数** | 1184 / 1184（100%）|
| **测试覆盖率** | 91%（模块级）|
| **代码行数** | ~4,380 语句 |
| **种子因子数** | 9 个 |
| **测试文件** | 35+ 个 |
| **调度任务** | 4 个（L1/L2/L3 + Health Check）|

---

## 附录

### A. 项目结构

```
factor_system/
├── fts/                          # 核心源码
│   ├── cli.py                    # 统一命令行入口
│   ├── data.py                   # 数据层统一入口（FTSDataProvider）
│   ├── data_mcp.py               # 腾讯自选股 MCP 适配层
│   ├── llm.py                    # LLM 客户端（OpenAI/Anthropic/Mock）
│   ├── config/                   # 配置系统
│   ├── core/                     # 核心契约（enums + TypedDict）
│   ├── factor_engine/            # 因子引擎（三层循环）
│   │   ├── evolution_loop.py     # L2 演化主循环
│   │   ├── meta_loop.py          # L1 元循环
│   │   ├── portfolio_loop.py     # L3 组合循环
│   │   ├── evaluation_chain.py   # 三级评估链
│   │   ├── macro_evolution.py    # 宏观演化（LLM 改逻辑）
│   │   ├── micro_evolution.py    # 微观演化（optuna 调参）
│   │   ├── seed_pool.py          # 种子池
│   │   ├── state.py              # 状态管理 + trace_id
│   │   ├── experience_chain.py   # 经验链
│   │   ├── verifier.py           # Verifier 判定
│   │   ├── contracts.py          # 契约层（TypedDict）
│   │   ├── walk_forward.py       # 走航验证
│   │   └── monitor.py            # 状态查询
│   ├── monitor/                  # 监控系统
│   │   ├── __init__.py           # check_all_status / SystemStatusReport
│   │   ├── http_server.py        # Web UI 仪表盘
│   │   └── elite_tracker.py      # Elite 因子跟踪
│   ├── scheduler/                # 调度器
│   │   ├── engine.py             # APScheduler 引擎
│   │   ├── tasks.py              # 任务注册表
│   │   └── watchdog.py           # 看门狗
│   ├── pipeline/                 # 因子推演管线
│   └── strategies/               # 策略层
├── config/                       # 配置文件
│   ├── settings.yaml             # YAML 配置
│   └── .gitignore
├── tests/                        # 测试（35+ 文件，1184 用例）
├── docs/                         # 文档
│   ├── FTS_manual.md             # ← 本文档
│   ├── CODE_WIKI.md              # 代码 Wiki
│   ├── production_plan.md        # 生产就绪计划
│   └── harness/                  # Harness 工程规范
├── memory/                       # 运行时持久化（自动创建）
├── .env                          # 环境变量（敏感信息，已 gitignore）
└── start_fts.ps1                 # 启动脚本
```

### B. 快速参考

```bash
# 日常使用流程
.\start_fts.ps1                    # 1. 加载环境变量
fts version                         # 2. 确认版本
fts monitor                         # 3. 查看系统状态
fts evolution run --symbol 510300   # 4. 启动因子演化
fts portfolio run                   # 5. 构建组合
fts ui                              # 6. 打开仪表盘监控

# 查看帮助
fts --help
fts evolution run --help
```
