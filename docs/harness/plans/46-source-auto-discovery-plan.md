# 46 — 知识源自动发现计划（Source Auto-Discovery）

> 版本: v2.104.0+105
> 状态: ✅ 实施完成（M1-M4） · 优先级: P2 · 负责人: FTS Agent
> 关联: plans/44（L1 知识补给增强）、bulk_collector.py（10 固定源）、GAP 登记待办
> 决策（2026-08-17）：用户确认自动找源方案落 plans 文档，随后独立实施；与 plans/44 的 ①-④ 修复（v2.104.0+102 已完成）正交。
> 定位（2026-08-17 修订）：最终目的是**尽可能多的高质量因子**，而非文章知识——源只是手段，因子评审/质检是唯一质量关卡。方案全流程自动（找源→采集→维护），**不设人工源确认环节**：坏源由"零因子产出"自动淘汰，不浪费人工判断。
> 实施（v2.104.0+104）：M1-M4 全部完成——SourceRegistry/SourceProber/SourceDiscoverer/DynamicSourceCollector 新建，collect_all 动态源集成，产出回写，配置化阈值；test_source_registry.py 新建 + test_bulk_collector.py 扩展。

## 一、背景与问题定位

### 1.1 当前源管理方式（代码事实）

`fts/factor_engine/extractors/bulk_collector.py` 现有 10 个源（arxiv/openalex/eastmoney/global/nonen/crossref/nber/cninfo/sina/semanticscholar）全部**硬编码**：

- 每个源一个手写 Collector 类（`ArxivBulkCollector` 等），解析逻辑内嵌
- `collect_bulk(source)` 用 `if/elif` 分派，`collect_all` 用固定元组枚举 10 源
- 新增源 = 手写类 + 改分派分支 + 改枚举列表 + 手写测试，**全人工**
- 源失效（如本次东财 400）只能人工发现，无自动感知/替代

### 1.2 目标差距

| 维度 | 现状 | 目标 |
|------|------|------|
| 源接入 | 人工（每源手写类+注册） | 自动发现 → 探活 → 注册 → 采集，**全流程无人** |
| 源健康 | 无监控，失效无人知 | 健康度计分，连续失败自动冷却/退役 |
| 源数量 | 固定 10，天花板低 | 动态扩展，随检索发现持续增长 |
| 因子产出 | 源质量无反馈，坏源持续空采 | **以因子产出为唯一导向**：零提取/零通过自动淘汰 |

**核心定位**：源的价值 = 能稳定提取出通过评审质检的因子。因此：
- 取消人工内容确认环节（内容好坏不由人判断，由因子评审质检判断）
- 坏源 = "连续 N 轮零提取/零通过"的源 → 自动降权/停用
- 源可以多、可以糙，只要贡献高质量因子就保留——**多源是因子供给的保障，不是被审对象**

## 二、目标与成功标准

| # | 目标 | 可验证成功标准 |
|---|------|---------------|
| S1 | 自动发现新论文/研报源，全流程无需人工 | 运行发现任务后，`l1_source_discovery` 新增候选源，经探活≥1 个晋升注册表；无任何人工确认动作 |
| S2 | 探活层拦截技术坏源 | 候选源 HTTP 失败/结构不识别 → 不入注册表，审计如实记录 |
| S3 | 动态源接入采集 | `collect_all` 口径 = 固定源 + 注册表 active 源，计数日志按 source 分列 |
| S4 | 健康度自动管理（技术维度） | 连续失败 N 次 → cooldown 停采；长期失败 → retired |
| S5 | 因子产出导向淘汰（业务维度） | 源连续 M 轮提取/验证**零产出** → 自动降权/停用；产出恢复即复权 |
| S6 | 零虚报 | 探活不过不注册；采集计数如实标注来源与时效 |

## 三、方案设计：四层管道

```
发现层 ──→ 探活层 ──→ 注册层 ──→ 采集/健康度层
(WebSearch  (HTTP嗅探   (DuckDB      (canary试采
 +LLM提取)   +类型识别)  注册表SSOT)   +冷却/退役)
```

### 3.1 发现层（Discovery）— `fts/factor_engine/extractors/source_discovery.py`（新建）

- **输入**：主题模板（commodity futures research API / energy market report / 中文研报数据源 / 商品研报 RSS / 能源机构 publication 等）
- **手段**：`WebSearchExtractor` 复用检索 → LLM 从搜索结果提取候选源 `{name, url, type_guess(json/rss/html), region, language}`
- **输出**：候选源写 `l1_source_discovery` 暂存表（探活前不入正式注册表），与注册表按归一化 URL 去重
- **开关**：`l1_source_discovery_enabled`（默认 on），运行频率跟随 L1 采集任务低频触发（如每日 1 次）

### 3.2 探活层（Probe）— 纯规则、零 LLM

- HTTP 嗅探（HEAD/GET 小样本），识别返回类型：
  - **JSON API**：Content-Type + 首字节 `{` → 抽样校验 title/date/link 字段可用
  - **RSS/Atom**：`<rss>`/`<feed>` 标记 → 解析样本条目
  - **HTML 列表**：`<a>` 密度 + 标题特征 → 链接提取样本
  - **PDF/zip**：记录类型，暂不深采（仅登记）
- **评分**：可用性（HTTP 200 / 解析成功率 / 字段完整度 / 更新频率），≥阈值才进注册层
- **关键约束**：探活不过的源**绝不注册**（防 LLM 幻觉源污染）

### 3.3 注册层（Registry）— DuckDB `l1_knowledge_sources`（SSOT）

表结构（E.4 短连接 + filelock，对齐既有存储规范）：

```sql
l1_knowledge_sources(
  source_id TEXT PRIMARY KEY,          -- 动态源唯一标识（url 归一化哈希或显式 id）
  name TEXT, url TEXT,
  type TEXT,                           -- json / rss / html
  region TEXT, language TEXT,
  discoverer_trace_id TEXT,            -- 发现链路
  probe_score REAL,
  status TEXT,                         -- pending / active / cooldown / retired
  first_seen TEXT, last_probe TEXT,
  consecutive_failures INT DEFAULT 0,  -- 技术维度：连续采集失败
  zero_output_rounds INT DEFAULT 0,    -- 业务维度：连续零因子产出轮数（2026-08-17 新增）
  canary_result TEXT,                  -- ok / fail
  updated_at TEXT
)
```

- 解析器由 `type` 驱动：JSON→schema 字段映射；RSS→feedparser；HTML→链接提取 + **LLM 兜底**（杂源标题/摘要提取，受 token 预算约束）
- 注册即进入采集池（pending → canary → active）

### 3.4 采集/健康度层（Collector + Health）

- 动态源与固定源统一走 `collect_bulk(source_id)`，复用 `l1_knowledge_cache` 去重存储（`(source, ref_id)` 唯一索引不变）
- **canary**：新源小批量试采 N 次（默认 3）→ 连续成功晋升 `active`；失败退回 `pending`
- **健康度双维度（2026-08-17 修订，对齐"因子产出导向"）**：
  - 技术维度：`consecutive_failures` 达阈值（默认 3）→ `cooldown`（停采）；累计失败（如 10 次）→ `retired`
  - **业务维度**：`zero_output_rounds` 记录"连续 M 轮采集→深读提取→验证均零因子产出"（默认 M=5）→ 自动降权/停用；该源后续产出恢复（提取出通过验证的因子）即清零复权。因子产出计数挂接现有 `l1_knowledge_cache` 按 source 聚合（injected 数由注入链路回写），**不新增人工判断**
- **因子产出的唯一定义**：源提取的候选通过 L1 verifier → 注入 → L2 评审/质检的因子数量。源内容好坏不由人判断，由这条现有质检链路裁决（与 plans/44 评审质检统一）
- **计数契约**：`collect_all` 审计 = 固定源 + 注册表 active 源；日志 `[bulk] 动态源采集完成 source=<id> collected=N`

## 四、集成点

| 位置 | 改动 |
|---|---|
| `fts/factor_engine/extractors/source_discovery.py`（新） | 发现 + 探活 + LLM 兜底解析 |
| `fts/factor_engine/extractors/source_registry.py`（新） | 注册表 CRUD + 健康度流转 + canary |
| `fts/factor_engine/extractors/bulk_collector.py` | `collect_all` 改为固定源 + 注册表 active 源循环；`_http_get` 复用 |
| `fts/config/settings.py` | 新增 `l1_source_discovery_enabled`、探活/健康度阈值参数 |
| `fts/factor_engine/extractors/futures_pipeline.py` | `BulkKnowledgeExtractor` 可选接入动态源扫描 |

## 五、风险与降级（诚实边界）

- **LLM 幻觉源** → 探活层硬校验（HTTP 200 + 内容特征）兜底，不过不注册
- **新源不稳定** → canary + cooldown 冷却，独立失败不阻断整体（沿用 best-effort 语义）
- **HTML 杂源误解析** → 规则优先 + LLM 提取，结构仍乱的源降级为"仅登记不深采"
- **坏源污染因子池** → 不会发生：因子必须过 L1 verifier → L2 评审/质检才会注入/晋升，坏源最多贡献无效候选，由 `zero_output_rounds` 自动停用
- **不虚报**：探活/采集计数如实标注来源与时效；动态源失败计数进入审计日志
- **预算**：发现层 LLM 提取受 `l1_extractor_max_factors` 同源配额约束，HTML 兜底解析走深读预算

> 全流程人工介入点：**无**。找源、探活、注册、采集、健康度（技术+产出双维度）全部自动；付费/闭源源授权除外（如接入需 API key 的源，授权动作由用户完成，属外部前置条件而非流程内人工环节）。

## 六、任务拆分与验收（里程碑）

### M1：注册表 + 探活层（纯规则，无 LLM）
- [ ] 建 `l1_knowledge_sources` 表 + `SourceRegistry` CRUD/状态流转
- [ ] `SourceProber`：HTTP 嗅探 + JSON/RSS/HTML 类型识别 + 评分
- [ ] 测试：注册表 CRUD/状态流转 + 探活类型识别（mock http）+ 坏源拒绝

### M2：发现层（WebSearch + LLM 提取）
- [ ] `SourceDiscoverer`：主题检索 → LLM 提取候选源 → 写 discovery 表
- [ ] 与注册表 URL 归一化去重
- [ ] 测试：WebSearch+LLM 提取（mock）/ 重复源拦截 / 幻觉源标记

### M3：canary + 健康度 + collect_all 集成
- [ ] canary 试采（默认 3 次）→ active；失败退 pending
- [ ] 技术健康度：连续失败 → cooldown / retired
- [ ] **业务健康度：`zero_output_rounds` 产出淘汰（连续 M 轮零产出停用，产出恢复复权）**
- [ ] `collect_all` 固定源 + active 动态源循环
- [ ] **因子产出计数回写**（injected 数按 source 聚合挂接注入链路）
- [ ] 测试：canary 晋升/冷却/退役 + 产出淘汰/复权 + `collect_all` 含动态源计数（+ 现有 10 源回归）

### M4：HTML 杂源 LLM 兜底解析
- [ ] 杂源标题/摘要提取（LLM，受预算约束）
- [ ] 降级登记机制（结构不识别 → 仅登记不深采）
- [ ] 测试：杂源解析 + 降级登记

## 七、测试计划（预估 +18 用例）

- `test_source_registry.py`：CRUD / 状态流转（pending→active→cooldown→retired）/ 健康度计数 / **产出淘汰与复权**
- `test_source_discovery.py`：WebSearch+LLM 提取（mock）/ 探活类型识别（mock http）/ 坏源拒绝
- `test_bulk_collector.py` 扩展：动态源接入 `collect_bulk` / canary 晋升与冷却 / `collect_all` 含动态源计数 / 因子产出回写

## 八、版本与文档

- 版本：实施后 `python scripts/bump_version.py --build`（单日 build 不限次）
- 文档：`01-architecture.md`（动态源管道）+ `06-testing.md`（用例数）+ `07-operations.md`（版本历史）同步
