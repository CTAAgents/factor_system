# 41 — L1 知识注入与因子注入增强计划（A/C/D + 提取器增强）

> 版本: v2.105.0+7
> 状态: 🔄 进行中 · 优先级: P1 · 负责人: FTS Agent · 关联: GAP-121, GAP-123, plans/40
> 范围调整（2026-08-16 确认）：本轮落地 **A（WebSearchExtractor）+ max_factors 5→8 + C（实时链知识）+ D（预算/分批）**；
> 提取器源配置化（B）与平台 API 直连（C 远期）登记为后续差距项。

## 一、背景与问题定位

2026-08-16 能化链 L1 实测：单次运行知识注入近乎为 0（`web_collector` 未配置跳过感知、`chain_knowledge` 为静态 861 字符描述），因子注入受 4 个瓶颈限制：

| # | 瓶颈 | 现状 | 影响 |
|---|------|------|------|
| 1 | 感知层空转 | `MetaLoop.web_collector=None` → Step1 跳过 | 市场快照知识注入 = 0 |
| 2 | 提取器源固定 | 源清单硬编码 + 天软首次后自动暂停 | 知识源固定不增长、静态源重复利用 |
| 3 | 提取器配额低 | 研报/论文各 `max_factors=5` | 单源产出少 |
| 5 | 预算与分批单点 | `max_bootstraps=20` 单批 LLM 生成 | 单批 prompt 多样性受限 |

## 二、方案设计

### A 层 — web_collector 感知接入 + WebSearchExtractor 动态源（本轮落地）

- A1: `_make_web_collector` 已存在（[meta_loop.py#L1892](../01-architecture.md 索引)），在调度入口 `l1_meta_loop_job` 创建并传入 `MetaLoop(web_collector=...)` → 12 品种市场快照注入 bootstrap prompt。
- A2: 新增 `WebSearchExtractor`（`fts/factor_engine/extractors/web_search.py`）：按量化平台/能化链关键词调必应搜索 → 去标签文本交 `_llm_extract_factors` 提取候选，实现"每轮动态换一批新知识"；管道硬编码注册。
- A3: 提取器 `max_factors 5→20` 并配置化：新增 `FTSConfig.l1_extractor_max_factors`（`config/settings.yaml` + `FTS_L1_EXTRACTOR_MAX_FACTORS` 环境变量），`FuturesExtractorPipeline` 构造时读取并注入 LLM 提取源（研报/论文/宏观/WebSearch）；天软 tinysoft 为静态 YAML 感知源不参与；`_llm_extract_factors` 输出 token 预算同步放大（4000→8000）。
- 降级：单源失败仅 warning，不阻断整体。

### B 层 — 提取器源配置化（登记后续差距项，本轮不落地）

- B1: 源清单下沉 `config/extractors.yaml` 注册表（新增源免改代码）。
- B2: 平台 API 直连（聚宽/米筐等，多数闭源，收益不确定）。

### C 层 — 能源链实时知识注入（本轮落地）

- C1: `_inject_chain_knowledge` 扩展为"静态链知识 + 实时产业状态"双段：实时段经 `FTSDataProvider` 拉取 12 训练品种近 60 日 OHLCV，计算子链价差代理（SC-FU/SC-BU/SC-TA/SC-L/MA-SA）、波动聚集代理、库存/基差水位代理（价格位置偏离）——异常/缺失自动降级。

### D 层 — 预算上调 + 按子链分批（本轮落地）

- D1: `DEFAULT_L1_BUDGET_CONFIG`：`max_bootstraps_per_run 20→30`、`daily_token_limit 50_000→60_000`。
- D2: energy 市场按四子链分批 bootstrap（能源/聚酯/油化工/煤化工各一批，按品种数比例分配，每批独立 chain_focus 注入 prompt）；futures 保持单批（向后兼容）。

## 三、影响文件

| 文件 | 变更 |
|:-----|:-----|
| `fts/scheduler/jobs.py` | `l1_meta_loop_job` 接入 web_collector（A1） |
| `fts/config/settings.py` | 新增 `l1_extractor_max_factors` 配置项（A3） |
| `config/settings.yaml` | `l1_extractor_max_factors: 20`（A3） |
| `fts/factor_engine/extractors/futures_pipeline.py` | WebSearchExtractor 注册 + max_factors 配置注入（A/A3） |
| `fts/factor_engine/extractors/web_search.py` | 新增 WebSearchExtractor（A） |
| `fts/factor_engine/extractors/alternative_sources.py` | 宏观/公告提取器 max_factors 配置化（A3） |
| `fts/factor_engine/extractors/base.py` | `_llm_extract_factors` 默认 20 + max_tokens 4000→8000（A3） |
| `fts/factor_engine/meta_loop.py` | 实时链知识注入（C）+ 按子链分批（D2） |
| `fts/factor_engine/contracts.py` | DEFAULT_L1_BUDGET_CONFIG 上调（D1） |
| `fts/llm.py` | bootstrap prompt 支持 chain_focus（D2） |
| `tests/...` | 新增/更新测试（A/C/D 各层） |

## 四、验证方式

1. A 层：WebSearchExtractor mock 搜索测试（网络隔离）+ jobs 传入 web_collector 后 `_perceive_market` skipped=False（mock）。
2. C 层：实时知识注入内容断言（含价差/库存/开工段） + 数据缺失降级测试。
3. D 层：budget 配置断言 + 子链分批计数断言（energy 4 批、futures 1 批）。
5. 受影响模块定向回归 + ruff 全绿。
