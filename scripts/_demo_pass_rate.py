"""
演示脚本: 因子质量评分卡通过率模拟
"""

from fts.factor_engine.factor_quality_card import FactorQualityCard

print('=== 因子质量评分卡通过率模拟 ===')
print()

# 创建评分卡
card = FactorQualityCard()

# 模拟不同质量水平的因子 (提供完整参数)
# (因子名, IC, ICIR, Sharpe, Calmar, decay, turnover, corr, logic, freq, coverage, capacity)
test_cases = [
    ('顶级因子 (A 级)', 0.10, 4.0, 4.0, 2.5, 0.05, 0.2, 0.15, 5, 'daily', 0.95, 200_000_000),
    ('优秀因子 (A 级)', 0.08, 3.0, 3.5, 2.0, 0.08, 0.25, 0.25, 5, 'daily', 0.90, 100_000_000),
    ('良好因子 (B 级)', 0.05, 2.0, 2.5, 1.2, 0.15, 0.3, 0.4, 4, 'daily', 0.75, 50_000_000),
    ('中等因子 (B 级)', 0.04, 1.5, 1.8, 0.8, 0.25, 0.35, 0.5, 3, 'daily', 0.65, 25_000_000),
    ('一般因子 (C 级)', 0.02, 0.8, 1.0, 0.4, 0.4, 0.45, 0.65, 2, 'daily', 0.45, 10_000_000),
    ('较差因子 (C 级)', 0.01, 0.5, 0.5, 0.2, 0.55, 0.03, 0.75, 1, 'daily', 0.3, 5_000_000),
    ('很差因子 (C 级)', 0.005, 0.2, 0.2, 0.1, 0.8, 0.02, 0.9, 1, 'daily', 0.15, 1_000),
]

print('【默认配置: A/B 级通过，C 级淘汰】')
header = f'{"因子":<25} {"总分":>6} {"等级":>4} {"通过?":>6}'
print(header)
print('-' * 50)

passed_count = 0
for name, ic, icir, sharpe, calmar, decay, turnover, corr, logic, freq, coverage, cap in test_cases:
    score = card.evaluate(
        factor_id='test',
        ic=ic, icir=icir,
        sharpe=sharpe, calmar=calmar,
        decay_rate=decay, turnover=turnover,
        correlation_max=corr, logic_score=logic,
        data_frequency=freq,
        cross_symbol_coverage=coverage, capacity_estimate=cap,
    )
    total = score['total_score']
    grade = score['grade']
    passed = grade in ('A', 'B')
    if passed:
        passed_count += 1
    status = 'PASS' if passed else 'FAIL'
    print(f'{name:<25} {total:>6.1f} {grade:>4} {status:>6}')

print()
rate = passed_count / len(test_cases) * 100
print(f'通过率: {passed_count}/{len(test_cases)} = {rate:.0f}%')

# 对比不同阈值配置
print()
print('【不同阈值配置下的通过率对比】')
print()

configs = [
    ('严格 (A>=42, B>=32)', {'grade_A_threshold': 42.0, 'grade_B_min': 32.0}),
    ('默认 (A>=40, B>=30)', {'grade_A_threshold': 40.0, 'grade_B_min': 30.0}),
    ('宽松 (A>=38, B>=28)', {'grade_A_threshold': 38.0, 'grade_B_min': 28.0}),
]

for config_name, cfg in configs:
    card_config = {'grade_A_threshold': cfg['grade_A_threshold'], 'grade_B_min': cfg['grade_B_min']}
    test_card = FactorQualityCard(card_config)
    passed = 0
    for _, ic, icir, sharpe, calmar, decay, turnover, corr, logic, freq, coverage, cap in test_cases:
        score = test_card.evaluate(
            factor_id='test', ic=ic, icir=icir,
            sharpe=sharpe, calmar=calmar,
            decay_rate=decay, turnover=turnover,
            correlation_max=corr, logic_score=logic,
            data_frequency=freq,
            cross_symbol_coverage=coverage, capacity_estimate=cap,
        )
        if score['grade'] in ('A', 'B'):
            passed += 1
    rate = passed / len(test_cases) * 100
    print(f'  {config_name}: {passed}/{len(test_cases)} = {rate:.0f}% 通过率')

# 显示最佳因子详细维度分数
print()
print('【顶级因子详细维度分数】')
best = test_cases[0]
score = card.evaluate(
    factor_id='best',
    ic=best[1], icir=best[2],
    sharpe=best[3], calmar=best[4],
    decay_rate=best[5], turnover=best[6],
    correlation_max=best[7], logic_score=best[8],
    data_frequency=best[9],
    cross_symbol_coverage=best[10], capacity_estimate=best[11],
)
for dim in score['dimension_scores']:
    print(f"  {dim['name']}: {dim['score']:.1f}/5.0")
print(f"  总分: {score['total_score']}/50 (等级: {score['grade']})")

# 显示边缘因子的低分项
print()
print('【良好因子 (B 级) 详细维度分数】')
mid = test_cases[2]
score2 = card.evaluate(
    factor_id='mid',
    ic=mid[1], icir=mid[2],
    sharpe=mid[3], calmar=mid[4],
    decay_rate=mid[5], turnover=mid[6],
    correlation_max=mid[7], logic_score=mid[8],
    data_frequency=mid[9],
    cross_symbol_coverage=mid[10], capacity_estimate=mid[11],
)
for dim in score2['dimension_scores']:
    print(f"  {dim['name']}: {dim['score']:.1f}/5.0")
print(f"  总分: {score2['total_score']}/50 (等级: {score2['grade']})")

# 找出低分项
print()
print('【低分项预警 (< 3.0)】')
for dim in score2['dimension_scores']:
    if dim['score'] < 3.0:
        print(f"  ⚠️ {dim['name']}: {dim['score']:.1f}/5.0 - {dim['description']}")