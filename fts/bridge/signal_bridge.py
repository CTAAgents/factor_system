"""
fts.bridge.signal_bridge — SignalBridge 信号桥接实现（Phase 25，v2.38.0）。

支持三种传输协议：
    - JSON 文件（默认，无外部依赖）
    - Redis（可选依赖 redis-py，[bridge] extra）
    - REST（标准库 http.client，无额外依赖）

设计原则:
    - 统一 publish(latest) 接口，协议差异封装在内部
    - Redis 依赖缺失时优雅降级：publish 抛 BridgeError，不静默失败
    - REST 使用标准库，无需额外依赖
    - trace_id 贯穿信号内容（由调用方保证）

版本: v1.0.0
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 支持的协议枚举（字符串常量，便于 CLI 直用）
PROTOCOLS: tuple[str, ...] = ("json", "redis", "rest")

# Redis 依赖探测（一次完成）
_has_redis = False
try:  # pragma: no cover - 依赖探测
    import redis  # noqa: F401

    _has_redis = True
except ImportError:  # pragma: no cover
    pass


class BridgeError(RuntimeError):
    """信号桥接操作失败。"""


@dataclass
class BridgeStatus:
    """桥接状态摘要。"""

    protocol: str
    available: bool
    detail: str
    latest_signal_id: str = ""
    latest_timestamp: str = ""


class SignalBridge:
    """信号桥接器。

    Args:
        protocol: 传输协议（json / redis / rest）
        output_dir: JSON 协议输出目录（协议为 json 时使用）
        redis_url: Redis 连接 URL（协议为 redis 时使用，如 redis://localhost:6379/0）
        redis_key: Redis 信号 key（默认 fts:signals:latest）
        rest_url: REST 目标 URL（协议为 rest 时使用，如 http://127.0.0.1:8765/signal）
    """

    def __init__(
        self,
        protocol: str = "json",
        output_dir: str | Path = "signals",
        redis_url: str = "redis://localhost:6379/0",
        redis_key: str = "fts:signals:latest",
        rest_url: str = "",
    ) -> None:
        if protocol not in PROTOCOLS:
            raise BridgeError(f"不支持的协议: {protocol}（可选 {PROTOCOLS}）")
        self.protocol = protocol
        self.output_dir = Path(output_dir)
        self.redis_url = redis_url
        self.redis_key = redis_key
        self.rest_url = rest_url
        self._redis_client: Optional[Any] = None

    # ─── 主接口 ──────────────────────────────────────────

    def publish(self, signal: dict[str, Any]) -> str:
        """发布信号到目标协议。

        Args:
            signal: FactorSignal 契约字典（含 signal_id/timestamp/signals）

        Returns:
            已发布的 signal_id

        Raises:
            BridgeError: 协议不可用或发布失败
        """
        if "signal_id" not in signal:
            raise BridgeError("信号缺少 signal_id 字段")
        if self.protocol == "json":
            self._publish_json(signal)
        elif self.protocol == "redis":
            self._publish_redis(signal)
        elif self.protocol == "rest":
            self._publish_rest(signal)
        logger.info("[BRIDGE] %s 协议发布信号 %s", self.protocol, signal["signal_id"])
        return signal["signal_id"]

    def latest(self) -> Optional[dict[str, Any]]:
        """读取最近一次发布的信号（协议支持时）。

        Returns:
            信号字典；不可读取（如 REST 服务端）返回 None。
        """
        if self.protocol == "json":
            return self._read_json()
        if self.protocol == "redis":
            return self._read_redis()
        return None

    def status(self) -> BridgeStatus:
        """返回桥接状态摘要。"""
        try:
            sig = self.latest()
            latest_id = (sig or {}).get("signal_id", "")
            latest_ts = (sig or {}).get("timestamp", "")
            detail = f"{self.protocol} 协议就绪"
            available = True
        except BridgeError as e:
            latest_id, latest_ts = "", ""
            detail = str(e)
            available = False
        return BridgeStatus(
            protocol=self.protocol,
            available=available,
            detail=detail,
            latest_signal_id=latest_id,
            latest_timestamp=latest_ts,
        )

    # ─── JSON 协议 ───────────────────────────────────────

    def _json_path(self) -> Path:
        return self.output_dir / "latest_signal.json"

    def _publish_json(self, signal: dict[str, Any]) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self._json_path()
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(signal, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)  # 原子替换
        except OSError as e:
            raise BridgeError(f"JSON 信号写入失败: {e}") from e

    def _read_json(self) -> Optional[dict[str, Any]]:
        path = self._json_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise BridgeError(f"JSON 信号读取失败: {e}") from e

    # ─── Redis 协议 ──────────────────────────────────────

    def _get_redis(self) -> Any:
        if not _has_redis:
            raise BridgeError("redis-py 未安装，请执行 pip install .[bridge]")
        if self._redis_client is None:
            try:
                import redis

                self._redis_client = redis.Redis.from_url(self.redis_url)
            except Exception as e:  # noqa: BLE001
                raise BridgeError(f"Redis 连接失败: {e}") from e
        return self._redis_client

    def _publish_redis(self, signal: dict[str, Any]) -> None:
        try:
            client = self._get_redis()
            payload = json.dumps(signal, ensure_ascii=False)
            client.set(self.redis_key, payload)
            client.set(f"{self.redis_key}:ts", str(time.time()))
        except BridgeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise BridgeError(f"Redis 信号写入失败: {e}") from e

    def _read_redis(self) -> Optional[dict[str, Any]]:
        try:
            client = self._get_redis()
            raw = client.get(self.redis_key)
            if raw is None:
                return None
            return json.loads(raw)
        except BridgeError:
            raise
        except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            raise BridgeError(f"Redis 信号读取失败: {e}") from e

    # ─── REST 协议 ───────────────────────────────────────

    def _publish_rest(self, signal: dict[str, Any]) -> None:
        if not self.rest_url:
            raise BridgeError("REST 协议需要 rest_url 参数")
        try:
            import http.client
            from urllib.parse import urlparse

            url = urlparse(self.rest_url)
            port = url.port or (443 if url.scheme == "https" else 80)
            conn_cls = (
                http.client.HTTPSConnection
                if url.scheme == "https"
                else http.client.HTTPConnection
            )
            conn = conn_cls(url.hostname, port, timeout=5)
            body = json.dumps(signal, ensure_ascii=False)
            conn.request(
                "POST",
                url.path or "/signal",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            resp.read()  # 消费响应体
            conn.close()
            if resp.status >= 400:
                raise BridgeError(f"REST 发布失败: HTTP {resp.status}")
        except BridgeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise BridgeError(f"REST 信号发送失败: {e}") from e


def utc_now_iso() -> str:
    """UTC 当前时间 ISO 格式（信号时间戳辅助）。"""
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "PROTOCOLS",
    "BridgeError",
    "BridgeStatus",
    "SignalBridge",
    "utc_now_iso",
]
