# FTS 用户手册

> **版本 2.104.0+30 · 最后更新 2026-08-14**

FTS 是一个**因子智能系统**——它每天自动分析市场数据，生成交易信号，帮你做量化投资决策。当前主系统支持**期货**市场因子演化与信号生成（股票链路已于 v2.104.0 剥离至独立项目 fts-stock）。

---

## 目录

- [快速上手](#快速上手)
- [日常使用流程](#日常使用流程)
- [FTS 的一天](#fts-的一天)
- [因子质检六层框架](#因子质检六层框架)
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

> 推荐使用 Python 3.11+（项目实际验证环境为 Python 3.12）。

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
# FTS version: 2.104.0+24
# Factor engine version: 2.104.0+24
# Config memory_dir: memory
```

> 如果提示 `fts` 命令找不到，请使用模块模式：
> ```powershell
> python -m fts.cli version
> ```

### 第四步：启动因子演化

系统支持**期货**市场因子演化（股票链路已于 v2.104.0 剥离至独立项目 fts-stock）：

**期货横截面模式**（跨品种因子演化，默认）：
```powershell
fts evolution run --max-generations 3
```

系统会：
1. 从多源（DuckDB 缓存 → 通达信 → 天勤 → AKShare → 合成数据）加载期货品种数据
2. 用 **81 个期货专用种子因子**开始演化（覆盖动量/期限结构/持仓资金流/流动性/波动率/拥挤度等方向）
3. 每代尝试改进因子代码和参数
4. 经过三级评估 + 6 项强制审计 + 质量评分卡
5. 输出 elite 因子到精英池

### 第五步：打开监控仪表盘

```powershell
fts ui
```

浏览器访问 `http://127.0.0.1:9100`，可以看到：
- 系统健康状态
- 三个循环的运行状态
- Elite 因子列表（按信号相关性聚类簇分组展示与筛选）
- Token 消耗统计
- 熔断状态

---

## 日常使用流程

### 你每天该做什么

```
早晨 07:59 → L1 Meta-Loop 自动运行（市场感知 + 知识补给，工作日）
凌晨 00:00 → L2 Evolution 自动运行（因子演化，工作日）
下午 17:30 → 期货多源数据自动同步（工作日）
晚间 19:00 → L3 组合权重重算（工作日，默认 equal_weight 每日重算）
晚间 20:00 → 期货信号管道自动运行（工作日）
每周六 08:00 → 期货核心品种动态池刷新（sync_liquidity_pool）
随时       → fts ui 打开仪表盘查看状态
```

### 如果想手动操作

```powershell
# 1. 加载配置
.\start_fts.ps1

# 2. 查看系统状态
fts monitor

# 3. 运行 L1 市场感知
fts meta-loop run

# 4. 运行因子演化（期货横截面，默认）
fts evolution run --max-generations 5

# 5. 运行 L3 组合构建（--force-recompute 强制重算）
fts portfolio run --synthesis-mode sharpe_weight

# 6. 查看产出的因子
fts factor list

# 7. 查看某个因子的详情
fts factor show fct_3f9a2b1c

# 8. 查看种子因子
fts factor seeds

# 9. 手动同步期货数据（全品种 82 个）
fts data sync-futures

# 10. 查看多源数据状态（熔断器/成功率）
fts data status
```

### 新手推荐路线

```powershell
# 第1天：熟悉系统
fts version              # 看版本
fts monitor              # 看状态
fts scheduler list       # 看有哪些定时任务（当前 13 个）

# 第2天：跑一次因子演化
fts evolution run --max-generations 3

# 第3天：查看结果
fts factor list          # 看有没有 elite 因子
fts ui                   # 打开仪表盘

# 第4天：构建组合
fts portfolio run --synthesis-mode sharpe_weight
```

---

## FTS 的一天

系统每天自动运行多个循环与信号管道，不需要你手动操作。以下为定时任务全景（共 13 个）：

| 任务 | 调度 | 说明 |
|------|------|------|
| `l1_meta_loop` | 工作日 07:59 | L1 市场感知 + 知识补给 |
| `l2_evolution_loop` | 工作日 00:00 | L2 因子演化（核心） |
| `l3_portfolio_loop` | 工作日 06:00 | L3 期货组合构建 + 权重重算（equal_weight 每日重算，开盘前） |
| `futures_signal_pipeline` | 工作日 20:00 | 期货横截面信号管道 |
| `sync_futures_data` | 工作日 17:30 | 同步期货 K 线（全品种 82 个） |
| `health_check` | 每 10 分钟 | 健康检查 |
| `data_quality_eval` | 每 5 分钟 | 数据质量评估 |
| `monthly_decay_eval` | 每月 1 日 04:00 | 月度治理（准入重审 + 因子衰减评估） |
| `data_level_monitor` | 每日 04:00 | 数据级监控 |
| `logic_monitor` | 每日 22:00 | 逻辑监控 |
| `factor_inspector` | 每日 03:00 | 精英因子巡检 |
| `sync_liquidity_pool` | 每周六 08:00 | 期货核心品种动态池刷新 |
| `mhf_signal` | 每 30 分钟 | MHF 中高频信号发布 |

### 🌅 早上 07:59 — L1 Meta-Loop（市场感知 + 知识补给）

系统起床后的第一件事：看看今天市场发生了什么。

```
1. 联网收集财经新闻和市场数据
2. 生成市场摘要（今天涨了还是跌了？有什么大事？）
3. 如果发现新的交易模式，生成候选因子
4. 把候选因子注入到演化池
```

这步叫"知识补给"——相当于研究员早上先看一遍新闻。

### 🌙 凌晨 00:00 — L2 Evolution Loop（因子演化）

核心环节。系统通宵干活，寻找赚钱的因子。

```
对每一代（默认最多 10 代）:
  Step 1: UCT 父因子选择
    - 平衡探索与利用，选择最优父因子
  Step 2: LLM 宏观演化
    - 拿一个种子因子，让 AI 分析它的优缺点
    - AI 写出新的因子代码
  Step 3: 参数调优
    - optuna 自动搜索最优参数（100 次尝试）
  Step 4: 三级评估（含多持有期 IC / IC t 值 / 胜率 / Q1-Q5 分组等）
    - Level 1: 回测验证（IC > 0.03？夏普 > 1.5？）
    - Level 2: 经济逻辑评分（有经济学道理吗？）
    - Level 3: 多重检验（不是碰巧的吧？）
  Step 5: 因子审计（6 项强制审计）
    - 因果检验 / OOS / 跨品种 / 压力测试 / 多重检验 / 数据窥探
  Step 6: 质量评分卡（50 分制）
    - IC 稳定性/Sharpe/换手率/容量/频率/覆盖率/鲁棒性/经济逻辑
  Step 7: 通过 → 晋升 Elite 精英池
         未通过 → 记录失败原因到经验链
         触发熔断 → 停止演化

保护机制（熔断）:
  - Token 消耗 > 预算 2 倍 → 熔断
  - 连续 5 代 IC < 0.005 → 熔断
  - 失败率 > 95% → 熔断
  - 连续 3 代质量分 < 30 → 熔断
```

### 📊 工作日每日 19:00 — L3 Portfolio Loop（组合构建 + 权重重算）

把 Elite 因子组合成交易信号。组合权重按 `l3_weight_recompute_cadence` 重算（v2.104.0+7 默认 daily 每日重算；配置为 weekly 时周五重算、其余交易日冻结复用，冷启动保护）。

```
1. 加载所有 elite 因子
2. 信号合成（4 种模式）
   - equal_weight: 等权 1/N
   - sharpe_weight: 按 Sharpe 加权（期货默认）
   - elastic_net: Elastic Net 回归
   - adaptive: 制度自适应权重（Regime-aware + 风格维度）
3. 组合质检三标准
   - 合成增益: 组合夏普 / 最佳单因子夏普 > 1
   - 分散化增益: 组合夏普 / 权重加权夏普 > 1
   - 回撤控制: 组合最大回撤 < 成分因子平均回撤
4. 组合级风控
   - 回撤止损: 组合回撤 > 10% 触发减仓建议
   - 相关性熔断: 成员相关 > 0.8 触发平仓建议
5. 输出最终组合信号
```

### 📈 每日 20:00 — 期货信号管道

独立生成期货横截面信号报告（与 L3 解耦，工作日运行）。

```
1. 加载 L3 组合因子与基础权重（factor_weights.json，v2.105.0 起因子选择由 L3 负责，严格模式：缺失即退出）
2. 从 DuckDB 加载期货品种日线数据
3. Regime 档位缩放权重调整（bull/bear 放大趋势类、oscillate 放大反转类、high_vol 整体收缩）
4. 品种级 IC 自适应权重（全局权重 × 品种 IC）+ 加权合成综合得分
5. 输出排名 + 信号报告到 reports/futures/{date}/
```

### 🗓 每周六 08:00 — 期货动态池刷新

基于真实主力合约流动性快照，维护期货核心品种动态池（默认 25 个，渐进式替换，缓存缺失自动回退静态池）。

### ⏰ 每 10 分钟 — 健康检查

系统每 10 分钟检查一次各循环的状态，确保一切正常。

### ⚡ 每 30 分钟 — MHF 中高频信号

基于 30 分钟 bar 生成中高频反转混合信号，经 SignalBridge 发布（JSON 协议）。最新 bar 无更新时幂等跳过。

---

## 因子质检六层框架

FTS 的因子质检遵循"六层框架 + 三阶段组合构建"，从数据到组合层层设卡，把伪信号拦在门外。所有因子晋升 elite 前必须通过。

### 六层质检（因子个体有效性）

| 层级 | 核心问题 | 关键指标/方法 |
|------|---------|---------------|
| 第一层 数据预处理 | 数据干净吗？ | 换月复权、展期成本、夜盘跳空标记、双维标准化 |
| 第二层 双维 IC | 有真实预测力吗？ | 截面 IC + 时序 IC、ICIR、IC t 值、日度胜率 |
| 第三层 分组回测 | 分层单调吗？ | Q1-Q5 五分组、q5-q1 价差、最大连续亏损、信号翻转频率 |
| 第四层 衰减与持有期 | 预测力何时失效？ | 多持有期 IC（1/5/10/20 日）、IC 衰减曲线、最佳持有期 |
| 第五层 相关性与拥挤度 | 信号独特吗？ | 因子间相关去冗余、板块联动检测、拥挤度预警 |
| 第六层 稳健性 | 换环境还成立吗？ | 样本外验证、跨品种泛化、压力测试、多重检验、参数扰动 |

### 三阶段组合构建

```
阶段一  六层框架逐因子质检 → 确定候选池
阶段二  多因子合成 + 组合构建 → 组合质检三标准（合成/分散化/回撤增益）
阶段三  组合级风控与执行 → 回撤止损 + 相关性熔断
```

### 常用质检命令

```powershell
# 回测单个因子（含多持有期 IC、成本敏感性等完整评估）
fts backtest run --factor-id fct_xxx

# 批量回测 + 对比排名
fts backtest batch

# 查看因子聚类分布（信号相关性分组）
fts factor stats

# 查看因子演化血缘
fts factor lineage fct_xxx

# 种子因子验证（完整性/语法/跨文件重复）
fts seed validate
```

---

## 命令速查

### 系统管理

| 命令 | 作用 |
|------|------|
| `fts version` | 查看版本号 |
| `fts monitor` | 查看系统状态（支持 --json 参数） |
| `fts ui` | 打开 Web 仪表盘（默认端口 9100） |
| `fts --help` | 查看所有命令 |

### 因子演化

| 命令 | 作用 |
|------|------|
| `fts evolution run` | 启动演化（默认期货横截面模式） |
| `fts evolution run --max-generations 5` | 只跑 5 代 |
| `fts evolution run --days 750` | 指定回溯天数（默认 750） |
| `fts evolution run --universe futures` | 期货横截面模式（唯一品种池选项） |

### L1 Meta-Loop

| 命令 | 作用 |
|------|------|
| `fts meta-loop run` | 手动运行 L1 市场感知 |

### L3 Portfolio

| 命令 | 作用 |
|------|------|
| `fts portfolio run` | 手动运行 L3 组合构建 |
| `fts portfolio run --universe futures` | 使用期货 elite 因子 |
| `fts portfolio run --synthesis-mode sharpe_weight` | 使用 Sharpe 加权 |
| `fts portfolio run --synthesis-mode elastic_net` | 使用 Elastic Net 回归 |
| `fts portfolio run --synthesis-mode adaptive` | 使用制度自适应权重 |
| `fts portfolio run --force-recompute` | 强制重算权重（跳过冻结日） |

### 因子管理

| 命令 | 作用 |
|------|------|
| `fts factor list` | 列出所有 elite 因子 |
| `fts factor list --cluster 0` | 按信号聚类簇 ID 筛选（DuckDB 模式） |
| `fts factor list --diverse --total-count 10 --max-per-cluster 3` | 多样性选择（按信号聚类簇配额，默认单簇最多 3 个） |
| `fts factor show <因子ID>` | 查看因子详情（含评估报告和质量评分卡） |
| `fts factor seeds` | 列出种子因子 |
| `fts factor stats` | 因子聚类分布统计（信号相关性分组） |
| `fts factor lineage <因子ID>` | 查询因子演化血缘 |
| `fts factor review list` | 列出待审查因子队列（审查工作流） |

### 数据管理

| 命令 | 作用 |
|------|------|
| `fts data status` | 查看多源熔断器/成功率状态 |
| `fts data sync-futures` | 主动同步期货 K 线（全品种 82 个） |
| `fts data cross-check --symbol RB0 --date 2026-08-04` | 多源交叉验证指定品种+日期 |
| `fts data fuse --symbol RB0 --strategy MEDIAN` | 多源 K 线融合（MEDIAN/MEAN/WEIGHTED/HIERARCHICAL/TRIMMED_MEAN） |

### 回测与特征

| 命令 | 作用 |
|------|------|
| `fts backtest run` | 单个因子回测 |
| `fts backtest batch` | 批量回测 + 对比排名 |
| `fts backtest compare` | 对比回测多个因子 |
| `fts feature list` | 列出特征算子 |
| `fts feature analyze` | 特征重要性分析 |
| `fts gp evolve` | 运行 GP 遗传规划因子演化 |

### 反馈与信号

| 命令 | 作用 |
|------|------|
| `fts feedback trigger` | 手动触发反馈事件 |
| `fts feedback live-ic` | 实盘 IC vs 回测 IC 对比报告 |
| `fts bridge serve` | 启动信号桥接 REST 服务（对接下游） |
| `fts bridge publish` | 发布信号到目标协议 |
| `fts bridge status` | 查看信号桥接状态 |

### 调度管理

| 命令 | 作用 |
|------|------|
| `fts scheduler run` | 启动定时调度器 |
| `fts scheduler list` | 查看定时任务列表（13 个） |

### Web UI 仪表盘

```powershell
fts ui                          # 启动，默认 http://127.0.0.1:9100
fts ui --port 8080              # 换端口
fts ui --host 0.0.0.0           # 局域网可访问
```

仪表盘自动每 10 秒刷新一次，包含：
- **4 个指标卡**：系统健康、版本号、Token 用量、Elite 因子数
- **循环状态卡**：L1/L2/L3 各自的运行状态、最近运行时间、错误信息
- **熔断状态**：显示当前是否触发熔断及原因
- **因子列表**：所有 elite 因子的 ID、名称、IC、夏普、质量分（按信号相关性聚类簇分组展示与筛选）

---

## 配置指南

### 配置文件在哪

`config/settings.yaml` —— 大部分情况不用动。配置加载优先级：**环境变量 > YAML > 代码默认值**。

```yaml
# 主要配置项
default_market: "futures"              # 默认市场类型（期货，唯一支持）
llm_backend: "openai"                  # LLM 后端（openai/anthropic/mock）
llm_temperature: 1.2                   # LLM 采样温度（0.0-2.0，越高生成越多样）
max_generations: 10                    # 每轮演化最大代数
population_size: 20                    # 种群大小
micro_trials_per_generation: 50        # 每代调参次数
evolution_mode: "hybrid"               # 演化模式: operator/code/hybrid
portfolio_max_factors: 20              # L3 最大因子数
portfolio_top_n: 5                     # L3 选用因子数
portfolio_decay_days: 90               # L3 因子衰减天数
log_level: "INFO"                      # 日志级别
log_file: "logs/fts.log"               # 日志文件

# 质检相关（可在 .env 用环境变量覆盖）
# FTS_EVAL_HORIZONS=1,5,10,20              # 多持有期 IC 持有期列表
# FTS_COST_SENSITIVITY_ENABLED=1           # 启用成本敏感性扫描
# FTS_INJECT_OVERNIGHT_GAP=1               # 注入夜盘隔夜跳空标记列
# FTS_OVERNIGHT_GAP_THRESHOLD=0.01         # 跳空标记阈值
# FTS_L3_TURNOVER_PENALTY=0.0              # 组合换手惩罚系数
```

### Verifier 配置

Verifier 配置决定因子晋升门槛，在 `config/settings.yaml` 中修改：

```yaml
# L2 Verifier（因子演化）
verifier:
  min_sharpe: 2.0
  max_correlation: 0.5
  max_turnover: 0.50
  max_decay_rate: 0.30
  min_n_factors: 3
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
| `FTS_FUTURES_ELITE_DIR` | 期货 elite 目录 | `memory/knowledge/factors/futures_elite` |
| `FTS_LOG_LEVEL` | 日志级别 | `INFO` |
| `FTS_MAX_WORKERS` | 并行数 | `4` |
| `FTS_DEFAULT_MARKET` | 默认市场 | `futures` |
| `FTS_LLM_BACKEND` | LLM 后端（openai/anthropic/mock） | 自动检测 |
| `FTS_EVAL_HORIZONS` | 多持有期 IC 持有期列表 | `1,5,10,20` |
| `FTS_COST_SENSITIVITY_ENABLED` | 启用成本敏感性扫描 | `0`（关） |
| `FTS_INJECT_OVERNIGHT_GAP` | 注入夜盘跳空标记列 | `0`（关） |
| `FTS_L3_TURNOVER_PENALTY` | 组合换手惩罚系数 | `0.0` |

### 数据源

**期货**：系统从本地 DuckDB 数据库（`data/fts_history.duckdb`）读取期货品种日线数据，支持 **82 个连续合约**（25 核心动态池 + 全量）；期货基本面数据经 AKShare 期货基本面源获取。

**数据源降级链**（熔断器 + 缓存 + 降级）：

```
日线/分钟: DUCKDB_CACHE → TDX_LOCAL(通达信 17709) → TQ_PYTHON(天勤) → AKSHARE → SYNTHETIC(合成兜底)
tick:       tick_cache → TQSDK_TICK
实时价:     TDX_LOCAL → AKSHARE
```

- 不需要额外的账号或 Key
- 网络不可用时自动降级到合成数据（不影响测试运行）
- 多源交叉验证：同日期 ≥2 源数据差异 >0.5% 时自动记录分歧
- 任一源连续 5 次失败标记 UNAVAILABLE，6 小时后冷却探活

---

## 常见问题

### 1. `fts` 命令找不到

```powershell
# 方案 A：重新安装
pip install -e .

# 方案 B：用模块模式
python -m fts.cli version
```

### 2. 因子演化触发熔断

```
熔断原因: 失败率熔断 / 连续低 IC 熔断 / Token 熔断
```

最常见的原因：**没有配置真实的 LLM API Key**。

```powershell
# 检查当前 LLM 类型
fts monitor
# 如果看到 "LLM backend: MockLLMClient" → 没配 Key

# 修复：设置环境变量
$env:OPENAI_API_KEY="sk-你的key"
$env:OPENAI_BASE_URL="https://api.deepseek.com/v1"
$env:OPENAI_MODEL="deepseek-chat"

# 或使用启动脚本
.\start_fts.ps1
```

### 3. 多次运行后立即熔断

旧的状态文件没有清理。如果是测试环境：

```powershell
# 清理演化状态
Remove-Item memory/evolution/state.json

# 清理经验链（可选）
Remove-Item memory/evolution/experience_chain.jsonl
```

### 4. 怎么看因子好不好

```powershell
# 列出所有 elite 因子
fts factor list

# 查看某个因子的详细评估报告
fts factor show fct_xxxx

# 重点关注：
# - IC（信息系数）：> 0.03 算不错
# - ICIR（IC 均值/标准差）：> 1.5 算不错
# - IC t 值：统计显著才算数
# - 质量分：>= 40 为 A 级，>= 30 为 B 级
# - monotonicity（单调性）：True 才好
# - multi_horizon（多持有期 IC）：了解因子有效半衰期与最佳调仓频率
# - 审计报告：必须 6 项全通过
```

### 5. 因子审计是什么

因子在晋升 elite 前需要通过 6 项强制审计：

| 审计项 | 说明 |
|--------|------|
| 因果检验 | Granger 因果检验 / 反事实分析 |
| 样本外验证 | WalkForward OOS 滚动验证 |
| 跨品种验证 | ≥80% 品种 IC 为正 |
| 压力测试 | 极端行情下表现 |
| 多重检验 | Bonferroni / FDR 校正 |
| 数据窥探检验 | 确保无未来函数 |

> 审计未通过的因子不会晋升 elite，但会记录到经验链供后续参考。

### 6. 质量评分卡是什么

质量评分卡是因子晋升前的最终评估环节（50 分制）：

| 维度 | 分值 |
|------|------|
| IC 稳定性 | 0-8 |
| Sharpe | 0-8 |
| 换手率 | 0-8 |
| 容量 | 0-5 |
| 频率 | 0-5 |
| 覆盖率 | 0-5 |
| 鲁棒性 | 0-6 |
| 经济逻辑 | 0-5 |

**分级**：A 级 ≥40 分，B 级 ≥30 分，C 级 <30 分（淘汰）

### 7. 怎么获取真实数据

期货数据同步（默认全品种）：

```powershell
fts data sync-futures            # 全品种 82 个（--symbol 指定品种）
```

验证数据是否正常：

```powershell
# 期货面板
python -c "from fts.data import FTSDataProvider; panel, dates = FTSDataProvider().get_futures_panel(['RB0','TA0'], days=250); print(len(panel))"
```

### 8. 怎么看系统在干什么

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
| 执行模式流程图 | `harness/execution_modes_flowchart.md` |
| 业务流程图 | `harness/business_flow.md` |
| 生产部署 | `docs/harness/plans/production_plan.md` |
| 工程规范 | `docs/harness/` |
| 角色职责 | `agents/fts-agent.md` |
| 策略手册 | `docs/harness/reports/strategy_manual.md` |
| 因子质检补全计划 | `docs/harness/plans/25-futures-factor-qc-complement-plan.md` |
| README | `README.md` |

### 工程指标

| 指标 | 值 |
|------|:---:|
| 版本 | v2.104.0+30 |
| 测试用例 | 6950（日常 not-slow 6924 + slow 26） |
| Python 源码 | 220+ 文件 |
| 种子因子（期货） | 81 |
| 定时任务 | 13 个 |
| 期货品种 | 82 个连续合约（25 核心动态池 + 全量） |

### 项目结构（简版）

```
factor_system/
├── fts/                   # 核心代码
│   ├── cli.py             # 命令行入口
│   ├── config/            # 配置系统
│   │   └── settings.py    # FTSConfig（全局配置，环境变量 > YAML > 默认）
│   ├── core/              # 核心枚举与契约
│   │   ├── enums.py       # 枚举定义（DataSource 等）
│   │   └── contracts.py   # 核心契约（FactorProgram、BacktestMetrics 等）
│   ├── data.py            # 统一数据层（期货）
│   ├── data_futures.py    # 期货数据适配（DuckDB + 多源降级链）
│   ├── data_futures_fundamental.py  # 期货基本面数据（AKShare）
│   ├── data_sources/      # 多源数据适配器（TDX_LOCAL/TQ_PYTHON/AKShare 等）
│   ├── llm.py             # LLM 客户端（OpenAI/Anthropic/Mock）
│   ├── factor_engine/     # 因子引擎（核心）
│   │   ├── seed_pool.py          # 种子池管理器
│   │   ├── seed_data_futures_full.py  # 期货种子（81）
│   │   ├── factor_clustering.py  # 信号相关性聚类分组（全流程统一）
│   │   ├── evolution_futures.py  # L2 期货演化主循环
│   │   ├── evolution_loop.py     # L2 演化主循环
│   │   ├── meta_loop.py          # L1 元循环
│   │   ├── portfolio_loop.py     # L3 组合循环（含组合质检三标准 + 风控接线）
│   │   ├── evaluation_chain.py   # 三级评估链（多持有期 IC/IC t 值/胜率/Q1-Q5 等）
│   │   ├── audit.py              # 因子审计（6 项强制）
│   │   ├── factor_quality_card.py  # 质量评分卡
│   │   ├── factor_db/            # 因子资产库 DuckDB（schema/repository/lineage）
│   │   ├── expr_dsl/             # 表达式 DSL（parser/compiler/executor）
│   │   └── barra/                # Barra 风格中性化
│   ├── monitor/           # 监控 + Web UI（http_server.py）
│   ├── scheduler/         # 定时任务调度（13 个任务）
│   ├── workflow/          # 工作流编排
│   ├── store/             # 存储层（DuckDB/SQLite/存储注册表）
│   ├── bridge/            # 信号桥接（JSON/Redis/REST）
│   ├── live_trade/        # 模拟交易与实盘接线
│   ├── risk/              # 风险度量与管理
│   └── ml/                # ML 模型层
├── scripts/               # 辅助脚本
│   ├── sync_futures_data.py        # 期货数据同步
│   ├── futures_signal_pipeline.py  # 期货信号管道
│   ├── sync_liquidity_pool.py      # 动态池刷新
│   └── run_futures_evolution.py    # 期货因子演化启动
├── config/                # 配置文件（settings.yaml）
├── tests/                 # 测试（6950 个）
├── docs/                  # 文档（含 harness 工程规范）
├── reports/               # 信号报告（按日期分文件夹）
└── memory/                # 运行时数据（自动创建，含动态池/权重快照）
```
