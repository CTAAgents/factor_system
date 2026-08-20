CLAUDE.md — FTS 编码行为准则 @AGENTS.md
本文件定义 FTS 项目的 AI 编码行为准则，适用于所有 AI 助手和开发者。作为项目标准文件，不因开发环境变化而变化。

权衡：以下准则偏向谨慎而非速度。对简单任务可自行判断。

1. 先思考，再编码
不要猜测。不要隐藏疑惑。把 tradeoff 摊在桌上。

实施之前：
- 明确陈述你的假设。如果不确定，就问。
- 如果存在多种解释，全部列出来——不要悄悄选一个。
- 如果有更简单的方案，直接说出来。该反对时就反对。
- 如果某件事不清楚，停下来。说出疑惑点，然后问。

2. 简单至上
解决该问题的最小代码量。不写投机代码。
- 不做需求之外的额外功能。
- 不为一次性代码做抽象。
- 不写没人要求的"灵活性"或"可配置性"。
- 不为不可能发生的场景写错误处理。
- 如果你写了 200 行但本可以 50 行搞定，重写它。

3. 外科手术式修改
只动必须动的。只清理自己的烂摊子。
- 修改现有代码时：不要"顺手改进"旁边的代码、注释或格式；不要重构没坏的东西；遵循原有风格。
- 你的改动产生孤儿代码时：清理因你的改动而不再使用的 import/变量/函数；不要删除早就存在的 dead code，除非被要求。
- 检验标准：每一行改动的代码都应能直接追溯到用户的请求。

4. 目标驱动执行
定义成功标准。循环验证直至达标。
把任务转化为可验证的目标：
- 模糊任务 → 明确目标："加个验证" → "给无效输入写测试，然后让它们通过"；"修这个 bug" → "写一个复现它的测试，然后让测试通过"；"重构 X" → "确保重构前后测试全部通过"。
多步骤任务先简述计划：1. [步骤] → 验证：[检查方式]；2. ...；3. ...
强的成功标准让你能自主循环迭代。弱的标准需要持续澄清。

5. HARNESS 工程规范优先 — 强制性规则
本准则优先级高于以上所有规则。任何工作必须严格遵守 docs/harness/ 目录下的工程规范。

5.1 文档先行原则
任何架构/流程变更，必须先更新以下文档再写代码：
- docs/harness/01-architecture.md（架构图）
- docs/harness/02-lifecycle.md（阶段定义）
- docs/harness/06-testing.md（测试用例与覆盖率）
- docs/harness/07-operations.md（版本历史）
- docs/harness/08-gap-analysis.md（差距管理）
- docs/harness/09-advancement-plan.md（晋级计划）
- docs/archive/plans/production_plan.md（生产就绪计划，已归档）

5.2 commit 前 13 项检查清单 — 必须全部通过
1. 数据流/架构变更是否反映？ → docs/harness/01-architecture.md
2. 阶段/文件名/产出物是否反映？ → docs/harness/02-lifecycle.md
3. 新配置项是否登记？ → pyproject.toml / docs/harness/03-configuration.md
4. 降级/熔断/超时路径是否更新？ → docs/harness/01-architecture.md / 04-resilience.md
5. 新指标/日志是否已加？ → docs/harness/05-observability.md
6. 测试文件和用例数是否更新？ → docs/harness/06-testing.md
7. 版本号和版本历史是否追加？ → docs/harness/07-operations.md + pyproject.toml
8. 差距登记/关闭是否更新？ → docs/harness/08-gap-analysis.md
9. 晋级里程碑是否更新？ → docs/harness/09-advancement-plan.md
10. 流程文档是否同步？ → docs/archive/plans/production_plan.md / business_flow.md
11. CLAUDE.md 职责变更是否反映？ → CLAUDE.md
12. README 工程指标（测试数/覆盖率/版本）是否同步？ → README.md
13. README 快速参考是否刷新？ → README.md

5.3 契约优先原则
先定义 TypedDict/接口契约，再实现代码。变更前必须更新 docs/harness/01-architecture.md 中的接口定义。

5.4 测试随重构原则
每阶段先写测试，测试全绿才能进入下一阶段。当前 8469 测试（2026-08-20 收集口径），覆盖率 94%（全量回归口径），目标 100%。
分级测试政策：日常任务只跑受影响的**模块/集成测试**；**全量回归**仅两类时机执行——发布前（里程碑版本 bump/晋级里程碑）必跑 + 每月底例行巡检一次。日常 build bump 不触发全量回归。禁止每次任务跑全量。

5.5 trace_id 全链路原则
trace_id 必须贯穿所有模块、文档和日志。所有 CLI 子命令和工作流启动时必须生成 trace_id。

5.6 角色边界原则
Agent 职责不可越界。FTS 专注因子发现、评估、组合与演化（v3.0.0 起为因子生产系统，策略合成迁移 Regime-Driven）。数据采集加工由 Data-Core 负责，交易决策由 FDT 负责。

5.7 差距管理原则
重大技术债务必须登记到 docs/harness/08-gap-analysis.md，按 P0/P1/P2 优先级推进。

5.8 版本号纪律原则（SemVer build 段制）
版本号 = 里程碑版本 + build 段（x.y.z[+N]），统一通过 scripts/bump_version.py 管理，禁止手工改 pyproject.toml：
- 日常开发（GAP 实现、测试补充、bugfix、文档同步、数据修复）→ `python scripts/bump_version.py --build --message "..."`，build 段 +1，并同步 pyproject.toml、README 徽章、07-operations.md 版本历史与全部文档版本头
- 里程碑发布（晋级里程碑完成 + 全量回归通过 + 可交付）→ `python scripts/bump_version.py --type patch/minor/major --message "..."`，正式版本 +1 且 build 段清零
- 单日护栏仅约束里程碑 bump（一天最多一次，确需新版本用 --force）；build bump 不限次
- bump 后文档版本头同步由 scripts/update_doc_versions.py --apply 自动完成

5.9 安全与可移植性原则
- 禁止硬编码绝对路径：一律使用相对路径、`Path(__file__)` 或配置系统解析
- 禁止在代码/配置中硬编码 API key 等敏感凭据：必须通过 `.env` + `os.environ` 读取，`.env` 不入库
- 脚本禁止 `sys.path.insert` 写入绝对路径引导导入

生效标志
这些准则生效的标志：
- diff 中不必要的改动减少；因过度复杂而重写的次数减少
- 澄清性问题在实现之前提出（而非在犯错之后）
- 所有代码变更都有对应的文档更新；所有 commit 前都通过了 13 项检查清单
- trace_id 贯穿所有模块和日志

---

## RHI 递归 Harness 自进化（FTS 适配版，v9.22.0+）

将本 CLAUDE.md 作为 Harness prompt 进行版本化自优化（参考 RHI arXiv:2607.15524 + MemoHarness arXiv:2607.14159），评分维度已按 FTS 实际规则适配：

```bash
python scripts/rhi_global_setup.py init     # 首版快照
python scripts/rhi_global_setup.py step     # 执行一轮自优化（每次修改 CLAUDE.md 后运行）
python scripts/rhi_global_setup.py status   # 查看评分、改进率与收敛状态
```

评分维度（FTS 适配版）：
- memory_coverage (0.30)：memory 存储体系 + D:\Knowledge 知识库引用
- rule_completeness (0.30)：13 项检查清单 + 反模式(AP) + verify_doc_consistency
- consistency (0.20)：docs/harness 文档体系 + trace_id 全链路 + 差距管理
- clarity (0.20)：CLAUDE.md 本体行数（<500 最佳）与版本号纪律

改进率低于 0.3 或达最大轮次后自动收敛。
