# FTS 测试策略

> 版本: v0.1.0
> 最后更新: 2026-07-18

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
       │ 单元测试      │    ← factor_engine 各模块独立测试（10 个测试文件）
       │              │
       └──────────────┘
```

| 层级 | 测试文件数 | 用例数 | 说明 |
|:-----|:----------|:-------|:-----|
| 单元测试 | 10 | ~200 | factor_engine 各模块 |
| 集成测试 | 1 | ~20 | strategies 策略层 |
| E2E | 0 | 0 | 全流程端到端（待补充） |

---

## 2. 测试目录结构

```
tests/
├── __init__.py
├── conftest.py                      # 全局 fixture
│
├── factor_engine/                   # 10 个测试文件
│   ├── __init__.py
│   ├── conftest.py                  # factor_engine fixture
│   ├── test_contracts.py            # 契约定义测试
│   ├── test_evaluation_chain.py     # 三级评估链测试
│   ├── test_evolution_loop.py       # L2 主循环测试
│   ├── test_experience_chain.py     # 经验链测试
│   ├── test_factor_program.py       # 因子程序（安全沙箱）测试
│   ├── test_meta_loop.py            # L1 元循环测试
│   ├── test_portfolio_loop.py       # L3 组合循环测试
│   ├── test_program.py              # Program.md 测试
│   ├── test_seed_pool.py            # 种子池测试
│   └── test_verifier.py             # Verifier 锁定协议测试
│
└── strategies/                      # 1 个测试文件
    ├── __init__.py
    └── test_multi_factor.py         # 多因子策略测试
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

## 4. 覆盖统计（v0.1.0）

### 总体统计

| 指标 | 值 |
|:-----|:---|
| Total statements | 2924 |
| Overall coverage | 71% |
| 测试用例数 | 220 passed, 0 failed |

### 模块覆盖详情

```
Name                                      Stmts   Miss  Cover
──────────────────────────────────────────────────────────────
fts\__init__.py                               1      0   100%
fts\cli.py                                  118    118     0%
fts\core\__init__.py                          0      0   100%
fts\core\contracts.py                         3      3     0%
fts\core\enums.py                            17     17     0%
fts\factor_engine\__init__.py                16      0   100%
fts\factor_engine\contracts.py              257      0   100%
fts\factor_engine\evaluation_chain.py       134     14    90%
fts\factor_engine\evolution_loop.py         165     59    64%
fts\factor_engine\experience_chain.py       121      9    93%
fts\factor_engine\factor_program.py         106     15    86%
fts\factor_engine\macro_evolution.py         68     10    85%
fts\factor_engine\meta_loop.py              446     69    85%
fts\factor_engine\micro_evolution.py         72     50    31%
fts\factor_engine\monitor.py                106     44    58%
fts\factor_engine\portfolio_loop.py         326     27    92%
fts\factor_engine\program.py                104      0   100%
fts\factor_engine\seed_pool.py               51      0   100%
fts\factor_engine\state.py                   94     14    85%
fts\factor_engine\verifier.py                66      2    97%
fts\monitor.py                               61     61     0%
fts\pipeline\__init__.py                      4      4     0%
fts\pipeline\base.py                         49     49     0%
fts\pipeline\factor_combiner.py              95     95     0%
fts\scheduler\__init__.py                     3      3     0%
fts\scheduler\tasks.py                       49     49     0%
fts\strategies\__init__.py                    4      0   100%
fts\strategies\base_v2.py                   156     76    51%
fts\strategies\multi_factor_strategy.py     231     62    73%
fts\strategies\rules\__init__.py              1      1     0%
──────────────────────────────────────────────────────────────
TOTAL                                      2924    851    71%
```

### 高覆盖模块（>=80%）

| 模块 | 覆盖率 | 说明 |
|:-----|:-------|:-----|
| `contracts.py` (factor_engine) | **100%** | 契约定义全覆盖 |
| `seed_pool.py` | **100%** | 种子池全覆盖 |
| `program.py` | **100%** | Program.md 全覆盖 |
| `verifier.py` | **97%** | 锁定/解锁/异常路径全覆盖 |
| `portfolio_loop.py` | **92%** | 组合构建与信号产出核心路径 |
| `experience_chain.py` | **93%** | 经验链存储与读取 |
| `evaluation_chain.py` | **90%** | 三级评估链 |

### 低覆盖模块（<50%）

| 模块 | 覆盖率 | 说明 |
|:-----|:-------|:-----|
| `micro_evolution.py` | **31%** | optuna 依赖在 evolution extra 中，大部分路径未测试 |
| `monitor.py` (factor_engine) | **58%** | 监控 CLI 路径未完全覆盖 |
| `cli.py` | **0%** | 无测试文件 |
| `monitor.py` (fts) | **0%** | 无测试文件 |
| `pipeline/base.py` | **0%** | 无测试文件 |
| `pipeline/factor_combiner.py` | **0%** | 无测试文件 |
| `scheduler/tasks.py` | **0%** | 无测试文件 |

---

## 5. 测试用例统计

| 测试文件 | 用例数 | 覆盖模块 |
|:---------|:-------|:---------|
| `tests/factor_engine/test_contracts.py` | ~15 | 契约定义 |
| `tests/factor_engine/test_evaluation_chain.py` | ~25 | 三级评估链 |
| `tests/factor_engine/test_evolution_loop.py` | ~20 | L2 主循环 |
| `tests/factor_engine/test_experience_chain.py` | ~20 | 经验链 |
| `tests/factor_engine/test_factor_program.py` | ~15 | 因子程序 |
| `tests/factor_engine/test_meta_loop.py` | ~35 | L1 元循环 |
| `tests/factor_engine/test_portfolio_loop.py` | ~30 | L3 组合循环 |
| `tests/factor_engine/test_program.py` | ~15 | Program.md |
| `tests/factor_engine/test_seed_pool.py` | ~15 | 种子池 |
| `tests/factor_engine/test_verifier.py` | ~15 | Verifier |
| `tests/strategies/test_multi_factor.py` | ~15 | 多因子策略 |
| **合计** | **220** | |

---

## 6. 测试原则

1. **测试随重构**：每阶段先写测试，测试全绿才能进入下一阶段
2. **mock 外部依赖**：LLM 调用使用 MockLLMClient，数据层使用 mock
3. **trace_id 验证**：测试必须验证 trace_id 是否正确传播
4. **Verifier 锁定测试**：必须测试锁定后的只读行为
5. **覆盖率门禁**：新增代码必须有对应测试，覆盖率不得低于模块当前水平
