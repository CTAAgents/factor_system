# Seeds — 种子因子库

## 概述

`seeds/` 目录存储所有种子因子的 YAML 定义。种子因子是 FTS 因子演化系统的起点，通过 `seed_loader.py` 加载后注入 `SeedPool`，作为 L1 演化、L2 评估和 L3 组合的输入。

## 目录结构

```
seeds/
├── README.md                    # 本文档
├── futures/                     # 期货种子因子
│   ├── momentum.yaml            #   动量 (5)
│   ├── term_structure.yaml      #   期限结构 (3)
│   ├── position_flow.yaml       #   资金流向 (3)
│   ├── liquidity.yaml           #   流动性 (3)
│   ├── higher_moments.yaml      #   高阶矩 (3)
│   ├── volatility.yaml          #   波动率 (2)
│   ├── fundamental.yaml         #   基本面 (4)
│   ├── crowding.yaml            #   拥挤度 (6)
│   ├── alpha_behavior.yaml      #   Alpha 行为 (4)
│   ├── high_frequency.yaml      #   高频 (6)
│   ├── options.yaml             #   期权 (3)
│   ├── market_regime.yaml       #   市场状态 (8)
│   ├── cta_registry.yaml        #   CTA 注册表 (7)
│   └── operator_dict.yaml       #   算子字典 (24)
└── energy/                      # 能化专属种子因子（GAP-121）
    ├── crack_spread.yaml        #   裂解价差/炼化利润 (2, eng_)
    ├── polyester_chain.yaml     #   聚酯链加工差/库存周期 (2, eng_)
    ├── energy_basis_inventory.yaml  # 能化基差/库存回归 (2, eng_)
    ├── energy_chain_linkage.yaml    # 链内联动/季节性开工 (2, eng_)
    └── energy_chain.yaml        #   链传导专属知识种子 (6, ec_)
```

**共计 185 个期货种子因子**（股票种子已随股票剥离迁至 fts-stock）
**能化专属 14 个种子因子**（eng_* 8 个量价代理 + ec_* 6 个链传导专属，market=energy）

## 因子类型

YAML 加载器自动检测三种因子类型，按以下优先级判断：

1. **Code-based**（`code` 字段存在）→ 直接执行完整 Python 代码
2. **Fundamental**（`field_defs` 字段存在）→ 基本面因子模板 + 字段注入
3. **Expression**（`expression` 字段存在）→ Alpha 表达式 + 代码模板生成

### Code-based 因子

适用于期货因子和内置股票因子。包含完整的 `factor_program()` 函数实现。

```yaml
family: momentum
version: "1.0"
market: futures
factors:
  - name: fut_xsmom
    description: 截面动量因子
    market: futures
    params:
      lookback: 20
      holding: 1
    input_fields:
      - close
    lookback: 25
    output_type: signal
    frequency: daily
    economic_logic:
      theory: 4
      behavioral: 3
      microstructure: 3
      institutional: 4
      narrative: 截面动量：做多过去收益高的品种
    code: |2

      def factor_program(data, params):
          import numpy as np
          close = data['close'].values if hasattr(data, 'close') else data['close']
          n = len(close)
          j = int(params.get('lookback', 20))
          if n < j + 2:
              return np.zeros(n)
          ret = np.zeros(n)
          ret[j:] = (close[j:] - close[:-j]) / np.maximum(close[:-j], 1e-10)
          sig = np.tanh(ret / 0.05)
          return np.clip(sig, -1.0, 1.0)
```

### Expression 因子

适用于 WQ101/Qlib158/GTJA191 等 Alpha 因子。使用简化的 Alpha 表达式，由加载器自动生成完整代码。

```yaml
family: wq101
version: "1.1"
market: stock
factors:
  - name: alpha_001
    description: "Alpha#001: 条件波动率-动量复合信号"
    market: stock
    expression: |-
      rank(ts_argmax(signed_power(ifelse(returns<0, ts_stddev(returns,20), close), 2), 5)) - 0.5
```

表达式中可用的算子：`rank`, `scale`, `ifelse`, `ts_sum`, `ts_mean`, `ts_stddev`, `ts_corr`, `ts_covariance`, `ts_argmax`, `ts_argmin`, `ts_rank`, `ts_min`, `ts_max`, `ts_product`, `signed_power`, `decay_linear`, `delta`, `delay`, `log`, `sign`, `abs`, `neg`, `highday`, `lowday`。

### Fundamental 因子

适用于基本面因子。通过 `field_defs` 注入数据字段，`field_check` 验证数据可用性。

```yaml
family: fundamental
version: "1.0"
market: stock
factors:
  - name: fund_val_pe
    description: 低PE估值因子
    market: stock
    expression: |-
      np.tanh(1.0 / (np.maximum(pe_ttm, 0.1) / 15.0))
    field_defs: |-
      pe_ttm = data['pe_ttm'].values if hasattr(data, 'pe_ttm') else data.get('pe_ttm')
    field_check: |-
      pe_ttm is not None and len(pe_ttm) > 0 and np.any(pe_ttm > 0)
    input_fields:
      - pe_ttm
    lookback: 1
```

## 字段说明

### 文件级字段

| 字段 | 类型 | 必填 | 说明 |
|:-----|:-----|:-----|:-----|
| `family` | string | ✅ | 因子家族标识，用于分组管理和多样性控制 |
| `version` | string | ✅ | YAML 文件版本号 |
| `market` | string | ✅ | 适用市场：`futures` / `stock` |

### 因子级字段

| 字段 | 类型 | 必填 | 说明 |
|:-----|:-----|:-----|:-----|
| `name` | string | ✅ | 因子唯一名称 |
| `description` | string | ✅ | 因子描述（用于日志和文档） |
| `market` | string | ✅ | 适用市场（通常与文件级一致） |
| `code` | string | Code-based | 完整 `factor_program()` Python 代码 |
| `expression` | string | Expression/Fundamental | Alpha 表达式 |
| `params` | object | ❌ | 因子参数（代码中通过 `params.get()` 读取） |
| `input_fields` | list[string] | ❌ | 所需输入数据字段（Expression 类型自动推断） |
| `lookback` | int | ❌ | 最小回看窗口（Expression 类型自动估算） |
| `output_type` | string | ❌ | 输出类型：`signal`（默认） |
| `frequency` | string | ❌ | 频率：`daily`（默认） |
| `economic_logic` | object | ❌ | 经济逻辑四维评分 |
| `economic_logic.theory` | int | ❌ | 理论支撑 (0-5) |
| `economic_logic.behavioral` | int | ❌ | 行为金融 (0-5) |
| `economic_logic.microstructure` | int | ❌ | 微观结构 (0-5) |
| `economic_logic.institutional` | int | ❌ |  institutional (0-5) |
| `economic_logic.narrative` | string | ❌ | 经济逻辑叙述 |
| `family` | string | ❌ | 因子家族（覆盖文件级默认值） |

## 命名规范

### 文件命名

- **期货**: `{family}.yaml`，如 `momentum.yaml`、`crowding.yaml`
- **股票外部种子**: `{source}.yaml`，如 `wq101.yaml`、`qlib158.yaml`
- **股票内置**: `builtin.yaml`
- **基本面**: `fundamental.yaml`

### 因子命名

- **期货**: `fut_{family}_{variant}`，如 `fut_xsmom`、`fut_tsmom`、`fut_basis_level`
- **WQ101**: `alpha_{NNN}`，如 `alpha_001` ~ `alpha_101`
- **Qlib158**: `qlib_{NNN}`，如 `qlib_001` ~ `qlib_158`
- **GTJA191**: `gtja_{NNN}`，如 `gtja_001` ~ `gtja_191`
- **内置**: 简短描述名，如 `momentum`、`volatility_reversion`、`pmi_proxy`
- **基本面**: `fund_{type}_{metric}`，如 `fund_val_pe`、`fund_growth_revenue`

## 如何添加新因子

### 步骤 1: 选择目标文件

- 向现有家族添加 → 编辑对应 YAML 文件
- 新增家族 → 在对应市场目录下创建新 YAML 文件

### 步骤 2: 编写因子定义

选择合适的因子类型（Code-based / Expression / Fundamental），按上述 Schema 编写。

### 步骤 3: 验证

```python
from fts.factor_engine.seed_loader import load_factors_from_yaml, verify_yaml_integrity

# 加载单个文件
factors = load_factors_from_yaml("seeds/futures/momentum.yaml")

# 验证完整性
report = verify_yaml_integrity()
```

### 步骤 4: 运行测试

```bash
pytest tests/factor_engine/test_seed_loader.py -v
```

## 加载机制

### 双路径读取

`SeedPool.load_all_seeds()` 按以下优先级加载：

1. **YAML 路径**（主路径）：从 `seeds/` 目录加载
2. **硬编码兜底**：YAML 加载失败时回退到 `seed_pool.py` 中的硬编码定义

### 市场过滤

```python
from fts.factor_engine.seed_loader import load_all_yaml_seeds

# 仅加载期货
futures = load_all_yaml_seeds(market="futures")

# 仅加载股票
stocks = load_all_yaml_seeds(market="stock")

# energy 市场：混入加载通用期货种子 + 能化专属种子（GAP-121）
energy = load_all_yaml_seeds(market="energy")

# 加载全部
all_seeds = load_all_yaml_seeds()
```

### 完整性验证

`verify_yaml_integrity()` 扫描所有 YAML 文件，检查：
- 文件是否可解析
- 每个因子是否有 `name` 字段
- 类型检测是否成功
- 代码/表达式是否存在

## 版本历史

| 版本 | 日期 | 变更 |
|:-----|:-----|:-----|
| v1.0 | 2026-08-05 | 创建 README（Phase 1 Task 1.1 完成） |

## 一致性元数据

| 组件 | 路径 | 说明 |
|:-----|:-----|:-----|
| SeedLoader | `fts/factor_engine/seed_loader.py` | YAML 加载器实现 |
| SeedPool | `fts/factor_engine/seed_pool.py` | 种子池（含硬编码兜底） |
| 迁移脚本 | `scripts/migrate_seeds_to_yaml.py` | 从硬编码迁移到 YAML |
| 测试 | `tests/factor_engine/test_seed_loader.py` | YAML 加载单元测试 |
