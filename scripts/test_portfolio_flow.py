"""模拟运行 portfolio_loop 主流程，验证 L2→L3 数据流日志"""
import logging
import json
import tempfile
import hashlib
from pathlib import Path

# 设置日志级别和格式
logging.basicConfig(level=logging.INFO, format='%(message)s')

# 模拟 PortfolioLoop 完整流程
print("=" * 70)
print("模拟运行 PortfolioLoop 主流程")
print("=" * 70)

# 创建临时目录模拟 elite 因子池
with tempfile.TemporaryDirectory() as tmpdir:
    elite_dir = Path(tmpdir)
    
    # ── 步骤 1: 模拟 L2 写入索引 ──
    print("\n【步骤 1】模拟 L2 写入相关性索引")
    print("-" * 50)
    
    l2_index = {
        "source": "l2_seed_correlation_check",
        "trace_id": "test_trace_001",
        "created_at": "2026-08-05T10:00:00",
        "threshold": 0.95,
        "total_pairs": 2,
        "correlations": [
            {
                "factor_id_a": "fut_factor_001",
                "factor_id_b": "fut_factor_002",
                "pearson": 0.9828,
                "spearman": 0.9750,
            },
            {
                "factor_id_a": "fut_factor_003",
                "factor_id_b": "fut_factor_004",
                "pearson": 0.9612,
                "spearman": 0.9580,
            },
        ],
    }
    index_path = elite_dir / "_l2_seed_correlation_index.json"
    index_path.write_text(json.dumps(l2_index, ensure_ascii=False, indent=2))
    print(f"✓ 写入 L2 索引: {index_path}")
    print(f"  - 高相关因子对数量: {len(l2_index['correlations'])}")
    for pair in l2_index['correlations']:
        max_abs = max(abs(pair['pearson']), abs(pair['spearman']))
        print(f"  - {pair['factor_id_a']} × {pair['factor_id_b']}: max_corr={max_abs:.4f}")
    
    # ── 步骤 2: 模拟 L2 写入 elite 因子的 correlation_metadata ──
    print("\n【步骤 2】模拟 L2 写入 elite 因子元数据")
    print("-" * 50)
    
    factor_configs = [
        ("fut_factor_001", "Trade Imbalance", 2.5, 0.9828, "fut_factor_002"),
        ("fut_factor_002", "Order Flow Imbalance", 2.3, 0.9828, "fut_factor_001"),
        ("fut_factor_003", "PCR Ratio", 1.9, 0.9612, "fut_factor_004"),
        ("fut_factor_004", "Put Call Ratio", 2.0, 0.9612, "fut_factor_003"),
        ("fut_factor_005", "Term Structure", 1.8, 0.0, None),
        ("fut_factor_006", "Volatility Skew", 1.5, 0.0, None),
        ("fut_factor_007", "Momentum Factor", 2.2, 0.0, None),
        ("fut_factor_008", "Mean Reversion", 1.6, 0.0, None),
    ]
    
    for fid, name, sharpe, max_corr, partner in factor_configs:
        data = {
            "factor_id": fid,
            "name": name,
            "code": f"factor_code_{fid}",
            "evaluation": {
                "level_1_backtest": {
                    "sharpe": sharpe,
                    "ic": 0.03 + sharpe * 0.01,
                    "turnover_monthly": 0.3,
                }
            },
        }
        
        # 为有关联的因子添加 correlation_metadata
        if partner:
            data["correlation_metadata"] = {
                "l2_seed_flags": [{
                    "partner_factor_id": partner,
                    "pearson": max_corr,
                    "spearman": max_corr * 0.99,
                    "max_abs": max_corr,
                    "source": "l2_seed_correlation_check",
                }],
                "flag_count": 1,
                "max_corr_detected": max_corr,
            }
        
        (elite_dir / f"{fid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        code_hash = hashlib.sha256(f"factor_code_{fid}".encode()).hexdigest()[:8]
        meta_str = f", correlation_metadata 已写入" if partner else ""
        print(f"✓ {fid}: {name} (Sharpe={sharpe}, code_hash={code_hash}{meta_str})")
    
    # ── 步骤 3: 模拟 L3 加载 elite 因子 ──
    print("\n【步骤 3】模拟 L3 加载 elite 因子")
    print("-" * 50)
    
    from fts.factor_engine.portfolio_loop import load_elite_factors, load_l2_correlation_index
    
    factors = load_elite_factors(elite_dir)
    print(f"✓ 加载精英因子: {len(factors)} 个")
    for f in factors:
        corr_meta = f.get("correlation_metadata", {})
        l2_flags = corr_meta.get("l2_seed_flags", [])
        flag_info = f", L2 标记 {len(l2_flags)} 个高相关对" if l2_flags else ""
        print(f"  - {f['name']} (id={f['factor_id']}, sharpe={f['sharpe']:.2f}{flag_info})")
    
    # 加载 L2 索引
    l2_prior = load_l2_correlation_index(elite_dir)
    print(f"\n✓ 加载 L2 相关性索引: {len(l2_prior)} 对高相关因子")
    
    # ── 步骤 4: 模拟 L3 正交化（无分层模式）──
    print("\n【步骤 4】模拟 L3 正交化（无分层模式）")
    print("-" * 50)
    
    from fts.factor_engine.portfolio_loop import orthogonalize_factors
    
    # 创建模拟信号
    signals = [
        {
            "factor_id": f["factor_id"],
            "sharpe": f["sharpe"],
            "weight": 1.0 / len(factors),
            "retained": True,
            "name": f["name"],
        }
        for f in factors
    ]
    
    result = orthogonalize_factors(
        signals, max_corr_threshold=0.7, factors=factors,
        use_tiered=False, l2_prior_correlations=l2_prior,
    )
    
    print(f"\n✓ 正交化完成: {len(result)} 个信号")
    for s in result:
        flags = s.get("correlation_flags", [])
        l2_flags = [f for f in flags if f.get("type") == "l2_seed_correlation"]
        flag_info = f", {len(l2_flags)} 个 L2 标记" if l2_flags else ""
        print(f"  - {s['name']}: retained={s.get('retained', True)}{flag_info}")
        for flag in l2_flags:
            print(f"    ⚠ {flag['reason']}")
    
    # ── 步骤 5: 模拟 L3 正交化（分层模式 — 触发 Phase 2）──
    print("\n【步骤 5】模拟 L3 正交化（分层模式 — 模拟 35+ 因子触发 Phase 2）")
    print("-" * 50)
    
    from fts.factor_engine.factor_optimizer import FactorOptimizer
    
    # 模拟 35 个因子（触发 use_tiered=True 且 Phase 2 执行）
    test_factors = []
    for i in range(35):
        fid = f"factor_{i+1:03d}"
        test_factors.append({
            "factor_id": fid,
            "name": f"Factor {i+1}",
            "sharpe": 1.5 + (i % 10) * 0.1,
            "code_hash": hashlib.sha256(f"code_{i:03d}".encode()).hexdigest()[:8],
        })
    
    # 添加 L2 先验（其中 2 对在 test_factors 中存在）
    test_l2_prior = [
        {"factor_id_a": "factor_001", "factor_id_b": "factor_002", "pearson": 0.98, "spearman": 0.97},
        {"factor_id_a": "factor_005", "factor_id_b": "factor_006", "pearson": 0.96, "spearman": 0.95},
        {"factor_id_a": "factor_099", "factor_id_b": "factor_100", "pearson": 0.97, "spearman": 0.96},  # 不存在于 test_factors
    ]
    
    print(f"输入因子数: {len(test_factors)}")
    print(f"L2 先验对数: {len(test_l2_prior)}")
    print(f"  - factor_001 × factor_002 (存在于因子池中)")
    print(f"  - factor_005 × factor_006 (存在于因子池中)")
    print(f"  - factor_099 × factor_100 (不存在于因子池中)")
    print()
    
    optimizer = FactorOptimizer()
    result_factors, summary = optimizer.tiered_orthogonalize(
        test_factors, max_corr_threshold=0.7, mode="mark",
        l2_prior_correlations=test_l2_prior,
    )
    
    print("\n=== Summary ===")
    print(f"输入因子数: {summary.get('input_count', 0)}")
    print(f"输出因子数: {summary.get('output_count', 0)}")
    print(f"L2 先验标记数: {summary.get('l2_prior_count', 0)}")
    print(f"Phase 1 标记数: {summary.get('phase1_marked', 0)}")
    print(f"Phase 2 标记数: {summary.get('phase2_marked', 0)}")
    print(f"Phase 2 新增: {summary.get('phase2_new_count', 'N/A')}")
    print(f"Phase 2 与 L2 重叠: {summary.get('phase2_overlap_count', 'N/A')}")
    print(f"耗时: {summary.get('elapsed_seconds', 0):.4f}s")
    
    # 查看每个因子的标记详情
    print("\n=== 因子标记详情 ===")
    for f in result_factors:
        flags = f.get("correlation_flags", [])
        if flags:
            sources = {}
            for flag in flags:
                src = flag.get("source", "unknown")
                sources[src] = sources.get(src, 0) + 1
            flag_summary = ", ".join(f"{k}={v}" for k, v in sources.items())
            print(f"✓ {f['name']}: {len(flags)} 个标记 ({flag_summary})")
            for flag in flags:
                print(f"    [{flag.get('source', '?')}] {flag['reason'][:80]}...")
        else:
            print(f"  {f['name']}: 无标记")

print("\n" + "=" * 70)
print("模拟运行完成")
print("=" * 70)
