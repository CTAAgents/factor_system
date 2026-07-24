# FTS — Factor Intelligence System 使用手册

> **版本**: 1.1.0 | **Python**: 3.10+ | **入口**: `python -m fts.cli` | **数据源**: MCP/akshare (腾讯/东方财富)

---

## 目 录

1. [什么是 FTS？](#1-什么是-fts)
2. [准备工作](#2-准备工作)
3. [安装步骤](#3-安装步骤)
4. [第一次运行](#4-第一次运行)
5. [演化模式详解](#5-演化模式详解)
6. [查看运行结果](#6-查看运行结果)
7. [配置说明](#7-配置说明)
8. [CLI 命令大全](#8-cli-命令大全)
9. [常见问题](#9-常见问题)
10. [术语解释](#10-术语解释)

---

## 1. 什么是 FTS？

### 1.1 FTS 是什么

FTS（Factor Intelligence System）是一个 **AI 自动挖掘投资因子的系统**。

简单来说，投资中有一种叫做"因子"的东西，比如：
- **动量因子**：过去涨得好的股票，未来可能继续涨
- **价值因子**：低估值个股，可能被市场低估
- **反转因子**：过去跌得太多，可能会反弹

FTS 就是让 AI 自动发现这些因子，并且不断进化改进它们。

### 1.2 FTS 能做什么

✅ **自动发现新因子**：AI 从市场数据中发现人类想不到的因子
✅ **因子进化**：让因子不断变得更强、更稳定
✅ **A 股 + ETF 支持**：支持沪深 300 成分股、ETF 等多种标的
✅ **信号输出**：生成可直接用于交易的信号

### 1.3 系统架构

FTS 有三层循环，就像一个工厂：

```
第一层：L1 Meta-Loop（每天 09:00）
├── 市场感知：看看最近市场发生了什么
├── Bootstrapping：生成初始因子想法
└── Debate 分析：多角度讨论因子质量

第二层：L2 Evolution Loop（每晚 23:00）
├── Macro(LLM)：AI 修改因子逻辑
├── Micro(optuna)：自动调参优化
└── 三级评估链：严格检验因子质量

第三层：L3 Portfolio Loop（每周一 06:00）
├── 精英因子加载：选出最好的因子
├── 正交化处理：避免因子之间重复
└── 信号合成：生成最终交易信号
```

### 1.4 数据源

FTS 基于 **腾讯自选股 MCP (akshare)** 获取 A 股和 ETF 行情数据，无需额外配置数据基础设施。

---

## 2. 准备工作

### 2.1 检查电脑配置

- **操作系统**：Windows 10/11（推荐）
- **Python 版本**：Python 3.10 或更高版本
- **内存**：至少 8GB（推荐 16GB）
- **网络**：需要联网（获取数据和调用 AI）

### 2.2 安装 Python

如果你还没有安装 Python：

1. 打开 https://www.python.org/downloads/
2. 下载 Python 3.10 或更高版本
3. 安装时勾选 **"Add Python to PATH"**

### 2.3 获取 AI API Key

FTS 需要调用 AI 来生成和修改因子，你可以选择：

**方案一（推荐）：DeepSeek**
1. 注册 DeepSeek 账号：https://platform.deepseek.com/
2. 创建 API Key
3. 保存好这个 Key（后面会用到）

**方案二：OpenAI**
1. 注册 OpenAI 账号：https://platform.openai.com/
2. 创建 API Key
3. 设置 `OPENAI_API_KEY` 环境变量

> 💡 **提示**：没有 API Key 也可以运行，FTS 会自动使用模拟 LLM 客户端，但演化效果会受限。

---

## 3. 安装步骤

### 3.1 打开命令行

按 `Win + R`，输入 `cmd`，按回车，打开命令提示符。

### 3.2 进入项目目录

```bash
cd d:\Programs\factor_system
```

### 3.3 创建虚拟环境

```bash
python -m venv .venv
```

这会创建一个独立的 Python 环境，避免和其他项目冲突。

### 3.4 激活虚拟环境

```bash
.venv\Scripts\activate
```

激活后，命令行前面会出现 `(.venv)`，表示已经进入虚拟环境。

### 3.5 安装 FTS 及数据依赖

```bash
# 安装 FTS（含 MCP 数据源支持）
pip install -e ".[mcp]"

# 如果要使用 LLM 因子演化：
pip install -e ".[llm,evolution]"

# 如果要全部安装：
pip install -e ".[llm,evolution,mcp,dev]"
```

### 3.6 验证安装

```bash
python -m fts.cli version
```

如果看到版本号，说明安装成功！

```
FTS version: 1.1.0
Factor engine version: 8.10.0
Config memory_dir: memory
```

---

## 4. 第一次运行

### 4.1 设置环境变量

在命令行中输入：

```powershell
# 必须设置（API Key）
$env:OPENAI_API_KEY = "你的 API Key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:OPENAI_MODEL = "deepseek-chat"
```

> ⚠️ **注意**：每次打开新的命令行窗口都需要重新设置。如果想永久保存，请参考 [7.3 永久设置环境变量](#73-永久设置环境变量)。

### 4.2 运行模式概览

FTS 支持两种演化模式：

| 模式 | 适用场景 | 命令 |
|------|----------|------|
| **单标模式** | 分析单只股票（如平安银行 000001） | `--universe single --symbol 000001` |
| **沪深 300 横截面** | 多只股票一起分析（推荐） | `--universe csi300 --max-stocks 30` |

### 4.3 🌟 完整示例：沪深 300 ETF 因子演化

以下以 **沪深 300ETF（510300）** 为例，演示完整的因子演化流程。

#### 步骤 1：查看 ETF 行情数据

先确认 MCP 数据源能正常获取数据：

```python
# 运行一个快速测试（在 Python 交互环境中）
from fts.data_mcp import MCPDataProvider
provider = MCPDataProvider()
df = provider.get_ohlcv("510300", days=250)
print(df.head())
print(f"行数: {len(df)}")
print(f"日期范围: {df.index[0]} ~ {df.index[-1]}")
```

有网络时输出示例（真实 510300 收盘价约 3.9 元）：
```
                open    high     low   close     volume
date
2026-07-17  3.9180  3.9240  3.9040  3.9210  187520600
2026-07-20  3.9210  3.9400  3.9090  3.9250  212345800
2026-07-21  3.9250  3.9450  3.9120  3.9380  198765400
2026-07-22  3.9380  3.9550  3.9200  3.9420  234567900
2026-07-23  3.9420  3.9580  3.9280  3.9450  215678300

行数: 250
日期范围: 2025-10-01 ~ 2026-07-23
```

> ⚠️ **注意**：如果网络不可用，FTS 会自动使用**合成数据**降级运行。合成数据是随机生成的数值，**非真实行情**，仅用于测试和验证系统功能。在生产使用前请确保网络连通。

#### 步骤 2：全市场演化（单标模式）

对沪深 300ETF 单独进行因子发现和演化：

```powershell
python -m fts.cli evolution run --symbol 510300 --max-generations 5
```

运行后会看到类似这样的输出：

```
[evolution] trace_id=ftr_a1b2c3d4_20260724T120000 run_id=run_b3c4d5e6_20260724T120000
[evolution] max_generations=5
[evolution] symbol=510300
[evolution] data shape: (500, 5), forward_returns: 500
[evolution] LLM backend: MockLLMClient

┌───────┬──────────┬────────┬────────┬──────────────┬───────┐
│  Gen  │    IC    │  ICIR  │ Sharpe │ Monotonicity │ Pass  │
├───────┼──────────┼────────┼────────┼──────────────┼───────┤
│   0   │  0.0523  │  1.24  │  1.85  │    True      │  YES  │
│   1   │  0.0618  │  1.56  │  2.12  │    True      │  YES  │
│   2   │  0.0487  │  1.18  │  1.67  │    True      │  YES  │
│   3   │  0.0712  │  1.82  │  2.41  │    True      │  YES  │
│   4   │  0.0554  │  1.31  │  1.78  │    True      │  YES  │
└───────┴──────────┴────────┴────────┴──────────────┴───────┘

[evolution] 完成: status=completed generations=5 elite_count=2
[evolution] Tokens consumed: 4393
```

#### 步骤 3：横截面演化（沪深 300 成分股）

同时分析沪深 300 ETF 的成分股，找到能选股的优质因子：

```powershell
python -m fts.cli evolution run --universe csi300 --max-stocks 50 --max-generations 10
```

运行输出：
```
[evolution] trace_id=ftr_d5e6f7g8_20260724T120000 run_id=run_e6f7g8h9_20260724T120000
[evolution] max_generations=10
[evolution] universe=csi300 (max_stocks=50)
[evolution] panel symbols=50, common_dates=245

[evolution] LLM backend: MockLLMClient

┌───────┬──────────┬────────┬────────┬──────────────┬───────┐
│  Gen  │    IC    │  ICIR  │ Sharpe │ Monotonicity │ Pass  │
├───────┼──────────┼────────┼────────┼──────────────┼───────┤
│   0   │  0.0483  │  1.12  │  1.65  │    True      │  YES  │
│   1   │  0.0536  │  1.28  │  1.82  │    True      │  YES  │
│   2   │  0.0601  │  1.47  │  2.01  │    True      │  YES  │
│  ...  │   ...    │  ...   │  ...   │    ...       │  ...  │
│   9   │  0.0659  │  1.61  │  2.18  │    True      │  YES  │
└───────┴──────────┴────────┴────────┴──────────────┴───────┘

[evolution] 完成: status=completed generations=10 elite_count=5
[evolution] Tokens consumed: 15230
```

> 💡 **提示**：横截面模式下 IC 更稳定，因为多只股票提供了更丰富的信号。

#### 步骤 4：查看精英因子

运行完成后，查看生成的精英因子：

```powershell
python -m fts.cli factor list
```

输出示例：
```
=== Elite Factors (5) ===
  - fct_a1b2c3d4 | momentum_etf_v2       | gen=3
  - fct_e5f6g7h8 | volume_flow_adjusted   | gen=5
  - fct_i9j0k1l2 | volatility_reversion   | gen=7
  - fct_m3n4o5p6 | macro_momentum         | gen=9
  - fct_q7r8s9t0 | quality_trend          | gen=10
```

#### 步骤 5：构建投资组合

使用精英因子构建组合信号：

```powershell
python -m fts.cli portfolio run
```

---

## 5. 演化模式详解

### 5.1 单标模式

**适用场景**：分析单只股票或 ETF，比如沪深 300ETF

```bash
# 沪深 300 ETF
python -m fts.cli evolution run --symbol 510300 --max-generations 5

# 单只股票
python -m fts.cli evolution run --symbol 000001 --max-generations 5
```

**优点**：数据量小，运行快，适合快速验证
**缺点**：种子因子可能失效，导致 IC=0 熔断

### 5.2 沪深 300 横截面模式（推荐）

**适用场景**：同时分析多只股票，挖掘选股因子

```bash
# 30 只股票
python -m fts.cli evolution run --universe csi300 --max-stocks 30 --max-generations 10

# 全部 76 只代表股
python -m fts.cli evolution run --universe csi300 --max-stocks 0 --max-generations 10
```

**优点**：IC 更稳定，不容易熔断，因子更通用
**缺点**：数据量大，运行时间长

### 5.3 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--max-generations` | 演化代数，越大因子越好但越慢 | 10 |
| `--symbol` | 单标模式下的品种代码（股票/ETF） | 000001 |
| `--universe` | 模式选择：single / csi300 | single |
| `--max-stocks` | 横截面模式的品种数量，0=全部 | 50 |

### 5.4 支持的标的类型

| 类型 | 代码格式 | 例子 |
|------|----------|------|
| 沪深 A 股 | 6 位数字 | 000001（平安银行）、600519（贵州茅台） |
| 沪深 ETF | 6 位数字，51/56/58/159 开头 | 510300（沪深 300ETF）、159915（创业板 ETF） |
| 科创板 | 688xxx | 688008（澜起科技） |

---

## 6. 查看运行结果

### 6.1 查看系统状态

```bash
python -m fts.cli monitor
```

输出示例：
```
=== FTS System Status ===
Overall healthy : YES
Checked at      : 2026-07-24T12:00:00
FTS version     : 8.10.0
Circuit broken  : NO
Stale (>24h)    : NO
Tokens today    : 15230

=== Loop Status ===
[OK]   L1  | status=unknown          | run_id=-                        | age=0.0h
[OK]   L2  | status=completed        | run_id=run_e6f7g8h9_20260724T120000 | age=0.1h
[OK]   L3  | status=unknown          | run_id=-                        | age=0.0h
```

### 6.2 查看精英因子

```bash
python -m fts.cli factor list
```

### 6.3 查看因子详情

```bash
python -m fts.cli factor show fct_a1b2c3d4
```

输出示例：
```json
{
  "factor_id": "fct_a1b2c3d4",
  "name": "momentum_etf_v2",
  "generation": 3,
  "code": "def factor_program(data, params):\n    ...",
  "params": {"window": 20},
  "economic_logic": {
    "theory": 4,
    "behavioral": 3,
    "microstructure": 4,
    "institutional": 4
  },
  "evaluation": {
    "level_1_backtest": {
      "ic": 0.0618,
      "icir": 1.56,
      "sharpe": 2.12,
      "max_drawdown": 0.12,
      "monotonicity": true
    },
    "level_2_economic": {"dimensions_passed": 4},
    "level_3_multiple": {"passed": true},
    "passed": true
  }
}
```

### 6.4 查看演化状态

```bash
cat memory/evolution/state.json
```

输出示例：
```json
{
  "run_id": "run_e6f7g8h9_20260724T120000",
  "started_at": "2026-07-24T12:00:00",
  "last_generation": 10,
  "total_factors_evaluated": 10,
  "total_factors_promoted": 5,
  "tokens_consumed": 15230,
  "budget_limit": 200000,
  "status": "completed"
}
```

---

## 7. 配置说明

### 7.1 环境变量配置

每次运行前需要设置以下变量：

```powershell
# OpenAI 兼容 API（推荐 DeepSeek）
$env:OPENAI_API_KEY = "你的 API Key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:OPENAI_MODEL = "deepseek-chat"

# Anthropic Claude（可选）
$env:ANTHROPIC_API_KEY = "你的 Anthropic Key"

# FTS 配置
$env:FTS_MEMORY_DIR = "memory"
$env:FTS_LOG_LEVEL = "INFO"
```

> FTS 会自动检测 LLM 后端：优先 OpenAI 兼容 API，其次 Anthropic，都没有时使用 MockLLMClient。

### 7.2 可选配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FTS_MAX_GENERATIONS` | 默认演化代数 | 10 |
| `FTS_MAX_WORKERS` | 并行工作数 | 4 |
| `FTS_LOG_LEVEL` | 日志级别（DEBUG/INFO/WARNING/ERROR） | INFO |
| `FTS_MEMORY_DIR` | 状态存储目录 | memory |
| `FTS_DEFAULT_MARKET` | 默认市场 | stock |

### 7.3 永久设置环境变量

如果你不想每次都输入，可以设置永久环境变量：

1. 右键 **"此电脑" → 属性 → 高级系统设置 → 环境变量**
2. 在 **"用户变量"** 中添加：
   - `OPENAI_API_KEY`
   - `OPENAI_BASE_URL` = `https://api.deepseek.com/v1`
   - `OPENAI_MODEL` = `deepseek-chat`
3. 点击确定，重启命令行

### 7.4 使用快速启动脚本

项目根目录提供了 `start_fts.ps1`，编辑后运行即可：

```powershell
# 编辑 start_fts.ps1，填入你的 API Key
$env:OPENAI_API_KEY = "sk-your-key-here"
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:OPENAI_MODEL = "deepseek-chat"

# 然后运行
.\start_fts.ps1
```

---

## 8. CLI 命令大全

### 8.1 版本信息

```bash
python -m fts.cli version
```

### 8.2 运行演化

```bash
# 单标的因子演化（默认 000001 平安银行）
python -m fts.cli evolution run --max-generations 10

# 指定股票
python -m fts.cli evolution run --symbol 600519 --max-generations 5

# 指定 ETF
python -m fts.cli evolution run --symbol 510300 --max-generations 5

# 沪深 300 横截面（30 只）
python -m fts.cli evolution run --universe csi300 --max-stocks 30 --max-generations 10
```

### 8.3 运行 Meta-Loop

```bash
python -m fts.cli meta-loop run
```

### 8.4 运行组合构建

```bash
python -m fts.cli portfolio run
```

### 8.5 查看系统状态

```bash
python -m fts.cli monitor
python -m fts.cli monitor --json    # JSON 格式输出
```

### 8.6 管理因子

```bash
python -m fts.cli factor list               # 列出精英因子
python -m fts.cli factor show <factor_id>   # 查看因子详情
```

### 8.7 调度器

```bash
python -m fts.cli scheduler run     # 启动调度器后台
python -m fts.cli scheduler list    # 列出已注册任务
```

---

## 9. 常见问题

### 9.1 运行时报错：API Key 无效

**原因**：API Key 没有设置或设置错误

**解决方法**：
```powershell
$env:OPENAI_API_KEY = "你的正确 API Key"
```

### 9.2 运行时报错：数据不可用

**原因**：网络不可用或 akshare 未安装

**解决方法**：
```bash
# 安装数据依赖
pip install -e ".[mcp]"

# 确认网络正常
python -c "import akshare; print(akshare.__version__)"
```

如果仍然不可用，FTS 会自动使用合成数据降级运行。

### 9.3 演化过程中熔断（Circuit broken）

**原因**：连续 3 代因子 IC 都很低（< 0.01）

**解决方法**：
1. 切换到横截面模式
```bash
python -m fts.cli evolution run --universe csi300 --max-stocks 30
```
2. 增加演化代数
```bash
python -m fts.cli evolution run --max-generations 20
```

### 9.4 没有精英因子晋级

**原因**：因子没有通过三级评估链

**解决方法**：
1. 查看失败轨迹：`cat memory/experience/failure/*.json`
2. 增加演化代数：`--max-generations 20`
3. 调整品种数量：`--max-stocks 50`

### 9.5 运行速度很慢

**原因**：品种数量太多或演化代数太大

**解决方法**：
1. 减少品种数量：`--max-stocks 20`
2. 减少演化代数：`--max-generations 5`
3. 先用单标模式快速测试，再用横截面模式跑正式

### 9.6 内存不足

**原因**：品种太多导致内存占用过大

**解决方法**：
```bash
python -m fts.cli evolution run --universe csi300 --max-stocks 30
```

---

## 10. 术语解释

### 10.1 因子（Factor）

因子是一种量化指标，用来预测资产的未来收益。比如动量因子认为过去涨得好的股票未来可能继续涨。

### 10.2 IC（Information Coefficient）

信息系数，衡量因子预测能力的指标。IC 值越大，因子预测能力越强。通常 IC > 0.03 是合格的。

### 10.3 Sharpe Ratio（夏普比率）

衡量因子风险调整后收益的指标。Sharpe > 1.5 是合格的。

### 10.4 横截面（Cross-Sectional）

同一时间点上多个品种的数据。比如同时看 30 只股票今天的因子值。

### 10.5 精英因子（Elite Factor）

通过三级评估链检验的优秀因子，被保存在精英因子池中。

### 10.6 演化（Evolution）

因子通过 AI 修改和参数优化，不断变得更好的过程。

### 10.7 熔断（Circuit Breaker）

当因子表现太差时，系统自动停止演化，避免浪费资源。

---

## 附录 A：沪深 300 代表股代码表（前 30 只）

| 代码 | 名称 | 代码 | 名称 |
|------|------|------|------|
| 000001 | 平安银行 | 000002 | 万科 A |
| 000333 | 美的集团 | 000568 | 泸州老窖 |
| 000651 | 格力电器 | 000725 | 京东方 A |
| 000858 | 五粮液 | 002027 | 分众传媒 |
| 002142 | 宁波银行 | 002304 | 洋河股份 |
| 002415 | 海康威视 | 002475 | 立讯精密 |
| 002594 | 比亚迪 | 300015 | 爱尔眼科 |
| 300059 | 东方财富 | 300124 | 汇川技术 |
| 300274 | 阳光电源 | 300750 | 宁德时代 |
| 600000 | 浦发银行 | 600030 | 中信证券 |
| 600036 | 招商银行 | 600276 | 恒瑞医药 |
| 600309 | 万华化学 | 600519 | 贵州茅台 |
| 600585 | 海螺水泥 | 600690 | 海尔智家 |
| 600809 | 山西汾酒 | 600887 | 伊利股份 |
| 600900 | 长江电力 | 601166 | 兴业银行 |

## 附录 B：常见 ETF 代码表

| 代码 | 名称 | 类型 |
|------|------|------|
| **510050** | 上证 50 ETF | 宽基 |
| **510300** | 沪深 300 ETF | 宽基 |
| **510500** | 中证 500 ETF | 宽基 |
| **512100** | 中证 1000 ETF | 宽基 |
| **588000** | 科创 50 ETF | 宽基 |
| **159915** | 创业板 ETF | 宽基 |
| **159949** | 创业板 50 ETF | 宽基 |
| **512880** | 证券 ETF | 行业 |
| **515790** | 光伏 ETF | 行业 |
| **516160** | 新能源 ETF | 行业 |
| **159992** | 创新药 ETF | 行业 |
| **159995** | 芯片 ETF | 行业 |
| **513050** | 中概互联 ETF | 跨境 |
| **513100** | 纳指 ETF | 跨境 |
| **518880** | 黄金 ETF | 商品 |
| **510880** | 红利 ETF | 策略 |
| **515050** | 5G ETF | 主题 |
| **517010** | 数字经济 ETF | 主题 |

## 附录 C：评估指标说明

### Level 1 回测指标

| 指标 | 说明 | 通过阈值 |
|------|------|----------|
| ic | 信息系数均值 | > 0.03 |
| icir | IC 比率 | > 0.5 |
| sharpe | 年化夏普比率 | > 1.5 |
| max_drawdown | 最大回撤 | < 0.2 |
| monotonicity | 十分位单调性 | True |
| turnover_monthly | 月度换手率 | < 0.5 |

### Level 2 经济逻辑

| 维度 | 说明 | 满分 | 达标 |
|------|------|------|------|
| theory | 理论基础 | 5 | ≥ 3 |
| behavioral | 行为偏差 | 5 | ≥ 3 |
| microstructure | 市场微观结构 | 5 | ≥ 3 |
| institutional | 制度因素 | 5 | ≥ 3 |

通过条件：四维评分 ≥ 3/4 维度达标

---

*文档版本：1.1.0 | 最后更新：2026-07-24*
