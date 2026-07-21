"""
FTS — Factor Trading System

从 FDT 剥离的独立因子策略系统，专注于多因子挖掘、演化与交易。
数据层由外部 Data-Core 项目提供（pip install datacore）。

核心模块：
    - core: 核心契约层（因子引擎 TypedDict 契约 + FTS 特有枚举）
    - factor_engine: 因子引擎（L1/L2/L3 三层循环 + 种子池 + 验证器）
    - pipeline: 因子推演管线（因子组合与融合）
    - strategies: 策略层（多因子策略）
    - scheduler: 调度层
    - cli: 统一命令行入口

版本: v0.1.0（从 FDT v8.10.0 剥离）
"""

__version__ = "1.0.0"
