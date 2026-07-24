# FTS 测试策略

> 版本: v1.1.0
> 最后更新: 2026-07-24

---

## 1. 测试金字塔

```
         ┌──────────┐
         │  E2E 测试 │    ← 全流程端到端验证（当前 0 个）
         │  (手动)   │
        ┌┴──────────┴┐
        │ 集成测试    │    ← 策略层集成验证（1 个测试文件）
        │            │
       ┌┴────────────┴┐
       │ 单元测试      │    ← 各模块独立测试（24 个测试文件）
       │              │
       └──────────────┘
```

| 层级 | 测试文件数 | 用例数 | 说明 |
|:-----|:----------|:-------|:-----|
| 单元测试 | 27 | ~1038 | 各模块独立测试 |
| 集成测试 | 2 | ~143 | strategies 策略层 |
| E2E | 1 | 10 | test_e2e.py |
| 合计 | 35 | 1181 | 全部通过 |

---

## 2. 测试目录结构

```
tests/
├── __init__.py
├── conftest.py                      # 全局 fixture
│
├── core/                            # 3 个测试文件
│   ├── __init__.py
│   ├── test_atomic.py               # 原子操作测试
│   ├── test_contracts.py            # core contracts 测试
│   └── test_enums.py                # enums 测试
│
├── factor_engine/                   # 13 个测试文件
│   ├── __init__.py
│   ├── conftest.py                  # factor_engine fixture
│   ├── test_contracts.py            # 契约定义测试
│   ├── test_evaluation_chain.py     # 三级评估链测试
│   ├── test_evolution_loop.py       # L2 主循环测试
│   ├── test_experience_chain.py     # 经验链测试
│   ├── test_factor_program.py       # 因子程序（安全沙箱）测试
│   ├── test_macro_evolution.py      # 宏观演化测试
│   ├── test_meta_loop.py            # L1 元循环测试
│   ├── test_monitor.py              # factor_engine monitor 测试
│   ├── test_portfolio_loop.py       # L3 组合循环测试
│   ├── test_program.py              # Program.md 测试
│   ├── test_seed_pool.py            # 种子池测试
│   └── test_verifier.py             # Verifier 锁定协议测试
│
├── pipeline/                        # 2 个测试文件
│   ├── __init__.py
│   ├── test_base.py                 # 管线基础测试
│   └── test_factor_combiner.py      # 因子组合器测试
│
├── scheduler/                       # 4 个测试文件
│   ├── __init__.py
│   ├── test_engine.py               # 调度引擎测试
│   └── test_tasks.py                # 调度任务测试
│
├── strategies/                      # 2 个测试文件
│   ├── __init__.py
│   ├── test_base_v2.py              # 策略基类测试
│   └── test_multi_factor.py         # 多因子策略测试
│
├── test_cli.py                      # CLI 入口测试
├── test_config_settings.py          # 配置管理测试
├── test_data.py                     # 数据层测试
├── test_llm.py                      # LLM 客户端测试
└── test_monitor.py                  # 项目级 monitor 测试
```

---

## 3. pytest 配置

定义在 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=fts --cov-report=term-missing -v"
```

执行命令：

```bash
# 运行全部测试并显示覆盖率
python -m pytest tests/ --cov=fts --cov-report=term-missing

# 运行指定模块测试
python -m pytest tests/factor_engine/ -v

# 运行单文件测试
python -m pytest tests/factor_engine/test_verifier.py -v
```

---

## 覆盖统计（v1.1.0）

### 总体统计

| 指标 | 值 |
|:-----|:---|
| Total statements | 3438 |
| Overall coverage | 96% |
| 测试用例数 | 1231 passed, 0 failed |
| 种子因子数 | 9（移除 6 个期货专用因子） |

### 模块覆盖详情

```
Name                                      Stmts   Miss  Cover
──────────────────────────────────────────────────────────────
fts\__init__.py                               1      0   100%
fts\cli.py                                  199     25    87%   67-68, 144, 146-148, 172-174, 198-200, 205-211, 220-224, 342
fts\config\__init__.py                        3      0   100%
fts\config\settings.py                       69      0   100%
fts\core\__init__.py                          0      0   100%
fts\core\atomic.py                           44      4    91%   62-63, 111-112
fts\core\contracts.py                         3      0   100%
fts\core\enums.py                            17      0   100%
fts\data.py                                 131      0   100%
fts\factor_engine\__init__.py                16      0   100%
fts\factor_engine\contracts.py              256      0   100%
fts\factor_engine\evaluation_chain.py       132      5    96%   66, 94, 151, 160, 169
fts\factor_engine\evolution_loop.py         165      2    99%   221, 442
fts\factor_engine\experience_chain.py       121      9    93%   98, 102, 130-131, 138-139, 182, 223-224
fts\factor_engine\factor_program.py         104      4    96%   155, 157, 226-227
fts\factor_engine\macro_evolution.py         63      0   100%
fts\factor_engine\meta_loop.py              442      1    99%   1135
fts\factor_engine\micro_evolution.py         72      6    92%   28-30, 93, 112-113
fts\factor_engine\monitor.py                105     40    62%   137-170, 175-189, 193
fts\factor_engine\portfolio_loop.py         324     27    92%   111, 154-155, 181, 225, 231-232, 270, 276-277, 505, 517-518, 737-759, 763
fts\factor_engine\program.py                101      0   100%
fts\factor_engine\seed_pool.py               50      0   100%
fts\factor_engine\state.py                   95      0   100%
fts\factor_engine\verifier.py                65      2    97%   128, 157
fts\llm.py                                  122     28    77%   45, 103, 108, 110, 118-132, 152, 156, 164-178
fts\monitor.py                               61      0   100%
fts\pipeline\__init__.py                      4      0   100%
fts\pipeline\base.py                         49      0   100%
fts\pipeline\factor_combiner.py              94      0   100%
fts\scheduler\__init__.py                     4      0   100%
fts\scheduler\engine.py                      86      0   100%
fts\scheduler\tasks.py                       49      0   100%
fts\strategies\__init__.py                    4      0   100%
fts\strategies\base_v2.py                   156      0   100%
fts\strategies\multi_factor_strategy.py     230      1    99%   420
fts\strategies\rules\__init__.py              1      0   100%
──────────────────────────────────────────────────────────────
TOTAL                                      3438    129    96%
```

### 高覆盖模块（>=80%）

| 模块 | 覆盖率 | 说明 |
|:-----|:-------|:-----|
| `__init__.py` (fts) | **100%** | 版本号 |
| `config/__init__.py` | **100%** | 配置包初始化 |
| `config/settings.py` | **100%** | 配置管理全覆盖 |
| `core/__init__.py` | **100%** | 核心包初始化 |
| `core/atomic.py` | **91%** | fail-request 原子操作 |
| `core/contracts.py` | **100%** | 核心契约 |
| `core/enums.py` | **100%** | 枚举定义 |
| `data.py` | **100%** | 数据层全覆盖 |
| `factor_engine/__init__.py` | **100%** | 因子引擎包初始化 |
| `contracts.py` (factor_engine) | **100%** | 契约定义全覆盖 |
| `macro_evolution.py` (factor_engine) | **100%** | 宏观演化全覆盖 |
| `seed_pool.py` | **100%** | 种子池全覆盖 |
| `program.py` | **100%** | Program.md 全覆盖 |
| `state.py` | **100%** | 状态管理全覆盖 |
| `pipeline/__init__.py` | **100%** | 管线包初始化 |
| `pipeline/base.py` | **100%** | 管线基础全覆盖 |
| `pipeline/factor_combiner.py` | **100%** | 因子组合器全覆盖 |
| `scheduler/__init__.py` | **100%** | 调度器包初始化 |
| `scheduler/engine.py` | **100%** | 调度引擎全覆盖 |
| `scheduler/tasks.py` | **100%** | 调度任务全覆盖 |
| `strategies/__init__.py` | **100%** | 策略包初始化 |
| `strategies/base_v2.py` | **100%** | 策略基类全覆盖 |
| `strategies/rules/__init__.py` | **100%** | 规则包初始化 |
| `monitor.py` (fts) | **100%** | 项目级监控全覆盖 |
| `meta_loop.py` | **99%** | L1 元循环 |
| `evolution_loop.py` | **99%** | L2 主循环 |
| `multi_factor_strategy.py` | **99%** | 多因子策略 |
| `verifier.py` | **97%** | 锁定/解锁/异常路径全覆盖 |
| `evaluation_chain.py` | **96%** | 三级评估链 |
| `factor_program.py` | **96%** | 因子程序 |
| `experience_chain.py` | **93%** | 经验链存储与读取 |
| `micro_evolution.py` | **92%** | 微观演化 |
| `portfolio_loop.py` | **92%** | 组合构建与信号产出核心路径 |
| `cli.py` | **87%** | CLI 入口 |

### 低覆盖模块（<80%）

| 模块 | 覆盖率 | 说明 |
|:-----|:-------|:-----|
| `monitor.py` (factor_engine) | **62%** | 因子引擎监控部分路径 |
| `llm.py` | **77%** | LLM 客户端 |

---

## 5. 测试用例统计

| 测试文件 | 用例数 | 覆盖模块 |
|:---------|:-------|:---------|
| `tests/core/test_contracts.py` | ~39 | core contracts |
| `tests/core/test_enums.py` | ~17 | enums |
| `tests/core/test_atomic.py` | ~30 | 原子操作 |
| `tests/test_config_settings.py` | ~32 | 配置管理 |
| `tests/factor_engine/test_contracts.py` | ~16 | 契约定义 |
| `tests/factor_engine/test_evaluation_chain.py` | ~32 | 三级评估链 |
| `tests/factor_engine/test_evolution_loop.py` | ~54 | L2 主循环 |
| `tests/factor_engine/test_experience_chain.py` | ~13 | 经验链 |
| `tests/factor_engine/test_factor_program.py` | ~32 | 因子程序 |
| `tests/factor_engine/test_macro_evolution.py` | ~30 | 宏观演化 |
| `tests/factor_engine/test_meta_loop.py` | ~77 | L1 元循环 |
| `tests/factor_engine/test_monitor.py` | ~23 | 因子引擎监控 |
| `tests/factor_engine/test_portfolio_loop.py` | ~42 | L3 组合循环 |
| `tests/factor_engine/test_program.py` | ~16 | Program.md |
| `tests/factor_engine/test_seed_pool.py` | ~9 | 种子池 |
| `tests/factor_engine/test_verifier.py` | ~12 | Verifier |
| `tests/pipeline/test_base.py` | ~25 | 管线基础 |
| `tests/pipeline/test_factor_combiner.py` | ~33 | 因子组合器 |
| `tests/scheduler/test_tasks.py` | ~31 | 调度任务 |
| `tests/scheduler/test_engine.py` | ~35 | 调度引擎 |
| `tests/strategies/test_base_v2.py` | ~55 | 策略基类 |
| `tests/strategies/test_multi_factor.py` | ~88 | 多因子策略 |
| `tests/test_cli.py` | ~50 | CLI 入口 |
| `tests/test_llm.py` | ~22 | LLM 客户端 |
| `tests/test_monitor.py` | ~46 | 项目级监控 |
| `tests/test_data.py` | ~68 | 数据层 |
| **合计** | **1231** | |

---

## 6. 测试原则

1. **测试随重构**：每阶段先写测试，测试全绿才能进入下一阶段
2. **mock 外部依赖**：LLM 调用使用 MockLLMClient，数据层使用 mock
3. **trace_id 验证**：测试必须验证 trace_id 是否正确传播
4. **Verifier 锁定测试**：必须测试锁定后的只读行为
5. **覆盖率门禁**：新增代码必须有对应测试，覆盖率不得低于模块当前水平
