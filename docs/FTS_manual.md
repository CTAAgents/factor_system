# FTS 用户手册

> **版本 1.6.0 · 最后更新 2026-08-03**

FTS 是一个**因子智能系统**——它每天自动分析市场数据，生成交易信号，帮你做量化投资决策。支持 A 股/ETF 和**期货**双市场因子演化与信号生成。

---

## 目录

- [快速上手](#快速上手)
- [日常使用流程](#日常使用流程)
- [FTS 的一天](#fts-的一天)
- [命令速查](#命令速查)
- [配置指南](#配置指南)
- [常见问题](#常见问题)
- [更多资料](#更多资料)

---

## 快速上手

### 第一步：安装

```bash
# 克隆项目
git clone https://github.com/CTAAgents/factor_system.git
cd factor_system

# 安装基础版
pip install -e .

# 或者安装完整版（推荐）
pip install -e ".[evolution,llm,mcp,dev]"
```

### 第二步：配置 API Key

系统需要一个 LLM（大语言模型）来驱动因子演化。创建一个 `.env` 文件：

```ini
# .env 文件（已自动加入 .gitignore，不会泄露）
OPENAI_API_KEY=sk-你的key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

然后运行启动脚本加载配置：

```powershell
.\start_fts.ps1
```

> **没有 API Key 也能用**：系统会使用 MockLLMClient 模拟运行，但不会产生真正的因子。

### 第三步：验证安装

```powershell
# 查看版本
fts version

# 看到类似输出就对了:
# FTS version: 1.6.0
# Factor engine version: 1.6.0
```

> 如果提示 `fts` 命令找不到，请使用模块模式：
> ```powershell
> python -m fts.cli version
> ```

### 第四步：启动因子演化

系统支持**股票**和**期货**双市场因子演化：

**股票模式**（以沪深 300 ETF 为例）：
```powershell
fts evolution run --symbol 510300 --max-generations 3
```

**期货横截面模式**（跨品种因子演化）：
```powershell
fts evolution run --universe futures --max-generations 3
```

系统会：
1. 从腾讯自选股/东方财富 API 获取 A 股/ETF 数据，或从 DuckDB 加载期货截面数据
2. 用 482 个种子因子（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面）开始演化
3. 期货模式额外加载 12 大因子家族 50+ 子因子（期限结构、动量、波动率等）
4. 每代尝试改进因子代码和参数
5. 输出评估结果

### 第五步：打开监控仪表盘

```powershell
fts ui
```

浏览器访问 `http://127.0.0.1:9100`，可以看到：
- 系统健康状态
- 三个循环的运行状态
- Elite 因子列表
- Token 消耗统计

---

## 日常使用流程

### 你每天该做什么

```
早晨 08:30 → L1 Meta-Loop 自动运行（市场感知 + 知识补给）
晚间 20:00 → L3 Portfolio 自动运行（组合构建 + 信号合成）
晚间 20:30 → 期货信号管道自动运行（横截面信号报告）
晚间 23:00 → L2 Evolution 自动运行（因子演化）
随时       → fts ui 打开仪表盘查看状态
```

### 如果想手动操作

```powershell
# 1. 加载配置
.\start_fts.ps1

# 2. 查看系统状态
fts monitor

# 3. 运行因子演化（单只股票）
fts evolution run --symbol 000001 --max-generations 5

# 4. 运行横截面演化（沪深300全部成分股）
fts evolution run --universe csi300 --max-stocks 20

# 5. 运行期货横截面因子演化
fts evolution run --universe futures --max-generations 3

# 6. 查看产出的因子
fts factor list

# 7. 查看某个因子的详情
fts factor show fct_3f9a2b1c
```

### 新手推荐路线

```powershell
# 第1天：熟悉系统
fts version              # 看版本
fts monitor              # 看状态
fts scheduler list       # 看有哪些定时任务

# 第2天：跑一次因子演化
fts evolution run --symbol 510300 --max-generations 3

# 第3天：查看结果
fts factor list          # 看有没有 elite 因子
fts ui                   # 打开仪表盘

# 第4天：构建组合
fts portfolio run
```

---

## FTS 的一天

系统每天自动运行三个循环 + 信号管道，不需要你手动操作。

### 🌅 早上 08:30 — L1 Meta-Loop（市场感知 + 知识补给）

系统起床后的第一件事：看看今天市场发生了什么。

```
1. 联网收集财经新闻和市场数据
2. 生成市场摘要（今天涨了还是跌了？有什么大事？）
3. 如果发现新的交易模式，生成候选因子
4. 把候选因子注入到演化池
```

这步叫"知识补给"——相当于研究员早上先看一遍新闻。

### 🌙 晚上 23:00 — L2 Evolution Loop（因子演化）

核心环节。系统通宵干活，寻找赚钱的因子。

```
对每一代（默认最多 10 代）:
  Step 1: LLM 改逻辑
    - 拿一个种子因子，让 AI 分析它的优缺点
    - AI 写出新的因子代码
  Step 2: 参数调优
    - optuna 自动搜索最优参数（100 次尝试）
  Step 3: 三级评估
    - Level 1: 回测验证（IC > 0.03？夏普 > 1.5？）
    - Level 2: 经济逻辑评分（有经济学道理吗？）
    - Level 3: 多重检验（不是碰巧的吧？）
  Step 4: 通过 → 晋升 Elite 精英池
         未通过 → 记录失败原因，下一轮改进

如果连续失败太多 → 熔断，停止演化
```

### 📊 晚上 20:00 — L3 Portfolio Loop（组合构建）

把 Elite 因子组合成交易信号，每日运行。

```
1. 加载所有 elite 因子
2. 信号合成
3. 剔除相关度太高的因子（正交化）
4. 计算衰减（过时的因子降低权重）
5. 输出最终组合信号
```

### 📈 晚上 20:30 — 期货信号管道

独立生成期货横截面信号报告，每日运行。

```
1. 加载期货 Elite 因子（IC>0.3 顶级因子）
2. 从 DuckDB 加载 59 个期货品种日线数据
3. 方向校正（截面 IC 法，自动反转 IC<0 的因子）
4. IC 加权合成综合得分
5. 输出排名 + 信号报告到 reports/{date}/
```

### ⏰ 每 10 分钟 — 健康检查

系统每 10 分钟检查一次三个循环的状态，确保一切正常。

---

## 命令速查

### 系统管理

| 命令 | 作用 |
|------|------|
| `fts version` | 查看版本号 |
| `fts monitor` | 查看系统状态 |
| `fts ui` | 打开 Web 仪表盘 |
| `fts --help` | 查看所有命令 |

### 因子演化

| 命令 | 作用 |
|------|------|
| `fts evolution run` | 启动演化（默认标的 000001） |
| `fts evolution run --symbol 510300` | 对沪深 300 ETF 做演化 |
| `fts evolution run --max-generations 5` | 只跑 5 代 |
| `fts evolution run --universe csi300` | 横截面模式（沪深 300 成分股） |
| `fts evolution run --universe futures` | 期货横截面模式（跨品种因子演化） |
| `fts evolution run --universe csi300 --max-stocks 20` | 横截面模式，限 20 只股票 |

### 因子管理

| 命令 | 作用 |
|------|------|
| `fts factor list` | 列出所有 elite 因子 |
| `fts factor show <因子ID>` | 查看因子详情 |

### 其他

| 命令 | 作用 |
|------|------|
| `fts meta-loop run` | 手动运行 L1 市场感知 |
| `fts portfolio run` | 手动运行 L3 组合构建 |
| `fts scheduler list` | 查看定时任务 |
| `fts scheduler run` | 启动定时调度器 |

### Web UI 仪表盘

```powershell
fts ui                          # 启动，默认 http://127.0.0.1:9100
fts ui --port 8080              # 换端口
fts ui --host 0.0.0.0           # 局域网可访问
```

仪表盘自动每 10 秒刷新一次，包含：
- **4 个指标卡**：系统健康、版本号、Token 用量、Elite 因子数
- **3 个循环状态卡**：L1/L2/L3 各自的运行状态、最近运行时间、错误信息
- **因子列表**：所有 elite 因子的 ID、名称、IC、夏普

---

## 配置指南

### 配置文件在哪

`config/settings.yaml` —— 大部分情况不用动。

```yaml
# 主要配置项
default_market: "stock"               # 默认市场类型（stock/futures）
max_generations: 10                   # 每轮演化最大代数
micro_trials_per_generation: 50       # 每代调参次数
log_level: "INFO"                     # 日志级别
```

### 环境变量

最重要的环境变量（存在 `.env` 文件里）：

```ini
OPENAI_API_KEY=sk-xxx                # LLM API Key（必须）
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

其他可选环境变量：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FTS_MEMORY_DIR` | 数据存储目录 | `memory` |
| `FTS_LOG_LEVEL` | 日志级别 | `INFO` |
| `FTS_MAX_WORKERS` | 并行数 | `4` |

### 数据源

**A 股/ETF**：系统从**腾讯自选股 API**（qt.gtimg.cn）和**东方财富 API**（akshare）获取日线数据。

**期货**：系统从本地 DuckDB 数据库（`data/fts_history.duckdb`）读取 59 个期货品种日线数据，数据通过 AKShare `futures_zh_daily_sina` 下载。

**期货基本面**：系统支持通过 AKShare 获取库存、仓单、基差等基本面数据。

- 不需要额外的账号或 Key
- 网络不可用时自动使用**合成数据**（不影响测试运行）
- 数据源 3 级降级：DuckDB → AKShare → 合成数据

---

## 常见问题

### 1. `fts` 命令找不到

```powershell
# 方案 A：重新安装
pip install -e .

# 方案 B：用模块模式
python -m fts.cli version
```

### 2. 因子演化总是熔断

```
失败率熔断: 100.00% > 90.00%
```

最常见的原因：**没有配置真实的 LLM API Key**。

```powershell
# 检查当前 LLM 类型
fts monitor
# 如果看到 "LLM backend: MockLLMClient" → 没配 Key

# 修复：设置环境变量
$env:OPENAI_API_KEY="sk-你的key"
$env:OPENAI_BASE_URL="https://api.deepseek.com/v1"

# 或使用启动脚本
.\start_fts.ps1
```

### 3. 多次运行后立即熔断

旧的状态文件没有清理。如果是测试环境：

```powershell
Remove-Item memory/evolution/state.json
```

### 4. 怎么看因子好不好

```powershell
# 列出所有 elite 因子
fts factor list

# 查看某个因子的详细评估报告
fts factor show fct_xxxx

# 重点关注：
# - IC（信息系数）：> 0.03 算不错
# - Sharpe（夏普比率）：> 1.5 算不错
# - monotonicity（单调性）：True 才好
```

### 5. 怎么获取真实数据

A 股/ETF 使用腾讯自选股公开 API，不需要额外配置。
期货数据需要先运行下载脚本：

```powershell
python scripts/download_futures.py
```

验证数据是否正常：

```powershell
# A 股
python -c "from fts.data import FTSDataProvider; df = FTSDataProvider().get_ohlcv('510300', days=250); print(df.shape)"

# 期货
python -c "from fts.data import FTSDataProvider; panel, dates = FTSDataProvider().get_futures_panel(['RB0','TA0'], days=250); print(len(panel))"
```

### 6. 怎么看系统在干什么

```powershell
# 文本界面
fts monitor

# Web 界面（推荐）
fts ui
# 打开 http://127.0.0.1:9100
```

---

## 更多资料

| 资源 | 位置 |
|------|------|
| 代码 Wiki | `CODE_WIKI.md` |
| 执行模式流程图 | `docs/execution_modes_flowchart.md` |
| 业务流程图 | `docs/business_flow.md` |
| 生产部署 | `docs/production_plan.md` |
| 工程规范 | `docs/harness/` |
| 角色职责 | `agents/fts-agent.md` |
| 策略手册 | `docs/strategy_manual.md` |
| README | `README.md` |

### 工程指标

| 指标 | 值 |
|------|:---:|
| 版本 | v1.6.0 |
| 测试通过 | 1502 / 1502（100%）|
| 覆盖率 | 99%（46 个模块）|
| 种子因子 | 482（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面）|
| 期货专用种子 | 12 大因子家族 50+ 子因子 |
| 定时任务 | 5 个 |

### 项目结构（简版）

```
factor_system/
├── fts/                   # 核心代码
│   ├── cli.py             # 命令行入口
│   ├── data.py            # 统一数据层（A 股/ETF/期货）
│   ├── data_mcp.py        # MCP 数据适配（akshare 腾讯/东方财富）
│   ├── data_futures.py    # 期货数据适配（DuckDB + AKShare）
│   ├── data_futures_fundamental.py  # 期货基本面数据
│   ├── llm.py             # AI 客户端
│   ├── factor_engine/     # 因子引擎（核心）
│   │   ├── seed_data/     # 种子因子库（WQ101 + Qlib158 + GTJA191）
│   │   ├── evolution_loop.py    # L2 演化主循环
│   │   ├── meta_loop.py         # L1 元循环
│   │   ├── portfolio_loop.py    # L3 组合循环
│   │   ├── seed_data_futures.py # 期货基础种子因子
│   │   └── seed_data_futures_full.py # 期货全量种子因子（12 家族）
│   ├── monitor/           # 监控 + Web UI
│   └── scheduler/         # 定时任务调度
├── scripts/               # 辅助脚本
│   ├── download_futures.py          # 期货数据下载
│   ├── futures_signal_pipeline.py   # 期货信号管道
│   ├── futures_strategy.py          # 期货组合策略
│   ├── futures_l3_portfolio.py      # 期货 L3 组合构建
│   └── run_futures_evolution.py     # 期货因子演化启动
├── config/                # 配置文件
├── tests/                 # 测试（1502 个）
├── docs/                  # 文档
│   └── harness/           # 工程规范
├── reports/               # 信号报告（按日期分文件夹）
└── memory/                # 运行时数据（自动创建）
```