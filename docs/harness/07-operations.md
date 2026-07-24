# FTS 运维与版本管理

> 版本: v1.1.0
> 最后更新: 2026-07-24

---

## 1. 版本历史

| 版本 | 日期 | 说明 |
|:-----|:-----|:-----|
| **v0.1.0** | 2026-07-18 | 从 FDT 剥离，完成 Phase 1-7，220 测试全绿 |
| **v1.1.0** | 2026-07-24 | MCP 数据源迁移：Data-Core → akshare(腾讯/东方财富)；移除 6 个期货专用种子因子；CLI 移除 `--universe futures`；默认市场改为 stock；1231 测试全绿 |
| **v1.0.0** | 2026-07-19 | 本地原生部署：进程守护/热重载/HTTP 监控/Windows 服务/CI/CD/E2E 测试/部署文档、1231 测试全绿 |
| **v0.4.0** | 2026-07-19 | EliteFactorTracker、AutoRetireManager、WalkForwardOptimizer、EvaluationChain 走航集成、1104 测试全绿 |
| **v0.3.0** | 2026-07-19 | Data-Core 集成适配、FDT 残留清除、覆盖率提升至 96%、969 测试全绿、原子持久化 |
| **v0.2.0** | 2026-07-18 | CLI 引擎真实调用、Config+memory 目录、Scheduler 引擎、覆盖率提升至 89%、778 测试全绿 |

### 版本号位置

FTS 项目版本号定义在两个位置，变更时必须同步更新：

| 文件 | 字段 |
|:-----|:-----|
| `fts/__init__.py` | `__version__ = "1.0.0"` |
| `pyproject.toml` | `version = "1.0.0"` |

异常引擎内部版本号位于 `fts/factor_engine/__init__.py` 的 `EVOLUTION_VERSION`（当前 v1.1.0），与 FTS 项目版本同步。

---

## 2. 安装方式

### 基础安装

```bash
# 从项目根目录安装
pip install .

# 带可选依赖
pip install .[evolution]    # 带 optuna 演化支持
pip install .[llm]          # 带 LLM 支持
pip install .[mcp]         # 带 MCP 数据支持（akshare 腾讯/东方财富）
pip install .[dev]          # 带开发工具（pytest）
pip install .[evolution,llm,mcp,dev]  # 全部
```

### 核心依赖

| 依赖 | 版本要求 | 用途 |
|:-----|:---------|:-----|
| numpy | >=1.24 | 数值计算 |
| pandas | >=2.0 | 数据处理 |
| pyyaml | >=6.0 | YAML 配置读取 |

### 可选依赖

| extra | 依赖 | 用途 |
|:------|:-----|:-----|
| evolution | optuna>=3.0 | 贝叶斯调参 |
| llm | openai>=1.0, anthropic>=0.20 | LLM 因子演化 |
| data | datacore | Data-Core 数据接入 |
| dev | pytest>=7.4, pytest-cov>=4.1 | 测试工具 |

---

## 3. CLI 入口

### 统一入口

```bash
python -m fts.cli <command> [options]
```

或通过注册的脚本命令：

```bash
fts <command> [options]
```

### 子命令列表

| 子命令 | 选项 | 说明 |
|:-------|:-----|:-----|
| `version` | — | 打印版本号 |
| `monitor` | `--json` | 检查所有循环健康状态 |
| `evolution run` | `--max-generations N` | 启动 L2 因子演化主循环 |
| `meta-loop run` | — | 启动 L1 Meta-Loop |
| `portfolio run` | — | 启动 L3 组合构建 |
| `factor list` | `--elite-dir PATH` | 列出 elite 因子 |
| `factor show <factor_id>` | `--elite-dir PATH` | 查看单个因子详情 |

### 使用示例

```bash
# 查看版本
python -m fts.cli version

# 健康检查
python -m fts.cli monitor
python -m fts.cli monitor --json    # JSON 格式输出

# 因子演化
python -m fts.cli evolution run --max-generations 20

# 因子管理
python -m fts.cli factor list
python -m fts.cli factor show factor_abc123
```

---

## 4. 状态检查

### 健康监控命令

```bash
python -m fts.cli monitor
```

输出示例：

```
=== FTS System Status ===
Overall healthy : YES
Checked at      : 2026-07-18T10:30:00
FTS version     : 1.1.0
Circuit broken  : NO
Stale (>24h)    : NO
Tokens today    : 0

=== Loop Status ===
[OK]   L1  | status=running          | run_id=run_1658136000_a1b2c3     | age=0.0h
[OK]   L2  | status=completed        | run_id=run_1658136000_d4e5f6     | age=0.0h
[OK]   L3  | status=completed        | run_id=run_1658136000_g7h8i9     | age=0.0h
```

### 监控指标

| 指标 | 说明 | 告警阈值 |
|:-----|:-----|:---------|
| healthy | 整体健康状态 | False = 告警 |
| circuit_broken | 熔断状态 | True = 紧急 |
| stale | 超过 24h 未更新 | True = 告警 |
| age_hours | 距上次运行小时数 | >24h = stale |
| tokens_consumed | Token 消耗 | 按 budget 阈值 |
| status | 运行/暂停/完成/熔断 | circuit_broken = 紧急 |

### 状态文件位置

各循环的状态持久化到 `memory/` 目录：

| 循环 | 状态文件 |
|:-----|:---------|
| L1 Meta-Loop | `memory/meta_loop/state.json` |
| L2 Evolution Loop | `memory/evolution/state.json` |
| L3 Portfolio Loop | `memory/portfolio/state.json` |

---

## 5. 版本升级流程

### 常规升级步骤

1. **更新版本号**
   - 修改 `fts/__init__.py` 中的 `__version__`
   - 修改 `pyproject.toml` 中的 `version`

2. **更新文档**
   - 在本文件（`07-operations.md`）版本历史中添加新版本记录
   - 如有架构变更，更新 `01-architecture.md`
   - 如有测试变更，更新 `06-testing.md`
   - 如有差距关闭，更新 `08-gap-analysis.md`

3. **同步 README.md**
   - 更新版本徽章
   - 更新测试数和覆盖率
   - 同步 API 使用示例、模块列表、文档链接
   - 确认 13 项 commit 检查清单第 12 项（README 同步）通过

4. **运行测试**
   ```bash
   python -m pytest tests/ --cov=fts --cov-report=term-missing
   ```
   确认全部通过

5. **提交并打标签**
   ```bash
   git tag v0.2.0
   ```

### 版本号变更规则

| 变更类型 | 示例 | 条件 |
|:---------|:-----|:-----|
| MAJOR | v1.0.0 | 重大架构变更 |
| MINOR | v0.2.0 | 功能新增 / 阶段完成 |
| PATCH | v0.1.1 | bug 修复 / 文档更新 |
