# 58 — GitHub 推送范围治理计划（Push Safety）

> 版本: v3.0.0+25
> 日期: 2026-08-20 · 状态: ✅ 已实施 · 优先级: P1
> 关联: .gitignore "Push safety" 段、scripts/cleanup_push_scope.py、CLAUDE.md §5.9（安全与可移植性）

---

## 1. 背景

RHI 递归 Harness 自进化落地（plans/57 之后新增 `scripts/rhi_global_setup.py`）引入运行时目录 `.rhi/`，不在旧 `.gitignore` 覆盖范围，触发对 FTS 推送到 GitHub 范围的全面重审。

## 2. 审计结论（2026-08-20）

- **仓库历史干净**：`git ls-files` 中无 `.env`、`data/`、`memory/`（除 `.gitkeep` 占位）、`*.duckdb`、`*.db`、`*.log` 被跟踪
- **代码层零硬编码凭据**：`fts/` 全量扫描无 `sk-`/`password`/`api_key` 实值
- **配置模板安全**：`config/*.yaml` 仅注释与占位符；`.env.example` 为占位值；CI 用 `${{ secrets.GITHUB_TOKEN }}` 引用，零外部凭据
- **唯一实质遗漏**：`.rhi/`（RHI 运行时历史）未在 `.gitignore` → 已修复

## 3. 禁止推送清单（严格禁止，发现即清理）

| 类别 | 内容 | 依据 |
|:-----|:-----|:-----|
| 真实凭据 | `.env` / `.env.local` / `.env.production` / `credentials*` / `*.key` / `*.pem` / `*.p12` / `*.jks` / `*.crt` | CLAUDE.md §5.9 |
| 运行时数据 | `.rhi/`、`data/`、`memory/`、`seeds/`、`logs/` | 运行时产物非代码 |
| 数据库文件 | `*.duckdb`、`*.db`、`*.db-wal`、`*.db-shm` | 防御性兜底 |
| 日志/调试 | `*.log`、`debug_llm_response_*.txt`、`reports/`、`output/`、`signals/` | 可能含敏感中间产物 |
| 一次性探索脚本 | `scripts/_*.py`（12 个，本次清理） | 非交付物、仓库噪音 |

## 4. 允许推送清单（本次审计确认干净）

| 类别 | 内容 |
|:-----|:-----|
| 核心代码 | `fts/`（全部模块）、`tests/` |
| 正式脚本 | `scripts/`（`bump_version.py`、`verify_doc_consistency.py`、`update_doc_versions.py`、`cleanup_push_scope.py`、`rhi_global_setup.py` 等非 `_` 开头） |
| 规范文档 | `AGENTS.md`、`CLAUDE.md`、`CODE_WIKI.md`、`README.md`、`CHANGELOG.md`、`COMPLIANCE.md`、`DISCLAIMER.md`、`LICENSE`、`NOTICE` |
| Harness 体系 | `docs/harness/`（01-09 + plans + design + acceptance）、`docs/FTS_manual.md` |
| 配置模板 | `config/*.yaml`、`.env.example`（占位符） |
| CI/部署 | `.github/workflows/`、`deploy/k8s/` |
| 项目资产 | `articles/`、`diagrams/`、`agents/`、`pyproject.toml`、`requirements.txt`、`start_fts.ps1` |

## 5. 清理动作（已实施）

1. `.gitignore` 新增 "Push safety" 段：`.rhi/` + 防御性规则（`*.duckdb`/`*.db`/`*.log`/证书私钥/`.env.local` 等）
2. 12 个 `_` 开头一次性探索脚本移出 git 跟踪（`git rm --cached`，本地文件保留）：
   `_attribution_energy_holdout.py`、`_check_contract_kline.py`、`_check_contract_kline2.py`、`_check_migrate_result.py`、`_p2_fork_engines.py`、`_probe_stage2.py`、`_s1_inject_close.py`、`_signal_common.py`、`_test_aks_contracts.py`、`_test_aks_fields.py`、`_test_tq_contracts.py`、`_tmp_probe_history.py`
3. **误拦修复**：`seeds/`（L1 配置层）从忽略改为入库——旧 `seeds/` 规则被移除，26 个 YAML（`seeds/futures/*.yaml` ×20、`seeds/energy/*.yaml` ×5、`seeds/README.md`）已 `git add` 纳入跟踪；`cleanup_push_scope.py` 禁止列表同步移除 `seeds/`

## 6. 自动化工具（scripts/cleanup_push_scope.py）

```bash
# 清理 '_' 探索脚本（dry-run 预览）
python scripts/cleanup_push_scope.py prune
# 实际执行 git rm --cached（保留本地文件）
python scripts/cleanup_push_scope.py prune --apply
# 验证推送范围安全（扫描 git 跟踪中的禁止项）
python scripts/cleanup_push_scope.py verify
```

### 6.1 验证步骤（推送前执行）

1. `python scripts/cleanup_push_scope.py verify` → 输出 `禁止推送项: 0` 且 `'_' 探索脚本: 无残留`
2. `python scripts/verify_doc_consistency.py` → 14/14 通过
3. `git status` 人工复核：仅预期文件（代码/文档/脚本）在列
4. 通过后 commit + push

## 7. 待推送清单（截至 v3.0.0+9，未 commit）

- 新增未跟踪：`scripts/rhi_global_setup.py`、`scripts/cleanup_push_scope.py`、本计划文档
- 已跟踪修改（90+）：`.gitignore`、`AGENTS.md`、`CLAUDE.md`、`CODE_WIKI.md`、`README.md`、`docs/FTS_manual.md`、`docs/harness/`（01-09 + plans + design 版本头 +9）

## 8. 后续建议

- **P2**：CI 增加 `cleanup_push_scope.py verify` 步骤，防止禁止项回归（`docs/harness/08-gap-analysis.md` 登记）
- **P2**：历史 `_` 脚本本地保留 30 天后按需归档删除（`scripts/_` 目录确认无引用后）
