"""
fts.core.atomic — 原子文件写入与读取。
    
所有 state.json/pool.json/combo.json 写入使用原子操作，
避免进程崩溃产生残缺文件。

用法:
    from fts.core.atomic import atomic_write, atomic_read
    
    # 原子写入
    atomic_write("/path/to/state.json", {"key": "value"})
    
    # 安全读取
    data = atomic_read("/path/to/state.json")

版本: v0.1.0
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def atomic_write(path: str | Path, data: Any, *, 
                 make_dir: bool = True,
                 encoding: str = "utf-8") -> None:
    """原子写入 JSON 文件。
    
    使用临时文件 + rename 策略，进程崩溃不会产生残缺文件。
    
    Args:
        path: 目标文件路径
        data: 要序列化的数据（可 JSON 序列化）
        make_dir: 是否自动创建父目录（默认 True）
        encoding: 文件编码
    
    Raises:
        OSError: 写入失败（目标目录无权限等）
        TypeError: 数据不可 JSON 序列化
    """
    p = Path(path)
    if make_dir:
        p.parent.mkdir(parents=True, exist_ok=True)
    
    # 临时文件（同目录确保原子 rename）
    tmp = p.with_suffix(".tmp")
    
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding=encoding)
        os.replace(str(tmp), str(p))  # 原子 replace（跨平台，目标已存在时安全）
    except Exception:
        # 清理临时文件
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def atomic_read(path: str | Path, *, 
                default: Any = None,
                encoding: str = "utf-8") -> Any:
    """安全读取 JSON 文件。
    
    如果文件不存在或不合法，返回 default。不会抛出异常。
    
    Args:
        path: 文件路径
        default: 读取失败时的默认值
        encoding: 文件编码
    
    Returns:
        反序列化后的数据，或 default
    """
    p = Path(path)
    if not p.exists():
        return default
    
    try:
        return json.loads(p.read_text(encoding=encoding))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("读取失败 [%s]: %s", p, e)
        return default


# ─── state.py 兼容 ────────────────────────────────────────

def atomic_write_state(path: str | Path, state: dict[str, Any], *, 
                       backup_count: int = 3) -> None:
    """原子写入状态文件，保留最近 backup_count 个备份。
    
    备份命名: path -> path.bak.0, path.bak.1, ...
    """
    p = Path(path)
    
    # 如果已有旧文件，做备份轮转
    if p.exists():
        for i in range(backup_count - 1, -1, -1):
            bak = p.with_suffix(f".bak.{i}")
            prev = p.with_suffix(f".bak.{i - 1}") if i > 0 else p
            if prev.exists():
                try:
                    os.replace(str(prev), str(bak))
                except OSError:
                    pass
    
    atomic_write(path, state)


__all__ = [
    "atomic_write",
    "atomic_read",
    "atomic_write_state",
]
