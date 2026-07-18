# FTS 开发生命周期

> 版本: v0.1.0
> 最后更新: 2026-07-18

---

## 1. 阶段划分

FTS 从 FDT 剥离共经历 7 个 Phase，目前全部完成：

| 阶段 | 内容 | 状态 | 产出物 |
|:-----|:-----|:-----|:-------|
| **Phase 1** | FTS 核心契约 + 因子引擎骨架 + Data-Core 集成验证 | ✅ 完成 | 因子引擎框架 |
| **Phase 2** | 因子引擎完整实现（三层循环 + 种子池数据类型感知） | ✅ 完成 | 可用的因子进化引擎 |
| **Phase 3** | 数据处理管线（pipeline 抽象基类 + factor_combiner） | ✅ 完成 | 衍生数据管线骨架 |
| **Phase 4** | 多因子策略 + CLI + 调度 | ✅ 完成 | 完整可运行系统 |
| **Phase 5** | 测试覆盖 + pylint 清理 + FDT 侧清理 | ✅ 完成 | 交付就绪 |
| **Phase 6** | Memory 重定向：FTS 独立 memory 路径，FDT_PATH 环境变量解耦 | ✅ 完成 | 独立持久化 |
| **Phase 7** | FTS 层级提升为独立项目，claude.md / fts-coding.mdc 割离 | ✅ 完成 | 完整项目独立 |

---

## 2. 文件命名规范

- **Python 文件**: `snake_case.py`
- **测试文件**: `test_<module_name>.py`
- **配置文件**: `settings.yaml`
- **Markdown 文档**: `NN-topic.md`（NN 为两位数字序号）
- **程序配置文件**: `Program.md`（首字母大写，位于项目根目录）

示例：
```
fts/
├── factor_engine/
│   ├── evolution_loop.py
│   ├── meta_loop.py
│   ├── portfolio_loop.py
│   └── evaluation_chain.py
tests/
├── factor_engine/
│   ├── test_evolution_loop.py
│   ├── test_meta_loop.py
│   └── test_portfolio_loop.py
```

---

## 3. 版本号命名规则

遵循语义化版本号 `MAJOR.MINOR.PATCH`：

| 级别 | 变更类型 | 示例 |
|:-----|:---------|:-----|
| **MAJOR** | 重大架构变更（如 LangGraph 迁移） | v1.0.0 → v2.0.0 |
| **MINOR** | 功能新增或阶段完成 | v0.1.0 → v0.2.0 |
| **PATCH** | bug 修复或文档更新 | v0.1.0 → v0.1.1 |

当前版本：**v0.1.0**

### 版本号同步规则

FTS 包含两个版本号，修改时必须同步：

| 位置 | 用途 | 当前值 |
|:-----|:-----|:-------|
| `fts/__init__.py` | FTS 项目版本 | `"0.1.0"` |
| `pyproject.toml` | 包版本 | `"0.1.0"` |

`fts.factor_engine.__init__.py` 中的 `EVOLUTION_VERSION` 为因子引擎内部版本号（v8.10.0），继承自 FDT，与 FTS 项目版本独立。

---

## 4. session_id 与 trace_id 生成规则

### trace_id

```
trace_id = "{timestamp}-{uuid4_hex8}"
```

- `{timestamp}`: `int(time.time())`，Unix 时间戳
- `{uuid4_hex8}`: `uuid4().hex[:8]`，UUID4 的前 8 位十六进制字符

示例：`1658136000-a1b2c3d4`

### run_id

```
run_id = "run_{timestamp}_{uuid4_hex6}"
```

示例：`run_1658136000_a1b2c3`

### session_id

session_id 用于区分 CLI 每次执行：

- CLI 启动时自动生成
- 格式与 trace_id 相同，但作用域为整个 CLI 会话
- 传递到各子命令作为日志聚合标识

### 全链路传播

所有模块必须遵循以下规则：

1. CLI 入口生成 `trace_id` 和 `session_id`
2. 传递给 `factor_engine` 各模块（通过函数参数）
3. 管线各 stage 必须传播 `trace_id`（通过 `DataPayload.trace_id`）
4. 监控和日志记录必须包含 `trace_id`
5. scheduler 任务执行时生成独立的带前缀的 trace_id

---

## 5. 角色定义

### 当前角色

| 角色 | 职责 | 备注 |
|:-----|:-----|:-----|
| **AI Agent** | FTS 全链路开发：编码、测试、文档、部署 | 当前版本全部由 Agent 完成 |

### 未来角色（预留）

| 角色 | 职责 | 预计引入时间 |
|:-----|:-----|:-------------|
| **人类审核员** | Verifier 锁定审核、Program.md 批准、P0 差距审查 | v0.2.0+ |
| **运维工程师** | 生产环境部署、调度配置、故障恢复 | v0.3.0+ |

### 角色边界

- AI Agent 不得执行人类审核员的职责（如解锁 Verifier）
- AI Agent 不得修改已锁定的 Program.md
- AI Agent 不得将 trace_id 省略或绕过

---

## 6. 状态机

FTS 项目整体状态：

```
[初始] → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → [v0.1.0]
                                                                                      │
                                                                                      ▼
                                                                               [v0.2.0 目标]
```

各循环的状态：

```
[stopped] → [running] → [paused/completed] → [circuit_broken] → [recovered/stopped]
```
