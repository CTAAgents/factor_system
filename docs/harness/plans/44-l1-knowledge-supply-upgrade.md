# 44 — L1 每日知识补给增强计划（300 篇/天 + 四方向升级）

> 版本: v3.1.0
> 状态: ✅ 实施完成（Phase 1-2 + 全球多语种补丁 + Phase 3 漏斗/闭环已全部完成） · 优先级: P1 · 负责人: FTS Agent
> 关联: GAP-123（软失败重写）、GAP-126（提取器配置化，open）、GAP-127（平台 API，远期）、GAP-131（拒绝候选落盘，v2.104.0+82 已闭环）、plans/41（L1 知识注入增强）、plans/43
> 决策（2026-08-16）：用户确认四方向全做（编译修复+失败复活 / 知识源扩容 / 验证漏斗优化 / L1→L2 闭环），核心目标 **每天全球范围内阅读 ≥300 篇论文/研报做知识补给**，节奏=先出方案再实施。

## 一、背景与问题定位

### 1.1 当前 L1 因子获取漏斗（2026-08-16 实测基线）

```
知识源(5) → 候选生成(≤30/run) → 验证漏斗(4 维) → 注入(≈28/run)
```

| # | 环节 | 现状（代码事实） | 问题 |
|---|------|-----------------|------|
| 1 | 论文源 | arXiv 4 类（q-fin.ST/GN/PM/RM）× `max_results=3`（[futures_pipeline.py#L347](../01-architecture.md)），摘要截断 500 字符 | **每天仅 12 篇**，与"全球 ≥300 篇/天"差 ~25 倍 |
| 2 | 研报源 | 东财 API `pageSize=5`（[futures_pipeline.py#L125](../01-architecture.md)），reportType=1 个股研报，**仅中文** | **每天仅 5 篇**且无全球/英文研报覆盖，无行业/策略/量化研报覆盖 |
| 3 | WebSearch | 3 条**固定**中文关键词（[web_search.py#L60](../01-architecture.md)） | 检索方向不随知识缺口/市场异动调整 |
| 4 | 编译失败 | 规则修复 6 策略（GAP-131 扩展后）兜不住时直接拒绝 | 2026-08-16 实测 2 个因子（`fut_carry_roll yield`/`fut_hei_ma`）失败后不可复活 |
| 5 | 软失败 | narrative<20 等软失败仅 LLM 重写 1 次 | 重写后仍不达标即丢弃（实测 4 个经重写救回，仍存在损耗） |
| 6 | 去重 | 仅名称精确匹配（`_is_duplicate_by_name` + `is_duplicate`） | 语义重复（同构不同名）可重复注入 |
| 7 | L1→L2 | 注入 1346 个候选，**无晋升转化率跟踪** | 闭环缺失，L1 质量无法反向优化 |

### 1.2 "300 篇/天"目标量级差距

| 维度 | 现状 | 目标 | 差距 |
|------|------|------|------|
| 论文 | 12 篇/天（4 类 × 3） | ≥300 篇/天（全球） | ~25 倍 |
| 研报 | 5 篇/天 | 纳入 300 篇口径 | ~20 倍 |
| LLM token | 估算 151K/run（超 50K 预算 3 倍，非真实计量） | 分层计量，深读层受预算约束 | 需真实计量 |

**可行性结论**：300 篇**不能全文逐篇 LLM 阅读**（300 篇 × 2-5K token 全文 = 0.6-1.5M token，远超任何日预算）。可行路线 = **三层管线**：批量采集（零 LLM token）→ embedding 粗筛（零 LLM token）→ 命中子集 LLM 深读提取（受预算约束）。

## 二、目标与成功标准

| # | 目标 | 可验证成功标准 |
|---|------|---------------|
| S1 | 每天全球采集 ≥300 篇论文/研报，**不限于中英文** | 当日批量采集日志按来源分列：arXiv(英文全球论文) + OpenAlex(多语种论文，含日/德/法/韩/西/俄) + 东财(中文研报) 合计 `bulk_collected ≥ 300`；非中英语种研报（IEEJ/KEEI/IFPEN）与全球能源报告（CFTC/IEA/OPEC/EIA）单列，language 字段如实标注 |
| S2 | 编译失败因子可 LLM 修复或复活 | 规则修复失败的候选经 LLM 修复后 `validate_factor_code` 通过并注入；或落 `l1_rejected_*` 后可重试/人工重注入 |
| S3 | 验证漏斗转化率提升 | 软失败损耗（narrative）显著下降；语义重复被拦截；单次运行注入 ≥ 当前基线（energy 28/30） |
| S4 | L1→L2 闭环可见 | `state_kv` 新增 L1→L2 转化统计，每日报告输出 injected→consumed→promoted 转化率 |
| S5 | 预算可控 | LLM token 分层真实计量，深读层 + bootstrap 合计 ≤ 日预算（60K，实测后校准，超限登记决策） |

## 三、方案设计

### P0 层 — 采集-筛选-深读三层管线（300 篇/天核心）

新建 `fts/factor_engine/extractors/bulk_collector.py`：

**① 批量采集层（零 LLM token，全球多源）**

覆盖全球公开论文/研报来源（**不限中英文，多语种如实分列**，2026-08-16 用户确认口径）：

| 采集器 | 源 | 语种/地域 | 采集量目标（/天） |
|--------|-----|----------|------------------|
| `ArxivBulkCollector` | arXiv q-fin 4 类 × `l1_source_arxiv_max_results`（默认 50），submittedDate 倒序，ID 哈希增量去重 | 英文，全球量化/金融论文 | ~150 |
| `OpenAlexBulkCollector` | OpenAlex 开放学术**多语种分路**：`l1_openalex_languages`（默认 en/zh/ja/de/fr/ko/es/ru）逐语种本地化关键词 query，覆盖全球期刊 + SSRN 预印本 + 各语种学术论文；`language` 字段如实标注 | **多语种（英/中/日/德/法/韩/西/俄），全球** | ~100 |
| `EastmoneyReportBulkCollector` | 东财研报 `pageSize` 100 × ≤2 页，`reportType` 覆盖个股/行业/策略，标题+关键词过滤（量化/CTA/期货/化工/能化/商品/宏观） | 中文，国内券商 | ~100 |
| `GlobalReportBulkCollector` | 国际机构公开报告：CFTC COT 周报、EIA WPSR、IEA 石油市场月报、OPEC MOMR、ENTSO-E/EU 能源市场报告（公开 HTML best effort） | 英文，全球能源/商品 | ~30 |
| `NonEnReportBulkCollector` | **新增（非中英语种研报）**：日本 IEEJ（日本エネルギー経済研究所）、韩国 KEEI（에너지경제연구원）、法国 IFPEN（IFP Énergies nouvelles）能源与商品公开研究报告（无官方 API 走网页检索，best effort） | **日/韩/法，非中英语种研报** | ~30 |

- 存储：DuckDB 表 `l1_knowledge_cache`（source/ref_id/date/title/abstract/url/language，`(source, ref_id)` 唯一索引防重），日增量写入；`fts/store/duckdb_lock` 短连接 + filelock（对齐 E.4 S1）。
- 输出契约：`collect(source) -> {collected: int, new: int, deduped: int, errors: list}`，日志 `[bulk] 采集完成 source=arxiv collected=200 new=180 deduped=20`。
- **300 篇口径（全球多语种，按来源分列，不虚报）**：arXiv 150（英文全球论文）+ OpenAlex 100（**多语种论文**，含日/德/法/韩/西/俄）+ 东财 100（中文研报）+ 日韩法能源研报 30（**非中英语种**）+ 国际能源报告 30（英文）≈ **410 ≥ 300**。单源缺口用其他源补足；付费/闭源源只采摘要不采全文；**非英/中论文与研报进入同一粗筛与深读链路**——embedding 用多语种模型（paraphrase-multilingual-MiniLM-L12-v2 原生支持 50+ 语种）跨语种相关性筛选，LLM 深读直接读原文（日/德/法均支持），不额外翻译、不额外耗 token。

**② 粗筛层（零 LLM token）**
- 新增 `fts/factor_engine/extractors/knowledge_filter.py`：
  - `TextEmbedder`：轻量本地 embedding（`sentence-transformers` 中文多语模型 `paraphrase-multilingual-MiniLM-L12-v2`，~120MB，惰性加载；模型缺失自动降级为关键词规则粗筛——量化/期货/CTA/能化等命中词计数，如实标注降级）。
  - `KnowledgeRelevanceFilter`：query 模板（"商品期货 量化因子 CTA 能化产业链"）× 每条记录余弦相似度，≥ `l1_embedding_threshold`（默认 0.30）命中 → 深读子集（目标 40~60 篇/天）。
- 语义去重（C4）复用同一 embedding：与现有种子/已注入候选 `name+code 语义` 余弦 > `l1_dedup_threshold`（默认 0.90）判重。

**③ 深读提取层（LLM token，受预算约束）**
- 命中子集（40~60 篇）分批发 `_llm_extract_factors`（复用现有提取器基类），`max_factors=20` 配额不变；摘要级输入（~400 token/篇），token 分层计量。
- 预算测算：60 篇 × (400 in + ~100 out) ≈ 30K token/天，与 bootstrap（30 候选 × 5K 估算 = 30K）合计 ~60K —— **恰好贴预算上限，需真实计量后校准**（见 §5）。

### 方向 1 — 编译修复 + 失败复活（直接闭环 GAP-131 场景）

- **C1 LLM 编译修复**：`LLMClient` 新增 `fix_factor_code(code, error_reason, trace_id) -> Optional[str]`（OpenAI 实现 + `MockLLMClient` 实现）；bootstrap 编译验证失败且 `fix_factor_code`（规则 6 策略）修复失败时调用 LLM 修复，再 `validate_factor_code` 复核，通过即按自动修复标记。
- **C2 失败复活**：新增 `l1_rejected_retry`（默认 true）——bootstrap Step 2.5 后扫描同 market 的 `l1_rejected_*` 目录，对 `is_executable=False` 且 `l1_rejection.reasons` 含"编译失败"的候选：① 优先 LLM 修复代码 → 重新验证 → 通过即注入；② 修复仍失败则保留（不重复尝试同轮）。注入成功即从 rejected 目录移走。降级：目录损坏/缺失跳过不阻断。
- 关联：GAP-131 的 `_persist_rejected` 已为 C2 提供落盘基础（v2.104.0+82 已闭环）。

### 方向 2 — 知识源扩容

- **A1 WebSearch 动态检索**：新增 `KnowledgeGapQueryGenerator`——统计 factor_pool 已注入因子的 `parent_topic`/`name` 维度分布，定位空白维度（如"库存""季节性""展期"），生成缺口 query（每轮换新）；叠加当日市场异动 query（从 market_snapshot 异动品种/子链提取，如"SC 价差 异动"）。`l1_dynamic_websearch`（默认 true）。
- **A2 源扩容** = P0 采集层（arXiv/东财扩容）+ 可选新增交易所仓单/库存报告源（复用东财 RPT 接口）；GAP-126 提取器配置化登记为后续项（本轮不落地，避免范围膨胀）。

### 方向 3 — 验证漏斗优化

- **C3 narrative 补全**：`_llm_extract_factors` 输出后统一检查 `economic_logic.narrative`，<20 字时用"因子名 + 机制要点"模板补全至达标（零额外 LLM 调用）；仍不足才触发既有 LLM 重写。
- **C4 语义去重**：见 P0 ②（embedding 相似度），替换/补充名称精确匹配。
- **B1 prompt 负面样本**：bootstrap prompt 注入已注入因子名清单（≤20 个，控制 token），引导 LLM 生成非重复机制。

### 方向 4 — L1→L2 闭环

- **D1 转化率统计**：`state_kv` 新增 `meta_loop/{market}/l1_l2_funnel` = `{injected, l2_consumed, l2_promoted, updated_at}`；`evolution_loop --chain energy` 消费 `l1_injected_*` 时回写 consumed，晋升时回写 promoted；新增报告脚本 `scripts/l1_l2_funnel_report.py`（每日输出转化率）。
- **D2 消费速率监控**：report 对比 injected vs consumed 增量，积压 >N 天输出 warning（防 L1 无限注入）。

## 四、配置与契约变更

新增 `config/settings.yaml` / `FTSConfig`：

| 配置 | 默认 | 说明 |
|------|------|------|
| `l1_source_arxiv_max_results` | 50 | arXiv 每类别拉取数（3→50） |
| `l1_source_report_page_size` | 100 | 东财研报分页大小（5→100） |
| `l1_embedding_enabled` | true | 文本 embedding 粗筛/语义去重开关 |
| `l1_embedding_threshold` | 0.30 | 相关性粗筛阈值 |
| `l1_dedup_threshold` | 0.90 | 语义去重阈值 |
| `l1_rejected_retry` | true | 失败候选复活开关 |
| `l1_dynamic_websearch` | true | WebSearch 动态 query 开关 |
| `l1_knowledge_deepread_max` | 60 | 深读子集上限（篇/天） |
| `l1_openalex_languages` | `["en","zh","ja","de","fr","ko","es","ru"]` | OpenAlex 多语种分路语种清单（非中英语种全球覆盖） |
| `l1_non_en_reports_enabled` | true | 非中英语种研报源（IEEJ/KEEI/IFPEN）开关 |
| `l1_l2_backlog_days` | 7 | L1→L2 积压 warning 阈值（天），D2 报告用 |

契约：
- `LLMClient.fix_factor_code` 新接口（基类默认 None + OpenAI 实现 + Mock 实现）。
- `collect_bulk(source)` / `filter_relevant(records)` / `dedup_semantic(candidate)` 模块级函数，供测试直接调用。

## 五、预算与 token 治理

- 现状 `_estimate_tokens` 为估算（1000 + 候选×5000 + 缺口×200），实测 energy `tokens_consumed=151K` vs `budget_limit=50K`（3 倍超，估算失真）。
- 改造：提取器层按实际调用计量（LLMCallRecord 累计，见 `fts/llm.py`），`state.tokens_consumed` 写真实值；分层预算——采集/粗筛 0、深读 ≤30K、bootstrap ≤30K。
- 若真实计量后超 60K：登记决策上调 `daily_token_limit`（关联 GAP 项，不静默放行）。

## 六、测试计划

| 测试文件 | 覆盖 |
|---------|------|
| `test_bulk_collector.py` | 多源（arXiv/OpenAlex 多语种分路/东财/GlobalReport/NonEnReport）mock：多语种 language 字段与分路 query 断言/增量去重/分页/过滤/计数契约/API 失败降级/缓存唯一索引/各语种源独立失败不阻断 |
| `test_knowledge_filter.py` | 相关性粗筛命中/模型缺失关键词降级/语义去重阈值边界 |
| `test_llm_code_fix.py` | `fix_factor_code` LLM 接口契约 + 集成（规则失败→LLM 修复→通过注入）+ Mock 实现 |
| `test_l1_rejected_retry.py` | 失败复活全流程/修复仍失败保留/移走已注入/目录损坏降级 |
| `test_websearch_dynamic_query.py` | 知识缺口 query 生成/异动 query/开关 |
| `test_l1_l2_funnel.py` | 转化统计回写（injected/consumed/promoted）/报告输出/消费积压 warning/MetaLoop.run 注入回写接线/SeedManager 消费与晋升回写 |
| `test_meta_loop_phase3.py` | C4 语义去重拦截与开关/B1 负面样本注入 prompt/C3 bootstrap narrative 补全 |
| 受影响回归 | test_factor_program / test_meta_loop / test_extractors 目录 / test_evolution_*（SeedManager） |

## 七、风险与降级

| 风险 | 降级 |
|------|------|
| 东财 API pageSize=100 限流/改版 | 分页降级（逐页 20 条）+ 失败仅 warning；采集量如实报告 |
| embedding 模型下载/加载失败（离线） | 关键词规则粗筛降级，标记 `degraded=keyword_filter` |
| 深读 token 超预算 | 子集上限 `l1_knowledge_deepread_max` 收紧；超限登记预算上调决策 |
| SSRN/国际机构源抓取失效（无官方 API / 反爬 / 付费墙） | 单源失败仅 warning 并如实分列计数，其余源正常采集；SSRN 走 OpenAlex 覆盖（含 SSRN 预印本），付费内容仅采摘要不采全文 |
| "全球 300 篇"多语种口径 | 按来源分列计数（arXiv 英文 + OpenAlex 多语种 + 东财中文 + 日韩法能源研报 + 国际能源报告），单源缺口用其他源补足；付费/闭源源不虚报 |
| OpenAlex 多语种 query 命中率低（部分语种能源量化研究少） | 每语种用本地化关键词 query；命中不足按语种如实分列，不虚报 |
| IEEJ/KEEI/IFPEN 反爬/改版/无 RSS | best effort：单源失败仅 warning 并如实分列计数，其余源正常采集；只采摘要不采全文 |
| LLM 修复产生新编译错误 | 修复代码必须过 `validate_factor_code` 才采纳，失败保留原状 |
| 语义去重误杀（同因子不同实现） | 阈值 0.90 保守 + 仅拦截高相似，日志可审计 |

## 八、实施分期

- **Phase 1（本轮优先，依赖最少）**：C1 LLM 编译修复 + C2 失败复活 → 测试 → 回归。
- **Phase 2（300 篇能力）**：P0 三层管线（bulk_collector + embedder + 深读）+ 东财/arXiv 扩容 + A1 WebSearch 动态化 → 测试 → 真实运行验证采集 ≥300 篇。
  - **Phase 2 补丁（2026-08-16 用户确认"全球范围内不限中英文"）**：OpenAlex 多语种分路（`l1_openalex_languages`）+ 新增 `NonEnReportBulkCollector`（IEEJ/KEEI/IFPEN 日韩法能源研报）→ 测试。
- **Phase 3（漏斗+闭环）**：C3 narrative 补全 + C4 语义去重 + B1 prompt 负面样本 + D1/D2 L1→L2 闭环 → 测试 → 报告。
- 每 Phase 完成：`ruff check` + `mypy` + 受影响模块回归 + `bump_version.py --build` + 文档同步（01-arch/03-config/06-testing/07-operations/08-gap/本计划）。

## 九、文档同步清单

- 01-architecture.md（L1 流程图：三层管线 + rejected 复活 + L1→L2 漏斗）
- 03-configuration.md（§新增 8 项配置）
- 06-testing.md（新增测试文件用例数）
- 07-operations.md（版本历史）
- 08-gap-analysis.md（如登记新 GAP：L1 token 预算治理 / 英文研报源受限）
- README.md（工程指标）

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|--------------|-----------|---------|
| `bulk_collector.py` collect 计数契约 → §三 P0① | 单日 `bulk_collected ≥ 300` 或按来源如实分列 | `test_bulk_collector.py` + 真实运行日志 |
| `LLMClient.fix_factor_code` → §三 方向1 C1 | 规则修复失败后 LLM 修复候选通过 validate | `test_llm_code_fix.py` |
| `l1_rejected_*` 复活流程 → §三 方向1 C2 | 复活候选注入后从 rejected 移走 | `test_l1_rejected_retry.py` |
| `state_kv` l1_l2_funnel → §三 方向4 | consumed/promoted 回写正确 | `test_l1_l2_funnel.py` |
| bootstrap C4 语义去重 → §三 方向3 | 语义高相似候选 is_duplicate 置位 | `test_meta_loop_phase3.py` |
| `_build_bootstrap_prompt` 负面样本段 → §三 方向3 B1 | prompt 含"已注入因子（负面样本）"段 | `test_meta_loop_phase3.py` |
