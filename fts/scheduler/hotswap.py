"""
fts.scheduler.hotswap — 热重载支持。

开发模式下监听 fts/ 目录变更，检测到修改后自动 reload 受影响模块。

用法:
    watcher = HotSwapWatcher(watch_dirs=["fts/factor_engine"])
    watcher.start()  # 非阻塞

版本: v0.1.0
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class HotSwapWatcher:
    """文件变更监听 + 模块热重载。
    
    使用 watchdog 库监听目录变更，检测到修改后自动 reload 受影响模块。
    如果 watchdog 未安装，静默降级（仅打印日志）。
    
    Usage:
        watcher = HotSwapWatcher(["fts/factor_engine"])
        watcher.start()
    """
    
    def __init__(self, watch_dirs: Optional[list[str]] = None):
        self.watch_dirs = [Path(d) for d in (watch_dirs or ["fts"])]
        self._observer = None
        self._running = False
    
    def start(self) -> bool:
        """启动文件监听（非阻塞）。
        
        Returns:
            True=启动成功, False=watchdog 未安装
        """
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.warning("watchdog 未安装，热重载不可用。pip install watchdog")
            return False
        
        class _ReloadHandler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.src_path.endswith(".py"):
                    _reload_module(event.src_path)
        
        self._observer = Observer()
        for d in self.watch_dirs:
            if d.exists():
                self._observer.schedule(_ReloadHandler(), str(d), recursive=True)
                logger.info("[hotswap] watching: %s", d)
            else:
                logger.warning("[hotswap] watch dir not found: %s", d)
        self._observer.start()
        self._running = True
        logger.info("[hotswap] watcher started (%d dirs)", len(self.watch_dirs))
        return True
    
    def stop(self) -> None:
        """停止监听。"""
        if self._observer is not None:
            self._observer.stop()
            self._observer = None
        self._running = False
        logger.info("[hotswap] watcher stopped")
    
    @property
    def running(self) -> bool:
        return self._running


def _reload_module(file_path: str) -> None:
    """热重载单个模块（若已导入）。"""
    path = Path(file_path)
    if not path.exists() or path.suffix != ".py":
        return
    
    # Convert file path to module name
    rel_path = path.relative_to(Path.cwd()) if path.is_absolute() else path
    parts = list(rel_path.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    module_name = ".".join(parts)
    
    if module_name in sys.modules:
        try:
            importlib.reload(sys.modules[module_name])
            logger.info("[hotswap] reloaded: %s", module_name)
        except Exception as e:
            logger.warning("[hotswap] reload failed [%s]: %s", module_name, e)


__all__ = ["HotSwapWatcher", "_reload_module"]
