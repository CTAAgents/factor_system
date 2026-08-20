"""
fts/config/settings.py — FTS 全局配置。

配置加载优先级（高 → 低）:
    1. 环境变量（FTS_* 前缀）
    2. YAML 配置文件
    3. 本模块定义的默认值
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ─── 默认路径 ────────────────────────────────────────────

DEFAULT_MEMORY_DIR = "memory"
# v2.86.0: 股票精英因子目录 stocks_elite/（与期货 futures_elite/ 命名对齐）。
# 股票剥离后 FTS 主系统定位期货因子系统，elite_dir 默认对齐期货精英目录。
DEFAULT_ELITE_DIR = "memory/knowledge/factors/futures_elite"
DEFAULT_FUTURES_ELITE_DIR = "memory/knowledge/factors/futures_elite"
# 能源产业链专属精英目录（GAP-Ixxx，独立于通用期货精英目录）
DEFAULT_ENERGY_CHAIN_ELITE_DIR = "memory/knowledge/factors/energy_chain_elite"


# ─── 配置类 ──────────────────────────────────────────────


@dataclass
class FTSConfig:
    """FTS 全局配置。"""

    # ── 路径配置 ──
    memory_dir: str = field(default_factory=lambda: os.getenv("FTS_MEMORY_DIR", DEFAULT_MEMORY_DIR))
    elite_dir: str = field(default_factory=lambda: os.getenv("FTS_ELITE_DIR", DEFAULT_ELITE_DIR))
    futures_elite_dir: str = field(
        default_factory=lambda: os.getenv("FTS_FUTURES_ELITE_DIR", DEFAULT_FUTURES_ELITE_DIR)
    )
    energy_chain_elite_dir: str = field(
        default_factory=lambda: os.getenv("FTS_ENERGY_CHAIN_ELITE_DIR", DEFAULT_ENERGY_CHAIN_ELITE_DIR)
    )

    def get_elite_dir(self, market: str = "futures") -> str:
        """获取 elite 目录（股票剥离后统一为期货精英目录）。

        Args:
            market: 兼容参数（历史调用方传 "futures"/"stock"），股票剥离后忽略；
                    "energy" 返回能源产业链专属精英目录

        Returns:
            期货 elite 目录路径
        """
        if market == "energy":
            return self.energy_chain_elite_dir
        return self.futures_elite_dir

    # ── 数据配置 ──
    # 全局默认市场（v2.104.0+103 临时切 energy；v3.0.0+1 反转回 futures）：
    # 双系统切分后（plans/57）FTS 为因子生产系统，默认工作市场为全部期货
    # （§5.4 全期货覆盖 84 品种/17 产业链），门控任务（_market_gate）据此切换执行集
    default_market: str = field(default_factory=lambda: os.getenv("FTS_DEFAULT_MARKET", "futures"))

    # ── 宏观字段增强层（v2.32.0）──
    macro_field_injection: bool = field(default_factory=lambda: os.getenv("FTS_MACRO_FIELD_INJECTION", "1") == "1")
    macro_lag_days: int = field(default_factory=lambda: int(os.getenv("FTS_MACRO_LAG_DAYS", "30")))

    # ── LLM 配置 ──
    llm_backend: str = field(default_factory=lambda: os.getenv("FTS_LLM_BACKEND", ""))
    # LLM 采样温度；提高可增加因子多样性（默认 1.2 > provider 默认 1.0）
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("FTS_LLM_TEMPERATURE", "1.2")))

    # ── 演化配置 ──
    # ── 演化模式 (Phase C.2): operator(算子主干) / operator_first(算子优先,LLM/GP兜底) / code(代码创新) / hybrid(混合) / batch(批量挖掘 GAP-I201) ──
    evolution_mode: str = field(default_factory=lambda: os.getenv("FTS_EVOLUTION_MODE", "operator_first"))
    max_generations: int = 10
    population_size: int = 20
    micro_trials_per_generation: int = 50

    # ── 微观演化两阶段漏斗 (GAP-I205, v2.68.0) ──
    # 粗筛（低 trials 随机搜索快速打分，低于阈值淘汰）→ 精筛（trials 按粗筛得分自适应 + TPE 早停）
    micro_staged_evolution: bool = field(default_factory=lambda: os.getenv("FTS_MICRO_STAGED", "1") == "1")
    # 粗筛试验次数（低 trials 快速打分）
    micro_coarse_trials: int = field(default_factory=lambda: int(os.getenv("FTS_MICRO_COARSE_TRIALS", "20")))
    # 粗筛淘汰阈值：粗筛 IC 低于该值直接淘汰，不进入精筛
    micro_coarse_ic_floor: float = field(default_factory=lambda: float(os.getenv("FTS_MICRO_COARSE_IC_FLOOR", "0.02")))

    # ── 结构性聚类配额 (GAP-077, v2.102.0) ──
    # 以信号相关性聚类配额控制多样性：family 概念已彻底移除（v2.104.0+25），
    # 因子分组/配额统一按信号相关性聚类（_count_cluster_members）。
    structure_cluster_quota_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_CLUSTER_QUOTA_ENABLED", "1") == "1"
    )
    # 每结构簇最大因子数
    structure_cluster_max: int = field(default_factory=lambda: int(os.getenv("FTS_CLUSTER_MAX", "15")))
    # 判定"同类"的相关性阈值（略宽于 GAP-I206 的 0.9：0.9 强拦截 vs 0.85 数量配额）
    structure_cluster_corr_threshold: float = field(
        default_factory=lambda: float(os.getenv("FTS_CLUSTER_CORR_THRESHOLD", "0.85"))
    )

    # ── SHAP 批量计算降频 (GAP-080, v2.102.0) ──
    # KernelExplainer 每因子评估量 ≈ n_extreme×2 × nsamples 次单行因子执行，
    # 原默认 50×2×100=1 万次/因子（~14s 串行）成晋升链新瓶颈。降频三项均为
    # 信息型审查（SHAP 成功即通过）的采样参数，缩小样本不改变门禁语义。
    # 极端样本数（top+bottom 各 N）
    shap_n_extreme: int = field(default_factory=lambda: int(os.getenv("FTS_SHAP_N_EXTREME", "25")))
    # KernelExplainer 背景样本数
    shap_n_background: int = field(default_factory=lambda: int(os.getenv("FTS_SHAP_N_BACKGROUND", "50")))
    # 每个极端样本的 KernelExplainer 扰动次数（nsamples，降频核心）
    shap_nsamples: int = field(default_factory=lambda: int(os.getenv("FTS_SHAP_NSAMPLES", "50")))

    # ── 成功模式定向演化 (Phase 1.2 P0-1, 26 号计划 §6) ──
    # 从经验链成功轨迹聚合近期成功模式（方法/算子/窗口维度，明确排除 family），
    # 注入 MacroEvolver prompt 作 soft 偏向（参考非硬性约束）。五重防过拟合：
    # soft 偏向 + 时间衰减 + 滚动窗口 + 开关 + 样本下限。
    evolution_success_pattern_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_SUCCESS_PATTERN_ENABLED", "1") == "1"
    )
    # 成功模式滚动窗口（天）
    success_pattern_window_days: int = field(default_factory=lambda: int(os.getenv("FTS_SUCCESS_PATTERN_WINDOW", "14")))
    # 样本下限：窗口内成功轨迹 < 该值 → 空报告（不注入，防过拟合）
    success_pattern_min_sample: int = field(
        default_factory=lambda: int(os.getenv("FTS_SUCCESS_PATTERN_MIN_SAMPLE", "10"))
    )

    # ── 提前达标停止 (Phase 3 P1-3, 26 号计划 §8) ──
    # 连续 K 代零晋升 → 提前结束 run，节约 token 预算。保守默认关闭（验证见
    # plans/26 §8.7.1：修复后真实 run 连续 15 代零晋升），开启需显式设 env。
    evolution_stop_enabled: bool = field(default_factory=lambda: os.getenv("FTS_EVOLUTION_STOP_ENABLED", "0") == "1")
    # 连续零晋升代数阈值 K（达到即提前结束并正常收尾）
    evolution_stop_consecutive_empty_generations: int = field(
        default_factory=lambda: int(os.getenv("FTS_EVOLUTION_STOP_EMPTY_GENS", "5"))
    )

    # ── 极值扰动一票否决 (GAP-F15, v2.73.0) ──
    # 极值剔除百分位：评估链剔除信号上下该百分位的极端样本后重算 IC，
    # ic_drop 降幅 > 25%（HighICScreener.extreme_drop_max）触发 V2 一票否决。
    extreme_perturb_pct: float = field(default_factory=lambda: float(os.getenv("FTS_EXTREME_PERTURB_PCT", "0.01")))

    # ── 多持有期 IC 体系 (GAP-060, v2.90.0) ──
    # evaluate_backtest / cross_section_evaluate_backtest 的多持有期扫描
    # （逗号分隔，如 "1,5,10,20"）；空 = 关闭。v2.90.0 默认启用 (1,5,10,20)。
    eval_horizons: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in os.getenv("FTS_EVAL_HORIZONS", "1,5,10,20").split(",") if x.strip().isdigit()
        )
    )
    # 可交易性压力层（GAP-061）：evaluate_backtest 是否附加成本敏感性/滑点放大分析（默认关闭）
    cost_sensitivity_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_COST_SENSITIVITY_ENABLED", "0") == "1"
    )
    # G11（35-gap-closure-plan §5.4）：日换手硬剔除阈值（信号翻转率口径 turnover/(21×2)）。
    # 2026-08-13 判定（v2.104.0+9 修订）：期货换手成本低，但换手率过高同样无交易价值——
    # 期货主系统默认 0.45 开启（P95 校准：83 个 active 期货因子真实分布 P95=0.456，
    # 仅拦 top ~5% 天天翻仓的极端抖动因子；股票侧 fts-stock 如需更严可设 0.30=P90 参考值）。
    # env FTS_FACTOR_TURNOVER_DAILY_MAX：数值=覆盖阈值；"off"/"none"/"0"=关闭；空值=默认 0.45
    factor_turnover_daily_max: Optional[float] = field(
        default_factory=lambda: (
            lambda v: None if v.strip().lower() in ("off", "none", "0") else (float(v) if v else 0.45)
        )(os.getenv("FTS_FACTOR_TURNOVER_DAILY_MAX", ""))
    )
    # 夜盘/隔夜跳空列注入（GAP-066 + G8）：get_ohlcv 附加 overnight_gap/overnight_gap_flag 列
    # G8（v2.104.0, D5）：默认开启（跳空标记进入因子面板）
    inject_overnight_gap_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_INJECT_OVERNIGHT_GAP", "1") == "1"
    )
    # 显著隔夜跳空阈值（绝对值比例，默认 1%）
    overnight_gap_flag_threshold: float = field(
        default_factory=lambda: float(os.getenv("FTS_OVERNIGHT_GAP_THRESHOLD", "0.01"))
    )
    # G8（v2.103.0+15）：面板级断K/跳空清洗标记（data_gap/gap_anomaly 列，默认开启）
    inject_data_gap_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_INJECT_DATA_GAP", "1") == "1"
    )

    # ── L2 准入去冗余 (GAP-I206, v2.71.0) ──
    # 演化因子晋升 elite 前与既有 elite 做信号相关性检查，超过阈值拒绝晋升（防 elite 池相关性膨胀）
    l2_elite_corr_threshold: float = field(
        default_factory=lambda: float(os.getenv("FTS_L2_ELITE_CORR_THRESHOLD", "0.9"))
    )
    # 既有 elite 相关性检查的最大扫描数（容量护栏，避免晋升时全量重算）
    l2_elite_corr_max_scan: int = field(default_factory=lambda: int(os.getenv("FTS_L2_ELITE_CORR_MAX_SCAN", "50")))
    # L2 准入去冗余调试日志（无既有 elite 放行时输出 debug）
    l2_elite_corr_debug: bool = field(default_factory=lambda: os.getenv("FTS_L2_ELITE_CORR_DEBUG", "0") == "1")
    # 正交化闭环：高相关因子不直接拒绝，OLS 残差质量合格则正交化版本入库（GAP-I206 补充，v2.71.0）
    l2_elite_orthogonalize: bool = field(default_factory=lambda: os.getenv("FTS_L2_ELITE_ORTHOGONALIZE", "1") == "1")
    # 正交化残差与参照 elite 信号的最大相关性（低于该值视为已正交，默认 0.3）
    l2_orthogonal_residual_corr_max: float = field(
        default_factory=lambda: float(os.getenv("FTS_L2_ORTHOGONAL_RESIDUAL_CORR_MAX", "0.3"))
    )
    # 正交化残差最小保留比（残差 std / 原信号 std，低于该值视为独立信息不足拒绝）
    l2_orthogonal_min_retained_ratio: float = field(
        default_factory=lambda: float(os.getenv("FTS_L2_ORTHOGONAL_MIN_RETAINED_RATIO", "0.3"))
    )
    # Q1-Q10 入库质检结论门禁（GAP-135, v2.105.0）：晋升前校验 qa_review.q1_q10_passed，
    # 结论「禁止入库/待综合评定」时拒绝晋升——评审质检结论与入库必须一致（此前 GP 因子
    # 「禁止入库」结论仍被晋升入库的缺陷）。默认开启，可经 env FTS_L2_QA_GATE_ENABLED=false 关闭。
    l2_qa_gate_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L2_QA_GATE_ENABLED", "1") == "1"
    )

    # ── L2 晋升子链放行 (GAP-144, v2.105.0+8) ──
    # 单链特异因子晋升保护：Verifier 用全链 IC 门槛（min_ic=0.03），单链特异/部分链
    # 因子全链 IC 被无效/反向子链稀释 < 门槛时在晋升入口被拦截（走不到落库落画像）。
    # 开启后，energy 链因子存在 effective 子链（t 检验三门槛画像）时豁免 IC/ICIR
    # 稀释维度（Sharpe/回撤/OOS 等其它维度仍硬判），复用 plans/49 §B2 语义。
    # 灰度默认关（兼容现状），仅 market="energy" 生效。
    l2_subchain_waiver_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L2_SUBCHAIN_WAIVER_ENABLED", "0") == "1"
    )

    # ── L2 去冗余-正交基底 (GAP-I206 补充, v2.72.0) ──
    # 正交基底维护（Gram-Schmidt 迭代残差化）：候选因子对基底逐因子 OLS 残差，
    # 与整个基底正交后入库并注册为新基底成员；基底按 Sharpe 降序保留上限
    l2_orthogonal_basis_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L2_ORTHOGONAL_BASIS_ENABLED", "1") == "1"
    )
    # 正交基底最大成员数（超出时按 Sharpe 降序淘汰最弱成员）
    l2_orthogonal_basis_max_size: int = field(
        default_factory=lambda: int(os.getenv("FTS_L2_ORTHOGONAL_BASIS_MAX_SIZE", "10"))
    )
    # 基底成员最小 Sharpe（低于该值不再入选基底）
    l2_orthogonal_basis_min_sharpe: float = field(
        default_factory=lambda: float(os.getenv("FTS_L2_ORTHOGONAL_BASIS_MIN_SHARPE", "1.0"))
    )

    # ── L2 Barra 风格暴露控制 (GAP-I304, v2.79.0) ──
    # 横截面评估链是否自动构建 Barra 风格暴露并叠加风格回归中性化（全市场覆盖）。
    # 面板字段缺失的风格自动跳过（BarraStyleEngine 全 NaN 处理），不阻断评估。
    l2_barra_style_neutral: bool = field(default_factory=lambda: os.getenv("FTS_L2_BARRA_STYLE_NEUTRAL", "1") == "1")

    # ── L2 训练池面板回溯天数 (GAP-133, v2.104.0+107) ──
    # energy/futures 各 L2 评估作业统一回溯天数：750 日在
    # _build_wf_config 回退分支下可切 4 个完整 OOS 窗口（审计
    # oos_consistency 需 n_windows≥2）；env FTS_L2_PANEL_DAYS 可覆盖。
    l2_panel_days: int = field(default_factory=lambda: int(os.getenv("FTS_L2_PANEL_DAYS", "750")))

    # ── 因子衰减自动退役闭环 (GAP-I305, v2.72.0) ──
    # 滚动 6M IC 线性回归斜率分级：|slope| >= observe_slope → 观察；
    # |slope| >= retire_slope → 退役。slope 归一化到 [-1.0, 1.0]
    decay_observe_slope: float = field(default_factory=lambda: float(os.getenv("FTS_DECAY_OBSERVE_SLOPE", "0.10")))
    decay_retire_slope: float = field(default_factory=lambda: float(os.getenv("FTS_DECAY_RETIRE_SLOPE", "0.20")))
    # 衰减分级最小 IC 序列长度（不足视为 normal）
    decay_slope_min_points: int = field(default_factory=lambda: int(os.getenv("FTS_DECAY_SLOPE_MIN_POINTS", "6")))
    # 自动退役是否启用（关闭时仅打日志不实际退役）
    decay_auto_retire_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_DECAY_AUTO_RETIRE_ENABLED", "1") == "1"
    )

    # ── 批量挖掘漏斗 (GAP-I201, v2.65.0, evolution_mode="batch" 时生效) ──
    # 每代批量候选生成数
    batch_size: int = field(default_factory=lambda: int(os.getenv("FTS_BATCH_SIZE", "20")))
    # 通过粗筛后进入细评估的最大候选数（预算护栏）
    batch_max_candidates: int = field(default_factory=lambda: int(os.getenv("FTS_BATCH_MAX_CANDIDATES", "5")))
    # 粗筛并行线程数
    batch_max_workers: int = field(default_factory=lambda: int(os.getenv("FTS_BATCH_MAX_WORKERS", "4")))
    # 批量生成随机种子（同父多后代可复现）
    batch_random_seed: int = field(default_factory=lambda: int(os.getenv("FTS_BATCH_RANDOM_SEED", "42")))
    # GAP-I502 (v2.83.0): 批量粗筛执行器后端（thread/process/dask/ray，可插拔分布式预留）
    executor_backend: str = field(default_factory=lambda: os.getenv("FTS_EXECUTOR_BACKEND", "thread"))
    # 执行器后端并行工作数
    executor_max_workers: int = field(default_factory=lambda: int(os.getenv("FTS_EXECUTOR_MAX_WORKERS", "4")))

    # ── 并行 ──
    max_workers: int = field(default_factory=lambda: int(os.getenv("FTS_MAX_WORKERS", "4")))

    # ── L1 Meta-Loop ──
    # GAP-I103 (v2.80.0): 另类知识源——公告/舆情提取器开关（股票管道）
    l1_announcement_extractor_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L1_ANNOUNCEMENT_EXTRACTOR_ENABLED", "1") == "1"
    )
    # GAP-I103 (v2.80.0): 另类知识源——宏观事件提取器开关（股票/期货管道）
    l1_macro_extractor_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L1_MACRO_EXTRACTOR_ENABLED", "1") == "1"
    )
    # plans/41 A3 (v2.104.0+71): L1 提取器单次 LLM 最大因子数（max_factors 配置化，
    # 替换散落硬编码 20，管道构造时读取并注入 LLM 提取源——研报/论文/宏观/WebSearch；
    # 天软 tinysoft 为静态 YAML 感知源，不调用 LLM，不参与该配额）
    l1_extractor_max_factors: int = field(
        default_factory=lambda: int(os.getenv("FTS_L1_EXTRACTOR_MAX_FACTORS", "20"))
    )
    # plans/44 C2 (v2.104.0+83): L1 拒绝候选复活开关——每次运行 Step 2.75 扫描
    # l1_rejected_* 目录，对编译失败候选规则/LLM 修复后重新验证注入（GAP-131 落盘闭环）
    l1_rejected_retry: bool = field(
        default_factory=lambda: os.getenv("FTS_L1_REJECTED_RETRY", "1") == "1"
    )
    # plans/44 P0（300 篇/天）：全球多源批量采集——arXiv 每类别 / OpenAlex 每源拉取数
    l1_source_arxiv_max_results: int = field(
        default_factory=lambda: int(os.getenv("FTS_L1_SOURCE_ARXIV_MAX_RESULTS", "50"))
    )
    # 东财研报分页大小（5→100，全行业覆盖 + 关键词过滤）
    l1_source_report_page_size: int = field(
        default_factory=lambda: int(os.getenv("FTS_L1_SOURCE_REPORT_PAGE_SIZE", "100"))
    )
    # 批量采集层开关（false 跳过采集层，保持旧路径向后兼容）
    l1_bulk_enabled: bool = field(default_factory=lambda: os.getenv("FTS_L1_BULK_ENABLED", "1") == "1")
    # embedding 粗筛/语义去重开关
    l1_embedding_enabled: bool = field(default_factory=lambda: os.getenv("FTS_L1_EMBEDDING_ENABLED", "1") == "1")
    # 相关性粗筛阈值
    l1_embedding_threshold: float = field(
        default_factory=lambda: float(os.getenv("FTS_L1_EMBEDDING_THRESHOLD", "0.30"))
    )
    # 语义去重阈值
    l1_dedup_threshold: float = field(
        default_factory=lambda: float(os.getenv("FTS_L1_DEDUP_THRESHOLD", "0.90"))
    )
    # 深读子集上限（篇/天，token 预算约束）
    l1_knowledge_deepread_max: int = field(
        default_factory=lambda: int(os.getenv("FTS_L1_KNOWLEDGE_DEEPREAD_MAX", "60"))
    )
    # WebSearch 动态检索开关（知识缺口 + 当日异动驱动 query）
    l1_dynamic_websearch: bool = field(
        default_factory=lambda: os.getenv("FTS_L1_DYNAMIC_WEBSEARCH", "1") == "1"
    )
    # 语义去重接入开关（bootstrap 候选 vs 已注入候选 embedding 相似度拦截）
    l1_semantic_dedup: bool = field(
        default_factory=lambda: os.getenv("FTS_L1_SEMANTIC_DEDUP", "1") == "1"
    )
    # plans/44 Phase 2 补丁（2026-08-16 用户确认"全球范围内不限中英文"）：
    # OpenAlex 多语种分路语种清单（ISO 639-1，逐语种本地化关键词检索，language 字段如实标注）
    l1_openalex_languages: list[str] = field(
        default_factory=lambda: [
            lang.strip() for lang in os.getenv("FTS_L1_OPENALEX_LANGUAGES", "en,zh,ja,de,fr,ko,es,ru").split(",") if lang.strip()
        ]
    )
    # 非中英语种研报源开关（IEEJ/KEEI/IFPEN 日韩法能源研报，best effort）
    l1_non_en_reports_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L1_NON_EN_REPORTS_ENABLED", "1") == "1"
    )
    # plans/46 (v2.104.0+103): 知识源自动发现（Source Auto-Discovery）开关与阈值
    l1_source_discovery_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L1_SOURCE_DISCOVERY_ENABLED", "1") == "1"
    )
    # 每轮发现任务最大候选源数（LLM 提取后进入探活的上限）
    l1_source_discovery_max_candidates: int = field(
        default_factory=lambda: int(os.getenv("FTS_L1_SOURCE_DISCOVERY_MAX_CANDIDATES", "10"))
    )
    # 探活达标最低评分（0.0~1.0，HTTP 200 + 结构可解析 + 评分达标才注册）
    l1_source_probe_min_score: float = field(
        default_factory=lambda: float(os.getenv("FTS_L1_SOURCE_PROBE_MIN_SCORE", "0.5"))
    )
    # canary 试采成功轮数（连续 N 次采集成功 → pending 晋升 active）
    l1_source_canary_rounds: int = field(
        default_factory=lambda: int(os.getenv("FTS_L1_SOURCE_CANARY_ROUNDS", "3"))
    )
    # 业务维度：连续零因子产出轮数阈值（达到即自动停用，产出恢复复权）
    l1_source_zero_output_rounds: int = field(
        default_factory=lambda: int(os.getenv("FTS_L1_SOURCE_ZERO_OUTPUT_ROUNDS", "5"))
    )
    # plans/44 D2 (Phase 3): L1→L2 积压 warning 阈值（天）——L1 注入未被 L2 消费超过
    # 该天数且存在积压时，l1_l2_funnel_report 输出 warning（防 L1 无限注入）
    l1_l2_backlog_days: int = field(
        default_factory=lambda: int(os.getenv("FTS_L1_L2_BACKLOG_DAYS", "7"))
    )

    # ── L3 Portfolio ──
    portfolio_max_factors: int = 20
    portfolio_top_n: int = 5
    portfolio_decay_days: int = 90
    # GAP-I302 (v2.74.0): 组合优化器机构化——optimizer 模式目标（risk_parity/mvo）
    portfolio_optimizer_mode: str = field(
        default_factory=lambda: os.getenv("FTS_PORTFOLIO_OPTIMIZER_MODE", "risk_parity")
    )
    # GAP-I303 (v2.85.0): 组合目标函数换手惩罚项 λ（0=关闭，λ 越大权重变动越收缩、换手越低）
    # v2.103.0+7 (35-gap-closure-plan G3): 默认开启 0.15（原 0.0 关闭）
    l3_turnover_penalty: float = field(default_factory=lambda: float(os.getenv("FTS_L3_TURNOVER_PENALTY", "0.15")))
    # GAP-072 (v2.99.0): 权重重算频率（解绑 L3 与信号管道）
    # cadence=daily: 每日重算权重；cadence=weekly: 仅在 l3_weight_recompute_weekday 重算（默认周五收盘后）
    # v2.104.0+7 (2026-08-13): 默认改为 daily——L3 组合与信号管道权重每日重算，
    # 避免冻结日复用旧快照导致因子池骤减（8/12 快照仅 1 因子的根因）。
    l3_weight_recompute_cadence: str = field(
        default_factory=lambda: os.getenv("FTS_L3_WEIGHT_RECOMPUTE_CADENCE", "daily")
    )
    # 周度重算日（Python weekday: 0=周一 ... 4=周五；默认周五，L3 组合与信号管道权重周度重算）
    l3_weight_recompute_weekday: int = field(
        default_factory=lambda: int(os.getenv("FTS_L3_WEIGHT_RECOMPUTE_WEEKDAY", "4"))
    )
    # G3 换手预算分配开关（v2.103.0+17，2026-08-13）：true=启用（单日换手 > daily_turnover_cap=0.30 剔除弱信号）
    # false=关闭（默认）：组合换手控制由粘性约束 + 换手惩罚 λ 双通道兜底；
    # 期货周频 elastic_net 场景关闭可避免 sharpe 被 SHARPE_CAP 截断后评分并列导致的误剔最强因子
    l3_turnover_budget_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L3_TURNOVER_BUDGET_ENABLED", "0") == "1"
    )
    # G1 同向敞口惩罚参数（35-gap-closure-plan G1，v2.104.0+X 配置化）：
    # 触发条件：因子 IC 同向权重占比 ≥ l3_g1_align_threshold（ic>0 看多 / ic<0 看空）；
    # compress_curve ∈ {linear, sqrt(更温和), exp(更激进)}；默认值与历史硬编码一致。
    l3_g1_enabled: bool = field(default_factory=lambda: os.getenv("FTS_L3_G1_ENABLED", "1") == "1")
    l3_g1_align_threshold: float = field(
        default_factory=lambda: float(os.getenv("FTS_L3_G1_ALIGN_THRESHOLD", "0.60"))
    )
    l3_g1_max_compress: float = field(
        default_factory=lambda: float(os.getenv("FTS_L3_G1_MAX_COMPRESS", "0.50"))
    )
    l3_g1_compress_curve: str = field(
        default_factory=lambda: os.getenv("FTS_L3_G1_COMPRESS_CURVE", "linear")
    )

    # ── C6 (v2.100.1): 因子自动重校准（decayed → 微调而非直接退役）──
    recalibration_enabled: bool = field(default_factory=lambda: os.getenv("FTS_RECALIBRATION_ENABLED", "0") == "1")
    recalibration_coarse_trials: int = field(
        default_factory=lambda: int(os.getenv("FTS_RECALIBRATION_COARSE_TRIALS", "20"))
    )
    recalibration_min_ic_gap: float = field(
        default_factory=lambda: float(os.getenv("FTS_RECALIBRATION_MIN_IC_GAP", "0.0"))
    )

    # ── 期货换月复权与展期成本（v2.58.0，GAP-046）──
    # 期货连续合约 K 线是否默认返回换月后复权序列（因子计算用）
    futures_adjusted: bool = field(default_factory=lambda: os.getenv("FTS_FUTURES_ADJUSTED", "true").lower() == "true")
    # 展期成本系数（基点/次，回测持仓穿越换月日扣除）
    roll_cost_bps: float = field(default_factory=lambda: float(os.getenv("FTS_ROLL_COST_BPS", "2.0")))
    # 分钟缓存最大新鲜度（天，v2.101.0：独立于日线缓存窗口——生产日线 30 天窗口不适用于分钟，
    # 旧分钟缓存持续命中会挡住 TDX 实时分钟拉取；默认 1 天）
    minute_cache_max_age_days: int = field(default_factory=lambda: int(os.getenv("FTS_MINUTE_CACHE_MAX_AGE_DAYS", "1")))

    # ── 期货截面中性化 + 回测真实性仿真（v2.59.0，GAP-F03/F02）──
    # 期货横截面因子评估是否做板块/产业链中性化（剥离产业链系统性偏差）
    futures_neutralization: bool = field(
        default_factory=lambda: os.getenv("FTS_FUTURES_NEUTRALIZATION", "true").lower() == "true"
    )
    # 字段增强层（GAP-083 阶段 C）：启用 iFinD/Wind 增强源补充 hold/settle/pre_settle/oi_change。
    # 默认关闭——需配置 mcp_enabled=true + set_mcp_handler 注入客户端（API Key 认证）后开启。
    futures_enhance_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_FUTURES_ENHANCE_ENABLED", "false").lower() == "true"
    )
    # 天勤 TQSDK 源 opt-in（v3.0.0+2，GAP-159，plans/57 QuantData 主链路彻底解耦）：
    # 主链路为 QUANTDATA 时天勤增强源（TQSDKEnhanceSource）/分钟源（TQSDKSource 5m）/
    # tick 源（TQSDKTickSource）不再默认挂载（此前默认注册致感知链路逐品种自建天勤连接 +
    # 15s wait_update，L1 Meta-Loop 实测每品种 ~20s）；QuantData 下 hold 已为 L0 权威
    # （open_interest），天勤增强冗余。需要天勤 fallback/增强时显式设
    # FTS_TQSDK_SOURCES_ENABLED=true 恢复旧行为。
    tqsdk_sources_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_TQSDK_SOURCES_ENABLED", "false").lower() == "true"
    )
    # 回测是否启用涨跌停拦截 + 停牌过滤（真实成交仿真）
    backtest_trade_filter: bool = field(
        default_factory=lambda: os.getenv("FTS_BACKTEST_TRADE_FILTER", "true").lower() == "true"
    )
    # 期货涨跌停判定阈值（单日涨跌幅 ≥ 该值视为涨跌停，无法成交）
    futures_limit_pct: float = field(default_factory=lambda: float(os.getenv("FTS_FUTURES_LIMIT_PCT", "0.08")))

    # ── 横截面评估全矩阵化（plans/37，panel_vector）──
    # 用预对齐面板 + 全矩阵化 IC（fts/factor_engine/panel_vector.py）替代横截面
    # 评估的逐日 spearmanr 循环（信号构建恒逐品种）。Phase 3（v2.104.0+57）起
    # 默认开启：对照测试全绿 + 评估链 on/off 逐位一致。算子因子面板化执行
    # （execute_factor_panel）经 plans/39 §11（v2.104.0+58）实测真实缺口面板
    # 0.3x <5x 门槛登记豁免摘除——仅 IC 计算走矩阵化，信号恒逐品种零漂移。
    # 可设 FTS_CROSS_SECTION_PANEL_VECTOR=false 关闭。
    cross_section_panel_vector: bool = field(
        default_factory=lambda: os.getenv("FTS_CROSS_SECTION_PANEL_VECTOR", "true").lower() == "true"
    )

    # ── 算子 numba 内核（plans/38 批 4）──
    # 定点 @njit 清除含 NaN 多趟聚合 + 面板 2D 的 Python 循环（cvar_95/99、ts_rank、
    # ts_zscore，见 fts/factor_engine/numba_kernels.py）。仅 numba/llvmlite 安装后
    # 生效：缺失/版本冲突/FTS_OPS_NUMBA=false → 回退现值实现，零语义漂移。
    ops_numba: bool = field(default_factory=lambda: os.getenv("FTS_OPS_NUMBA", "true").lower() == "true")

    # ── L3 信号矩阵一等公民增量库（plans/40 D 层；plans/51 B1 激活）──
    # L3 组合重算经 l3_signal_service.load_or_build_signal_matrix 复用已入库
    # (factor, code, params) 信号（同窗口因子级增量复用，窗口推进经 A2 形状防护
    # 安全重算）。可设 FTS_L3_SIGNAL_STORE=false 关闭（回退纯全量构建，零漂移）。
    l3_signal_store_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L3_SIGNAL_STORE", "true").lower() == "true"
    )
    # L3 信号矩阵库路径（登记于 storage_landscape.yaml l3_signal_assets 域，plans/51 B2）
    l3_signal_store_db: str = field(
        default_factory=lambda: os.getenv("FTS_L3_SIGNAL_STORE_DB", "data/l3_signal_store.duckdb")
    )
    # L3 信号缓存容量上限（plans/40 A 层；plans/51 C2 配置化，原模块级常量 20000）
    l3_signal_cache_entries: int = field(
        default_factory=lambda: int(os.getenv("FTS_L3_SIGNAL_CACHE_ENTRIES", "20000"))
    )
    # L3 信号矩阵增量窗口追加（plans/52，GAP-139）：窗口推进时对可复用因子仅重算
    # 新增交易日 + 窗口回退段（抽样对照验证兜底，验证不过自动全量，零漂移）；
    # false 回退"同窗口因子级复用"现行为（跨日全量重算）。
    l3_signal_store_append_window: bool = field(
        default_factory=lambda: os.getenv("FTS_L3_SIGNAL_APPEND_WINDOW", "true").lower() == "true"
    )
    # L3/评估链 Regime 画像报告段（plans/53 §A2）：横截面评估时构建"因子×制度"画像
    # （regime_ic_report → 晋升 metadata 落库，供组合层条件化与晋升门槛消费）。
    # 默认关：RegimeSeriesBuilder 滚动检测有秒级成本，批量评估场景不默认开启；
    # 仅 energy 面板且显式开启时生效。可设 FTS_L3_REGIME_IC_REPORT=false 关闭。
    l3_regime_ic_report_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_L3_REGIME_IC_REPORT", "false").lower() == "true"
    )

    # ── 回测容量约束（v2.67.0，GAP-I501）──
    # 回测是否启用容量限制（持仓市值 ≤ 品种日均成交额 × 比例，超限截断）
    backtest_capacity_cap: bool = field(
        default_factory=lambda: os.getenv("FTS_BACKTEST_CAPACITY_CAP", "true").lower() == "true"
    )
    # 持仓市值 / 品种日均成交额 上限比例（默认 1% 日均成交额，机构立项前置容量分析）
    capacity_cap_ratio: float = field(default_factory=lambda: float(os.getenv("FTS_CAPACITY_CAP_RATIO", "0.01")))

    # ── 样本外强制 + 保证金建模（v2.60.0，GAP-F08/F09）──
    # 因子晋升路径是否强制 WalkForward 冷启动样本外验证（数据不足时跳过并记录原因）
    force_walkforward: bool = field(
        default_factory=lambda: os.getenv("FTS_FORCE_WALKFORWARD", "true").lower() == "true"
    )
    # 品种保证金率表（{symbol: 保证金率}，未配置品种用默认 0.10）
    margin_rate_map: dict = field(default_factory=dict)
    # 最大保证金占用率（保证金占用/总权益，超过触发强平风险告警）
    max_margin_usage: float = field(default_factory=lambda: float(os.getenv("FTS_MAX_MARGIN_USAGE", "0.80")))

    # ── 数据源降级加固（v2.60.0，GAP-F04）──
    # WIND/IFIND MCP 客户端是否启用（false=未启用，明确降级跳过增强字段；
    # true=启用，但未注入客户端时显式抛错提示初始化）
    mcp_enabled: bool = field(default_factory=lambda: os.getenv("FTS_MCP_ENABLED", "false").lower() == "true")

    # ── DuckDB 并发模型（v2.86.0，GAP-056，design/E.1）──
    # 单写者 + 读连接池：所有写收敛到唯一 writer，读走独立读池，读写互不阻塞
    duckdb_single_writer: bool = field(
        default_factory=lambda: os.getenv("FTS_DUCKDB_SINGLE_WRITER", "true").lower() == "true"
    )
    duckdb_read_pool_size: int = field(default_factory=lambda: int(os.getenv("FTS_DUCKDB_READ_POOL_SIZE", "4")))
    duckdb_batch_size: int = field(default_factory=lambda: int(os.getenv("FTS_DUCKDB_BATCH_SIZE", "1000")))
    duckdb_commit_every: int = field(default_factory=lambda: int(os.getenv("FTS_DUCKDB_COMMIT_EVERY", "100")))

    # ── L3 Verifier ──
    verifier: dict = field(
        default_factory=lambda: {
            "min_sharpe": 1.5,
            "max_correlation": 0.5,
            "max_turnover": 0.50,
            "max_decay_rate": 0.30,
            "min_n_factors": 3,
            "max_sharpe": 12.0,
        }
    )

    # ── L3 因子选择/组合构建（plans/36，v2.104.0+43）──
    # factor_score.weights: 综合评分权重（sharpe_cap/icir/ic/turnover_inv，替代裸 Sharpe 排序选入）
    # cluster.threshold/top_n: P1 因子聚类参数（阈值敏感性 / 簇内代表数）
    l3: dict = field(
        default_factory=lambda: {
            "factor_score": {
                "weights": {"sharpe_cap": 0.30, "icir": 0.30, "ic": 0.20, "turnover_inv": 0.20},
            },
            "cluster": {"threshold": 0.7, "top_n": 1},
        }
    )

    # ── Regime 画像护栏参数（plans/53 §A/§C，regime_profile.py 参数化 SSOT）──
    # min_regime_samples/min_abs_ic: 制度画像 effective 门槛
    # min_positive_regimes: 晋升门槛——有效制度数低于此值拒绝晋升（防单制度过拟合）
    regime_profile: dict = field(default_factory=dict)

    # ── 日志 ──
    log_level: str = field(default_factory=lambda: os.getenv("FTS_LOG_LEVEL", "INFO"))
    log_file: str = field(default_factory=lambda: os.getenv("FTS_LOG_FILE", ""))


# ─── 全局实例 ────────────────────────────────────────────

_default_config: Optional[FTSConfig] = None


def get_config() -> FTSConfig:
    """获取全局配置实例（延迟初始化）。"""
    global _default_config
    if _default_config is None:
        _default_config = load_config()
    return _default_config


def is_weight_recompute_day(cfg: Optional[FTSConfig] = None, today: Optional[Any] = None) -> bool:
    """判断今日是否应重算 L3 组合 / 信号管道权重（GAP-072，v2.99.0）。

    解绑 L3 与信号管道：信号管道每日生成信号（因子值每日刷新），
    权重（L3 Elastic Net / 信号管道 Ridge）仅在重算日学习，其余日复用快照。

    Args:
        cfg: 配置实例（None 用全局配置）
        today: 日期（None 用今天；测试可注入 `datetime.date` 实例）

    Returns:
        True=今日重算权重；False=今日冻结权重，复用上次快照
    """
    c = cfg or get_config()
    cadence = (c.l3_weight_recompute_cadence or "weekly").lower()
    if cadence == "daily":
        return True
    if cadence == "weekly":
        d = today or date.today()
        return d.weekday() == int(c.l3_weight_recompute_weekday)
    logger.warning("未知 l3_weight_recompute_cadence=%s，安全回退为每日重算", cadence)
    return True


def load_config(config_path: Optional[str] = None) -> FTSConfig:
    """加载配置（YAML + 环境变量覆盖）。

    Args:
        config_path: YAML 配置文件路径，None=自动查找 config/settings.yaml

    Returns:
        FTSConfig 实例
    """
    cfg = FTSConfig()

    # 尝试加载 YAML 文件
    if config_path is None:
        config_path = os.getenv("FTS_CONFIG_FILE", "")
    if not config_path:
        # 自动查找 config/settings.yaml
        default_config = Path("config/settings.yaml")
        if default_config.exists():
            config_path = str(default_config)
    if config_path:
        p = Path(config_path)
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                try:
                    import yaml  # type: ignore[import-untyped]

                    yaml_cfg = yaml.safe_load(text) or {}
                    _apply_dict(cfg, yaml_cfg)
                except ImportError:
                    import json

                    json_cfg = json.loads(text)
                    _apply_dict(cfg, json_cfg)
            except Exception:
                pass

    # 环境变量覆盖（FTS_* 前缀）
    _apply_env_overrides(cfg)

    return cfg


def _apply_dict(cfg: FTSConfig, d: dict[str, Any]) -> None:
    """将字典值应用到配置实例。"""
    for key, value in d.items():
        if hasattr(cfg, key) and value is not None:
            if key in ("verifier", "l3") and isinstance(value, dict):
                current = getattr(cfg, key, {})
                if isinstance(current, dict):
                    current.update(value)
                    setattr(cfg, key, current)
                else:
                    setattr(cfg, key, value)
            else:
                setattr(cfg, key, value)


def _apply_env_overrides(cfg: FTSConfig) -> None:
    """FTS_* 环境变量覆盖配置。"""
    for key in dir(cfg):
        if key.startswith("_"):
            continue
        env_key = f"FTS_{key.upper()}"
        env_val = os.getenv(env_key)
        if env_val is not None:
            current = getattr(cfg, key)
            if isinstance(current, bool):
                setattr(cfg, key, env_val.lower() in ("1", "true", "yes"))
            elif isinstance(current, int):
                setattr(cfg, key, int(env_val))
            elif isinstance(current, float):
                setattr(cfg, key, float(env_val))
            else:
                setattr(cfg, key, env_val)


EVOLUTION_MODES: tuple[str, ...] = ("operator", "operator_first", "code", "hybrid", "batch")


def validate_evolution_mode(mode: str) -> str:
    """校验演化模式合法性。"""
    if mode not in EVOLUTION_MODES:
        raise ValueError(f"evolution_mode 必须是 {EVOLUTION_MODES} 之一, 实际: {mode}")
    return mode


__all__ = [
    "FTSConfig",
    "get_config",
    "load_config",
    "DEFAULT_MEMORY_DIR",
    "DEFAULT_ELITE_DIR",
    "EVOLUTION_MODES",
    "validate_evolution_mode",
]
