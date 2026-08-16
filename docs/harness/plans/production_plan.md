# FTS 生产就绪计划

> 版本: v2.104.0+90
> 最后更新: 2026-08-07

> ⚠️ **重要说明**：FTS 当前为**开发阶段**，尚未达到生产就绪状态。本文档记录生产部署所需的各项准备工作。

---

## 1. 生产就绪检查清单

### 1.1 基础设施

| # | 项目 | 状态 | 优先级 | 备注 |
|:-:|:-----|:----:|:-------|:-----|
| 1 | 容器化（Dockerfile） | ❌ 未开始 | P0 | 见下方 §1.7 基础设施明细表 |
| 2 | docker-compose 编排 | ❌ 未开始 | P0 | 见下方 §1.7 基础设施明细表 |
| 3 | CI/CD 流水线 | ✅ 已完成 | P0 | 见下方 §1.7 基础设施明细表 |
| 4 | 环境变量管理 | ⚠️ 部分完成 | P1 | 见下方 §1.7 基础设施明细表 |
| 5 | 日志聚合 | ❌ 未开始 | P1 | 见下方 §1.7 基础设施明细表 |
| 6 | 数据备份 | ⚠️ 部分完成 | P1 | 见下方 §1.7 基础设施明细表 |
| 7 | 进程守护 | ⚠️ 部分完成 | P1 | 见下方 §1.7 基础设施明细表 |

### 1.2 监控告警

| # | 项目 | 状态 | 优先级 | 备注 |
|:-:|:-----|:----:|:-------|:-----|
| 1 | HTTP 健康端点 | ✅ 已完成 | P0 | `GET /health` |
| 2 | 熔断告警 | ⚠️ 部分完成 | P0 | 见下方 §1.6 监控明细表 |
| 3 | 指标采集 | ✅ 已完成 | P1 | 见下方 §1.6 监控明细表 |
| 4 | 可视化仪表盘 | ⚠️ 部分完成 | P1 | 见下方 §1.6 监控明细表 |
| 5 | 异常告警通道 | ❌ 未开始 | P1 | 邮件/钉钉/企业微信 |

### 1.3 稳定性

| # | 项目 | 状态 | 优先级 | 备注 |
|:-:|:-----|:----:|:-------|:-----|
| 1 | 熔断器 | ✅ 已完成 | P0 | 三阈值自动停止 |
| 2 | 原子持久化 | ✅ 已完成 | P0 | 临时文件 + os.replace |
| 3 | 备份轮转 | ✅ 已完成 | P1 | 保留最近 3 个备份 |
| 4 | 静默降级 | ✅ 已完成 | P1 | 可选依赖惰性导入 |
| 5 | 进程看门狗 | ✅ 已完成 | P1 | 30s 内 3 次重启策略 |
| 6 | 安全沙箱 | ✅ 已完成 | P0 | AST 预验证 + 受限 __builtins__ |
| 7 | 压力测试 | ⚠️ 部分完成 | P1 | 见下方 §1.5 测试计划明细表 |

### 1.4 测试保障

| # | 项目 | 状态 | 优先级 | 备注 |
|:-:|:-----|:----:|:-------|:-----|
| 1 | 单元测试 | ✅ 已完成 | P0 | 2387 测试用例通过（90 个测试文件） |
| 2 | 集成测试 | ✅ 已完成 | P1 | strategies 层已覆盖（含策略进化 55 用例） |
| 3 | E2E 测试 | ⚠️ 部分完成 | P1 | 见下方 §1.5 测试计划明细表 |
| 4 | 覆盖率 | ✅ 已完成 | P0 | 99% 总体覆盖 |
| 5 | 回归测试 | ❌ 未开始 | P1 | 见下方 §1.5 测试计划明细表 |

### 1.5 测试计划明细表

| 测试类型 | 状态 | 已实现 | 未完成项 |
|:---------|:----:|:-------|:---------|
| **压力测试** | ⚠️ 部分完成 | **测试文件**：`tests/factor_engine/test_stress_test.py`（32 用例，100% 覆盖）<br>**内置场景**（5 个）：<br>1. 原油暴跌（-300%，SC/CL，vol×3）<br>2. 双十一闪崩（-5%，RB，vol×5）<br>3. 股灾（-45%，IF/IH/IC，vol×2）<br>4. 疫情冲击（-30%，全品种*，vol×2）<br>5. 供给侧改革（+50%，RB，vol×1）<br>**通过阈值**：最大回撤 ≤ 40%<br>**验证覆盖**：方向正确通过、方向错误失败、空信号/无匹配优雅处理、通配符全品种匹配、恢复天数非负、回撤 cap 0~1 | 1. 生产环境定时运行配置<br>2. 性能指标采集（执行耗时、内存）<br>3. 压力报告自动生成（HTML/PDF）<br>4. 结果阈值可配置化 |
| **E2E 测试** | ⚠️ 部分完成 | **测试文件**：`tests/test_e2e.py`（10 场景）+ `tests/factor_engine/test_factor_lifecycle.py`（14 场景），共 24 个闭环用例<br>**10 个核心场景**：<br>1. 完整因子演化（SeedPool → MacroEvolver → EvalChain → Verifier → Elite 入库）<br>2. Meta-Loop 知识补给（L1 运行 + 种子注入）<br>3. 组合构建（Elite 加载 → 正交化 → 衰减 → 信号合成）<br>4. 走航验证（WalkForward 多窗口一致性）<br>5. 因子跟踪（Tracker 初始化 → 更新 → 淘汰）<br>6. 市场制度检测（OHLCV → Regime 分类）<br>7. 交易成本调整（净夏普 ≤ 毛夏普）<br>8. 压力测试（已知场景组合不崩溃）<br>9. Data-Core 降级（数据源不可用时合成数据兜底）<br>10. Scheduler 集成（任务注册 → 调度 → 执行）<br>**测试隔离**：合成数据 + MockLLMClient + tmp_path 隔离 | 1. 真实数据源集成测试（MCP/期货行情）<br>2. 多市场覆盖（当前仅期货，缺股票/ETF）<br>3. CI/CD 流水线自动触发<br>4. 失败场景自动截图/日志归档 |
| **回归测试** | ❌ 未开始 | — | **计划方案**：<br>1. **频率**：每日 02:00 自动运行<br>2. **测试集**：核心路径（L1→L2→L3 全链路）+ 上周新增用例 + 全量 2387+ 现有用例<br>3. **告警阈值**：通过率 < 95% 触发企业微信/钉钉通知<br>4. **失败归档**：自动保存至 `reports/regression/{date}/`<br>5. **工具链**：`pytest --cov=fts --cov-fail-under=99 -x --timeout=120`<br>6. **前置依赖**：Phase 1 CI/CD 流水线完成后接入 |

### 1.6 监控明细表

| 监控项目 | 状态 | 已实现 | 未完成项 |
|:---------|:----:|:-------|:---------|
| **熔断告警** | ⚠️ 部分完成 | **Prometheus 告警规则**：`config/prometheus_alerts.yml`<br>- `FTSDataSourceCircuitOpen`：`fts_circuit_open == 1` 持续 1m → critical<br>- `FTSDataSourceSuccessRateLow`：成功率 < 80% 持续 5m → warning<br>- `FTSDataSourceDown`：`up == 0` 持续 2m → critical<br>- `FTSSchedulerNotRunning` / `FTSSchedulerTaskFailed`<br>- `FTSEliteFactorCountLow` / `FTSLoopStuck`<br>**AlertManager 配置**：`config/alertmanager.yml`，含路由分组（critical/warning）、抑制规则、webhook 通知<br>**K8s 部署**：`deploy/k8s/` 含 Prometheus + AlertManager 的 Deployment/Service/ConfigMap/Ingress 共 9 个 manifest | 1. `fts_circuit_open` 指标硬编码为 0，未绑定真实熔断器状态<br>2. AlertManager webhook URL 为占位符 `localhost:5001`，未配置实际通知通道<br>3. 无企业微信/钉钉/Slack 通知适配器 |
| **指标采集** | ✅ 已完成 | **Prometheus 指标端点**：`GET /metrics`（`fts/monitor/http_server.py`）<br>**基础指标**：`fts_up`、`fts_started_at`、`fts_tokens_consumed`、`fts_elite_factor_count`、`fts_combo_sharpe`<br>**循环状态**：`fts_loop_status_L1/L2/L3`（1=正常）<br>**因子生命周期**：`fts_factor_decay_active_count`、`fts_decaying_count`、`fts_critical_count`、`fts_deprecated_count`（`prometheus_metrics.py`）<br>**Regime/权重**：`fts_regime_current`、`fts_weight_rebalance_total`、`fts_regime_confidence`、`fts_regime_entropy_norm`、`fts_regime_exposure_scale`、`fts_regime_blend_hhi`、`fts_regime_name`（28 计划，2026-08-11：`record_regime_metrics`）<br>**Live 因子**：`fts_live_factor_ic`、`fts_live_factor_sharpe`、`fts_live_factor_deviation_alerts_total`<br>**风控**：`fts_risk_check_total`、`fts_risk_check_blocked_total`<br>**反馈闭环**：`fts_feedback_triggers_total`、`fts_feedback_events_pending`、`fts_feedback_processing_total`、`fts_feedback_attribution_accuracy`、`fts_feedback_recommendations_accepted`、`fts_evolution_new_factors`、`fts_evolution_effective_rate`<br>**数据源指标端点**：`GET /metrics/data-sources`（`fts_circuit_open`、`fts_data_source_success_rate`）<br>**Prometheus 配置**：`config/prometheus.yml`（4 个 job：fts-metrics / fts-data-sources / fts-health / prometheus，scrape 间隔 10~30s）<br>**K8s 部署**：`deploy/k8s/` Prometheus ConfigMap/Deployment/Service | — |
| **可视化仪表盘** | ⚠️ 部分完成 | **内嵌 Web 仪表盘**：`fts/monitor/http_server.py`（`DASHBOARD_HTML`）<br>- 4 个指标卡片（系统健康/FTS 版本/Token 消耗/Elite 因子数）<br>- 3 个循环状态卡片（L1/L2/L3 状态/运行ID/Token/错误）<br>- Elite 因子列表表格（因子ID/名称/代数/IC/夏普/来源）<br>- 10 秒自动刷新，运行状态/熔断/错误颜色区分<br>**启动命令**：`fts ui --port 9100` | 1. 无 Grafana 仪表盘 JSON 模板<br>2. 无历史趋势图表（仅实时快照）<br>3. 无告警状态面板<br>4. 无 Regime/权重变化趋势可视化 |

### 1.7 基础设施明细表

| 基础设施项目 | 状态 | 已实现 | 未完成项 |
|:-------------|:----:|:-------|:---------|
| **容器化（Dockerfile）** | ❌ 未开始 | — | 需编写 Dockerfile（基于 python:3.12-slim），含依赖安装、配置文件挂载、非 root 用户运行 |
| **docker-compose 编排** | ❌ 未开始 | — | 需编写 docker-compose.yml，编排 FTS + Prometheus + AlertManager + Grafana 多服务 |
| **CI/CD 流水线** | ✅ 已完成 | **GitHub Actions**：`.github/workflows/ci.yml`<br>- 触发：push 到 main/develop + PR 到 main<br>- 矩阵：Python 3.10/3.11/3.12<br>- 步骤：checkout → setup-python → pip install .[dev,evolution] → pytest --cov → codecov 上传 | 1. 无 Docker 镜像构建与推送步骤<br>2. 无 lint 检查（ruff/mypy）<br>3. 无部署（deploy）阶段 |
| **环境变量管理** | ⚠️ 部分完成 | **示例文件**：`.env.example`<br>- 定义了 LLM 后端（OpenAI/Anthropic）所需 API Key、Base URL、Model<br>- 定义了 FTS 可选配置（MEMORY_DIR、ELITE_DIR、DEFAULT_MARKET、MAX_WORKERS、LOG_LEVEL、CONFIG_FILE）<br>- `.env` 已在 `.gitignore` 中排除<br>**自动加载**：`fts/__init__.py` 通过 `dotenv.load_dotenv()` 自动加载项目根目录的 `.env` 文件<br>**代码读取**：`fts/config/settings.py` 通过 `os.getenv("FTS_*")` 读取，`fts/llm.py` 通过 `os.getenv("OPENAI_*")` 读取<br>**修复脚本**：`scripts/fix_env_management.py`（支持 `--check`/`--fix` 模式） | 见下方未完成项明细表 |
| **日志聚合** | ❌ 未开始 | — | **计划方案**：<br>1. 结构化日志（JSON 格式，含 trace_id/模块/级别/时间戳）<br>2. 按天轮转（`logging.handlers.TimedRotatingFileHandler`）<br>3. 日志目录：`logs/`，保留 30 天<br>4. 工具：`python-json-logger` 或自定义 JSON Formatter |
| **数据备份** | ⚠️ 部分完成 | **备份脚本**：`scripts/backup_and_migrate_prod.py`<br>- 迁移前自动备份 DuckDB（`data/backup/` 目录）<br>- 备份完整性校验（大小比对）<br>- 迁移后验证（表结构/数据量/索引）<br>- 备份设为只读保护<br>**CLI 命令**：`fts catalog backup`<br>- 备份 DuckDB `factor_catalog` + JSON elite 因子<br>- 按时间戳命名，DuckDB/JSON 双路径备份<br>- 支持 `--json` 格式输出 | 1. 无定时自动备份调度<br>2. 无备份轮转策略（保留最近 N 个）<br>3. 无远程备份（S3/OSS） |
| **进程守护** | ⚠️ 部分完成 | **ProcessWatchdog**：`fts/scheduler/watchdog.py`<br>- 子进程崩溃自动重启<br>- 重启策略：3 次/30 秒 → 熔断 5 分钟<br>- 支持 `stop()` 信号停止<br>**CLI 命令**：`fts scheduler watch` | 1. 无 systemd/Linux 服务配置<br>2. 无 Windows 服务配置<br>3. 无开机自启集成 |

#### 环境变量管理 — 未完成项明细表

| # | 未完成项 | 优先级 | 状态 | 说明 |
|:-:|:---------|:------:|:----:|:-----|
| 1 | `python-dotenv` 未在依赖中声明 | P1 | ✅ 已修复（v2.22.0+） | 已添加 `python-dotenv>=1.0` 到 `pyproject.toml` 的 `dependencies` |
| 2 | `.env.example` 部分变量被注释 | P1 | ✅ 已修复（v2.22.0+） | 已取消注释 FTS 可选配置变量（MEMORY_DIR、ELITE_DIR 等）并提供默认值 |
| 3 | `.env.example` 遗漏 `FTS_LLM_BACKEND` | P1 | ✅ 已修复（v2.22.0+） | 已添加 FTS_LLM_BACKEND（后选择器，openai/anthropic/mock） |
| 4 | `.env.example` 遗漏 `ANTHROPIC_MODEL` | P1 | ✅ 已修复（v2.22.0+） | 已添加 ANTHROPIC_MODEL（默认 claude-sonnet-4-20250514） |
| 5 | 无密钥管理方案（Vault/环境变量加密） | P2 | ❌ 未开始 | 当前仅依赖 `.env` 文件 + `.gitignore` 防护，存在密钥泄露风险；见下方 §1.8 方案对比 |

### 1.8 密钥管理方案对比

| 维度 | 当前方案（.env + python-dotenv） | HashiCorp Vault | AWS Secrets Manager |
|:----|:-------------------------------|:----------------|:-------------------|
| **安全等级** | ⚠️ 低 — 明文密钥文件，依赖文件系统权限 | ✅ 高 — 动态密钥、自动轮转、访问审计 | ✅ 高 — IAM 权限控制、自动轮转、加密存储 |
| **部署复杂度** | ✅ 低 — 零配置，复制 `.env` 即可运行 | ⚠️ 中 — 需部署 Vault 集群（或使用 HCP Cloud），配置认证策略 | ⚠️ 中 — 需创建 Secret 并配置 IAM 角色/用户 |
| **运行时开销** | ✅ 无 — 启动时一次加载到环境变量 | ⚠️ 中 — 每次访问需 API 调用，需考虑缓存策略 | ⚠️ 中 — 每次访问需 API 调用，有缓存层但有成本 |
| **密钥轮转** | ❌ 手动 — 修改 `.env` 文件后重启进程 | ✅ 自动 — 支持 TTL 过期自动轮转，应用侧可监听变更 | ✅ 自动 — 支持按天/周/月自动轮转，有版本管理 |
| **访问审计** | ❌ 无 — 无法追踪谁在何时读取了密钥 | ✅ 全 — 所有访问均有日志，支持审计集成 | ✅ 全 — CloudTrail 审计，支持 IAM Access Analyzer |
| **多云/本地兼容** | ✅ 全 — 纯文件方案，任何环境一致 | ✅ 全 — 支持多云/本地/混合部署 | ⚠️ 部分 — AWS 原生，其他云需额外集成 |
| **开发体验** | ✅ 优秀 — 本地开发零门槛，`.env.example` 作为模板 | ⚠️ 一般 — 本地开发需额外配置（dev Vault 或 mock） | ⚠️ 一般 — 本地开发需配置 AWS 凭证或使用 LocalStack |
| **集成成本** | ✅ 低 — 一行 `load_dotenv()` 即可 | ⚠️ 中 — 需引入 `hvac` 库，改写成动态加载模式 | ⚠️ 中 — 需引入 `boto3`，改写成 AWS SDK 调用模式 |
| **适用场景** | 开发环境、单人项目、内网部署 | 生产环境、多人团队、合规要求高的场景 | 生产环境、AWS 原生生态、需与 IAM 深度集成的场景 |
| **推荐度** | ⭐⭐⭐⭐⭐ （当前阶段） | ⭐⭐⭐ （P2 阶段评估） | ⭐⭐⭐ （P2 阶段评估） |

**结论**：当前阶段（开发期 vs 生产期切换前）**推荐继续使用 `.env` 方案**，核心原因：
1. **零部署成本** — 无需额外基础设施即可运行全流程
2. **开发友好** — 本地开发/调试/CI 无需处理密钥服务认证
3. **FTS 实际风险可控** — 当前仅涉及 LLM API Key，不涉及交易密钥或用户数据

**生产切换建议**：
- 当 FTS 接入交易执行模块（需管理交易密钥）时 → 升级到 Vault 或 AWS Secrets Manager
- 当部署环境超过 3 个（开发/测试/预发/生产）时 → 评估密钥管理方案
- 当团队超过 2 人时 → 增加密钥访问审计

---

## 2. 实施路线图

### Phase 1: 基础部署（P0 项）

**目标**: 能在生产环境启动并稳定运行

- [ ] 编写 Dockerfile（基于 python:3.12-slim）
- [ ] 编写 docker-compose.yml（FTS + 配置挂载）
- [x] 配置 CI/CD 流水线（push 后自动构建镜像）— `.github/workflows/ci.yml` 已存在，待扩展 Docker 构建
- [-] 实现熔断告警通知 — Prometheus 告警规则 + AlertManager 配置已就绪，需绑定真实熔断器状态并配置通知通道
- [ ] 配置 systemd 服务（Windows 服务或 Linux systemd）

### Phase 2: 可观测性（P1 项）

**目标**: 能监控系统运行状态并快速定位问题

- [ ] 结构化日志（JSON 格式，按天轮转）
- [x] Prometheus 指标暴露（`/metrics` 端点）— 已完成，含 20+ 指标 + 数据源专用端点
- [ ] Grafana 仪表盘模板
- [-] 告警通道配置（企业微信/钉钉机器人）— 告警规则已定义，通道未配置
- [-] 环境变量管理（`python-dotenv`）— `.env` 自动加载 + 依赖声明 + 模板完整性均已就绪，密钥管理纳入 Phase 4a

### Phase 3: 持续集成（P1 项）

**目标**: 自动化测试和部署流程

- [x] GitHub Actions 工作流（lint + test + build）— 测试已自动化，待加 lint 和 Docker 构建
- [ ] 自动回归测试（每日定时运行）
- [ ] 镜像标签管理（`latest` + `v*.*.*`）
- [-] 数据库/数据备份策略 — `fts catalog backup` CLI 已实现，待调度自动化

### Phase 4: 生产加固（P2 项）

**目标**: 提升系统生产级健壮性

- [ ] 容器资源限制（CPU/内存）
- [ ] 健康检查 liveness/readiness probe
- [ ] 配置热重载（已实现基础版，需增强）
- [ ] 多实例部署支持（分布式锁）
- [-] 安全审计（依赖扫描、密钥管理）— 密钥管理见下方 Phase 4a

### Phase 4a: 密钥管理升级（P2 项，条件触发）

**触发条件**（满足任一即启动）：
- FTS 接入交易执行模块（需管理交易密钥）
- 部署环境超过 3 个（开发/测试/预发/生产）
- 团队超过 2 人，需密钥访问审计

**实施步骤**：

| 步骤 | 内容 | 预估工作量 | 交付物 |
|:----|:-----|:----------|:-------|
| 1 | 评估方案选型（Vault vs AWS Secrets Manager vs 环境变量加密） | 1 天 | 选型决策文档 |
| 2 | 定义密钥分级策略（P0: API Key / P1: 配置密钥 / P2: 非敏感配置） | 0.5 天 | 密钥分级清单 |
| 3 | 实现密钥抽象层（`SecretProvider` 接口，支持 `.env`/Vault/AWS 三种后端） | 2 天 | `fts/config/secrets.py` + 单元测试 |
| 4 | 集成 LLM 密钥读取（`llm.py` 改用 `SecretProvider`） | 0.5 天 | 代码修改 + 集成测试 |
| 5 | 部署密钥管理服务（Vault 单节点 docker-compose 或 AWS Secret 创建） | 1-2 天 | 部署文档 + docker-compose 更新 |
| 6 | 实现密钥轮转自动化（Vault TTL 或 AWS 自动轮转） | 1 天 | 轮转脚本 + 测试 |
| 7 | 添加密钥访问审计日志 | 0.5 天 | 审计日志 + 告警规则 |
| 8 | 生产切换 runbook（灰度：先读密钥服务，降级回 `.env`） | 1 天 | Runbook 文档 |

**切换策略**：灰度执行，先读密钥服务，降级回 `.env`，确保 0 停机。

---

### Phase 5: 分布式挖掘集群拓扑（C4，2026-08-11，部署后置）

> **策略**：代码与单机 LocalCluster 验证已落地（见 plans/23 C4 实施设计），本小节为**真实多机部署拓扑方案**，待硬件/基建条件成熟后执行。

**架构拓扑（scheduler + N workers + 单写者 DuckDB）**

```text
┌────────────────────────── 调度层 ──────────────────────────┐
│  Dask Scheduler（tcp://scheduler:8786）                    │
│  - 任务分派/worker 心跳/失败重试                           │
│  - 单点（可配 HA：scheduler 进程守护）                     │
└────────────────────────────────────────────────────────────┘
        │ 调度
┌───────▼────────┐  ┌───────▼────────┐  ┌───────▼────────┐
│ Worker 1       │  │ Worker 2       │  │ Worker N       │
│ 批量粗筛/评估   │  │ 批量粗筛/评估   │  │ 批量粗筛/评估   │
│ 读 replica-1   │  │ 读 replica-2   │  │ 读 replica-N   │
└────────────────┘  └────────────────┘  └────────────────┘
        │ 只读                       ┌──────────────┐
        └───────────────────────────▶│ DuckDB 副本   │
                                     │ replica-*.db │
                                     └──────────────┘
                          ┌──────────────┐   写唯一
                          │ DuckDB 主写节点│◀── 演化晋升/审查落库
                          │ (单写者, 锁)  │    仅在写节点执行
                          └──────────────┘
```

**接线要点**：

| 项 | 方案 |
|:---|:-----|
| 调度器/Worker | `dask scheduler` + `dask worker tcp://scheduler:8786`（N 节点）；单机验证用 `Client(n_workers)` 本地集群等价语义 |
| 接入 | `BatchMiningConfig.executor_backend="dask"` + `FTS_EXECUTOR_BACKEND=dask`；集群模式 `DaskBackend(address="tcp://scheduler:8786")` |
| **DuckDB 数据访问** | **单写者多读副本**：写节点唯一（演化晋升/审查落库 `factor_reviews`/`factor_catalog`），Worker 只读挂载 `data/` 只读副本（或同一 NFS 只读挂载）——避免多进程写同一 DuckDB 文件锁冲突（GAP-056 架构延续；memory 教训：并发进程同文件不同配置连接报 "Can't open a connection..."） |
| 任务失败隔离 | dask 默认单任务异常仅该 future 抛，不中断整批；worker 掉线任务自动重派给存活 worker |
| 监控告警 | scheduler 心跳 + worker 存活探针；批次失败率超阈值告警；`BatchMiner.filter_batch` 结果按输入顺序保真（一致性 < 1e-9 验收） |
| 灰度路径 | 单机先 `process` → 单机 `dask`（LocalCluster）→ 双节点真实集群，逐级验证吞吐与一致性 |

**验收数据**：`scripts/benchmark_executor.py` 输出 dask vs process vs thread 吞吐对比表（纳入 Stage 3 验收报告）；真实集群批量评估吞吐 ≥ 单机 ProcessBackend 且结果一致性 < 1e-9、单 worker 故障不中断整批。

---

## 3. 依赖的 Harness 文档

| 文档 | 关联内容 |
|:-----|:---------|
| `../01-architecture.md` | 部署架构图 |
| `../04-resilience.md` | 熔断、降级、备份策略 |
| `../05-observability.md` | 监控指标、日志格式 |
| `../06-testing.md` | 测试策略、覆盖率门禁 |
| `../07-operations.md` | 版本管理、升级流程 |
| `../08-gap-analysis.md` | 已关闭差距项记录 |
| `../09-advancement-plan.md` | 晋级里程碑 |
| `production_plan.md`（本文档） | 生产就绪计划（自引用） |