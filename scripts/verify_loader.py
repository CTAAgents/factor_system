"""验证 loader 模块能正确生成 FactorProgram。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.factor_engine.seed_data.loader import (
    load_wq101_seeds,
    load_qlib158_seeds,
    load_all_external_seeds,
    get_external_seed_count,
)

# 验证计数
wq, ql, gj, fd, total = get_external_seed_count()
print(f"External seed count: WQ={wq}, Qlib={ql}, GTJA={gj}, Fundamental={fd}, Total={total}")

# 加载 WQ 101
wq_seeds = load_wq101_seeds()
print(f"Loaded WQ101: {len(wq_seeds)} FactorPrograms")
print(f"  First: {wq_seeds[0]['factor_id']} - {wq_seeds[0]['name']}")
print(f"  Last: {wq_seeds[-1]['factor_id']} - {wq_seeds[-1]['name']}")

# 验证 code 格式
first_code = wq_seeds[0]['code']
assert 'def factor_program' in first_code, "Missing factor_program function"
assert 'rank' in first_code, "Missing rank function"
assert 'close' in first_code, "Missing close variable"
print(f"  Code template OK: factor_program() present, {len(first_code)} chars")

# 加载 Qlib 158
ql_seeds = load_qlib158_seeds()
print(f"Loaded Qlib158: {len(ql_seeds)} FactorPrograms")
print(f"  First: {ql_seeds[0]['factor_id']} - {ql_seeds[0]['name']}")
print(f"  Last: {ql_seeds[-1]['factor_id']} - {ql_seeds[-1]['name']}")

# 加载全部
all_seeds = load_all_external_seeds()
print(f"Total external seeds: {len(all_seeds)}")

# 验证 FactorProgram 字段完整性
required_fields = ['factor_id', 'name', 'code', 'params', 'signature', 'economic_logic', 'source']
for fp in all_seeds[:3]:
    for field in required_fields:
        assert field in fp, f"Missing field {field} in {fp.get('name', '?')}"
    print(f"  {fp['name']}: source={fp['source']}, gen={fp['generation']}, "
          f"fields={fp['signature']['input_fields']}, lookback={fp['signature']['lookback']}")

print("\nAll loader verifications passed!")