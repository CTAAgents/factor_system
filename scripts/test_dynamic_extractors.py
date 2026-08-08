"""
测试动态提取器（方案 1）的完整流程。

验证:
1. ResearchReportExtractor 使用 LLM 提取因子
2. AcademicPaperExtractor 使用 arXiv + LLM 提取因子
3. 管道集成
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_dynamic_extractors")


def test_research_report_extractor():
    """测试 ResearchReportExtractor。"""
    from fts.llm import get_llm_client
    from fts.factor_engine.extractors.futures_pipeline import ResearchReportExtractor

    llm = get_llm_client()
    logger.info("LLM 客户端类型: %s", type(llm).__name__)

    ext = ResearchReportExtractor(llm_client=llm)
    candidates = ext.extract(trace_id="test_llm_001")
    logger.info("提取结果: %d 个候选", len(candidates))
    for c in candidates[:3]:
        name = c.get("name", "?")
        code_len = len(c.get("code", ""))
        source = c.get("source", "?")
        market = c.get("market", "?")
        logger.info("  - %s: code_len=%d, source=%s, market=%s", name, code_len, source, market)

    return candidates


def test_academic_paper_extractor():
    """测试 AcademicPaperExtractor。"""
    from fts.llm import get_llm_client
    from fts.factor_engine.extractors.futures_pipeline import AcademicPaperExtractor

    llm = get_llm_client()
    ext = AcademicPaperExtractor(llm_client=llm)
    candidates = ext.extract(trace_id="test_llm_002")
    logger.info("提取结果: %d 个候选", len(candidates))
    for c in candidates[:3]:
        name = c.get("name", "?")
        code_len = len(c.get("code", ""))
        source = c.get("source", "?")
        market = c.get("market", "?")
        logger.info("  - %s: code_len=%d, source=%s, market=%s", name, code_len, source, market)

    return candidates


def test_full_pipeline():
    """测试完整管道集成。"""
    from fts.llm import get_llm_client
    from fts.factor_engine.extractors import FuturesExtractorPipeline

    llm = get_llm_client()
    pipe = FuturesExtractorPipeline(llm_client=llm)
    logger.info("管道提取器:")
    for name, ext in pipe.extractors.items():
        logger.info("  - %s: %s, paused=%s", name, type(ext).__name__, ext.paused)

    candidates = pipe.extract(trace_id="test_llm_pipe_001")
    logger.info("管道提取结果: %d 个候选", len(candidates))
    for c in candidates[:5]:
        name = c.get("name", "?")
        source = c.get("source", "?")
        logger.info("  - %s: source=%s, code_len=%d", name, source, len(c.get("code", "")))

    return candidates


if __name__ == "__main__":
    print("=" * 60)
    print("测试 1: ResearchReportExtractor")
    print("=" * 60)
    r1 = test_research_report_extractor()
    print()

    print("=" * 60)
    print("测试 2: AcademicPaperExtractor")
    print("=" * 60)
    r2 = test_academic_paper_extractor()
    print()

    print("=" * 60)
    print("测试 3: 完整管道集成")
    print("=" * 60)
    r3 = test_full_pipeline()
    print()

    print("=" * 60)
    print("汇总:")
    print(f"  ResearchReportExtractor: {len(r1)} 个候选")
    print(f"  AcademicPaperExtractor:  {len(r2)} 个候选")
    print(f"  完整管道:               {len(r3)} 个候选")

    total = len(r1) + len(r2) + len(r3)
    if total > 0:
        print(f"\n  ✅ 动态提取器正常工作，共产生 {total} 个候选因子")
    else:
        print(f"\n  ⚠️  未产生候选因子（可能 LLM 客户端无响应）")