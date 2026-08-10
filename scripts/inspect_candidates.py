"""
检查动态提取器生成的候选因子详情，包括经济逻辑评分。
"""

import json
import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("inspect")

from fts.llm import get_llm_client
from fts.factor_engine.extractors.futures_pipeline import (
    ResearchReportExtractor,
    AcademicPaperExtractor,
)


def print_candidates(candidates, title):
    print("=" * 70)
    print(f"【{title}】共 {len(candidates)} 个候选")
    print("=" * 70)
    for i, c in enumerate(candidates):
        print(f"\n--- 候选 {i + 1}: {c.get('name')} ---")
        print(f"  来源: {c.get('source')}")
        print(f"  市场: {c.get('market')}")
        el = c.get("economic_logic", {})
        print(
            f"  经济逻辑评分: theory={el.get('theory')}, "
            f"behavioral={el.get('behavioral')}, "
            f"microstructure={el.get('microstructure')}, "
            f"institutional={el.get('institutional')}"
        )
        print(f"  narrative: {str(el.get('narrative', ''))[:100]}")
        print(f"  代码长度: {len(c.get('code', ''))} 字符")
        print(f"  参数: {json.dumps(c.get('params', {}), ensure_ascii=False)}")
        sig = c.get("signature", {})
        print(f"  签名: input_fields={sig.get('input_fields')}, lookback={sig.get('lookback')}")


def main():
    llm = get_llm_client()

    # 研报提取器
    ext1 = ResearchReportExtractor(llm_client=llm)
    c1 = ext1.extract(trace_id="inspect_001")
    print_candidates(c1, "源 2: 券商研报动态提取")

    # 论文提取器
    ext2 = AcademicPaperExtractor(llm_client=llm)
    c2 = ext2.extract(trace_id="inspect_002")
    print_candidates(c2, "源 3: 学术论文动态提取")

    # 汇总
    print("\n" + "=" * 70)
    print(f"汇总: 研报 {len(c1)} 个 + 论文 {len(c2)} 个 = {len(c1) + len(c2)} 个候选")
    print("=" * 70)

    # 统计经济逻辑评分有值的个数
    with_scores = sum(
        1
        for c in c1 + c2
        if isinstance(c.get("economic_logic", {}), dict)
        and any(v is not None for v in c["economic_logic"].values() if isinstance(v, (int, float)))
    )
    print(f"含经济逻辑评分的候选: {with_scores}/{len(c1) + len(c2)}")


if __name__ == "__main__":
    main()
