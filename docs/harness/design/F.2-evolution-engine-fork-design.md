# F.2 演化引擎分叉（evolution_loop → evolution_futures / evolution_stock）— 详细技术设计

> 版本: v2.103.0+11-draft
> 创建: 2026-08-12
> 状态: **设计中**（待评审，未实现）
> 关联: [F.1-data-contract-split-design.md](./F.1-data-contract-split-design.md)（契约拆分，Phase 3 依赖）、[14-cross-market-generalization-plan.md](file:///d:/Programs/factor_system/docs/harness/plans/14-cross-market-generalization-plan.md)（跨市场验证，分叉后独立演进）
> 背景: 期货/股票双管线独立化改造的核心——解除演化引擎内 13 处 market 分支的"双市场行为打架"，让两个市场各自的固有形状（期货合约/换月/持仓/产业链 vs 股票复权/T+1/行业市值）独立演化。

---

## 1. 目标与范围

**目标**: 将 `evolution_loop.py` 中全部市场行为分叉折叠进两份独立文件 `evolution_futures.py` / `evolution_stock.py`，各自按市场固有形状版本迭代，并保证迁移后行为等价。

**范围**:
- 分支盘点与分类（13 处 → 7 处行为分叉 + 6 处公共逻辑）
- 三阶段移植（纯配置折叠 → 数据形状折叠 → 契约下沉）
- 行为等价验证方案

**不在范围**:
- 数据契约拆分实现（见 F.1，Phase 3 联动）
- 数据供应分离（另行处理）
- 跨市场模块改造（分叉后保留 `fts_core` 共享适配层）

---

## 2. 分支盘点（调研结论）

### 2.1 全部 13 处 market 引用

| # | 行号 | 代码 | 语义 |
|---|------|------|------|
| 1 | L346 | `if market is None:` | 默认值兜底（公共，非分叉） |
| 2 | L357 | `if market == "stock" and _raw_mode == "hybrid":` → operator_first | **演化模式分叉** |
| 3 | L368 | `if ... market == "futures" ...` → FUTURES_SECTOR_MAP 注入 | **中性化分叉（期货）** |
| 4 | L390 | `if ... market == "stock" ...` → 行业/市值映射注入 | **中性化分叉（股票）** |
| 5 | L416 | `if market == "futures":` → futures_elite 目录 | **elite 目录分叉** |
| 6 | L456 | `elif market == "futures":` → FUTURES_VERIFIER_CONFIG | **verifier 分叉** |
| 7 | L553 | `if market == "futures":` → HighIC logic_min_score=1.0 | **high_ic 分叉** |
| 8 | L2518 | `record.get("market", "multi") in ("multi","other")` | **公共兼容逻辑** |
| 9 | L2544 | 同上（正交化后） | **公共兼容逻辑** |
| 10 | L2838 | `factor_market in ("multi", "other")` → 上下文市场 | **公共兼容逻辑** |
| 11 | L3427 | `long_only=(self.market in ("stock", "etf"))` | **long_only 分叉** |
| 12 | L4192 | `ic_threshold = 0.01 if futures else 0.02` | **IC 阈值分叉** |
| 13 | L4271 | 同上 | **IC 阈值分叉** |

### 2.2 分类结论

**行为分叉（7 处）** —— 需按市场折叠：

| 分支 | 行号 | 期货侧 | 股票侧 | 依赖数据形状? |
|---|---|---|---|---|
| 演化模式 | L357 | 保持原配置 | operator_first | 否 |
| 中性化注入 | L368/L390 | FUTURES_SECTOR_MAP（产业链） | 行业+市值映射 | **是** |
| elite 目录 | L416 | futures_elite | stocks_elite | 否 |
| verifier 配置 | L456 | FUTURES_VERIFIER_CONFIG | 全局 verifier | 否 |
| high_ic | L553 | logic_min_score=1.0 | 默认 | 否 |
| long_only | L3427 | False（多空） | True（仅做多） | 是 |
| IC 阈值 | L4192/4271 | 0.01 | 0.02 | 否 |

**公共兼容逻辑（6 处）** —— 不拆，两份文件各自保留同代码：
- L346 默认值兜底、L2518/L2544/L2838 因子 market 字段归一化（"multi"/"other" → 上下文市场）

> **结论**: 7 处行为分叉中 5 处**只依赖 market 字符串**（可立即常量折叠），2 处（中性化、long_only）依赖横截面数据形状（Phase 2 处理）。

---

## 3. 目标结构

### 3.1 分叉后文件结构

```
fts/factor_engine/
├── evolution_loop.py        # 保留为 thin facade（参数路由，转发给分叉实现）
├── evolution_futures.py     # 期货演化引擎（期货形状第一公民）
└── evolution_stock.py       # 股票演化引擎（股票形状第一公民）

fts_core/（共享层，两引擎共同依赖）
├── contracts/               # OHLCVBase/FusionMeta/逻辑契约（F.1 产物）
├── data_sources/            # tdx_local/akshare 薄封装、融合算法
├── llm.py / ops_library.py  # LLM 客户端、GP/算子库
└── monitor/ scheduler/      # 监控、调度框架
```

### 3.2 期货/股票各自固有形状（分叉后独立迭代方向）

| 维度 | evolution_futures.py | evolution_stock.py |
|---|---|---|
| 数据形状 | 合约序列/连续合约/换月 roll、持仓量、结算价 | 复权序列、T+1/涨跌停/停牌 |
| 中性化 | 产业链板块映射（FUTURES_SECTOR_MAP） | 行业 + 市值映射 |
| 截面 | 跨品种 × 同日期 | 多股票 × 同日期 |
| 多空 | 双向 | long_only |
| IC 阈值 | 0.01（低信噪比） | 0.02 |
| 演化模式 | 原配置 | operator_first 默认 |
| verifier | FUTURES_VERIFIER_CONFIG（放宽） | 全局默认 |

---

## 4. 移植顺序（三阶段）

### Phase 0: 冻结基线（前置，不写代码）

| 步骤 | 动作 | 验证 |
|---|---|---|
| P0.1 | `pytest tests/factor_engine/test_evolution_loop.py -v` 全绿 | 全绿 |
| P0.2 | 期货、股票各跑 2 代演化，导出 `golden_output_futures.json` / `golden_output_stock.json`（factor_id → evaluation 指标 + elite JSON hash） | 产物落盘 |
| P0.3 | 记录环境指纹（Python 版本、依赖版本、随机种子） | 记录 |

**目标**: 迁移后行为等价性有对照锚点。

### Phase 1: 复制文件 + 折叠"纯配置"分支（5 处）

| 动作 | 说明 |
|---|---|
| 复制 evolution_loop.py → evolution_futures.py / evolution_stock.py | 两份初始内容一致 |
| evolution_futures.py 折叠: L416(elite目录)、L456(verifier)、L553(high_ic)、L4192/4271(IC阈值 0.01)、L357(演化模式保持原配置) | 分支替换为**各自默认值硬编码**，删除 market 判定 |
| evolution_stock.py 折叠: L416(stocks_elite)、L456(全局verifier)、L553(默认high_ic)、L4192/4271(0.02)、L357(operator_first) | 同上 |

**验证**: 双市场 golden output 对比，diff 必须为 0。

### Phase 2: 折叠"数据形状相关"分支（2 处）

| 动作 | 说明 |
|---|---|
| evolution_futures.py: 保留 L368 FUTURES_SECTOR_MAP 注入，删除 L390 行业/市值注入；L3427 long_only 硬编码 False | 删除对侧分支 |
| evolution_stock.py: 保留 L390 行业/市值注入，删除 L368 板块注入；L3427 long_only 硬编码 True | 删除对侧分支 |

**验证**: 再跑 golden output 对比，仍为 0。

### Phase 3: 契约下沉 + 收尾

| 动作 | 说明 |
|---|---|
| 落地 F.1 契约拆分 | 引擎改用 StockOHLCV/FuturesOHLCV |
| 删除 evolution_loop.py 内 market 参数（或保留 thin facade 路由） | evolution_loop 仅转发 |
| 更新 CLI 路由: `evolution run --market futures` → evolution_futures.run() | cli.py 修改 |
| 更新 scheduler/monitor 引用 | 引用分叉实现 |

**验证**: 全量受测模块测试 + golden output 对比。

---

## 5. 行为等价验证方案（核心保障）

**等价性定义**: 对同一市场、同一输入（seed_pool/cross_section_data/参数），分叉前后演化产物（精英因子 JSON、评估指标、入库记录）完全一致。

三层验证：

### 5.1 Golden Output Diff（主锚点）

- Phase 0 冻结基线: `golden_output_{market}.json`（含 factor_id → evaluation 指标）
- 每阶段迁移后重跑同样命令，逐字段 diff，**必须为 0**
- 非零即回滚该阶段（git revert）

### 5.2 现网测试回归

- `tests/factor_engine/test_evolution_loop.py` 全量在分叉文件上跑绿
- 覆盖: test_evolution_l1_merge / test_l2_elite_redundancy / test_l2_orthogonalize / test_evolution_stop 等

### 5.3 折叠审计（人工）

- Phase 1/2 每个折叠点 code review: 确认"market 分支的 True/False 两侧，折叠后只保留对应侧"，不留 dead branch
- 审计清单: 7 处行为分叉逐一打勾

---

## 6. 技术约束与风险

| 风险/约束 | 说明 | 缓解 |
|---|---|---|
| 折叠引入行为漂移 | 手动折叠可能漏/错分支 | golden diff 锚点 + 折叠审计清单 |
| 公共逻辑被误删 | L2518/2544/2838 的 market 归一化是双市场共用 | 分类清单明确标注"不拆"，两份文件各保留 |
| thin facade 兼容性 | evolution_loop.py 被外部大量引用（cli/scheduler/monitor/scripts） | facade 保留全部构造参数签名，仅路由 |
| 随机种子一致性 | golden 对比需同种子同环境 | P0.3 环境指纹 + 固定种子 |
| 数据契约尚未拆分时先分叉 | Phase 1/2 依赖 F.1 落地 | 阶段顺序硬约束: 契约拆分（F.1）在 Phase 3，分叉 Phase 1/2 可先行 |

---

## 7. 文件改动清单

| 文件 | 动作 |
|------|------|
| `fts/factor_engine/evolution_futures.py` | **新增** — 期货演化引擎 |
| `fts/factor_engine/evolution_stock.py` | **新增** — 股票演化引擎 |
| `fts/factor_engine/evolution_loop.py` | **修改** — 降级为 thin facade（保留签名，路由转发） |
| `fts/cli.py` | **修改** — `evolution run` 路由到分叉实现 |
| `fts/scheduler/` | **修改** — 任务引用分叉实现 |
| `fts/monitor/` | **核对** — 状态查询引用分叉实现 |
| `scripts/`（run_futures_evolution.py 等） | **修改** — 入口对齐 |
| `tests/factor_engine/test_evolution_loop.py` | **修改/新增** — 分叉文件测试 |
| `tests/factor_engine/test_evolution_futures.py` / `test_evolution_stock.py` | **新增** — 各自行为断言 |

---

## 8. 验收标准

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | evolution_futures/evolution_stock 创建，market 分支折叠完成 | 代码审查 + grep 确认无 market 分叉残留 |
| 2 | 双市场 golden output diff = 0 | §5.1 |
| 3 | test_evolution_loop 相关测试全绿 | §5.2 |
| 4 | 7 处行为分叉逐一审计打勾，6 处公共逻辑保留 | §5.3 |
| 5 | CLI `evolution run --market <m>` 路由正确 | 双市场各跑 2 代 |
| 6 | facade 兼容旧调用（构造参数签名不变） | 回归测试 |

---

## 9. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 关联文档 | [F.1-data-contract-split-design.md](./F.1-data-contract-split-design.md)（契约拆分）、[14-cross-market-generalization-plan.md](file:///d:/Programs/factor_system/docs/harness/plans/14-cross-market-generalization-plan.md)（跨市场） |
| 依赖模块 | `fts/factor_engine/evolution_loop.py`、`fts/factor_engine/contracts.py`、`fts/cli.py`、`fts/scheduler/`、`fts/monitor/` |
| 前置条件 | Phase 0 基线冻结；F.1 契约拆分（Phase 3 前落地） |
| 后置影响 | 期货/股票独立版本迭代、独立晋级发布；数据供应分离以分叉引擎为消费方 |
| 可验证断言 | golden diff=0；test_evolution_loop 全绿；grep 无 market 分叉残留；CLI 路由正确 |
| 检验方式 | §5 行为等价验证方案 |
