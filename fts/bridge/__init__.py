"""
fts.bridge — VNPY 信号桥接层（Phase 25，v2.38.0）。

将 FTS 产出的交易信号（FactorSignal 契约）通过标准化协议输出到下游
交易系统（VNPY/FDT），支持三种协议:
    - ``json``: JSON 文件协议（默认，无外部依赖）
    - ``redis``: Redis 协议（需 redis-py，可选依赖 [bridge] extra）
    - ``rest``: REST 协议（HTTP POST/GET）

角色边界: 本层只做信号格式转换与传输，交易执行由下游系统负责。

用法:
    from fts.bridge import SignalBridge

    bridge = SignalBridge(protocol="json", output_dir="signals")
    bridge.publish(factor_signal_dict)

版本: v1.0.0
"""

from .signal_bridge import (
    BridgeError,
    BridgeStatus,
    SignalBridge,
)

__all__ = [
    "BridgeError",
    "BridgeStatus",
    "SignalBridge",
]
