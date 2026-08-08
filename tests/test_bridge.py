"""
tests/test_bridge.py — SignalBridge 信号桥接测试（Phase 25，v2.38.0）。

覆盖:
    - JSON 协议发布/读取/状态
    - Redis 协议（mock 客户端 + 依赖缺失降级）
    - REST 协议（mock HTTP + URL 缺失）
    - 协议枚举与非法协议
    - CLI bridge 子命令（publish/status/serve）
    - 边界情况（缺 signal_id / 空输入）

版本: v1.0.0
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from fts.bridge import BridgeError, SignalBridge


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def sample_signal():
    return {
        "signal_id": "sig_test_001",
        "portfolio_id": "pf_test",
        "timestamp": "2026-08-08T00:00:00",
        "frequency": "1d",
        "universe": ["RB0", "M0"],
        "signals": [
            {"symbol": "RB0", "direction": "long", "position": 0.6, "confidence": 0.8},
        ],
        "meta": {"trace_id": "tr_test", "factor_count": 2},
    }


@pytest.fixture
def fake_redis(monkeypatch):
    """注入假 redis 模块与客户端。"""
    mod = types.ModuleType("redis")

    class _FakeClient:
        def __init__(self):
            self._data: dict = {}

        def set(self, key, value, **kwargs):
            self._data[key] = value
            return True

        def get(self, key):
            return self._data.get(key)

    class _FakeRedis:
        @classmethod
        def from_url(cls, url, **kwargs):
            return _FakeClient()

    mod.Redis = _FakeRedis
    monkeypatch.setitem(sys.modules, "redis", mod)
    monkeypatch.setattr("fts.bridge.signal_bridge._has_redis", True)
    return mod


# ─── 协议与构造 ──────────────────────────────────────────

class TestProtocol:
    def test_invalid_protocol_raises(self):
        with pytest.raises(BridgeError):
            SignalBridge(protocol="ftp")

    def test_default_protocol_is_json(self):
        assert SignalBridge().protocol == "json"


# ─── JSON 协议 ───────────────────────────────────────────

class TestJsonProtocol:
    def test_publish_writes_file(self, sample_signal, tmp_path):
        bridge = SignalBridge(protocol="json", output_dir=tmp_path)
        sid = bridge.publish(sample_signal)
        assert sid == "sig_test_001"
        assert (tmp_path / "latest_signal.json").exists()

    def test_latest_roundtrip(self, sample_signal, tmp_path):
        bridge = SignalBridge(protocol="json", output_dir=tmp_path)
        bridge.publish(sample_signal)
        latest = bridge.latest()
        assert latest["signal_id"] == "sig_test_001"
        assert latest["universe"] == ["RB0", "M0"]

    def test_latest_when_empty(self, tmp_path):
        bridge = SignalBridge(protocol="json", output_dir=tmp_path)
        assert bridge.latest() is None

    def test_status_after_publish(self, sample_signal, tmp_path):
        bridge = SignalBridge(protocol="json", output_dir=tmp_path)
        bridge.publish(sample_signal)
        status = bridge.status()
        assert status.available is True
        assert status.latest_signal_id == "sig_test_001"
        assert status.protocol == "json"

    def test_missing_signal_id_raises(self, tmp_path):
        bridge = SignalBridge(protocol="json", output_dir=tmp_path)
        with pytest.raises(BridgeError):
            bridge.publish({"timestamp": "x"})

    def test_publish_overwrites_previous(self, sample_signal, tmp_path):
        bridge = SignalBridge(protocol="json", output_dir=tmp_path)
        bridge.publish(sample_signal)
        sample_signal["signal_id"] = "sig_test_002"
        bridge.publish(sample_signal)
        assert bridge.latest()["signal_id"] == "sig_test_002"


# ─── Redis 协议 ──────────────────────────────────────────

class TestRedisProtocol:
    def test_publish_and_read(self, sample_signal, fake_redis, tmp_path):
        bridge = SignalBridge(protocol="redis", output_dir=tmp_path)
        bridge.publish(sample_signal)
        latest = bridge.latest()
        assert latest is not None
        assert latest["signal_id"] == "sig_test_001"

    def test_missing_dependency_raises_on_publish(self, sample_signal, monkeypatch):
        monkeypatch.setattr("fts.bridge.signal_bridge._has_redis", False)
        bridge = SignalBridge(protocol="redis")
        with pytest.raises(BridgeError):
            bridge.publish(sample_signal)

    def test_missing_dependency_status_unavailable(self, monkeypatch):
        monkeypatch.setattr("fts.bridge.signal_bridge._has_redis", False)
        bridge = SignalBridge(protocol="redis")
        status = bridge.status()
        assert status.available is False
        assert "redis-py" in status.detail

    def test_empty_redis_returns_none(self, fake_redis, tmp_path):
        bridge = SignalBridge(protocol="redis", output_dir=tmp_path)
        assert bridge.latest() is None


# ─── REST 协议 ───────────────────────────────────────────

class TestRestProtocol:
    def test_missing_url_raises(self, sample_signal):
        bridge = SignalBridge(protocol="rest", rest_url="")
        with pytest.raises(BridgeError):
            bridge.publish(sample_signal)

    def test_publish_with_mock_connection(self, sample_signal, monkeypatch, tmp_path):
        # 记录请求
        captured = {}

        class _FakeResponse:
            status = 200

            def read(self):
                return b"ok"

        class _FakeConn:
            def __init__(self, *a, **kw):
                pass

            def request(self, method, path, body, headers):
                captured["method"] = method
                captured["path"] = path
                captured["body"] = body
                captured["headers"] = headers

            def getresponse(self):
                return _FakeResponse()

            def close(self):
                pass

        monkeypatch.setattr("http.client.HTTPConnection", _FakeConn)
        bridge = SignalBridge(protocol="rest", rest_url="http://127.0.0.1:8765/signal")
        sid = bridge.publish(sample_signal)
        assert sid == "sig_test_001"
        assert captured["method"] == "POST"
        assert captured["path"] == "/signal"
        assert json.loads(captured["body"])["signal_id"] == "sig_test_001"

    def test_http_error_raises(self, sample_signal, monkeypatch):
        class _FakeResponse:
            status = 500

            def read(self):
                return b"err"

        class _FakeConn:
            def __init__(self, *a, **kw):
                pass

            def request(self, *a, **kw):
                pass

            def getresponse(self):
                return _FakeResponse()

            def close(self):
                pass

        monkeypatch.setattr("http.client.HTTPConnection", _FakeConn)
        bridge = SignalBridge(protocol="rest", rest_url="http://127.0.0.1:1/signal")
        with pytest.raises(BridgeError):
            bridge.publish(sample_signal)

    def test_latest_unsupported_for_rest(self):
        bridge = SignalBridge(protocol="rest", rest_url="http://x/signal")
        assert bridge.latest() is None


# ─── CLI 子命令 ──────────────────────────────────────────

class TestCliBridge:
    def _run_cli(self, *argv):
        from fts.cli import main

        return main(list(argv))

    def test_bridge_publish_json(self, tmp_path, capsys):
        rc = self._run_cli(
            "bridge", "publish",
            "--protocol", "json",
            "--output-dir", str(tmp_path),
        )
        assert rc == 0
        assert "发布成功" in capsys.readouterr().out
        assert (tmp_path / "latest_signal.json").exists()

    def test_bridge_publish_from_file(self, tmp_path, sample_signal):
        signal_file = tmp_path / "in.json"
        signal_file.write_text(json.dumps(sample_signal), encoding="utf-8")
        out_dir = tmp_path / "out"
        rc = self._run_cli(
            "bridge", "publish",
            "--protocol", "json",
            "--input", str(signal_file),
            "--output-dir", str(out_dir),
        )
        assert rc == 0
        latest = json.loads((out_dir / "latest_signal.json").read_text(encoding="utf-8"))
        assert latest["signal_id"] == "sig_test_001"

    def test_bridge_publish_missing_file(self, tmp_path, capsys):
        rc = self._run_cli(
            "bridge", "publish",
            "--protocol", "json",
            "--input", str(tmp_path / "nope.json"),
            "--output-dir", str(tmp_path),
        )
        assert rc == 1
        assert "失败" in capsys.readouterr().out

    def test_bridge_publish_redis_missing_dep(self, monkeypatch, capsys):
        monkeypatch.setattr("fts.bridge.signal_bridge._has_redis", False)
        rc = self._run_cli("bridge", "publish", "--protocol", "redis")
        assert rc == 1

    def test_bridge_status_json(self, tmp_path, sample_signal):
        bridge = SignalBridge(protocol="json", output_dir=tmp_path)
        bridge.publish(sample_signal)
        rc = self._run_cli("bridge", "status", "--protocol", "json", "--output-dir", str(tmp_path))
        assert rc == 0

    def test_bridge_status_redis_unavailable(self, monkeypatch, capsys):
        monkeypatch.setattr("fts.bridge.signal_bridge._has_redis", False)
        rc = self._run_cli("bridge", "status", "--protocol", "redis")
        assert rc == 1  # 依赖缺失时状态不可用

    def test_bridge_serve_health_endpoint(self, tmp_path):
        import threading
        import urllib.request

        from fts.cli import _cmd_bridge_serve

        args = types.SimpleNamespace(host="127.0.0.1", port=8877)
        t = threading.Thread(target=_cmd_bridge_serve, args=(args,), daemon=True)
        t.start()
        try:
            import time

            for _ in range(50):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8877/health", timeout=1) as resp:
                        assert json.loads(resp.read())["status"] == "ok"
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                pytest.fail("REST 服务未就绪")
        finally:
            import socket

            s = socket.socket()
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", 8877))
                s.close()
            except OSError:
                pass
