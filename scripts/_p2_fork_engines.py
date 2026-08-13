"""scripts/_p2_fork_engines.py — P2 引擎分叉：折叠 evolution_futures/evolution_stock 的 market 分支。

F.2 引擎分叉 Phase 1/2: 对 evolution_loop.py 的复制品执行确定性折叠，
各自只保留对应市场行为。每个替换点均断言存在，防止静默失败。

用法:
    python scripts/_p2_fork_engines.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent / "fts" / "factor_engine"
FUT = ENGINE_DIR / "evolution_futures.py"
STK = ENGINE_DIR / "evolution_stock.py"


def apply(source: Path, pairs: list[tuple[str, str]]) -> None:
    text = source.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in text:
            print(f"[FAIL] {source.name}: 未找到替换点 →\n{old[:120]}...")
            sys.exit(1)
        text = text.replace(old, new, 1)
    source.write_text(text, encoding="utf-8")
    print(f"[OK] {source.name} 折叠完成")


# ── evolution_futures.py: 固定期货市场，删股票分支 ──
futures_pairs = [
    # 1. 演化模式: 固定 futures，删除 stock operator_first 分支
    (
        """        if market is None:
            from fts.config.settings import get_config

            market = get_config().default_market
        self.market = market
        # GAP-S11 (v2.67.0): 演化模式解析——股票演化默认 operator-first
        # （算子演化优先，LLM/GP 兜底），期货保持原配置行为。
        from fts.config.settings import get_config

        _raw_mode = getattr(get_config(), "evolution_mode", "hybrid")
        self.evolution_mode = _raw_mode
        if market == "stock" and _raw_mode == "hybrid":
            self.evolution_mode = "operator_first"
            logger.info("[EvolutionLoop] 股票演化默认 operator-first: 算子演化优先，LLM/GP 兜底")
        self.factor_db_path = factor_db_path""",
        """        # F.2 引擎分叉: 本文件固定期货市场，market 入参忽略
        market = "futures"
        self.market = market
        # GAP-S11 (v2.67.0): 期货演化保持原配置行为（不启用股票 operator-first）
        from fts.config.settings import get_config

        _raw_mode = getattr(get_config(), "evolution_mode", "hybrid")
        self.evolution_mode = _raw_mode
        self.factor_db_path = factor_db_path""",
    ),
    # 2. 中性化注入: 期货板块注入保留（market 恒真），删除股票行业/市值注入块
    (
        """        if self._is_cross_section and market == "futures" and self.industry_map is None:
            try:
                from fts.config.settings import get_config

                if get_config().futures_neutralization:
                    from fts.data_futures import FUTURES_SECTOR_MAP

                    self.industry_map = {
                        sym: sector for sector, symbols in FUTURES_SECTOR_MAP.items() for sym in symbols
                    }
                    logger.info(
                        "[EvolutionLoop] 期货板块中性化已启用: %d 品种映射到 %d 个产业链",
                        len(self.industry_map),
                        len(FUTURES_SECTOR_MAP),
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvolutionLoop] 期货板块映射注入失败，跳过中性化: %s", e)

        # v2.61.0 (GAP-S01): 股票横截面模式自动注入行业/市值映射（行业/市值中性化）
        # 读取 FTSConfig.stock_neutralization（默认 true，v2.57.0 遗留死配置，本版本接通）；
        # industry_map 已显式传入时跳过；键归一化：映射键 "600519.SH"/"600519.SZ" 剥离后缀
        # 生成裸代码键（面板 symbol 为裸代码 "600519"），同时保留原始键兼容两种格式。
        if self._is_cross_section and market == "stock" and self.industry_map is None:
            try:
                from fts.config.settings import get_config, load_cap_map, load_industry_map

                if get_config().stock_neutralization:
                    raw_industry = load_industry_map()
                    if raw_industry:
                        self.industry_map = _normalize_industry_keys(raw_industry)
                        logger.info(
                            "[EvolutionLoop] 股票行业中性化已启用: %d 条映射（归一化后 %d 键）",
                            len(raw_industry),
                            len(self.industry_map),
                        )
                    # 市值映射（cap_map_path 配置，缺失/为空返回空 dict → 仅行业去均值）
                    raw_cap = load_cap_map()
                    if raw_cap:
                        self.cap_map = _normalize_industry_keys(raw_cap)
                        logger.info(
                            "[EvolutionLoop] 股票市值中性化已启用: %d 条映射",
                            len(self.cap_map),
                        )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvolutionLoop] 股票行业/市值映射注入失败，跳过中性化: %s", e)""",
        """        if self._is_cross_section and self.industry_map is None:
            try:
                from fts.config.settings import get_config

                if get_config().futures_neutralization:
                    from fts.data_futures import FUTURES_SECTOR_MAP

                    self.industry_map = {
                        sym: sector for sector, symbols in FUTURES_SECTOR_MAP.items() for sym in symbols
                    }
                    logger.info(
                        "[EvolutionLoop] 期货板块中性化已启用: %d 品种映射到 %d 个产业链",
                        len(self.industry_map),
                        len(FUTURES_SECTOR_MAP),
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvolutionLoop] 期货板块映射注入失败，跳过中性化: %s", e)""",
    ),
    # 3. elite 目录: 恒 futures_elite
    (
        """        if elite_dir is None:
            if market == "futures":
                elite_dir = "memory/knowledge/factors/futures_elite"
            else:
                elite_dir = "memory/knowledge/factors/stocks_elite"
        self.elite_dir = Path(elite_dir)""",
        """        if elite_dir is None:
            elite_dir = "memory/knowledge/factors/futures_elite"
        self.elite_dir = Path(elite_dir)""",
    ),
    # 4. verifier: 恒 FUTURES_VERIFIER_CONFIG
    (
        """        if verifier is not None:
            self.verifier = verifier
        elif market == "futures":
            from .contracts import FUTURES_VERIFIER_CONFIG

            self.verifier = FactorVerifier(FUTURES_VERIFIER_CONFIG)
        else:
            self.verifier = get_global_verifier()""",
        """        if verifier is not None:
            self.verifier = verifier
        else:
            from .contracts import FUTURES_VERIFIER_CONFIG

            self.verifier = FactorVerifier(FUTURES_VERIFIER_CONFIG)""",
    ),
    # 5. high_ic_screener: 恒期货放宽配置
    (
        """        if market == "futures":
            # 期货市场放宽 V5 经济逻辑维度最低分（LLM 演化因子 L2 评分偏低）
            futures_config = HighICScreenConfig(logic_min_score=1.0)
            self.high_ic_screener = HighICScreener(config=futures_config)
        else:
            self.high_ic_screener = HighICScreener()""",
        """        # 期货市场放宽 V5 经济逻辑维度最低分（LLM 演化因子 L2 评分偏低）
        futures_config = HighICScreenConfig(logic_min_score=1.0)
        self.high_ic_screener = HighICScreener(config=futures_config)""",
    ),
    # 6. long_only: 恒 False（多空双向）
    (
        """            long_only=(self.market in ("stock", "etf")),""",
        """            long_only=False,""",
    ),
    # 7. IC 阈值（两处）: 恒 0.01
    (
        """        ic_threshold = 0.01 if self.market == "futures" else 0.02""",
        """        ic_threshold = 0.01""",
    ),
    (
        """        ic_abs = abs(float(np.mean(ics)))
        ic_threshold = 0.01 if self.market == "futures" else 0.02""",
        """        ic_abs = abs(float(np.mean(ics)))
        ic_threshold = 0.01""",
    ),
    # 8. min_pass_rate: 恒 0.7
    (
        """            min_pass_rate = 0.7 if getattr(self, "market", "stock") == "futures" else 0.9""",
        """            min_pass_rate = 0.7""",
    ),
]

# ── evolution_stock.py: 固定股票市场，删期货分支 ──
stock_pairs = [
    # 1. 演化模式: 固定 stock，保留 operator_first
    (
        """        if market is None:
            from fts.config.settings import get_config

            market = get_config().default_market
        self.market = market
        # GAP-S11 (v2.67.0): 演化模式解析——股票演化默认 operator-first
        # （算子演化优先，LLM/GP 兜底），期货保持原配置行为。
        from fts.config.settings import get_config

        _raw_mode = getattr(get_config(), "evolution_mode", "hybrid")
        self.evolution_mode = _raw_mode
        if market == "stock" and _raw_mode == "hybrid":
            self.evolution_mode = "operator_first"
            logger.info("[EvolutionLoop] 股票演化默认 operator-first: 算子演化优先，LLM/GP 兜底")
        self.factor_db_path = factor_db_path""",
        """        # F.2 引擎分叉: 本文件固定股票市场，market 入参忽略
        market = "stock"
        self.market = market
        # GAP-S11 (v2.67.0): 股票演化默认 operator-first（算子演化优先，LLM/GP 兜底）
        from fts.config.settings import get_config

        _raw_mode = getattr(get_config(), "evolution_mode", "hybrid")
        self.evolution_mode = _raw_mode
        if _raw_mode == "hybrid":
            self.evolution_mode = "operator_first"
            logger.info("[EvolutionLoop] 股票演化默认 operator-first: 算子演化优先，LLM/GP 兜底")
        self.factor_db_path = factor_db_path""",
    ),
    # 2. 中性化注入: 股票行业/市值注入保留（market 恒真），删除期货板块注入块
    (
        """        if self._is_cross_section and market == "futures" and self.industry_map is None:
            try:
                from fts.config.settings import get_config

                if get_config().futures_neutralization:
                    from fts.data_futures import FUTURES_SECTOR_MAP

                    self.industry_map = {
                        sym: sector for sector, symbols in FUTURES_SECTOR_MAP.items() for sym in symbols
                    }
                    logger.info(
                        "[EvolutionLoop] 期货板块中性化已启用: %d 品种映射到 %d 个产业链",
                        len(self.industry_map),
                        len(FUTURES_SECTOR_MAP),
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvolutionLoop] 期货板块映射注入失败，跳过中性化: %s", e)

        # v2.61.0 (GAP-S01): 股票横截面模式自动注入行业/市值映射（行业/市值中性化）
        # 读取 FTSConfig.stock_neutralization（默认 true，v2.57.0 遗留死配置，本版本接通）；
        # industry_map 已显式传入时跳过；键归一化：映射键 "600519.SH"/"600519.SZ" 剥离后缀
        # 生成裸代码键（面板 symbol 为裸代码 "600519"），同时保留原始键兼容两种格式。
        if self._is_cross_section and market == "stock" and self.industry_map is None:
            try:
                from fts.config.settings import get_config, load_cap_map, load_industry_map

                if get_config().stock_neutralization:
                    raw_industry = load_industry_map()
                    if raw_industry:
                        self.industry_map = _normalize_industry_keys(raw_industry)
                        logger.info(
                            "[EvolutionLoop] 股票行业中性化已启用: %d 条映射（归一化后 %d 键）",
                            len(raw_industry),
                            len(self.industry_map),
                        )
                    # 市值映射（cap_map_path 配置，缺失/为空返回空 dict → 仅行业去均值）
                    raw_cap = load_cap_map()
                    if raw_cap:
                        self.cap_map = _normalize_industry_keys(raw_cap)
                        logger.info(
                            "[EvolutionLoop] 股票市值中性化已启用: %d 条映射",
                            len(self.cap_map),
                        )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvolutionLoop] 股票行业/市值映射注入失败，跳过中性化: %s", e)""",
        """        # v2.61.0 (GAP-S01): 股票横截面模式自动注入行业/市值映射（行业/市值中性化）
        # 读取 FTSConfig.stock_neutralization（默认 true，v2.57.0 遗留死配置，本版本接通）；
        # industry_map 已显式传入时跳过；键归一化：映射键 "600519.SH"/"600519.SZ" 剥离后缀
        # 生成裸代码键（面板 symbol 为裸代码 "600519"），同时保留原始键兼容两种格式。
        if self._is_cross_section and self.industry_map is None:
            try:
                from fts.config.settings import get_config, load_cap_map, load_industry_map

                if get_config().stock_neutralization:
                    raw_industry = load_industry_map()
                    if raw_industry:
                        self.industry_map = _normalize_industry_keys(raw_industry)
                        logger.info(
                            "[EvolutionLoop] 股票行业中性化已启用: %d 条映射（归一化后 %d 键）",
                            len(raw_industry),
                            len(self.industry_map),
                        )
                    # 市值映射（cap_map_path 配置，缺失/为空返回空 dict → 仅行业去均值）
                    raw_cap = load_cap_map()
                    if raw_cap:
                        self.cap_map = _normalize_industry_keys(raw_cap)
                        logger.info(
                            "[EvolutionLoop] 股票市值中性化已启用: %d 条映射",
                            len(self.cap_map),
                        )
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvolutionLoop] 股票行业/市值映射注入失败，跳过中性化: %s", e)""",
    ),
    # 3. elite 目录: 恒 stocks_elite
    (
        """        if elite_dir is None:
            if market == "futures":
                elite_dir = "memory/knowledge/factors/futures_elite"
            else:
                elite_dir = "memory/knowledge/factors/stocks_elite"
        self.elite_dir = Path(elite_dir)""",
        """        if elite_dir is None:
            elite_dir = "memory/knowledge/factors/stocks_elite"
        self.elite_dir = Path(elite_dir)""",
    ),
    # 4. verifier: 恒全局 verifier
    (
        """        if verifier is not None:
            self.verifier = verifier
        elif market == "futures":
            from .contracts import FUTURES_VERIFIER_CONFIG

            self.verifier = FactorVerifier(FUTURES_VERIFIER_CONFIG)
        else:
            self.verifier = get_global_verifier()""",
        """        if verifier is not None:
            self.verifier = verifier
        else:
            self.verifier = get_global_verifier()""",
    ),
    # 5. high_ic_screener: 恒默认
    (
        """        if market == "futures":
            # 期货市场放宽 V5 经济逻辑维度最低分（LLM 演化因子 L2 评分偏低）
            futures_config = HighICScreenConfig(logic_min_score=1.0)
            self.high_ic_screener = HighICScreener(config=futures_config)
        else:
            self.high_ic_screener = HighICScreener()""",
        """        self.high_ic_screener = HighICScreener()""",
    ),
    # 6. long_only: 恒 True（仅做多）
    (
        """            long_only=(self.market in ("stock", "etf")),""",
        """            long_only=True,""",
    ),
    # 7. IC 阈值（两处）: 恒 0.02
    (
        """        ic_threshold = 0.01 if self.market == "futures" else 0.02""",
        """        ic_threshold = 0.02""",
    ),
    (
        """        ic_abs = abs(float(np.mean(ics)))
        ic_threshold = 0.01 if self.market == "futures" else 0.02""",
        """        ic_abs = abs(float(np.mean(ics)))
        ic_threshold = 0.02""",
    ),
    # 8. min_pass_rate: 恒 0.9
    (
        """            min_pass_rate = 0.7 if getattr(self, "market", "stock") == "futures" else 0.9""",
        """            min_pass_rate = 0.9""",
    ),
]


def main() -> int:
    # 重置为 evolution_loop.py 的干净副本（脚本幂等，可重复执行）
    template = ENGINE_DIR / "evolution_loop.py"
    template_text = template.read_text(encoding="utf-8")
    FUT.write_text(template_text, encoding="utf-8")
    STK.write_text(template_text, encoding="utf-8")

    apply(FUT, futures_pairs)
    apply(STK, stock_pairs)
    print("\n[P2] 引擎分叉折叠完成（evolution_futures / evolution_stock）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
