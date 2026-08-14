"""
fts.workflow.stages — CTA 手册 WorkFlow 阶段定义（11 阶段 + 质检闭环）。

对照《期货CTA多因子策略标准化作业手册》:
    阶段1 数据基建 → 阶段2 因子库挖掘 → 阶段3 预处理&正交 → 阶段4 单因子IC/IR检验
    → 阶段5 Regime识别 → 阶段6 因子合成 → 阶段7 信号转仓位&风险平价
    → 阶段8 组合风控 → 阶段9 滚动样本外回测 → 阶段10 仿真 → 阶段11 实盘爬坡
    + 第六章 因子质检工作流程（贯穿全生命周期）

每个阶段定义: 名称 / 描述 / 前置依赖 / 可执行动作（CLI 命令映射，支持动态占位符）。
动作 ``cmd`` 为 ``fts.cli`` 子命令参数列表（不含解释器前缀），或
``{script}`` 开头的脚本相对路径；``{factor_id}``/``{report_dir}`` 为动态占位符，
由执行器运行时解析。``kind="info"`` 表示阶段无 CLI 动作（信息展示）。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageAction:
    """阶段可执行动作。"""

    id: str
    label: str
    cmd: list[str] = field(default_factory=list)
    kind: str = "cli"  # "cli" | "script" | "info"
    timeout: int = 600  # 秒
    json_output: bool = False  # 是否解析 --json 输出为结构化产物


@dataclass(frozen=True)
class Stage:
    """WorkFlow 阶段节点。"""

    id: str
    index: int
    name: str
    desc: str
    depends_on: list[str]
    actions: list[StageAction]


# ─── 11 阶段 + 质检闭环定义 ─────────────────────────────────────
STAGES: list[Stage] = [
    Stage(
        id="s1",
        index=1,
        name="数据基建",
        depends_on=[],
        desc="构建干净、一致、无未来函数的基础数据集（连续交割月合约/交易日历/脏值标记/品种差异化成本）。",
        actions=[
            StageAction("a1", "查看数据状态", ["data", "status"], timeout=120),
            StageAction(
                "a2", "同步期货数据", ["data", "sync-futures", "--days", "120", "--json"], timeout=900, json_output=True
            ),
        ],
    ),
    Stage(
        id="s2",
        index=2,
        name="因子库挖掘",
        depends_on=["s1"],
        desc="构建逻辑清晰、经济学含义明确的因子库（量价/基本面/期限结构，参数可遍历，Shift 错位零未来函数）。",
        actions=[
            StageAction("a1", "查看种子因子库", ["factor", "seeds"], timeout=120),
            StageAction(
                "a2",
                "微观结构因子生成",
                ["factor", "micro-generate", "--limit", "5", "--json"],
                timeout=600,
                json_output=True,
            ),
        ],
    ),
    Stage(
        id="s3",
        index=3,
        name="预处理与正交化",
        depends_on=["s2"],
        desc="MAD 去极值 / 横截面逐日 Z-score / 正交基训练窗口内估计 / 去趋势，消除极端值与多重共线性。",
        actions=[
            StageAction("a1", "种子校验（含中性化/正交）", ["seed", "validate"], timeout=600),
            StageAction("a2", "特征算子一览", ["feature", "list"], timeout=120),
        ],
    ),
    Stage(
        id="s4",
        index=4,
        name="单因子IC/IR检验",
        depends_on=["s3"],
        desc="IC/IR 分类门槛（量价≥0.3/基本面≥0.4/期限结构≥0.35）+ 分层收益单调 + 置换检验 + 极端行情 IC 验证。",
        actions=[
            StageAction("a1", "因子库统计（IC/IR）", ["catalog", "stats", "--json"], timeout=180, json_output=True),
            StageAction("a2", "候选因子评估", ["factor", "micro-evaluate", "--json"], timeout=600, json_output=True),
        ],
    ),
    Stage(
        id="s5",
        index=5,
        name="Regime 识别与动态权重",
        depends_on=["s4"],
        desc="ADX/Hurst/波动率分位数/趋势一致性/截面离散度五指标投票判定 trend/oscillation/transition，因子权重动态切换。",
        actions=[
            StageAction(
                "a1", "因子清单（Regime 评估链内置）", ["factor", "list", "--json"], timeout=180, json_output=True
            ),
        ],
    ),
    Stage(
        id="s6",
        index=6,
        name="多因子合成",
        depends_on=["s5"],
        desc="等权 / IC 动态加权 / ElasticNet 滚动回归合成综合预测得分，模型稀疏可解释。",
        actions=[
            StageAction(
                "a1",
                "组合运行（等权合成）",
                ["portfolio", "run", "--universe", "futures", "--synthesis-mode", "equal_weight", "--force-recompute"],
                timeout=900,
            ),
            StageAction(
                "a2",
                "组合运行（Regime 自适应）",
                ["portfolio", "run", "--universe", "futures", "--synthesis-mode", "adaptive", "--force-recompute"],
                timeout=900,
            ),
        ],
    ),
    Stage(
        id="s7",
        index=7,
        name="信号转仓位与风险平价",
        depends_on=["s6"],
        desc="五层调仓机制（缓冲带/混合触发/换手阈值拦截/防僵尸/分批执行）+ 风险平价加权 + 板块中性约束。",
        actions=[
            StageAction(
                "a1",
                "单因子回测（五层调仓）",
                ["backtest", "run", "--factor-id", "{factor_id}", "--days", "250", "--capital", "200000"],
                timeout=900,
            ),
        ],
    ),
    Stage(
        id="s8",
        index=8,
        name="组合风控",
        depends_on=["s7"],
        desc="事前/事中/事后三层风控 + 期货特有场景（涨跌停封板/提保限仓/交割月移仓/夜盘跳空/熔断/主力切换异常）。",
        actions=[
            StageAction("a1", "因子库校验（含风控）", ["catalog", "verify"], timeout=600),
        ],
    ),
    Stage(
        id="s9",
        index=9,
        name="滚动样本外回测",
        depends_on=["s8"],
        desc="Walk-Forward 滚动窗口 + 过拟合排查（参数敏感度/置换/样本内外衰减率≤30%/时间段一致性/逻辑简约度）。",
        actions=[
            StageAction(
                "a1",
                "长窗口样本外回测",
                ["backtest", "run", "--factor-id", "{factor_id}", "--days", "500", "--output", "{report_dir}"],
                timeout=1200,
            ),
        ],
    ),
    Stage(
        id="s10",
        index=10,
        name="仿真柜台联调",
        depends_on=["s9"],
        desc="模拟柜台验证可执行性：仿真净值 vs 回测净值 ±5% 偏差 + 异常捕获 + 日志完整。由 live_trade/SimulatedPortfolio 提供。",
        actions=[
            StageAction("a1", "仿真状态（桥接/纸面交易）", ["bridge", "status"], timeout=120),
        ],
    ),
    Stage(
        id="s11",
        index=11,
        name="实盘分阶段上线",
        depends_on=["s10"],
        desc="资金三级爬坡（10%→50%→100%）+ 因子生命周期管理（60 日 IC 衰减>30% 或 IR<0.3 归零权重复审）。",
        actions=[
            StageAction("a1", "因子生命周期状态", ["factor", "stats", "--json"], timeout=120, json_output=True),
        ],
    ),
    Stage(
        id="qa",
        index=12,
        name="因子质检闭环",
        depends_on=["s4", "s11"],
        desc="第六章四段闭环 SOP：入库前质检 Q1-Q10（一票否决）→ 三级准入 → 月度/季度/半年度复检 → 退役判定 + 7 状态看板。",
        actions=[
            StageAction(
                "a1",
                "端到端质检验证（合成数据）",
                ["scripts/verify_qa_workflow.py", "--days", "150", "--synthetic"],
                kind="script",
                timeout=600,
            ),
        ],
    ),
]

_STAGE_BY_ID: dict[str, Stage] = {s.id: s for s in STAGES}


def get_stages() -> list[dict]:
    """阶段定义（API 输出，含动作）。"""
    return [
        {
            "id": s.id,
            "index": s.index,
            "name": s.name,
            "desc": s.desc,
            "depends_on": s.depends_on,
            "actions": [
                {"id": a.id, "label": a.label, "kind": a.kind, "timeout": a.timeout, "cmd": list(a.cmd)}
                for a in s.actions
            ],
        }
        for s in STAGES
    ]


def get_stage(stage_id: str) -> Stage | None:
    return _STAGE_BY_ID.get(stage_id)


__all__ = [
    "Stage",
    "StageAction",
    "STAGES",
    "get_stages",
    "get_stage",
]
