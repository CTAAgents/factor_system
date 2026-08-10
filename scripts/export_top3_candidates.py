"""
提取经济逻辑评分最高的前 3 个候选因子，导出代码详情。
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

from fts.llm import get_llm_client
from fts.factor_engine.extractors.futures_pipeline import (
    ResearchReportExtractor,
    AcademicPaperExtractor,
)


def calc_total_score(candidate):
    el = candidate.get("economic_logic", {})
    return el.get("theory", 0) + el.get("behavioral", 0) + el.get("microstructure", 0) + el.get("institutional", 0)


def main():
    llm = get_llm_client()

    # 收集所有候选
    all_candidates = []

    ext1 = ResearchReportExtractor(llm_client=llm)
    c1 = ext1.extract(trace_id="export_001")
    all_candidates.extend(c1)

    ext2 = AcademicPaperExtractor(llm_client=llm)
    c2 = ext2.extract(trace_id="export_002")
    all_candidates.extend(c2)

    # 按总分排序，取前 3
    ranked = sorted(all_candidates, key=calc_total_score, reverse=True)
    top3 = ranked[:3]

    print("=" * 70)
    print("经济逻辑评分 TOP 3 候选因子")
    print("=" * 70)

    output_dir = Path("output/top3_candidates")
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, c in enumerate(top3):
        name = c.get("name", "unknown")
        score = calc_total_score(c)
        el = c.get("economic_logic", {})
        source = c.get("source", "?")

        print(f"\n{'─' * 70}")
        print(f"第 {i + 1} 名: {name}  (总分: {score})")
        print(f"  来源: {source}")
        print(
            f"  评分: theory={el.get('theory')}, behavioral={el.get('behavioral')}, "
            f"microstructure={el.get('microstructure')}, institutional={el.get('institutional')}"
        )
        print(f"  narrative: {el.get('narrative', '')}")
        print(f"  params: {json.dumps(c.get('params', {}), ensure_ascii=False)}")
        sig = c.get("signature", {})
        print(f"  input_fields: {sig.get('input_fields')}")
        print(f"  lookback: {sig.get('lookback')}")
        print(f"  code:\n{c.get('code', '')}")

        # 导出到文件
        export = {
            "rank": i + 1,
            "name": name,
            "total_score": score,
            "source": source,
            "economic_logic": el,
            "params": c.get("params", {}),
            "signature": sig,
            "code": c.get("code", ""),
        }
        filepath = output_dir / f"{i + 1:02d}_{name}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)
        print(f"  → 已导出: {filepath}")

    print(f"\n{'─' * 70}")
    print(f"全部导出至: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
