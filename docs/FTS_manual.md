# FTS — Factor Intelligence System 使用说明

> **版本**: 1.0.0 | **Python**: 3.10+ | **入口**: `python -m fts.cli` | **架构**: 三层循环

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
- **价值因子**：市盈率低的股票，可能被低估
- **反转因子**：过去跌得太多的股票，可能会反弹

FTS 就是让 AI 自动发现这些因子，并且不断进化改进它们。

### 1.2 FTS 能做什么

✅ **自动发现新因子**：AI 从市场数据中发现人类想不到的因子
✅ **因子进化**：让因子不断变得更强、更稳定
✅ **多品种支持**：支持 A 股、期货等多种市场
✅ **信号输出**：生成可直接用于交易的信号

### 1.3 系统架构

FTS 有三层循环，就像一个工厂：

```
第一层：L1 Meta-Loop（每天）
├── 市场感知：看看最近市场发生了什么
├── Bootstrapping：生成初始因子想法
└── 9-Agent 辩论：多个 AI 互相讨论因子好不好

第二层：L2 Evolution Loop（每晚）
├── Macro(LLM)：AI 修改因子逻辑
├── Micro(optuna)：自动调参优化
└── 三级评估链：严格检验因子质量

第三层：L3 Portfolio Loop（每周）
├── 精英因子加载：选出最好的因子
├── 正交化处理：避免因子之间重复
└── 信号合成：生成最终交易信号
```

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
3. 安装时勾选"Add Python to PATH"

### 2.3 获取 AI API Key

FTS 需要调用 AI 来生成和修改因子，你需要准备：

1. 注册 DeepSeek 账号：https://platform.deepseek.com/
2. 创建 API Key
3. 保存好这个 Key（后面会用到）

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

### 3.5 安装 FTS

```bash
pip install -e .
```

等待安装完成（可能需要几分钟）。

### 3.6 安装 Data-Core（数据源）

```bash
pip install -e d:\Programs\data-core
```

### 3.7 验证安装

```bash
python -m fts.cli version
```

如果看到版本号，说明安装成功！

---

## 4. 第一次运行

### 4.1 设置环境变量

在命令行中输入：

```powershell
$env:OPENAI_API_KEY = "你的 API Key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:OPENAI_MODEL = "deepseek-v4-flash"
```

⚠️ **注意**：每次打开新的命令行窗口都需要重新设置这三个变量。

### 4.2 选择运行模式

FTS 支持三种运行模式：

| 模式 | 适用场景 | 命令 |
|------|----------|------|
| 单标模式 | 单个期货品种或单只股票 | `--universe single --symbol RB` |
| A 股横截面 | 多只股票一起分析（推荐） | `--universe csi300 --max-stocks 30` |
| 期货横截面 | 多个期货品种一起分析 | `--universe futures --max-stocks 20` |

### 4.3 运行示例（期货横截面）

```powershell
python -m fts.cli evolution run --universe futures --max-stocks 20 --max-generations 3
```

### 4.4 运行过程说明

运行后会看到类似这样的输出：

```
[evolution] trace_id=xxx run_id=xxx
[evolution] max_generations=3
[evolution] universe=futures (max_stocks=20)
[evolution] panel symbols=20, common_dates=66

┌───────┬────────────┬──────────┬──────────┬──────────────┬───────┐
│  Gen  │    IC      │  ICIR    │  Sharpe  │  Monotonicity│  Pass │
├───────┼────────────┼──────────┼──────────┼──────────────┼───────┤
│   0   │   0.0523   │  1.24    │   1.85   │     True     │  YES  │
│   1   │   0.0618   │  1.56    │   2.12   │     True     │  YES  │
│   2   │   0.0487   │  1.18    │   1.67   │     True     │  YES  │
└───────┴────────────┴──────────┴──────────┴──────────────┴───────┘

[evolution] Elite factors promoted: 3
[evolution] Tokens consumed: 4393
[evolution] Status: completed
```

---

## 5. 演化模式详解

### 5.1 单标模式

**适用场景**：分析单个品种，比如只看螺纹钢

```bash
python -m fts.cli evolution run --universe single --symbol RB --max-generations 5
```

**优点**：数据量小，运行快
**缺点**：种子因子可能失效，导致 IC=0 熔断

### 5.2 A 股横截面模式（推荐）

**适用场景**：同时分析多只股票，挖掘选股因子

```bash
python -m fts.cli evolution run --universe csi300 --max-stocks 30 --max-generations 10
```

**优点**：IC 更稳定，不容易熔断
**缺点**：数据量大，运行时间长

**跑全部 CSI 300 成分股**：

```bash
python -m fts.cli evolution run --universe csi300 --max-stocks 0 --max-generations 10
```

### 5.3 期货横截面模式

**适用场景**：同时分析多个期货品种

```bash
python -m fts.cli evolution run --universe futures --max-stocks 20 --max-generations 10
```

**跑全部期货品种**（56个）：

```bash
python -m fts.cli evolution run --universe futures --max-stocks 0 --max-generations 10
```

### 5.4 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--max-generations` | 演化代数，越大因子越好但越慢 | 10 |
| `--symbol` | 单标模式下的品种代码 | 000001 |
| `--universe` | 模式选择：single/csi300/futures | single |
| `--max-stocks` | 横截面模式的品种数量，0=全部 | 50 |

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
Checked at      : 2026-07-21T12:49:49
FTS version     : 8.10.0
Circuit broken  : NO
Stale (>24h)    : NO
Tokens today    : 4393

=== Loop Status ===
[OK]   L1  | status=unknown          | run_id=-                        | age=0.0h
[OK]   L2  | status=completed        | run_id=run_0f772b04_20260720T140618 | age=22.7h
[OK]   L3  | status=unknown          | run_id=-                        | age=0.0h
```

### 6.2 查看精英因子

```bash
python -m fts.cli factor list
```

输出示例：

```
=== Elite Factors (3) ===
  - fct_a1b2c3d4 | 动量反转因子 | gen=5
  - fct_e5f6g7h8 | 资金流因子 | gen=8
  - fct_i9j0k1l2 | 波动率因子 | gen=12
```

### 6.3 查看因子详情

```bash
python -m fts.cli factor show fct_a1b2c3d4
```

输出示例：

```json
{
  "factor": {
    "factor_id": "fct_a1b2c3d4",
    "name": "动量反转因子",
    "generation": 5,
    "program": {
      "type": "technical",
      "expression": "(RSI(14) - SMA(RSI(14), 5)) / ATR(14)",
      "params": {"rsi_period": 14, "sma_period": 5}
    },
    "economic_logic": {
      "theory": 4,
      "behavioral": 3,
      "microstructure": 4,
      "institutional": 3
    }
  },
  "evaluation": {
    "level_1_backtest": {
      "ic": 0.085,
      "icir": 1.82,
      "sharpe": 2.35,
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
  "run_id": "run_0f772b04_20260720T140618",
  "started_at": "2026-07-20T14:06:18",
  "last_generation": 3,
  "total_factors_evaluated": 3,
  "total_factors_promoted": 0,
  "tokens_consumed": 4393,
  "budget_limit": 200000,
  "status": "completed"
}
```

---

## 7. 配置说明

### 7.1 环境变量配置

每次运行前需要设置以下变量：

```powershell
$env:OPENAI_API_KEY = "你的 API Key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:OPENAI_MODEL = "deepseek-v4-flash"
```

### 7.2 可选配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FTS_MAX_GENERATIONS` | 默认演化代数 | 10 |
| `FTS_MAX_WORKERS` | 并行工作数 | 4 |
| `FTS_LOG_LEVEL` | 日志级别 | INFO |
| `FTS_MEMORY_DIR` | 状态存储目录 | memory |

### 7.3 永久设置环境变量

如果你不想每次都输入，可以设置永久环境变量：

1. 右键"此电脑" → 属性 → 高级系统设置 → 环境变量
2. 在"用户变量"中添加：
   - OPENAI_API_KEY
   - OPENAI_BASE_URL
   - OPENAI_MODEL
3. 点击确定，重启命令行

---

## 8. CLI 命令大全

### 8.1 版本信息

```bash
python -m fts.cli version
```

### 8.2 运行演化

```bash
python -m fts.cli evolution run [参数]
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
python -m fts.cli monitor --json  # JSON 格式输出
```

### 8.6 管理因子

```bash
python -m fts.cli factor list    # 列出精英因子
python -m fts.cli factor show <因子ID>  # 查看因子详情
```

### 8.7 调度器

```bash
python -m fts.cli scheduler run  # 启动调度器后台
python -m fts.cli scheduler list # 列出已注册任务
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

**原因**：Data-Core 没有安装或网络问题

**解决方法**：
```bash
pip install -e d:\Programs\data-core
```

### 9.3 演化过程中熔断（Circuit broken）

**原因**：连续 3 代因子 IC 都很低（< 0.01）

**解决方法**：切换到横截面模式
```bash
python -m fts.cli evolution run --universe csi300 --max-stocks 30
```

### 9.4 没有精英因子晋级

**原因**：因子没有通过三级评估链

**解决方法**：
1. 查看失败轨迹：`cat memory/evolution/failure/*.json`
2. 增加演化代数：`--max-generations 20`
3. 调整品种数量：`--max-stocks 50`

### 9.5 运行速度很慢

**原因**：品种数量太多或演化代数太大

**解决方法**：
1. 减少品种数量：`--max-stocks 20`
2. 减少演化代数：`--max-generations 5`

### 9.6 内存不足

**原因**：品种太多导致内存占用过大

**解决方法**：
```bash
python -m fts.cli evolution run --universe futures --max-stocks 30
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

## 附录：期货品种代码表

### 黑色系

| 代码 | 名称 |
|------|------|
| RB | 螺纹钢 |
| HC | 热卷 |
| I | 铁矿石 |
| JM | 焦煤 |
| J | 焦炭 |

### 有色金属

| 代码 | 名称 |
|------|------|
| CU | 铜 |
| AL | 铝 |
| ZN | 锌 |
| PB | 铅 |
| NI | 镍 |
| SN | 锡 |
| AU | 黄金 |
| AG | 白银 |

### 农产品

| 代码 | 名称 |
|------|------|
| M | 豆粕 |
| RM | 菜粕 |
| Y | 豆油 |
| P | 棕榈油 |
| C | 玉米 |
| CS | 淀粉 |
| SR | 白糖 |
| CF | 棉花 |

### 股指期货

| 代码 | 名称 |
|------|------|
| IF | 沪深300股指期货 |
| IH | 上证50股指期货 |
| IC | 中证500股指期货 |
| IM | 中证1000股指期货 |

---

## 附录：评估指标说明

### Level 1 回测指标

| 指标 | 说明 | 通过阈值 |
|------|------|----------|
| ic | 信息系数均值 | > 0.03 |
| icir | IC 比率 | > 0.5 |
| sharpe | 年化夏普比率 | > 1.5 |
| max_drawdown | 最大回撤 | < 0.3 |
| monotonicity | 十分位单调性 | True |

### Level 2 经济逻辑

| 维度 | 说明 | 满分 |
|------|------|------|
| theory | 理论基础 | 5 |
| behavioral | 行为偏差 | 5 |
| microstructure | 市场微观结构 | 5 |
| institutional | 制度因素 | 5 |

通过条件：四维评分 ≥ 3/4

---

*文档版本：1.0.0 | 最后更新：2026-07-21*
