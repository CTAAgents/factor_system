"""
构建 MCP 基本面缓存 — 从东方财富 mx API 获取数据并保存到本地缓存。

用法:
    python scripts/build_fundamental_cache.py

注意: 本脚本通过 mx_ashare_finance_data 工具获取数据，
      需在 TRAE Agent 会话中运行（使用 run_mcp 调用 mx API）。
"""

import json
import sys
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.data_mcp_bridge import _parse_mx_response


def parse_mx_response_text(text: str) -> dict:
    """解析 mx API 的 JSON 响应文本。

    Args:
        text: run_mcp 返回的 JSON 文本（包含 {"data": [...]}）

    Returns:
        结构化缓存数据: dict[symbol, dict[str, float]]
    """
    raw = json.loads(text)
    data = raw.get("data", [])
    return _parse_mx_response(data)


if __name__ == "__main__":
    # 使用方法:
    # 1. 在 Agent 中调用 run_mcp 获取 mx API 数据
    # 2. 将响应文本保存到文件
    # 3. 运行此脚本解析并保存缓存

    print("MCP 基本面缓存构建工具")
    print("=" * 40)
    print()
    print("使用方法:")
    print("  1. 使用 run_mcp 调用 mx_ashare_finance_data")
    print("  2. 将响应文本保存到 JSON 文件")
    print("  3. 运行: python scripts/build_fundamental_cache.py <input.json>")
    print()
    print("示例 run_mcp 查询:")
    print(
        '  query: "平安银行(000001)的市盈率TTM、市净率PB、总市值、ROE、EPS、营收增长率、净利润增长率、毛利率、每股净资产"'
    )
    print()
    print("支持批量查询最多500只股票")
