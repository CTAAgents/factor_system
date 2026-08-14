"""tests/workflow — CTA 手册 WorkFlow 工作流包单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))
