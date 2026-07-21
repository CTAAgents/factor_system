"""
fts.scheduler.watchdog — 进程级看门狗。

监控子进程存活状态，崩溃后自动拉起。
重启策略：连续重启 3 次且间隔 < 30 秒 → 熔断 5 分钟。

用法:
    watchdog = ProcessWatchdog(["python", "-m", "fts.cli", "scheduler", "run"])
    watchdog.run()  # 阻塞，直到收到停止信号

版本: v0.1.0
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

MAX_RESTARTS = 3
RESTART_WINDOW = 30     # 秒
CIRCUIT_BREAK_DURATION = 300  # 5 分钟


class ProcessWatchdog:
    """进程级看门狗 — 自动重启崩溃的子进程。"""
    
    def __init__(self, cmd: list[str], name: str = "fts"):
        self.cmd = cmd
        self.name = name
        self._restart_count = 0
        self._last_restart = 0.0
        self._circuit_open_until = 0.0
        self._should_stop = False
    
    def run(self) -> None:
        """守护运行 — 自动重启直到停止。"""
        logger.info("[watchdog] starting %s: %s", self.name, " ".join(self.cmd))
        
        while not self._should_stop:
            if self._is_circuit_broken():
                wait = self._circuit_open_until - time.time()
                if wait > 0:
                    logger.warning("[watchdog] circuit broken, waiting %.0fs", wait)
                    time.sleep(min(wait, 5))
                else:
                    self._restart_count = 0
                    self._circuit_open_until = 0.0
                    logger.info("[watchdog] circuit restored")
                continue
            
            try:
                proc = subprocess.Popen(self.cmd)
                proc.wait()
            except FileNotFoundError:
                logger.error("[watchdog] command not found: %s", self.cmd[0])
                time.sleep(60)
                continue
            except Exception as e:
                logger.error("[watchdog] process error: %s", e)
                time.sleep(10)
                continue
            
            if self._should_stop:
                break
            
            # 进程退出，记录重启
            now = time.time()
            if now - self._last_restart < RESTART_WINDOW:
                self._restart_count += 1
            else:
                self._restart_count = 1
            self._last_restart = now
            
            logger.warning(
                "[watchdog] %s exited (code=%d, restart=%d/%d)",
                self.name, proc.returncode, self._restart_count, MAX_RESTARTS,
            )
            
            if self._restart_count >= MAX_RESTARTS:
                self._circuit_open_until = time.time() + CIRCUIT_BREAK_DURATION
                logger.error("[watchdog] circuit broken for %.0fs", CIRCUIT_BREAK_DURATION)
    
    def stop(self) -> None:
        """停止看门狗。"""
        self._should_stop = True
        logger.info("[watchdog] stopping %s", self.name)
    
    def _is_circuit_broken(self) -> bool:
        return time.time() < self._circuit_open_until


__all__ = ["ProcessWatchdog", "MAX_RESTARTS", "RESTART_WINDOW", "CIRCUIT_BREAK_DURATION"]
