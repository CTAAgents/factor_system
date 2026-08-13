# FTS 跨市场泛化验证计划

> 版本: v2.103.0+33
> 创建: 2026-08-07
> 最后更新: 2026-08-07
> 状态: ✅ 已完成 (Phase A)

---

## 1. 背景与动机

当前 FTS 因子泛化优化（Phase A/B）已解决**期货市场内**的跨品种泛化验证问题（盲测品种池、品种分层训练、精英因子重验证）。但以下假设从未被验证：

| 假设 | 风险 | 当前状态 |
|:-----|:-----|:---------|
| 期货因子对股票/ETF 同样有效 | 期货因子可能过拟合到期货特有的期限结构/基差/库存等规律 | ❌ 无验证机制 |
| 股票因子对期货同样有效 | 股票因子（如动量/反转/波动率）可能依赖特定的微观结构 | ❌ 无验证机制 |
| 存在跨市场通用的定价因子 | 不知道哪些因子是市场特有的、哪些是资产定价通用的 | ❌ 无识别机制 |
| 股票/期货因子可以合并训练 | 两种市场的信号可能相互抵消或增强 | ❌ 无交叉验证 |

跨市场泛化验证的目标：
1. **量化评估**：期货因子在股票市场/ETF 市场的 IC 衰减程度
2. **通用因子识别**：找出在多个市场都有效的真正"通用定价因子"
3. **组合增强**：为跨资产组合构建提供因子有效性证据

---

## 2. 实施路线图

```
Phase A（P0，v2.27.0）✅                    Phase B（P1，v2.28.0）
├── A.1 数据对齐层 🏗️                       ├── B.1 股票→期货泛化验证
├── A.2 期货→股票泛化验证                     ├── B.2 期货→ETF 泛化验证
├── A.3 报告输出                              ├── B.3 报告输出
└── A.4 CLI 集成                              └── B.4 CLI 集成

Phase C（P2，v2.29.0）
├── C.1 通用因子自动识别
├── C.2 跨市场因子图谱
├── C.3 组合构建建议
└── C.4 文档 + 测试
```

---

## 3. Phase A — P0 优化项（期货→股票泛化验证）

### A.1 数据对齐层

**目标**: 实现期货因子到股票市场的统一数据接口，解决两市场数据格式差异。

**问题**: 期货因子依赖 `close/high/low/volume/open_interest` 等字段，而股票数据无 `open_interest`、有 `adjust_factor` 等字段。需要适配层让同一个因子代码能在两个市场运行。

**方案**:
- 定义 `CrossMarketDataAdapter` 类，负责：
  - 期货→股票：填充缺失字段（`open_interest=0`），处理复权差异
  - 股票→期货：使用 `adjust_factor` 做前复权对齐
- 统一输出格式：`DataFrame` 含 `open/high/low/close/volume` 5 个核心字段
- 数据源路由：期货走 `FuturesDataProvider`，股票走 `MCPDataProvider`

**改动文件**:
- `fts/cross_market/data_adapter.py` — 新建
- `fts/cross_market/__init__.py` — 新建

**验证标准**:
- 期货因子在股票数据上可执行（不抛异常）
- 统一输出格式包含所有必需字段
- 适配器可处理复权差异

### A.2 期货→股票泛化验证引擎

**目标**: 在沪深 300 成分股上验证期货 Elite 因子的有效性。

**问题**: 当前期货精英因子（`futures_elite/`）从未在股票市场测试过，不知道其跨市场 IC 表现。

**方案**:
- 加载所有期货精英因子（含已降级）
- 在沪深 300 成分股面板上计算截面 IC
- 核心指标：**跨市场 IC**（target_market_ic）、**IC 保持率**（target_market_ic / 期货市场 IC）、**跨市场显著比例**（IC 显著的品种占比）
- 分类标准：

| 类别 | 条件 | 含义 |
|:-----|:-----|:-----|
| 🌍 通用因子 | 跨市场 IC ≥ 0.02 且保持率 ≥ 50% | 在两个市场均有效 |
| 🔄 期货特异 | 跨市场 IC < 0.02 但期货 IC ≥ 0.03 | 仅期货市场有效 |
| ❌ 失效 | 跨市场 IC < 0.01 且期货 IC ≥ 0.03 | 跨市场完全失效 |

**改动文件**:
- `scripts/cross_market_revalidation.py` — 新建（主要脚本）
- `fts/cross_market/engine.py` — 新建（验证引擎核心逻辑）

**验证标准**:
- 加载全部期货精英因子
- 在沪深 300 面板上计算跨市场 IC
- 按分类标准输出通用/期货特异/失效三类因子

### A.3 报告输出

**目标**: 生成结构化的跨市场泛化验证报告。

**报告内容**:
1. 跨市场 IC 总览（平均 IC、IC 保持率分布）
2. 通用因子列表（Top 10 跨市场 IC）
3. 期货特异因子列表（期货有效但股票无效）
4. 失效因子列表
5. 跨市场 IC 分布直方图

**改动文件**:
- `scripts/cross_market_revalidation.py` — 报告生成逻辑

**验证标准**:
- 报告输出到 `reports/{date}/cross_market_revalidation_{date}.md`
- 报告包含上述 5 个章节

### A.4 CLI 集成

**目标**: 通过 `fts factor cross-market` 命令触发跨市场验证。

**方案**:
- CLI 新增 `factor cross-market` 子命令
- 支持参数：`--market`（目标市场，默认 stock）、`--days`（回看天数）、`--max-factors`（最大因子数）、`--max-stocks`（最大成分股数）

**改动文件**:
- `fts/cli.py` — 新增 `factor cross-market` 子命令

**验证标准**:
- `fts factor cross-market --market stock --days 120` 正常运行并输出报告
- `fts factor cross-market --market etf --days 120` 正常运行并输出报告

---

## 4. Phase B — P1 优化项（股票→期货 + 期货→ETF）

### B.1 股票→期货泛化验证

**目标**: 验证股票 Elite 因子在期货市场的有效性。

**方案**:
- 加载股票 Elite 因子（`elite/` 目录）
- 在期货全量品种面板上计算截面 IC
- 输出股票→期货跨市场 IC 报告

**改动文件**:
- `scripts/cross_market_revalidation.py` — 增加 `--direction stock-to-futures`

**验证标准**:
- 股票因子在期货上可执行
- 输出跨市场 IC 报告

### B.2 期货→ETF 泛化验证

**目标**: 验证期货 Elite 因子在 ETF 市场的有效性。

**方案**:
- 使用 `ETF_SUBSET`（14 个常见 ETF）作为目标市场
- 期货因子在 ETF 面板上计算截面 IC

**改动文件**:
- `scripts/cross_market_revalidation.py` — 增加 `--market etf` 支持

**验证标准**:
- 期货因子在 ETF 上可执行
- 输出跨市场 IC 报告

---

## 5. Phase C — P2 优化项（通用因子识别）

### C.1 通用因子自动识别

**目标**: 根据跨市场 IC 表现，自动标记因子的通用性等级。

**方案**:
- 在 `FactorProgram` 中新增 `cross_market_generalization` 字段（可选）
- 等级：`universal` / `futures_specific` / `stock_specific` / `unknown`
- 自动更新仓库中的因子记录

**改动文件**:
- `fts/factor_engine/contracts.py` — 新增字段
- `fts/cross_market/engine.py` — 自动识别逻辑

### C.2 跨市场因子图谱

**目标**: 输出跨市场因子有效性矩阵（因子 × 市场）。

**方案**:
- 矩阵行：因子名称
- 矩阵列：futures / stock / etf
- 矩阵值：IC 值 + 显著性标记

### C.3 组合构建建议

**目标**: 基于跨市场泛化结果，为跨资产组合提供因子权重建议。

**方案**:
- 通用因子：跨市场等权配置
- 市场特异因子：仅在对应市场使用

---

## 6. 时间估算

| 任务 | 预估工时 | 复杂度 | 依赖 |
|:-----|:---------|:-------|:-----|
| A.1 数据对齐层 | 1 天 | 低 | 无 |
| A.2 期货→股票验证引擎 | 2-3 天 | 中 | A.1 |
| A.3 报告输出 | 1 天 | 低 | A.2 |
| A.4 CLI 集成 | 0.5 天 | 低 | A.2 |
| B.1 股票→期货验证 | 1 天 | 低 | A.1 |
| B.2 期货→ETF 验证 | 0.5 天 | 低 | A.1 |
| C.1 通用因子识别 | 1-2 天 | 中 | A.2, B.1 |
| C.2 跨市场因子图谱 | 1 天 | 低 | C.1 |
| C.3 组合构建建议 | 1 天 | 低 | C.2 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件涉及 `fts/cross_market/`、`fts/cli.py`、`scripts/cross_market_revalidation.py`、`fts/factor_engine/contracts.py` |
| 可验证断言 | Phase A 完成后：期货因子在沪深 300 上可计算跨市场 IC、报告输出到 reports/、CLI 命令正常运行 |
| 检验方式 | 运行 `python scripts/cross_market_revalidation.py --direction futures-to-stock --days 120 --max-stocks 10` 验证脚本正常执行，输出报告 |