"""
fts.monitor.http_server — FTS Web UI 仪表盘服务器。

纯标准库实现，零额外依赖。
端点:
    GET /           → 现代仪表盘 HTML
    GET /api/status → 系统状态 JSON
    GET /api/factors → elite 因子列表 JSON
    GET /health     → 健康检查 JSON

用法:
    fts ui                    # 启动仪表盘（默认 9100 端口）
    fts ui --port 8080        # 自定义端口

版本: v1.1.0
"""

from __future__ import annotations

import json
import logging
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── 仪表盘 HTML（内嵌式单页应用）─────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FTS Dashboard</title>
<style>
  :root {
    --bg: #0f172a; --card: #1e293b; --text: #e2e8f0;
    --muted: #94a3b8; --border: #334155;
    --green: #22c55e; --red: #ef4444; --yellow: #eab308; --blue: #3b82f6;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; }
  header h1 { font-size: 24px; font-weight: 700; }
  header h1 span { color: var(--blue); }
  header .sub { color: var(--muted); font-size: 13px; margin-top: 4px; }
  .status-bar { display: flex; gap: 4px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .dot.green { background: var(--green); }
  .dot.red { background: var(--red); }
  .dot.yellow { background: var(--yellow); }
  .dot.blue { background: var(--blue); }

  /* 指标卡片网格 */
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
  .card .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .card .value { font-size: 28px; font-weight: 700; margin-top: 8px; }
  .card .value.green { color: var(--green); }
  .card .value.red { color: var(--red); }
  .card .value.yellow { color: var(--yellow); }
  .card .value.blue { color: var(--blue); }
  .card .note { font-size: 12px; color: var(--muted); margin-top: 4px; }

  /* 循环状态 */
  .loop-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }
  .loop-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
               padding: 20px; position: relative; }
  .loop-card .name { font-size: 14px; font-weight: 600; margin-bottom: 12px; display: flex;
                     justify-content: space-between; align-items: center; }
  .loop-card .name .badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 500; }
  .badge.ok { background: rgba(34,197,94,0.15); color: var(--green); }
  .badge.fail { background: rgba(239,68,68,0.15); color: var(--red); }
  .badge.warn { background: rgba(234,179,8,0.15); color: var(--yellow); }
  .loop-card .row { font-size: 13px; color: var(--muted); margin-bottom: 4px; }
  .loop-card .row span { color: var(--text); }
  .loop-card .error { color: var(--red); font-size: 12px; margin-top: 8px;
                      padding: 8px; background: rgba(239,68,68,0.1); border-radius: 6px; }

  /* 因子列表 */
  .section-title { font-size: 16px; font-weight: 600; margin-bottom: 12px;
                   display: flex; justify-content: space-between; align-items: center; }
  .factor-table { width: 100%; border-collapse: collapse; }
  .factor-table th { text-align: left; font-size: 12px; color: var(--muted);
                     padding: 8px 12px; border-bottom: 1px solid var(--border); }
  .factor-table td { font-size: 13px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
  .factor-table tr:hover td { background: rgba(59,130,246,0.05); }

  /* 底部 */
  footer { text-align: center; color: var(--muted); font-size: 12px; margin-top: 40px;
           padding-top: 16px; border-top: 1px solid var(--border); }

  @media (max-width: 768px) {
    .grid-4 { grid-template-columns: repeat(2, 1fr); }
    .loop-grid { grid-template-columns: 1fr; }
  }

  /* 轮播/刷新动画 */
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
  .loading { animation: pulse 1s infinite; }
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>FTS <span>Dashboard</span></h1>
      <div class="sub">因子智能系统 · 实时监控</div>
    </div>
    <div class="status-bar">
      <span class="dot" id="healthDot"></span>
      <span style="font-size:13px;color:var(--muted)" id="refreshTime">--</span>
    </div>
  </header>

  <!-- 指标卡片 -->
  <div class="grid-4">
    <div class="card"><div class="label">系统健康</div>
      <div class="value" id="cardHealth">--</div></div>
    <div class="card"><div class="label">FTS 版本</div>
      <div class="value blue" id="cardVersion">--</div></div>
    <div class="card"><div class="label">今日 Token</div>
      <div class="value" id="cardTokens">--</div></div>
    <div class="card"><div class="label">Elite 因子</div>
      <div class="value" id="cardFactors">--</div>
      <div class="note" id="factorNote"></div></div>
  </div>

  <!-- 循环状态 -->
  <div class="section-title">循环状态</div>
  <div class="loop-grid" id="loopGrid">
    <div class="loop-card"><div class="name">L1 <span class="badge loading">加载中...</span></div></div>
    <div class="loop-card"><div class="name">L2 <span class="badge loading">加载中...</span></div></div>
    <div class="loop-card"><div class="name">L3 <span class="badge loading">加载中...</span></div></div>
  </div>

  <!-- Elite 因子 -->
  <div class="section-title">Elite 因子 <span style="font-size:12px;color:var(--muted)" id="factorSummary"></span></div>
  <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden">
    <table class="factor-table">
      <thead><tr><th>因子ID</th><th>名称</th><th>代数</th><th>IC</th><th>夏普</th><th>来源</th></tr></thead>
      <tbody id="factorBody"><tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px">正在加载...</td></tr></tbody>
    </table>
  </div>

  <footer>FTS v<span id="footerVersion">--</span> · 每 10 秒自动刷新</footer>
</div>

<script>
async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

function updateTime() {
  document.getElementById('refreshTime').textContent = new Date().toLocaleTimeString('zh-CN');
}

function badgeClass(status) {
  if (!status) return 'warn';
  const s = String(status).toLowerCase();
  if (s === 'completed' || s === 'running') return 'ok';
  if (s === 'circuit_broken' || s === 'error') return 'fail';
  return 'warn';
}

function badgeText(status) {
  if (!status) return '未知';
  const m = { completed:'已完成', running:'运行中', circuit_broken:'熔断', paused:'已暂停', unknown:'未知', error:'错误' };
  return m[status] || status;
}

function sanitize(s) { return String(s ?? '--'); }

async function refresh() {
  try {
    const data = await fetchJSON('/api/status');

    // ── 健康指示器 ──
    const healthy = data.healthy;
    const hDot = document.getElementById('healthDot');
    hDot.className = 'dot ' + (healthy ? 'green' : 'red');

    // ── 卡片 ──
    document.getElementById('cardHealth').textContent = healthy ? '健康' : '异常';
    document.getElementById('cardHealth').className = 'value ' + (healthy ? 'green' : 'red');
    document.getElementById('cardVersion').textContent = sanitize(data.fts_version);
    document.getElementById('cardTokens').textContent = (data.total_tokens_today ?? 0).toLocaleString();

    // ── 循环状态 ──
    const loops = data.loops || [];
    const loopGrid = document.getElementById('loopGrid');
    loopGrid.innerHTML = '';
    for (const loop of loops) {
      const l = loop.loop_name || '?';
      const st = loop.status || 'unknown';
      const bc = badgeClass(st);
      const card = document.createElement('div');
      card.className = 'loop-card';
      card.innerHTML = `
        <div class="name">${sanitize(l)} <span class="badge ${bc}">${badgeText(st)}</span></div>
        <div class="row">运行 ID: <span>${sanitize(loop.run_id).slice(0,28) || '-'}</span></div>
        <div class="row">更新于: <span>${sanitize(loop.last_run_at).slice(0,19) || '-'}</span></div>
        <div class="row">已过: <span>${(loop.age_hours ?? 0).toFixed(1)} 小时</span></div>
        <div class="row">Token: <span>${(loop.tokens_consumed ?? 0).toLocaleString()}</span></div>
        ${loop.last_error ? `<div class="error">${sanitize(loop.last_error)}</div>` : ''}
      `;
      loopGrid.appendChild(card);
    }

    // ── 因子列表 ──
    document.getElementById('cardFactors').textContent = sanitize(data.elite_factor_count ?? 0);
    document.getElementById('cardFactors').className = 'value blue';
    const overload = data.overloaded_count;
    const retired = data.retired_count;
    let noteParts = [];
    if (overload > 0) noteParts.push('超载: ' + overload);
    if (retired > 0) noteParts.push('已淘汰: ' + retired);
    document.getElementById('factorNote').textContent = noteParts.length ? noteParts.join(' · ') : '';

    const fBody = document.getElementById('factorBody');
    fBody.innerHTML = '';

    try {
      const factors = await fetchJSON('/api/factors');
      const flist = factors.factors || [];
      if (flist.length === 0) {
        fBody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px">暂无 elite 因子</td></tr>';
      } else {
        for (const f of flist) {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td style="font-family:monospace;font-size:12px">${sanitize(f.factor_id).slice(0,16)}</td>
            <td>${sanitize(f.name)}</td>
            <td>${sanitize(f.generation)}</td>
            <td>${sanitize(f.ic)}</td>
            <td>${sanitize(f.sharpe)}</td>
            <td>${sanitize(f.source)}</td>
          `;
          fBody.appendChild(tr);
        }
      }
      document.getElementById('factorSummary').textContent = `共 ${flist.length} 个`;
    } catch {
      fBody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px">加载失败</td></tr>';
    }

    // ── 底部版本 ──
    document.getElementById('footerVersion').textContent = sanitize(data.fts_version);

  } catch (e) {
    document.getElementById('healthDot').className = 'dot red';
    console.error('Refresh failed:', e);
  }
  updateTime();
}

// 首次加载 & 每 10 秒刷新
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""


# ─── HTTP 处理 ──────────────────────────────────────────────

class _DashboardHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器 — 提供仪表盘和 API。"""

    def log_message(self, fmt, *args):
        logger.debug("HTTP %s", fmt % args)

    def _respond_json(self, data: Any, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str, ensure_ascii=False).encode("utf-8"))

    def _respond_html(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _build_status(self) -> dict:
        """构建 /api/status 响应。"""
        from . import check_all_status, SystemStatusReport

        try:
            root = Path.cwd()
            report: SystemStatusReport = check_all_status(root)
        except Exception:  # noqa: BLE001
            report = SystemStatusReport(
                healthy=False, loops=[], fts_version="?",
                any_circuit_broken=False, any_stale=False, total_tokens_today=0,
            )

        # 补充 elite 因子计数
        elite_dir = root / "memory" / "knowledge" / "factors" / "elite"
        elite_count = 0
        overload_count = 0
        retired_count = 0
        if elite_dir.exists():
            elite_count = len(list(elite_dir.glob("*.json")))
        overload_dir = root / "memory" / "knowledge" / "factors" / "overloaded"
        if overload_dir.exists():
            overload_count = len(list(overload_dir.glob("*.json")))
        retired_dir = root / "memory" / "knowledge" / "factors" / "retired"
        if retired_dir.exists():
            retired_count = len(list(retired_dir.glob("*.json")))

        data = {
            "healthy": report.healthy,
            "fts_version": report.fts_version,
            "any_circuit_broken": report.any_circuit_broken,
            "any_stale": report.any_stale,
            "total_tokens_today": report.total_tokens_today,
            "checked_at": report.checked_at,
            "elite_factor_count": elite_count,
            "overloaded_count": overload_count,
            "retired_count": retired_count,
            "loops": [
                {
                    "loop_name": l.loop_name,
                    "healthy": l.healthy,
                    "status": l.status,
                    "run_id": l.run_id,
                    "last_run_at": l.last_run_at,
                    "last_error": l.last_error,
                    "tokens_consumed": l.tokens_consumed,
                    "age_hours": l.age_hours,
                    "version": l.version,
                }
                for l in report.loops
            ],
        }
        return data

    def _build_factor_list(self) -> dict:
        """构建 /api/factors 响应。"""
        import json as _json

        elite_dir = Path.cwd() / "memory" / "knowledge" / "factors" / "elite"
        factors = []
        if elite_dir.exists():
            for fp in sorted(elite_dir.glob("*.json"), reverse=True)[:50]:
                try:
                    raw = _json.loads(fp.read_text(encoding="utf-8"))
                    bt = raw.get("evaluation", {}).get("level_1_backtest", {})
                    factors.append({
                        "factor_id": raw.get("factor_id", fp.stem),
                        "name": raw.get("name", fp.stem),
                        "generation": raw.get("generation", "?"),
                        "ic": f"{bt.get('ic', 0):.4f}",
                        "sharpe": f"{bt.get('sharpe', 0):.2f}",
                        "source": raw.get("source", "?"),
                    })
                except Exception:  # noqa: BLE001
                    continue
        return {"factors": factors, "count": len(factors)}

    def do_GET(self):  # noqa: N802
        path = self.path.rstrip("/")

        if path == "" or path == "/":
            self._respond_html(DASHBOARD_HTML)

        elif path == "/api/status":
            self._respond_json(self._build_status())

        elif path == "/api/factors":
            self._respond_json(self._build_factor_list())

        elif path == "/health":
            self._respond_json({
                "status": "ok",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })

        else:
            self._respond_json({"error": "not found"}, 404)


# ─── 指标注册表（兼容旧版调用） ──────────────────────────

_metrics: dict[str, Any] = {
    "fts_elite_factor_count": 0,
    "fts_loop_status_L1": 0,
    "fts_loop_status_L2": 0,
    "fts_loop_status_L3": 0,
    "fts_tokens_consumed": 0,
    "fts_combo_sharpe": 0.0,
    "fts_started_at": time.time(),
}


def set_metric(name: str, value: Any) -> None:
    """设置指标值。"""
    _metrics[name] = value


def get_metric(name: str, default: Any = 0) -> Any:
    """获取指标值。"""
    return _metrics.get(name, default)


# ─── 服务器 ──────────────────────────────────────────────────

class FTSDashboardServer:
    """FTS Web UI 仪表盘服务器。

    用法:
        server = FTSDashboardServer()
        server.start()  # 非阻塞线程
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9100):
        self.host = host
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[Thread] = None
        self._running = False

    def start(self) -> None:
        """启动 HTTP 服务器（非阻塞线程）。"""
        if self._running:
            logger.warning("[ui] Server already running")
            return
        try:
            self._server = HTTPServer((self.host, self.port), _DashboardHandler)
            self._thread = Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            self._running = True
            logger.info("[ui] FTS Dashboard: http://%s:%d", self.host, self.port)
            print(f"[ui] FTS Dashboard started: http://{self.host}:{self.port}")
        except OSError as e:
            logger.error("[ui] Server failed: %s", e)
            print(f"[ui] 启动失败: {e}")

    def stop(self) -> None:
        """停止 HTTP 服务器。"""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        self._running = False
        logger.info("[ui] Server stopped")

    @property
    def running(self) -> bool:
        return self._running


__all__ = [
    "FTSDashboardServer",
    "_DashboardHandler",
    "set_metric",
    "get_metric",
    "_metrics",
]
