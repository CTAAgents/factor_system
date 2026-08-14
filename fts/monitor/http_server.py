"""
fts.monitor.http_server — FTS Web UI 仪表盘服务器。

纯标准库实现，零额外依赖。
端点:
    GET /                → 现代仪表盘 HTML
    GET /api/status      → 系统状态 JSON
    GET /api/factors     → elite 因子列表 JSON
    GET /health          → 健康检查 JSON（含数据源状态，14.5）
    GET /metrics/data-sources → 多源数据源指标 JSON（14.5）
    GET /workflow        → WorkFlow UI SPA（web/workflow_ui/dist）
    GET /api/workflow/stages    → 阶段定义（11 阶段 + 质检闭环）
    GET /api/workflow/runs      → 运行批次列表
    GET /api/workflow/runs/{id} → 批次详情（阶段动作记录）
    GET /api/workflow/qa/board  → 质检状态看板（QA 7 状态）
    POST /api/workflow/runs                     → 创建批次
    POST /api/workflow/runs/{id}/run_all        → 端到端执行
    POST /api/workflow/runs/{id}/stage/{s}/action/{a}/run → 单动作执行

用法:
    fts ui                    # 启动仪表盘（默认 9100 端口）
    fts ui --port 8080        # 自定义端口

版本: 动态读取自 fts.__version__
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 数据源指标缓存（模块级别，GAP-14.5-001）
_metrics_cache: dict = {"data": None, "ts": 0.0}


# ─── 因子信号相关性聚类（UI 按"因子聚类"分组展示）──────────

# 聚类结果 TTL 缓存（30 分钟）：UI 每 10s 刷新，命中缓存避免反复执行因子信号
_cluster_cache: dict = {"key": "", "data": None, "ts": 0.0}
_CLUSTER_CACHE_TTL: float = 1800.0

# 参考品种（信号计算优先序）：因子代码列依赖差异大，多品种提高信号可计算率
_CLUSTER_REF_SYMBOLS: tuple[str, ...] = ("RB0", "CU0", "IF0")
_CLUSTER_DAYS: int = 500
# 层次聚类距离阈值：distance = 1 - |corr|，0.5 等价于 |corr| >= 0.5 视为同一簇
_CLUSTER_THRESHOLD: float = 0.5


def _compute_signal_clusters(
    code_factors: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """基于信号相关性对精英因子做层次聚类（薄包装，逻辑下沉 factor_clustering）。

    Args:
        code_factors: 含 factor_id/code/params/sharpe 的因子列表

    Returns:
        与 cluster_factors_by_signal 相同结构；失败返回 None（调用方降级为不分组）。
    """
    try:
        from fts.factor_engine.factor_clustering import cluster_factors_by_signal
    except Exception as exc:  # noqa: BLE001 — 聚类依赖缺失属非致命
        logger.warning("[ui] 因子聚类依赖导入失败: %s", exc)
        return None
    return cluster_factors_by_signal(
        code_factors,
        ref_symbols=_CLUSTER_REF_SYMBOLS,
        days=_CLUSTER_DAYS,
        cluster_threshold=_CLUSTER_THRESHOLD,
    )


def _cluster_factors_by_signal(code_factors: list[dict[str, Any]]) -> dict[str, Any] | None:
    """带 TTL 缓存的信号聚类入口；缓存 key 基于 elite 因子 ID 集合。

    Args:
        code_factors: 含 factor_id/code/params 的精英因子列表

    Returns:
        与 _compute_signal_clusters 相同结构；失败返回 None。
    """
    fids = sorted({f.get("factor_id") or "" for f in code_factors})
    fids = [f for f in fids if f]
    if len(fids) < 3:
        return None
    key = hashlib.sha256("|".join(fids).encode("utf-8")).hexdigest()[:16]
    now = time.time()
    if (
        _cluster_cache["key"] == key
        and _cluster_cache["data"] is not None
        and (now - _cluster_cache["ts"]) < _CLUSTER_CACHE_TTL
    ):
        return _cluster_cache["data"]
    try:
        data = _compute_signal_clusters(code_factors)
    except Exception as exc:  # noqa: BLE001 — 聚类失败非致命，UI 降级不分组
        logger.warning("[ui] 因子聚类计算失败: %s", exc)
        data = None
    if data is not None:
        _cluster_cache["key"] = key
        _cluster_cache["data"] = data
        _cluster_cache["ts"] = now
    return data


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
    --purple: #a855f7; --cyan: #06b6d4; --orange: #f97316; --pink: #ec4899;
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

  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
  .card .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .card .value { font-size: 28px; font-weight: 700; margin-top: 8px; }
  .card .value.green { color: var(--green); }
  .card .value.red { color: var(--red); }
  .card .value.yellow { color: var(--yellow); }
  .card .value.blue { color: var(--blue); }
  .card .note { font-size: 12px; color: var(--muted); margin-top: 4px; }

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

  .cluster-dist-section { margin-bottom: 24px; }
  .cluster-bar { display: flex; align-items: center; gap: 8px; padding: 6px 0; cursor: pointer; }
  .cluster-bar:hover { opacity: 0.8; }
  .cluster-bar .tag { font-size: 12px; min-width: 80px; font-weight: 600; }
  .cluster-bar .bar-track { flex: 1; height: 20px; background: rgba(255,255,255,0.05);
                           border-radius: 4px; overflow: hidden; position: relative; }
  .cluster-bar .bar-fill { height: 100%; border-radius: 4px; transition: width 0.5s ease;
                          display: flex; align-items: center; padding-left: 6px; }
  .cluster-bar .bar-fill span { font-size: 11px; color: #fff; text-shadow: 0 1px 2px rgba(0,0,0,0.5); }
  .cluster-bar .bar-stats { font-size: 11px; color: var(--muted); min-width: 120px; text-align: right; }

  .cluster-filter { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .cluster-filter .chip { font-size: 11px; padding: 4px 10px; border-radius: 12px;
                         border: 1px solid var(--border); cursor: pointer; color: var(--muted);
                         background: transparent; transition: all 0.2s; }
  .cluster-filter .chip:hover { border-color: var(--blue); color: var(--text); }
  .cluster-filter .chip.active { background: var(--blue); color: #fff; border-color: var(--blue); }
  .cluster-filter .chip.all { border-color: var(--muted); }
  .cluster-filter .chip.all.active { background: var(--muted); color: #fff; border-color: var(--muted); }

  .section-title { font-size: 16px; font-weight: 600; margin-bottom: 12px;
                   display: flex; justify-content: space-between; align-items: center; }
  .factor-table { width: 100%; border-collapse: collapse; }
  .factor-table th { text-align: left; font-size: 12px; color: var(--muted);
                     padding: 8px 12px; border-bottom: 1px solid var(--border);
                     position: sticky; top: 0; background: var(--card); z-index: 1; }
  .factor-table td { font-size: 13px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
  .factor-table tr.clickable { cursor: pointer; }
  .factor-table tr.clickable:hover td { background: rgba(59,130,246,0.08); }

  .cluster-header { background: rgba(59,130,246,0.08) !important; }
  .cluster-header td { padding: 8px 12px; font-weight: 700; font-size: 14px;
                      border-bottom: 2px solid var(--blue); }
  .cluster-header .cluster-count { font-size: 12px; font-weight: 400; color: var(--muted);
                              margin-left: 8px; }

  .detail-row { display: none; }
  .detail-row.open { display: table-row; }
  .detail-cell { padding: 0 !important; }
  .detail-panel { padding: 16px 20px; background: rgba(15,23,42,0.6);
                  border-bottom: 1px solid var(--border); }
  .detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .detail-item { display: flex; flex-direction: column; gap: 2px; }
  .detail-item .dl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.3px; }
  .detail-item .dv { font-size: 13px; color: var(--text); }
  .detail-item .dv.mono { font-family: monospace; font-size: 12px; }
  .detail-item .dv.green { color: var(--green); }
  .detail-item .dv.red { color: var(--red); }
  .detail-item .dv.yellow { color: var(--yellow); }
  .detail-section-title { font-size: 12px; font-weight: 600; color: var(--muted);
                          margin: 12px 0 8px; grid-column: 1 / -1;
                          border-bottom: 1px solid var(--border); padding-bottom: 4px; }
  .narrative-text { font-size: 12px; color: var(--muted); line-height: 1.5;
                    grid-column: 1 / -1; padding: 8px; background: rgba(0,0,0,0.2);
                    border-radius: 6px; max-height: 60px; overflow-y: auto; }
  .detail-row .expand-icon { font-size: 10px; color: var(--muted); margin-right: 6px;
                              transition: transform 0.2s; display: inline-block; }
  .detail-row .expand-icon.open { transform: rotate(90deg); }

  .status-tag { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 500; }
  .status-tag.active { background: rgba(34,197,94,0.15); color: var(--green); }
  .status-tag.observing { background: rgba(234,179,8,0.15); color: var(--yellow); }
  .status-tag.decaying { background: rgba(249,115,22,0.15); color: var(--orange); }
  .status-tag.critical_decay { background: rgba(239,68,68,0.15); color: var(--red); }
  .status-tag.retired { background: rgba(148,163,184,0.15); color: var(--muted); }
  .status-tag.rejected { background: rgba(239,68,68,0.15); color: var(--red); }

  .grade-tag { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
  .grade-tag.A { background: rgba(34,197,94,0.2); color: var(--green); }
  .grade-tag.B { background: rgba(234,179,8,0.2); color: var(--yellow); }
  .grade-tag.C { background: rgba(239,68,68,0.2); color: var(--red); }
  .grade-tag.N { background: rgba(148,163,184,0.2); color: var(--muted); }

  .cluster-color.cluster-color { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }

  footer { text-align: center; color: var(--muted); font-size: 12px; margin-top: 40px;
           padding-top: 16px; border-top: 1px solid var(--border); }

  @media (max-width: 768px) {
    .grid-4 { grid-template-columns: repeat(2, 1fr); }
    .loop-grid { grid-template-columns: 1fr; }
    .detail-grid { grid-template-columns: 1fr; }
    .cluster-bar .bar-stats { display: none; }
  }

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

  <div class="grid-4">
    <div class="card"><div class="label">系统健康</div>
      <div class="value" id="cardHealth">--</div></div>
    <div class="card"><div class="label">FTS 版本</div>
      <div class="value blue" id="cardVersion">--</div></div>
    <div class="card"><div class="label">今日 Token</div>
      <div class="value" id="cardTokens">--</div></div>
    <div class="card"><div class="label">期货 Elite 因子</div>
      <div class="value" id="cardFactors">--</div>
      <div class="note" id="factorNote"></div></div>
  </div>

  <div class="section-title">循环状态</div>
  <div class="loop-grid" id="loopGrid">
    <div class="loop-card"><div class="name">L1 <span class="badge loading">加载中...</span></div></div>
    <div class="loop-card"><div class="name">L2 <span class="badge loading">加载中...</span></div></div>
    <div class="loop-card"><div class="name">L3 <span class="badge loading">加载中...</span></div></div>
  </div>

  <div class="section-title">聚类分布 <span style="font-size:12px;color:var(--muted)" id="clusterSummaryNote"></span></div>
  <div id="clusterDistSection" class="cluster-dist-section"></div>

  <div class="cluster-filter" id="clusterFilter"></div>

  <div class="section-title">Elite 因子 <span style="font-size:12px;color:var(--muted)" id="factorSummary"></span></div>
  <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;overflow-x:auto">
    <table class="factor-table">
      <thead><tr>
        <th style="width:24px"></th>
        <th>名称</th>
        <th style="width:50px">代数</th>
        <th style="width:80px">IC</th>
        <th style="width:80px">夏普</th>
        <th style="width:90px">最大回撤</th>
        <th style="width:70px">月换手</th>
        <th style="width:60px">来源</th>
      </tr></thead>
      <tbody id="factorBody"><tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px">正在加载...</td></tr></tbody>
    </table>
  </div>

  <div class="section-title">候选因子（L1 池 · 未评估） <span style="font-size:12px;color:var(--muted)" id="candidateSummary"></span></div>
  <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;overflow-x:auto;max-height:480px;overflow-y:auto">
    <table class="factor-table">
      <thead><tr>
        <th>名称</th>
        <th style="width:80px">状态</th>
        <th style="width:90px">评估状态</th>
        <th style="width:130px">来源</th>
        <th style="width:70px">优先级</th>
        <th>父主题</th>
      </tr></thead>
      <tbody id="candidateBody"><tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px">正在加载...</td></tr></tbody>
    </table>
  </div>

  <footer>FTS v<span id="footerVersion">--</span> · 每 10 秒自动刷新</footer>
</div>

<script>
const CLUSTER_PALETTE = ['#22c55e','#3b82f6','#f97316','#a855f7','#ec4899',
                         '#06b6d4','#eab308','#14b8a6','#8b5cf6','#f43f5e',
                         '#f59e0b','#94a3b8'];

function getClusterColor(cid) {
  const i = parseInt(cid, 10);
  return CLUSTER_PALETTE[isNaN(i) ? 0 : (i % CLUSTER_PALETTE.length)];
}

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

let activeClusterFilter = null;
let cachedFactors = [];

function renderClusterDistribution(cluster_dist, cluster_summary, maxCount, applied) {
  const section = document.getElementById('clusterDistSection');
  const note = document.getElementById('clusterSummaryNote');
  if (!applied || !cluster_dist || Object.keys(cluster_dist).length === 0) {
    section.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:4px 0">聚类不可用（信号数据缺失或数据源不可用），按列表展示</div>';
    note.textContent = '';
    return;
  }
  const maxC = maxCount || 1;
  const totalClusters = Object.keys(cluster_dist).length;
  let html = '';
  for (const cid of Object.keys(cluster_dist)) {
    const count = cluster_dist[cid];
    const pct = (count / maxC * 100).toFixed(0);
    const color = getClusterColor(cid);
    const summary = (cluster_summary || []).find(s => String(s.cluster_id) === String(cid));
    const rep = summary ? summary.rep_name : '-';
    const avgIc = summary ? summary.avg_ic.toFixed(4) : '--';
    const avgSharpe = summary ? summary.avg_sharpe.toFixed(2) : '--';
    const isActive = activeClusterFilter === cid;
    html += '<div class="cluster-bar" onclick="toggleClusterFilter(\''+cid+'\')" style="opacity:'+(activeClusterFilter && !isActive ? 0.4 : 1)+'">'
      + '<div class="tag"><span class="cluster-color" style="background:'+color+'"></span>簇'+cid+'</div>'
      + '<div class="bar-track"><div class="bar-fill" style="width:'+pct+'%;background:'+color+'"><span>'+count+'</span></div></div>'
      + '<div class="bar-stats">代表:'+rep+' · IC: '+avgIc+' · Sharpe: '+avgSharpe+'</div></div>';
  }
  section.innerHTML = html;
  note.textContent = '共 '+totalClusters+' 个信号簇';
}

function toggleClusterFilter(cid) {
  activeClusterFilter = (activeClusterFilter === cid) ? null : cid;
  renderClusterFilterChips();
  renderFactorTable(cachedFactors);
}

function renderClusterFilterChips() {
  const filter = document.getElementById('clusterFilter');
  const clusters = {};
  (cachedFactors || []).forEach(function(f) {
    if (f.cluster_id !== undefined) clusters[f.cluster_id] = true;
  });
  let html = '<span class="chip all'+(activeClusterFilter ? '' : ' active')+'" onclick="activeClusterFilter=null;renderClusterFilterChips();renderFactorTable(cachedFactors)">全部</span>';
  for (var cid in clusters) {
    html += '<span class="chip'+(activeClusterFilter === cid ? ' active' : '')+'" onclick="toggleClusterFilter(\''+cid+'\')">簇'+cid+'</span>';
  }
  filter.innerHTML = html;
}

function toggleDetail(factorId) {
  var row = document.getElementById('detail-'+factorId);
  if (row) {
    var isOpen = row.classList.contains('open');
    var allRows = document.querySelectorAll('.detail-row');
    for (var i = 0; i < allRows.length; i++) { allRows[i].classList.remove('open'); }
    var allIcons = document.querySelectorAll('.expand-icon');
    for (var i = 0; i < allIcons.length; i++) { allIcons[i].classList.remove('open'); }
    if (!isOpen) {
      row.classList.add('open');
      var icon = document.querySelector('[data-expand="'+factorId+'"]');
      if (icon) icon.classList.add('open');
    }
  }
}

function buildDetailHtml(f) {
  var eco = f.economic_logic || {};
  var qs = f.quality_score;
  var status = f.status || 'active';
  var statusClass = 'active';
  if (['active','observing','decaying','critical_decay','retired','rejected'].indexOf(status) >= 0) statusClass = status;
  var grade = qs ? qs.grade : 'N';
  var gradeClass = (['A','B','C'].indexOf(grade) >= 0) ? grade : 'N';
  var monoText = f.monotonicity ? '是' : '否';
  var monoClass = f.monotonicity ? 'green' : 'red';
  var html = '<div class="detail-panel"><div class="detail-grid">'
    + '<div class="detail-section-title">基础信息</div>'
    + '<div class="detail-item"><span class="dl">因子 ID</span><span class="dv mono">'+sanitize(f.factor_id)+'</span></div>'
    + '<div class="detail-item"><span class="dl">市场</span><span class="dv">'+sanitize(f.market)+'</span></div>'
    + '<div class="detail-item"><span class="dl">状态</span><span class="dv"><span class="status-tag '+statusClass+'">'+status+'</span></span></div>'
    + '<div class="detail-section-title">评估指标</div>'
    + '<div class="detail-item"><span class="dl">评估状态</span><span class="dv">'+(f.evaluation_status === 'pending' ? '<span class="status-tag observing">未评估</span>' : '<span class="status-tag active">已评估</span>')+'</span></div>'
    + '<div class="detail-item"><span class="dl">ICIR</span><span class="dv">'+sanitize(f.icir)+'</span></div>'
    + '<div class="detail-item"><span class="dl">T 统计量</span><span class="dv">'+sanitize(f.t_stat)+'</span></div>'
    + '<div class="detail-item"><span class="dl">OOS 比率</span><span class="dv">'+sanitize(f.oos_ratio)+'</span></div>'
    + '<div class="detail-item"><span class="dl">单调性</span><span class="dv '+monoClass+'">'+monoText+'</span></div>'
    + '<div class="detail-section-title">经济逻辑评分</div>'
    + '<div class="detail-item"><span class="dl">理论 (Theory)</span><span class="dv">'+(eco.theory||0)+'</span></div>'
    + '<div class="detail-item"><span class="dl">行为 (Behavioral)</span><span class="dv">'+(eco.behavioral||0)+'</span></div>'
    + '<div class="detail-item"><span class="dl">微观结构 (Microstructure)</span><span class="dv">'+(eco.microstructure||0)+'</span></div>'
    + '<div class="detail-item"><span class="dl">制度 (Institutional)</span><span class="dv">'+(eco.institutional||0)+'</span></div>';
  if (eco.narrative) {
    html += '<div class="narrative-text">'+sanitize(eco.narrative)+'</div>';
  }
  if (qs) {
    html += '<div class="detail-section-title">质量评分卡</div>'
      + '<div class="detail-item"><span class="dl">总分</span><span class="dv">'+qs.total_score.toFixed(1)+'</span></div>'
      + '<div class="detail-item"><span class="dl">等级</span><span class="dv"><span class="grade-tag '+gradeClass+'">'+grade+'</span></span></div>';
  }
  return html + '</div></div>';
}

function renderFactorTable(factors) {
  cachedFactors = factors;
  var fBody = document.getElementById('factorBody');
  var hasCluster = factors.length > 0 && factors[0].cluster_id !== undefined;
  var filtered = activeClusterFilter ? factors.filter(function(f) { return String(f.cluster_id) === activeClusterFilter; }) : factors;
  if (filtered.length === 0) {
    fBody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px">暂无 elite 因子</td></tr>';
    document.getElementById('factorSummary').textContent = '0 个';
    return;
  }
  document.getElementById('factorSummary').textContent = '共 '+filtered.length+' 个';
  var currentCluster = undefined;
  var html = '';
  var detailHtml = '';
  for (var i = 0; i < filtered.length; i++) {
    var f = filtered[i];
    if (hasCluster && f.cluster_id !== currentCluster) {
      currentCluster = f.cluster_id;
      var color = getClusterColor(currentCluster);
      var clCount = filtered.filter(function(x) { return x.cluster_id === currentCluster; }).length;
      html += '<tr class="cluster-header"><td colspan="8">'
        + '<span class="cluster-color" style="background:'+color+'"></span>簇'+currentCluster
        + '<span class="cluster-count">'+clCount+' 个因子</span></td></tr>';
    }
    var notEvaluated = f.evaluation_status === 'pending';
    var sharpeVal = notEvaluated ? 0 : parseFloat(f.sharpe);
    var sharpeClass = sharpeVal >= 3 ? 'green' : (sharpeVal >= 1 ? '' : 'yellow');
    var icVal = notEvaluated ? 0 : parseFloat(f.ic);
    var icClass = icVal >= 0.05 ? 'green' : (icVal >= 0.03 ? '' : 'yellow');
    var ddVal = parseFloat(f.max_drawdown);
    var ddClass = ddVal < 0.1 ? 'green' : (ddVal < 0.2 ? '' : 'red');
    var evalTag = notEvaluated ? '<span class="status-tag observing">未评估</span>' : sanitize(f.ic);
    var evalSharpe = notEvaluated ? '' : sanitize(f.sharpe);
    html += '<tr class="clickable" onclick="toggleDetail(\''+f.factor_id+'\')">'
      + '<td><span class="expand-icon" data-expand="'+f.factor_id+'">▶</span></td>'
      + '<td style="font-weight:500">'+sanitize(f.name)+'</td>'
      + '<td>'+sanitize(f.generation)+'</td>'
      + '<td class="dv '+icClass+'">'+evalTag+'</td>'
      + '<td class="dv '+sharpeClass+'">'+evalSharpe+'</td>'
      + '<td class="dv '+ddClass+'">'+sanitize(f.max_drawdown)+'</td>'
      + '<td>'+sanitize(f.turnover)+'</td>'
      + '<td>'+sanitize(f.source)+'</td></tr>';
    detailHtml += '<tr class="detail-row" id="detail-'+f.factor_id+'"><td colspan="8" class="detail-cell">'+buildDetailHtml(f)+'</td></tr>';
  }
  fBody.innerHTML = html + detailHtml;
}

function renderCandidateTable(candidates) {
  var cBody = document.getElementById('candidateBody');
  if (!candidates || candidates.length === 0) {
    cBody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px">暂无候选因子</td></tr>';
    document.getElementById('candidateSummary').textContent = '0 个';
    return;
  }
  var pendingCnt = candidates.filter(function(c) { return c.status === 'pending'; }).length;
  document.getElementById('candidateSummary').textContent = '共 '+candidates.length+' 个 · 待注入 '+pendingCnt;
  var html = '';
  var lastSrc = '';
  for (var i = 0; i < candidates.length; i++) {
    var c = candidates[i];
    if (c.source !== lastSrc) {
      lastSrc = c.source;
      var srcCnt = candidates.filter(function(x) { return x.source === c.source; }).length;
      html += '<tr class="cluster-header"><td colspan="6">'
        + '<span class="cluster-color" style="background:#94a3b8"></span>'+sanitize(c.source)
        + '<span class="cluster-count">'+srcCnt+' 个</span></td></tr>';
    }
    var stTag;
    if (c.status === 'pending') stTag = '<span class="status-tag observing">待注入</span>';
    else if (c.status === 'elite') stTag = '<span class="status-tag active">已精英</span>';
    else if (c.status === 'injected') stTag = '<span class="status-tag active">已注入</span>';
    else stTag = '<span class="status-tag warn">'+sanitize(c.status)+'</span>';
    var evTag = c.evaluation_status === 'pending' ? '<span class="status-tag observing">未评估</span>' : '<span class="status-tag active">已评估</span>';
    html += '<tr>'
      + '<td style="font-weight:500">'+sanitize(c.name)+'</td>'
      + '<td>'+stTag+'</td>'
      + '<td>'+evTag+'</td>'
      + '<td>'+sanitize(c.source)+'</td>'
      + '<td>'+sanitize(c.priority)+'</td>'
      + '<td style="font-size:12px;color:var(--muted)">'+sanitize(c.parent_topic)+'</td></tr>';
  }
  cBody.innerHTML = html;
}

async function refresh() {
  try {
    var data = await fetchJSON('/api/status');
    var healthy = data.healthy;
    var hDot = document.getElementById('healthDot');
    hDot.className = 'dot ' + (healthy ? 'green' : 'red');
    document.getElementById('cardHealth').textContent = healthy ? '健康' : '异常';
    document.getElementById('cardHealth').className = 'value ' + (healthy ? 'green' : 'red');
    document.getElementById('cardVersion').textContent = sanitize(data.fts_version);
    document.getElementById('cardTokens').textContent = (data.total_tokens_today || 0).toLocaleString();

    var loops = data.loops || [];
    var loopGrid = document.getElementById('loopGrid');
    loopGrid.innerHTML = '';
    for (var i = 0; i < loops.length; i++) {
      var loop = loops[i];
      var l = loop.loop_name || '?';
      var st = loop.status || 'unknown';
      var bc = badgeClass(st);
      var card = document.createElement('div');
      card.className = 'loop-card';
      card.innerHTML = '<div class="name">'+sanitize(l)+' <span class="badge '+bc+'">'+badgeText(st)+'</span></div>'
        + '<div class="row">运行 ID: <span>'+(sanitize(loop.run_id).slice(0,28)||'-')+'</span></div>'
        + '<div class="row">更新于: <span>'+(sanitize(loop.last_run_at).slice(0,19)||'-')+'</span></div>'
        + '<div class="row">已过: <span>'+(loop.age_hours||0).toFixed(1)+' 小时</span></div>'
        + '<div class="row">Token: <span>'+((loop.tokens_consumed||0)).toLocaleString()+'</span></div>'
        + (loop.last_error ? '<div class="error">'+sanitize(loop.last_error)+'</div>' : '');
      loopGrid.appendChild(card);
    }

    document.getElementById('cardFactors').textContent = sanitize(data.elite_factor_count || 0);
    document.getElementById('cardFactors').className = 'value blue';
    var overload = data.overloaded_count;
    var retired = data.retired_count;
    var noteParts = [];
    if (overload > 0) noteParts.push('超载: '+overload);
    if (retired > 0) noteParts.push('已淘汰: '+retired);
    document.getElementById('factorNote').textContent = noteParts.length ? noteParts.join(' · ') : '';

    try {
      var factorsResp = await fetchJSON('/api/factors');
      var flist = factorsResp.factors || [];
      var clusterDist = factorsResp.cluster_distribution || {};
      var maxCount = 1;
      for (var k in clusterDist) { if (clusterDist[k] > maxCount) maxCount = clusterDist[k]; }
      var clusterSummary = factorsResp.cluster_summary || [];
      renderClusterDistribution(clusterDist, clusterSummary, maxCount, !!factorsResp.clustering_applied);
      cachedFactors = flist;
      renderClusterFilterChips();
      renderFactorTable(flist);
    } catch (e) {
      var fBody = document.getElementById('factorBody');
      fBody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px">加载失败</td></tr>';
    }

    try {
      var candResp = await fetchJSON('/api/candidates');
      renderCandidateTable(candResp.factors || []);
    } catch (e) {
      var cBody = document.getElementById('candidateBody');
      cBody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px">加载失败</td></tr>';
    }

    document.getElementById('footerVersion').textContent = sanitize(data.fts_version);
  } catch (e) {
    document.getElementById('healthDot').className = 'dot red';
    console.error('Refresh failed:', e);
  }
  updateTime();
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""


# ─── HTTP 处理 ──────────────────────────────────────────────


# 人审工作台页面（C8，2026-08-11）：内联样式无外链资源，微信排版兼容
REVIEW_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FTS Alpha 审查工作台</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }
  h1 { font-size: 22px; font-weight: 700; margin: 0 0 4px; }
  h1 span { color: #3b82f6; }
  .sub { color: #94a3b8; font-size: 13px; margin-bottom: 24px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 24px; }
  .section-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 12px; color: #94a3b8; padding: 8px 12px;
       border-bottom: 1px solid #334155; }
  td { font-size: 13px; padding: 8px 12px; border-bottom: 1px solid #334155; }
  input[type=text] { background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
                     border-radius: 6px; padding: 6px 8px; font-size: 12px; width: 100%; }
  button { border: none; border-radius: 6px; padding: 6px 12px; font-size: 12px;
           cursor: pointer; font-weight: 500; }
  .approve { background: #22c55e; color: #fff; }
  .reject { background: #ef4444; color: #fff; }
  button:hover { opacity: 0.85; }
  .tag { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 4px; }
  .tag.approved { background: rgba(34,197,94,0.15); color: #22c55e; }
  .tag.rejected { background: rgba(239,68,68,0.15); color: #ef4444; }
  .empty { color: #94a3b8; text-align: center; padding: 24px; }
  .mode-badge { display: inline-block; font-size: 11px; padding: 2px 10px; border-radius: 999px;
                background: rgba(59,130,246,0.15); color: #3b82f6; margin-left: 8px; }
  .mode-badge.manual { background: rgba(234,179,8,0.15); color: #eab308; }
  .need-human { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 4px;
                background: rgba(234,179,8,0.15); color: #eab308; }
  .btn-auto { background: #3b82f6; color: #fff; margin-right: 8px; }
</style>
</head>
<body>
<h1>FTS <span>Alpha 审查工作台</span></h1>
<div class="sub">人工审查前置 · approve/reject 回写 factor_reviews 表 + 经验链 · 与 CLI 同一后端<span class="mode-badge" id="modeBadge">模式: auto</span></div>

<div class="card">
  <div class="section-title">待审查队列 <span style="font-size:12px;color:#94a3b8" id="pendingCount"></span>
    <button class="btn-auto" onclick="runAutoReview()">运行机审</button>
    <span style="font-size:12px;color:#94a3b8">机审: 正常自动批准 / 低质自动驳回 / 异常值转人审</span>
  </div>
  <table>
    <thead><tr><th>因子 ID</th><th>名称</th><th>市场</th><th>来源</th><th>IC</th><th>Sharpe</th><th>标记</th><th>意见</th><th style="width:150px">操作</th></tr></thead>
    <tbody id="pendingBody"><tr><td colspan="9" class="empty">正在加载...</td></tr></tbody>
  </table>
</div>

<div class="card">
  <div class="section-title">最近审查记录</div>
  <table>
    <thead><tr><th>因子</th><th>决定</th><th>意见</th><th>审查人</th><th>时间</th></tr></thead>
    <tbody id="historyBody"><tr><td colspan="5" class="empty">正在加载...</td></tr></tbody>
  </table>
</div>

<script>
async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}
function sanitize(s) { return String(s ?? '--'); }

async function loadPending() {
  try {
    const d = await fetchJSON('/api/review/pending');
    const list = d.items || [];
    const mb = document.getElementById('modeBadge');
    if (mb) { mb.textContent = '模式: ' + sanitize(d.mode || 'auto'); mb.className = 'mode-badge' + (d.mode === 'manual' ? ' manual' : ''); }
    document.getElementById('pendingCount').textContent = '共 ' + list.length + ' 个';
    const body = document.getElementById('pendingBody');
    if (!list.length) { body.innerHTML = '<tr><td colspan="9" class="empty">暂无待审查因子</td></tr>'; return; }
    body.innerHTML = list.map(function (f) {
      return '<tr>'
        + '<td style="font-family:monospace;font-size:12px">' + sanitize(f.factor_id) + '</td>'
        + '<td>' + sanitize(f.name) + '</td>'
        + '<td>' + sanitize(f.market) + '</td>'
        + '<td>' + sanitize(f.source) + '</td>'
        + '<td>' + sanitize(f.ic) + '</td>'
        + '<td>' + sanitize(f.sharpe) + '</td>'
        + '<td>' + (f.needs_human ? '<span class="need-human" title="' + sanitize(f.review_reason) + '">需人工</span>' : '<span style="color:#22c55e">✓</span>') + '</td>'
        + '<td><input type="text" id="comment-' + sanitize(f.factor_id) + '" placeholder="审查意见(可选)"></td>'
        + '<td><button class="approve" onclick="decide(\'' + sanitize(f.factor_id) + '\',\'approve\')">批准</button> '
        + '<button class="reject" onclick="decide(\'' + sanitize(f.factor_id) + '\',\'reject\')">驳回</button></td>'
        + '</tr>';
    }).join('');
  } catch (e) {
    document.getElementById('pendingBody').innerHTML = '<tr><td colspan="9" class="empty">加载失败: ' + sanitize(e) + '</td></tr>';
  }
}

async function runAutoReview() {
  try {
    const r = await fetch('/api/review/auto', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    const d = await r.json();
    if (d.error) { alert('机审失败: ' + d.error); return; }
    alert('机审完成: 批准 ' + d.auto_approved + ' / 驳回 ' + d.auto_rejected + ' / 转人审 ' + (d.needs_human || []).length);
    loadPending(); loadHistory();
  } catch (e) { alert('机审失败: ' + e); }
}

async function decide(factorId, decision) {
  const comment = (document.getElementById('comment-' + factorId) || {}).value || '';
  try {
    const r = await fetch('/api/review/' + decision, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ factor_id: factorId, comment: comment, reviewer: 'web' }),
    });
    const d = await r.json();
    alert((decision === 'approve' ? '已批准: ' : '已驳回: ') + factorId + (d.error ? ('\n' + d.error) : ''));
    loadPending(); loadHistory();
  } catch (e) { alert('操作失败: ' + e); }
}

async function loadHistory() {
  try {
    const d = await fetchJSON('/api/review/history');
    const list = d.items || [];
    const body = document.getElementById('historyBody');
    if (!list.length) { body.innerHTML = '<tr><td colspan="5" class="empty">暂无审查记录</td></tr>'; return; }
    body.innerHTML = list.map(function (h) {
      return '<tr><td>' + sanitize(h.name) + '</td>'
        + '<td><span class="tag ' + sanitize(h.decision) + '">' + sanitize(h.decision) + '</span></td>'
        + '<td>' + sanitize(h.comment) + '</td>'
        + '<td>' + sanitize(h.reviewer) + '</td>'
        + '<td>' + sanitize(h.reviewed_at) + '</td></tr>';
    }).join('');
  } catch (e) { /* 忽略历史加载失败 */ }
}

loadPending(); loadHistory();
setInterval(loadPending, 15000);
</script>
</body>
</html>
"""


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

    def _respond_text(self, text: str, content_type: str = "text/plain; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def _build_status(self) -> dict:
        """构建 /api/status 响应。"""
        from . import check_all_status, SystemStatusReport

        try:
            root = Path.cwd()
            report: SystemStatusReport = check_all_status(root)
        except Exception:  # noqa: BLE001
            report = SystemStatusReport(
                healthy=False,
                loops=[],
                fts_version="?",
                any_circuit_broken=False,
                any_stale=False,
                total_tokens_today=0,
            )

        # 从 DuckDB 查询精英因子计数
        elite_count = 0
        overload_count = 0
        retired_count = 0

        def _fallback_json_stats() -> None:
            """DuckDB 不可用时降级到 JSON 文件统计。"""
            nonlocal elite_count, overload_count, retired_count
            elite_dir = root / "memory" / "knowledge" / "factors" / "futures_elite"
            if elite_dir.exists():
                elite_count = len(list(elite_dir.glob("*.json")))
            overload_dir = root / "memory" / "knowledge" / "factors" / "overloaded"
            if overload_dir.exists():
                overload_count = len(list(overload_dir.glob("*.json")))
            retired_dir = root / "memory" / "knowledge" / "factors" / "retired"
            if retired_dir.exists():
                retired_count = len(list(retired_dir.glob("*.json")))

        try:
            from ..factor_engine.factor_db.schema import DATABASE_PATH as _db_path
            import duckdb as _duckdb

            if _db_path.exists():
                _conn = _duckdb.connect(str(_db_path), read_only=True)
                try:
                    # 精英因子计数
                    row = _conn.execute(
                        "SELECT COUNT(*) FROM factor_catalog WHERE is_elite = TRUE AND status != 'deleted'"
                    ).fetchone()
                    elite_count = int(row[0]) if row else 0

                    # 按状态计数（overloaded 状态 + retired 状态）
                    status_row = _conn.execute("""
                        SELECT
                            COUNT(*) FILTER (WHERE status = 'overloaded') as overloaded,
                            COUNT(*) FILTER (WHERE status = 'retired') as retired
                        FROM factor_catalog
                    """).fetchone()
                    if status_row:
                        overload_count = int(status_row[0] or 0)
                        retired_count = int(status_row[1] or 0)
                finally:
                    _conn.close()
            else:
                # DuckDB 不存在：直接走 JSON 文件统计
                logger.warning("[ui] DuckDB 不存在，回退到 JSON 文件统计")
                _fallback_json_stats()
        except Exception:  # noqa: BLE001
            logger.warning("[ui] DuckDB 查询失败，回退到 JSON 文件统计")
            _fallback_json_stats()

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
                    "loop_name": loop.loop_name,
                    "healthy": loop.healthy,
                    "status": loop.status,
                    "run_id": loop.run_id,
                    "last_run_at": loop.last_run_at,
                    "last_error": loop.last_error,
                    "tokens_consumed": loop.tokens_consumed,
                    "age_hours": loop.age_hours,
                    "version": loop.version,
                }
                for loop in report.loops
            ],
        }
        return data

    def _build_factor_list(self) -> dict:
        """构建 /api/factors 响应，包含信号聚类分组和详细信息。

        数据源优先级: DuckDB → JSON 文件降级。
        """
        try:
            from ..factor_engine.factor_db.schema import DATABASE_PATH as _db_path

            if _db_path.exists():
                return self._build_factor_list_from_duckdb(_db_path)
        except Exception:  # noqa: BLE001
            logger.warning("[ui] DuckDB 因子查询失败，降级到 JSON 文件")
        return self._build_factor_list_json_fallback()

    def _apply_cluster_groups(
        self,
        factors: list[dict[str, Any]],
        code_factors: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """按信号相关性聚类给 factors 标注 cluster_id 并重排序。

        Args:
            factors: 展示用因子列表（无 code）
            code_factors: 含 factor_id/code/params 的完整精英列表（与 factors 同集合）

        Returns:
            (factors, cluster_meta)；cluster_meta = {applied, distribution, summary}
            聚类失败或全单因子簇时 applied=False，前端不分组展示。
        """
        cluster_meta: dict[str, Any] = {"applied": False, "distribution": {}, "summary": []}
        cluster_result = _cluster_factors_by_signal(code_factors)
        if not cluster_result:
            return factors, cluster_meta

        cluster_order = cluster_result["cluster_order"]
        cluster_members = cluster_result["cluster_members"]

        fid_to_factor = {f.get("factor_id"): f for f in factors}
        cluster_stats: dict[int, dict[str, Any]] = {}
        for cid in cluster_order:
            members = cluster_members.get(cid, [])
            ic_sum = sharpe_sum = 0.0
            cnt = 0
            best: tuple[str, float] | None = None
            for mfid in members:
                f = fid_to_factor.get(mfid)
                if f is None:
                    continue
                ic = float(f.get("ic", 0) or 0)
                sh = float(f.get("sharpe", 0) or 0)
                ic_sum += ic
                sharpe_sum += sh
                cnt += 1
                if best is None or sh > best[1]:
                    best = (str(f.get("name", mfid)), sh)
            if cnt == 0:
                continue
            cluster_stats[cid] = {
                "cluster_id": cid,
                "size": cnt,
                "avg_ic": round(ic_sum / cnt, 4),
                "avg_sharpe": round(sharpe_sum / cnt, 2),
                "rep_name": best[0] if best else "-",
            }
            for mfid in members:
                f = fid_to_factor.get(mfid)
                if f is not None:
                    f["cluster_id"] = cid

        # 全部为单因子簇 → 无分组意义，前端直接按列表展示
        if all(c["size"] <= 1 for c in cluster_stats.values()):
            return factors, cluster_meta

        cluster_meta["applied"] = True
        cluster_meta["distribution"] = {str(cid): c["size"] for cid, c in cluster_stats.items()}
        cluster_meta["summary"] = list(cluster_stats.values())
        order_index = {cid: i for i, cid in enumerate(cluster_order)}
        factors.sort(
            key=lambda f: (
                order_index.get(f.get("cluster_id"), 999),
                -float(f.get("sharpe", 0) or 0),
            )
        )
        return factors, cluster_meta

    def _build_factor_list_from_duckdb(self, db_path: Path) -> dict:
        """从 DuckDB 查询精英因子列表（含信号聚类分组和详细信息）。"""
        import duckdb as _duckdb

        conn = _duckdb.connect(str(db_path), read_only=True)
        try:
            # 检测可用表，构建动态 JOIN
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
            }
            has_evaluations = "factor_evaluations" in tables
            has_quality_scores = "factor_quality_scores" in tables

            select_cols = """
                fc.factor_id, fc.name, fc.generation, fc.source,
                fc.market, fc.status,
                fc.sharpe, fc.ic, fc.icir, fc.max_drawdown, fc.turnover_monthly,
                fc.economic_logic, fc.metadata
            """
            from_clause = "FROM factor_catalog fc"
            joins = ""

            if has_evaluations:
                select_cols += """
                    , fe.level_1_t_stat, fe.level_1_monotonicity, fe.level_1_oos_ratio,
                    fe.level_2_theory_score, fe.level_2_behavioral_score,
                    fe.level_2_microstructure_score, fe.level_2_institutional_score
                """
                joins += """
                    LEFT JOIN (
                        SELECT factor_id,
                            MAX(level_1_t_stat) AS level_1_t_stat,
                            BOOL_OR(level_1_monotonicity) AS level_1_monotonicity,
                            MAX(level_1_oos_ratio) AS level_1_oos_ratio,
                            MAX(level_2_theory_score) AS level_2_theory_score,
                            MAX(level_2_behavioral_score) AS level_2_behavioral_score,
                            MAX(level_2_microstructure_score) AS level_2_microstructure_score,
                            MAX(level_2_institutional_score) AS level_2_institutional_score
                        FROM factor_evaluations
                        GROUP BY factor_id
                    ) fe ON fc.factor_id = fe.factor_id
                """
            else:
                select_cols += """
                    , NULL AS level_1_t_stat, NULL AS level_1_monotonicity,
                    NULL AS level_1_oos_ratio,
                    NULL AS level_2_theory_score, NULL AS level_2_behavioral_score,
                    NULL AS level_2_microstructure_score, NULL AS level_2_institutional_score
                """

            if has_quality_scores:
                select_cols += """
                    , fqs.total_score AS quality_total_score,
                    fqs.grade AS quality_grade,
                    fqs.dimension_scores AS quality_dimension_scores
                """
                joins += """
                    LEFT JOIN (
                        SELECT factor_id,
                            MAX(total_score) AS total_score,
                            MAX(grade) AS grade,
                            MAX(dimension_scores) AS dimension_scores
                        FROM factor_quality_scores
                        GROUP BY factor_id
                    ) fqs ON fc.factor_id = fqs.factor_id
                """
            else:
                select_cols += """
                    , NULL AS quality_total_score,
                    NULL AS quality_grade,
                    NULL AS quality_dimension_scores
                """

            sql = f"""
                SELECT {select_cols}
                {from_clause} {joins}
                WHERE fc.is_elite = TRUE AND fc.status != 'deleted'
                ORDER BY fc.sharpe DESC
            """
            rel = conn.execute(sql)
            cols = [desc[0] for desc in rel.description]  # fetchall 前取列名
            result = rel.fetchall()

            factors: list[dict] = []

            for row in result:
                row_dict = dict(zip(cols, row))

                # 解析 economic_logic JSON
                eco = {}
                raw_eco = row_dict.get("economic_logic")
                if raw_eco:
                    try:
                        eco = json.loads(raw_eco) if isinstance(raw_eco, str) else raw_eco
                    except (json.JSONDecodeError, TypeError):
                        eco = {}

                # 解析 metadata 中的 quality_score
                quality_score_val = None
                raw_meta = row_dict.get("metadata")
                if raw_meta:
                    try:
                        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                        quality_score_val = meta.get("quality_score") if isinstance(meta, dict) else None
                    except (json.JSONDecodeError, TypeError):
                        quality_score_val = None

                # 优先使用 factor_quality_scores 表中的数据，否则从 metadata 获取
                qs_total = row_dict.get("quality_total_score")
                if qs_total is not None:
                    dim_scores = {}
                    raw_dims = row_dict.get("quality_dimension_scores")
                    if raw_dims:
                        try:
                            dim_scores = json.loads(raw_dims) if isinstance(raw_dims, str) else raw_dims
                        except (json.JSONDecodeError, TypeError):
                            dim_scores = {}
                    qs = {
                        "total_score": float(qs_total),
                        "grade": str(row_dict.get("quality_grade", "N/A")),
                        "dimension_scores": dim_scores,
                    }
                elif quality_score_val:
                    qs = {
                        "total_score": float(quality_score_val.get("total_score", 0)),
                        "grade": str(quality_score_val.get("grade", "N/A")),
                        "dimension_scores": quality_score_val.get("dimension_scores", {}),
                    }
                else:
                    qs = None

                ic_val = float(row_dict.get("ic", 0) or 0)
                sharpe_val = float(row_dict.get("sharpe", 0) or 0)

                factors.append(
                    {
                        "factor_id": str(row_dict.get("factor_id", "")),
                        "name": str(row_dict.get("name", "")),
                        "generation": str(row_dict.get("generation", "?")),
                        "ic": f"{ic_val:.4f}",
                        "sharpe": f"{sharpe_val:.2f}",
                        "source": str(row_dict.get("source", "?")),
                        "market": str(row_dict.get("market", "futures")),
                        "icir": f"{float(row_dict.get('icir', 0) or 0):.4f}",
                        "max_drawdown": f"{float(row_dict.get('max_drawdown', 0) or 0):.2%}",
                        "turnover": f"{float(row_dict.get('turnover_monthly', 0) or 0):.2f}",
                        "oos_ratio": f"{float(row_dict.get('level_1_oos_ratio', 0) or 0):.2f}",
                        "monotonicity": bool(row_dict.get("level_1_monotonicity", False)),
                        "t_stat": f"{float(row_dict.get('level_1_t_stat', 0) or 0):.2f}",
                        "economic_logic": {
                            "theory": int(eco.get("theory", 0)),
                            "behavioral": int(eco.get("behavioral", 0)),
                            "microstructure": int(eco.get("microstructure", 0)),
                            "institutional": int(eco.get("institutional", 0)),
                            "narrative": str(eco.get("narrative", "")),
                        },
                        "quality_score": qs,
                        "status": str(row_dict.get("status", "active")),
                        "evaluation_status": "evaluated" if (ic_val > 0 and sharpe_val > 0) else "pending",
                    }
                )

            # 信号相关性聚类分组（聚类查询独立执行，code/params 不入 UI 响应）
            code_factors: list[dict[str, Any]] = []
            try:
                code_rows = conn.execute(
                    "SELECT factor_id, code, params FROM factor_catalog WHERE is_elite = TRUE AND status != 'deleted'"
                ).fetchall()
                for cr in code_rows:
                    p = cr[2]
                    if isinstance(p, str):
                        try:
                            p = json.loads(p)
                        except (json.JSONDecodeError, TypeError):
                            p = {}
                    code_factors.append(
                        {
                            "factor_id": str(cr[0] or ""),
                            "code": str(cr[1] or "") if cr[1] is not None else "",
                            "params": p or {},
                        }
                    )
            except Exception:  # noqa: BLE001
                logger.warning("[ui] 聚类数据查询失败，降级不分组")
                code_factors = []
            factors, cluster_meta = self._apply_cluster_groups(factors, code_factors)

            return {
                "factors": factors,
                "count": len(factors),
                "cluster_distribution": cluster_meta["distribution"],
                "cluster_summary": cluster_meta["summary"],
                "clustering_applied": cluster_meta["applied"],
                "source": "duckdb",
            }
        finally:
            conn.close()

    def _build_factor_list_json_fallback(self) -> dict:
        """降级方案：从 JSON 文件读取精英因子数据。"""
        import json as _json

        elite_dir = Path.cwd() / "memory" / "knowledge" / "factors" / "futures_elite"
        factors: list[dict] = []
        code_factors: list[dict[str, Any]] = []

        if elite_dir.exists():
            for fp in sorted(elite_dir.glob("*.json"), reverse=True)[:200]:
                if fp.stem.startswith("_"):
                    continue
                try:
                    raw = _json.loads(fp.read_text(encoding="utf-8"))
                    bt = raw.get("evaluation", {}).get("level_1_backtest", {})
                    eco = raw.get("economic_logic", {})
                    qs = raw.get("quality_score", {})

                    ic_val = float(bt.get("ic", 0))
                    sharpe_val = float(bt.get("sharpe", 0))

                    factors.append(
                        {
                            "factor_id": raw.get("factor_id", fp.stem),
                            "name": raw.get("name", fp.stem),
                            "generation": raw.get("generation", "?"),
                            "ic": f"{ic_val:.4f}",
                            "sharpe": f"{sharpe_val:.2f}",
                            "source": raw.get("source", "?"),
                            "market": raw.get("market", "futures"),
                            "icir": f"{bt.get('icir', 0):.4f}",
                            "max_drawdown": f"{bt.get('max_drawdown', 0):.2%}",
                            "turnover": f"{bt.get('turnover_monthly', 0):.2f}",
                            "oos_ratio": f"{bt.get('oos_ratio', 0):.2f}",
                            "monotonicity": bool(bt.get("monotonicity", False)),
                            "t_stat": f"{bt.get('t_stat', 0):.2f}",
                            "economic_logic": {
                                "theory": int(eco.get("theory", 0)),
                                "behavioral": int(eco.get("behavioral", 0)),
                                "microstructure": int(eco.get("microstructure", 0)),
                                "institutional": int(eco.get("institutional", 0)),
                                "narrative": str(eco.get("narrative", "")),
                            },
                            "quality_score": {
                                "total_score": float(qs.get("total_score", 0)),
                                "grade": str(qs.get("grade", "N/A")),
                                "dimension_scores": qs.get("dimension_scores", {}),
                            }
                            if qs and qs.get("total_score")
                            else None,
                            "status": str(raw.get("status", "active")),
                            "evaluation_status": "evaluated"
                            if (bt.get("ic", 0) > 0 and bt.get("sharpe", 0) > 0)
                            else "pending",
                        }
                    )
                    code_factors.append(
                        {
                            "factor_id": raw.get("factor_id", fp.stem),
                            "code": raw.get("code", "") or "",
                            "params": raw.get("params") or {},
                        }
                    )
                except Exception:  # noqa: BLE001
                    continue

        factors, cluster_meta = self._apply_cluster_groups(factors, code_factors)

        return {
            "factors": factors,
            "count": len(factors),
            "cluster_distribution": cluster_meta["distribution"],
            "cluster_summary": cluster_meta["summary"],
            "clustering_applied": cluster_meta["applied"],
            "source": "json_fallback",
        }

    def _build_candidate_list(self) -> dict:
        """构建 /api/candidates 响应 — L1 候选因子池（factor_pool.json）。

        候选因子由 L1 Meta Loop / 提取管道产出，尚未经 L2 评估，
        evaluation_status=pending（未评估），无 IC/Sharpe 指标。
        """
        import json as _json

        pool_path = Path.cwd() / "memory" / "knowledge" / "factors" / "factor_pool.json"
        if not pool_path.exists():
            return {"count": 0, "pending_count": 0, "factors": []}
        try:
            pool = _json.loads(pool_path.read_text(encoding="utf-8"))
        except (_json.JSONDecodeError, OSError):
            return {"count": 0, "pending_count": 0, "factors": []}

        factors = pool.get("factors", [])
        items = []
        for f in factors:
            items.append(
                {
                    "factor_id": str(f.get("factor_id", "")),
                    "name": str(f.get("name", "")),
                    "source": str(f.get("source", "?")),
                    "status": str(f.get("status", "?")),
                    "evaluation_status": str(f.get("evaluation_status", "pending")),
                    "priority": str(f.get("priority", "-")),
                    "parent_topic": str(f.get("parent_topic", "-") or "-"),
                }
            )
        # 待注入(pending)优先，组内按来源排序
        items.sort(key=lambda x: (0 if x["status"] == "pending" else 1, x["source"], x["name"]))
        return {
            "count": len(items),
            "pending_count": sum(1 for x in items if x["status"] == "pending"),
            "factors": items,
        }

    def _respond_metrics(self, text: str):
        """响应 Prometheus 指标文本。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def _build_metrics(self) -> str:
        """构建 Prometheus 指标响应（文本格式）。"""
        lines: list[str] = []

        # ── 基础指标 ──
        lines.append("# HELP fts_up FTS 服务是否在线")
        lines.append("# TYPE fts_up gauge")
        lines.append("fts_up 1")
        lines.append("")

        lines.append("# HELP fts_started_at FTS 启动时间戳")
        lines.append("# TYPE fts_started_at gauge")
        lines.append(f"fts_started_at {_metrics.get('fts_started_at', 0)}")
        lines.append("")

        lines.append("# HELP fts_tokens_consumed FTS 今日 Token 消耗")
        lines.append("# TYPE fts_tokens_consumed counter")
        lines.append(f"fts_tokens_consumed {_metrics.get('fts_tokens_consumed', 0)}")
        lines.append("")

        lines.append("# HELP fts_elite_factor_count Elite 因子数量")
        lines.append("# TYPE fts_elite_factor_count gauge")
        lines.append(f"fts_elite_factor_count {_metrics.get('fts_elite_factor_count', 0)}")
        lines.append("")

        # ── 循环状态指标 ──
        for loop_name in ("L1", "L2", "L3"):
            lines.append(f"# HELP fts_loop_status_{loop_name.lower()} 循环状态 (1=正常)")
            lines.append(f"# TYPE fts_loop_status_{loop_name.lower()} gauge")
            lines.append(f"fts_loop_status_{loop_name.lower()} {_metrics.get(f'fts_loop_status_{loop_name}', 0)}")
            lines.append("")

        lines.append("# HELP fts_combo_sharpe 组合夏普比率")
        lines.append("# TYPE fts_combo_sharpe gauge")
        lines.append(f"fts_combo_sharpe {_metrics.get('fts_combo_sharpe', 0.0)}")
        lines.append("")

        # ── 因子生命周期 / Regime 权重指标 (A.2/A.3) ──
        try:
            from .prometheus_metrics import metrics_registry

            lines.extend(metrics_registry.render())
        except Exception as e:  # noqa: BLE001
            logger.error("获取生命周期/Regime 指标失败: %s", e)

        # ── DataQualityMonitor 指标 ──
        dq_monitor = get_data_quality_monitor()
        if dq_monitor is not None:
            try:
                lines.append(dq_monitor.get_prometheus_metrics())
            except Exception as e:  # noqa: BLE001
                logger.error("获取 DataQualityMonitor 指标失败: %s", e)
                lines.append(f"# ERROR 获取数据质量指标失败: {e}")
        else:
            # DataQualityMonitor 未注册时输出默认零值
            lines.extend(
                [
                    "# HELP fts_data_quality_data_completeness_ratio 数据完整性比率 (1.0=完美)",
                    "# TYPE fts_data_quality_data_completeness_ratio gauge",
                    "fts_data_quality_data_completeness_ratio 1.0",
                    "",
                    "# HELP fts_data_quality_market_data_valid 市场数据是否有效 (1=有效)",
                    "# TYPE fts_data_quality_market_data_valid gauge",
                    "fts_data_quality_market_data_valid 1.0",
                    "",
                    "# HELP fts_data_quality_total_checks 数据质量检查总次数",
                    "# TYPE fts_data_quality_total_checks counter",
                    "fts_data_quality_total_checks 0",
                    "",
                    "# HELP fts_data_quality_total_alerts 告警总次数",
                    "# TYPE fts_data_quality_total_alerts counter",
                    "fts_data_quality_total_alerts 0",
                    "",
                    "# HELP fts_data_quality_critical_alerts 严重告警次数",
                    "# TYPE fts_data_quality_critical_alerts counter",
                    "fts_data_quality_critical_alerts 0",
                    "",
                    "# HELP fts_data_quality_registered_factors 已注册基准的因子数",
                    "# TYPE fts_data_quality_registered_factors gauge",
                    "fts_data_quality_registered_factors 0",
                    "",
                ]
            )

        return "\n".join(lines)

    def _build_data_source_metrics(self) -> dict:
        """构建 /metrics/data-sources 响应（Phase 14.5），带 5s 内存缓存。

        返回结构:
            fts_version, checked_at, healthy, summary, sources, latest_sync
        """
        ttl = int(os.environ.get("FTS_METRICS_CACHE_TTL", "5"))
        now = time.time()
        if _metrics_cache["data"] is not None and now - _metrics_cache["ts"] < ttl:
            return _metrics_cache["data"]

        try:
            from fts.cli import _build_default_aggregator

            agg = _build_default_aggregator()
            status = agg.get_source_status()
        except Exception:  # noqa: BLE001
            status = {}

        any_open = any(s.get("circuit_open", False) for s in status.values())
        total_success = sum(s.get("total_success", 0) for s in status.values())
        total_failure = sum(s.get("total_failure", 0) for s in status.values())
        total_attempts = total_success + total_failure
        success_rate = (total_success / total_attempts) if total_attempts else 0.0

        # 嵌入最近一次同步摘要（支持 .json 和 .json.gz）
        latest_sync: Optional[dict] = None
        try:
            lineage_dir = Path.cwd() / "data" / "_lineage"
            if lineage_dir.exists():
                candidates = sorted(
                    list(lineage_dir.glob("sync_summary_*.json")) + list(lineage_dir.glob("sync_summary_*.json.gz")),
                    reverse=True,
                )
                if candidates:
                    latest = candidates[0]
                    raw_bytes = latest.read_bytes()
                    if latest.suffix == ".gz":
                        import gzip

                        raw_bytes = gzip.decompress(raw_bytes)
                    raw = json.loads(raw_bytes.decode("utf-8"))
                    # 截断 failures 列表到 10 个，避免响应过大
                    if "failures" in raw and isinstance(raw["failures"], list):
                        raw["failures"] = raw["failures"][:10]
                    latest_sync = raw
        except Exception:  # noqa: BLE001
            latest_sync = None

        result = {
            "fts_version": _safe_version(),
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "healthy": not any_open and success_rate >= 0.5,
            "summary": {
                "any_circuit_open": any_open,
                "total_success": total_success,
                "total_failure": total_failure,
                "success_rate": round(success_rate, 4),
                "source_count": len(status),
            },
            "sources": status,
            "latest_sync": latest_sync,
        }
        _metrics_cache["ts"] = now
        _metrics_cache["data"] = result
        return result

    def _build_prometheus_metrics(self) -> str:
        """构建 /metrics 响应（Prometheus 文本格式，Phase 15.0）。"""
        lines: list[str] = []
        _add = lines.append

        try:
            from fts.cli import _build_default_aggregator

            agg = _build_default_aggregator()
            status = agg.get_source_status()
        except Exception:  # noqa: BLE001
            status = {}

        any_open = any(s.get("circuit_open", False) for s in status.values())
        total_success = sum(s.get("total_success", 0) for s in status.values())
        total_failure = sum(s.get("total_failure", 0) for s in status.values())
        total_attempts = total_success + total_failure
        success_rate = (total_success / total_attempts) if total_attempts else 0.0

        version = _safe_version()

        # 版本信息
        _add("# HELP fts_version FTS version info")
        _add("# TYPE fts_version gauge")
        _add(f'fts_version{{version="{version}"}} 1')
        _add("")

        # 数据源健康度
        _add("# HELP fts_data_source_success_rate Data source overall success rate")
        _add("# TYPE fts_data_source_success_rate gauge")
        _add(f"fts_data_source_success_rate {success_rate:.4f}")
        _add("")
        _add("# HELP fts_circuit_open Whether any data source circuit is open (1=open)")
        _add("# TYPE fts_circuit_open gauge")
        _add(f"fts_circuit_open {'1' if any_open else '0'}")
        _add("")
        _add("# HELP fts_data_source_count Number of registered data sources")
        _add("# TYPE fts_data_source_count gauge")
        _add(f"fts_data_source_count {len(status)}")
        _add("")
        _add("# HELP fts_data_source_total_requests Total data source requests")
        _add("# TYPE fts_data_source_total_requests counter")
        _add(f"fts_data_source_total_requests_total {total_attempts}")
        _add("")
        _add("# HELP fts_data_source_failures_total Total data source failures")
        _add("# TYPE fts_data_source_failures_total counter")
        _add(f"fts_data_source_failures_total {total_failure}")
        _add("")

        # 各个源的详细指标
        _add("# HELP fts_source_info Data source individual status")
        _add("# TYPE fts_source_info gauge")
        for name, s in status.items():
            circuit = "1" if s.get("circuit_open", False) else "0"
            consec = s.get("consecutive_failures", 0)
            _add(f'fts_source_info{{source="{name}",circuit_open="{circuit}",consecutive_failures="{consec}"}} 1')
        _add("")

        # elite 因子计数
        try:
            root = Path.cwd()
            elite_dir = root / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_count = len(list(elite_dir.glob("*.json"))) if elite_dir.exists() else 0
        except Exception:  # noqa: BLE001
            elite_count = 0
        _add("# HELP fts_elite_factor_count Number of elite factors")
        _add("# TYPE fts_elite_factor_count gauge")
        _add(f"fts_elite_factor_count {elite_count}")

        return "\n".join(lines) + "\n"

    def _build_health(self) -> dict:
        """构建 /health 响应（Phase 14.5 集成数据源状态）。"""
        data: dict[str, Any] = {
            "status": "ok",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            from fts.cli import _build_default_aggregator

            agg = _build_default_aggregator()
            status = agg.get_source_status()
            any_open = any(s.get("circuit_open", False) for s in status.values())
            data["data_sources"] = {
                "any_circuit_open": any_open,
                "source_count": len(status),
                "sources": {
                    name: {
                        "circuit_open": s.get("circuit_open", False),
                        "consecutive_failures": s.get("consecutive_failures", 0),
                        "total_success": s.get("total_success", 0),
                        "total_failure": s.get("total_failure", 0),
                    }
                    for name, s in status.items()
                },
            }
            if any_open:
                data["status"] = "degraded"
        except Exception as e:  # noqa: BLE001
            data["data_sources_error"] = str(e)
        return data

    # ── C8 人审工作台端点（复用 FactorReviewWorkflow，GAP-I102） ──

    def _build_review_pending(self) -> dict:
        """构建 /api/review/pending 响应（待审查队列 + 机审标注，C8-2）。"""
        try:
            from ..factor_engine.factor_inspector import (
                AutoReviewPolicy,
                FactorReviewWorkflow,
                load_review_mode,
            )

            items = FactorReviewWorkflow().list_pending(limit=200)
            policy = AutoReviewPolicy.from_env()
            annotated: list[dict[str, Any]] = []
            for f in items:
                f = dict(f)
                decision, reason = policy.classify(f.get("ic"), f.get("sharpe"))
                f["needs_human"] = decision is None
                f["review_reason"] = reason
                annotated.append(f)
            return {"count": len(items), "mode": load_review_mode(), "items": annotated}
        except Exception as e:  # noqa: BLE001
            logger.error("[ui] 审查队列查询失败: %s", e)
            return {"count": 0, "items": [], "mode": "auto", "error": str(e)}

    def _build_review_history(self) -> dict:
        """构建 /api/review/history 响应（最近审查记录）。"""
        try:
            from ..factor_engine.factor_inspector import FactorReviewWorkflow

            conn = FactorReviewWorkflow()._conn()  # noqa: SLF001
            try:
                rows = conn.execute(
                    """
                    SELECT r.factor_id, r.decision, r.comment, r.reviewer, r.reviewed_at,
                           COALESCE(c.name, r.factor_id) AS name
                    FROM factor_reviews r
                    LEFT JOIN factor_catalog c ON c.factor_id = r.factor_id
                    ORDER BY r.reviewed_at DESC
                    LIMIT 50
                    """
                ).fetchall()
                cols = ["factor_id", "decision", "comment", "reviewer", "reviewed_at", "name"]
                return {"count": len(rows), "items": [dict(zip(cols, r)) for r in rows]}
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            logger.error("[ui] 审查历史查询失败: %s", e)
            return {"count": 0, "items": [], "error": str(e)}

    def _handle_review_decision(self, decision: str) -> None:
        """处理 POST /api/review/{approve|reject}：回写审查决定（与 CLI 同一后端）。"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            payload = json.loads(body or "{}")
            factor_id = str(payload.get("factor_id", "")).strip()
            if not factor_id:
                self._respond_json({"error": "factor_id 必填"}, 400)
                return
            from ..factor_engine.factor_inspector import FactorReviewWorkflow

            workflow = FactorReviewWorkflow()
            comment = str(payload.get("comment", "") or "")
            reviewer = str(payload.get("reviewer", "") or "web")
            result = (
                workflow.approve(factor_id, comment, reviewer)
                if decision == "approve"
                else workflow.reject(factor_id, comment, reviewer)
            )
            self._respond_json(result)
        except Exception as e:  # noqa: BLE001
            logger.error("[ui] 审查操作失败: %s", e)
            self._respond_json({"error": str(e)}, 500)

    def _handle_review_auto(self) -> None:
        """处理 POST /api/review/auto：批量机审（C8-2）。

        正常自动批准、低质自动驳回、异常值转人审；manual 模式拒绝（403）。
        """
        try:
            from ..factor_engine.factor_inspector import FactorReviewWorkflow

            payload: dict[str, Any] = {}
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = self.rfile.read(length).decode("utf-8")
                try:
                    payload = json.loads(body or "{}")
                except json.JSONDecodeError:
                    payload = {}
            force = bool(payload.get("force", False))
            result = FactorReviewWorkflow().auto_review(limit=200, force=force)
            self._respond_json(result)
        except ValueError as e:  # manual 模式拒绝
            self._respond_json({"error": str(e)}, 403)
        except Exception as e:  # noqa: BLE001
            logger.error("[ui] 机审执行失败: %s", e)
            self._respond_json({"error": str(e)}, 500)

    def do_GET(self):  # noqa: N802
        path = self.path.rstrip("/")

        if path == "" or path == "/":
            self._respond_html(DASHBOARD_HTML)

        elif path == "/api/status":
            self._respond_json(self._build_status())

        elif path == "/api/factors":
            self._respond_json(self._build_factor_list())

        elif path == "/api/candidates":
            self._respond_json(self._build_candidate_list())

        elif path == "/review":
            self._respond_html(REVIEW_HTML)

        elif path == "/api/review/pending":
            self._respond_json(self._build_review_pending())

        elif path == "/api/review/history":
            self._respond_json(self._build_review_history())

        elif path == "/metrics":
            self._respond_metrics(self._build_metrics())

        elif path == "/metrics/data-sources":
            self._respond_json(self._build_data_source_metrics())

        elif path == "/health":
            self._respond_json(self._build_health())

        elif path == "/api/v1/risk/status":
            self._respond_json(_build_risk_status())

        elif path == "/api/v1/live/factors":
            self._respond_json(_build_live_factors())

        elif path.startswith("/api/v1/live/factors/") and path.endswith("/deviation"):
            factor_id = path[len("/api/v1/live/factors/") : -len("/deviation")]
            self._respond_json(_build_live_deviation(factor_id))

        elif path == "/api/workflow/stages":
            self._respond_json(self._workflow_stages())

        elif path == "/api/workflow/runs":
            self._respond_json(self._workflow_runs())

        elif path == "/api/workflow/status":
            self._respond_json({"ok": True, "runs": _get_workflow()[0].list_runs(limit=5)})

        elif path == "/api/workflow/qa/board":
            self._respond_json(self._workflow_qa_board())

        elif path.startswith("/api/workflow/runs/"):
            run_id = path[len("/api/workflow/runs/") :]
            if "/" in run_id:
                self._respond_json({"error": "bad path"}, 400)
            else:
                self._respond_json(self._workflow_run_detail(run_id))

        elif path == "/workflow" or path.startswith("/workflow/") or path.startswith("/assets/"):
            self._serve_workflow_static(path)

        else:
            self._respond_json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        """处理 POST 请求（信号提交 / C8 审查决定）。"""
        path = self.path.rstrip("/")

        if path == "/api/v1/signal/submit":
            self._handle_signal_submit()
        elif path == "/api/review/approve":
            self._handle_review_decision("approve")
        elif path == "/api/review/reject":
            self._handle_review_decision("reject")
        elif path == "/api/review/auto":
            self._handle_review_auto()
        elif path == "/api/workflow/runs":
            self._respond_json(self._workflow_create_run())
        elif path.startswith("/api/workflow/runs/"):
            parts = path[len("/api/workflow/runs/") :].split("/")
            if len(parts) == 2 and parts[1] == "run_all":
                self._respond_json(self._workflow_run_all(parts[0]))
            elif len(parts) == 6 and parts[1] == "stage" and parts[3] == "action" and parts[5] == "run":
                self._respond_json(self._workflow_run_action(parts[0], parts[2], parts[4]))
            else:
                self._respond_json({"error": "not found"}, 404)
        else:
            self._respond_json({"error": "not found"}, 404)

    def _handle_signal_submit(self):
        """处理信号提交：验证 → 风控 → 模拟成交。"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            signal = json.loads(body or "{}")
        except Exception as e:  # noqa: BLE001
            self._respond_json({"error": f"invalid json: {e}"}, 400)
            return

        # 1. 格式验证
        from ..factor_engine.signal_contract import SignalValidator

        errors = SignalValidator().validate(signal)
        if errors:
            self._respond_json({"approved": False, "errors": errors}, 422)
            return

        # 2. 风控检查
        risk = _get_risk_manager()
        account = _sim_account_status()
        positions = _sim_positions()
        result = risk.check(signal, account, positions)
        _record_risk_metrics(result)

        if not result.get("approved"):
            self._respond_json(
                {
                    "approved": False,
                    "violations": result.get("blocking_violations", []),
                },
                403,
            )
            return

        # 3. 模拟成交
        try:
            adapter = _get_sim_adapter()
            if not adapter.is_connected():
                adapter.connect({})
            order = adapter.submit_signal(signal)
        except Exception as e:  # noqa: BLE001
            logger.exception("[ui] 模拟成交失败")
            self._respond_json({"approved": False, "error": str(e)}, 500)
            return

        self._respond_json({"approved": True, "order": order})

    # ─── WorkFlow API（CTA 手册端到端工作流） ──────────────────
    def _workflow_stages(self) -> list[dict]:
        from ..workflow import get_stages

        return get_stages()

    def _workflow_runs(self) -> dict:
        store, _ = _get_workflow()
        return {"runs": store.list_runs(limit=50)}

    def _workflow_run_detail(self, run_id: str) -> dict:
        store, _ = _get_workflow()
        run = store.get_run(run_id)
        if run is None:
            return {"error": f"run not found: {run_id}"}
        return {"run": run, "stage_runs": store.get_stage_runs(run_id)}

    def _workflow_qa_board(self) -> dict:
        """质检状态看板（QA 7 状态分布，手册 6.8）。"""
        try:
            from ..factor_engine.factor_db.repository import FactorRepository
            from ..factor_engine.qa import status_board

            repo = FactorRepository(market="futures")
            try:
                factors = repo.list_factors(market="futures", status=None, limit=500)
            finally:
                repo.close()
            board = status_board(factors)
            board["factors"] = len(factors)
            return board
        except Exception as e:  # noqa: BLE001
            logger.warning("[ui] QA board 构建失败: %s", e)
            return {"error": str(e), "factors": 0}

    def _workflow_create_run(self) -> dict:
        store, _ = _get_workflow()
        started_stage = self._request_body().get("started_stage", "s1")
        run_id = store.create_run(started_stage)
        return {"ok": True, "run_id": run_id, "started_stage": started_stage}

    def _workflow_run_all(self, run_id: str) -> dict:
        store, executor = _get_workflow()
        if store.get_run(run_id) is None:
            return {"error": f"run not found: {run_id}"}
        body = self._request_body()
        start_stage = body.get("start_stage") or body.get("started_stage") or "s1"
        action_id = body.get("action_id")
        executor.run_all(run_id, start_stage=start_stage, action_id=action_id)
        return {"ok": True, "run_id": run_id, "start_stage": start_stage}

    def _workflow_run_action(self, run_id: str, stage_id: str, action_id: str) -> dict:
        store, executor = _get_workflow()
        if store.get_run(run_id) is None:
            return {"error": f"run not found: {run_id}"}
        return executor.run_stage(run_id, stage_id, action_id)

    def _request_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            return json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def _serve_workflow_static(self, path: str) -> None:
        """托管 WorkFlow SPA 构建产物（/workflow → web/workflow_ui/dist）。"""
        import mimetypes

        dist = Path(__file__).resolve().parent.parent.parent / "web" / "workflow_ui" / "dist"
        if path.startswith("/assets/"):
            rel = path.lstrip("/")
        elif path.startswith("/workflow"):
            rel = path[len("/workflow") :].lstrip("/")
        else:
            rel = ""
        if not rel or rel.endswith("/"):
            rel = "index.html"
        target = (dist / rel).resolve()
        if not str(target).startswith(str(dist.resolve())) or not target.is_file():
            target = dist / "index.html"
        if not target.is_file():
            self._respond_html(
                "<h2>WorkFlow UI 未构建</h2>"
                "<p>请先构建前端: <code>cd web/workflow_ui &amp;&amp; npm install &amp;&amp; npm run build</code></p>"
            )
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(target.read_bytes())


# ─── WorkFlow 运行时单例（懒加载，避免 import 时创建 DB） ─────────

_WORKFLOW_RUNTIME: Optional[tuple[Any, Any]] = None


def _get_workflow() -> tuple[Any, Any]:
    """返回 (WorkflowStore, WorkflowExecutor) 单例。"""
    global _WORKFLOW_RUNTIME
    if _WORKFLOW_RUNTIME is None:
        from ..workflow import WorkflowExecutor, WorkflowStore

        store = WorkflowStore()
        _WORKFLOW_RUNTIME = (store, WorkflowExecutor(store))
    return _WORKFLOW_RUNTIME


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

# ─── DataQualityMonitor 引用 ─────────────────────────────

_data_quality_monitor: Optional[Any] = None


def set_data_quality_monitor(monitor: Any) -> None:
    """设置 DataQualityMonitor 实例用于指标暴露。"""
    global _data_quality_monitor
    _data_quality_monitor = monitor


def get_data_quality_monitor() -> Optional[Any]:
    """获取 DataQualityMonitor 实例。"""
    return _data_quality_monitor


# ─── C.2 实盘对接端点辅助 ──────────────────────────────

_risk_manager: Optional[Any] = None
_sim_adapter: Optional[Any] = None
_live_monitor: Optional[Any] = None


def _get_risk_manager() -> Any:
    """获取全局 RiskManager 实例。"""
    global _risk_manager
    if _risk_manager is None:
        from ..risk import RiskManager

        _risk_manager = RiskManager()
    return _risk_manager


def _get_sim_adapter() -> Any:
    """获取全局 SimulatedTradeAdapter 实例。"""
    global _sim_adapter
    if _sim_adapter is None:
        from ..risk import SimulatedTradeAdapter

        _sim_adapter = SimulatedTradeAdapter()
    return _sim_adapter


def _get_live_monitor() -> Any:
    """获取全局 LiveFactorMonitor 实例。"""
    global _live_monitor
    if _live_monitor is None:
        from .live_factor_monitor import LiveFactorMonitor

        _live_monitor = LiveFactorMonitor()
    return _live_monitor


def _sim_account_status() -> dict[str, Any]:
    """构造模拟账户状态。"""
    try:
        return _get_sim_adapter().get_account_status()
    except Exception:  # noqa: BLE001
        return {
            "total_equity": 1_000_000.0,
            "balance": 1_000_000.0,
            "peak_equity": 1_000_000.0,
            "daily_pnl": 0.0,
            "position_value": 0.0,
        }


def _sim_positions() -> dict[str, Any]:
    """构造模拟持仓（从适配器读取）。"""
    try:
        adapter = _get_sim_adapter()
        positions: dict[str, Any] = {}
        for sym in ("RB0", "CU0", "TA0"):
            pos = adapter.get_position(sym)
            if pos.get("market_value", 0) or pos.get("quantity", 0):
                positions[sym] = pos
        return positions
    except Exception:  # noqa: BLE001
        return {}


def _record_risk_metrics(result: dict[str, Any]) -> None:
    """将风控检查结果写入 Prometheus 指标。"""
    from .prometheus_metrics import metrics_registry

    for check in result.get("checks", []):
        check_name = check.get("check_name", "unknown")
        passed = bool(check.get("passed", False))
        metrics_registry.record_risk_check(check_name, "passed" if passed else "blocked")


def _build_risk_status() -> dict[str, Any]:
    """构建 /api/v1/risk/status 响应。"""
    try:
        risk = _get_risk_manager()
        account = _sim_account_status()
        positions = _sim_positions()
        # 空信号探测
        result = risk.check(
            {"signal_id": "probe", "signals": [], "timestamp": ""},
            account,
            positions,
        )
        violations = [c for c in result.get("checks", []) if not c.get("passed", False)]
        return {
            "positions": list(positions.keys()),
            "risk_level": "critical" if violations else "normal",
            "violations": violations,
            "account": account,
        }
    except Exception as e:  # noqa: BLE001
        logger.error("[ui] 风控状态查询失败: %s", e)
        return {"risk_level": "unknown", "error": str(e)}


def _build_live_factors() -> dict[str, Any]:
    """构建 /api/v1/live/factors 响应。"""
    try:
        monitor = _get_live_monitor()
        alerts = monitor.check_deviation()
        return {
            "factors": [
                {"factor_id": fid, "live": monitor._live.get(fid, {})}  # noqa: SLF001
                for fid in monitor.get_factor_ids()
            ],
            "alerts": alerts,
            "count": len(monitor.get_factor_ids()),
        }
    except Exception as e:  # noqa: BLE001
        logger.error("[ui] Live 因子查询失败: %s", e)
        return {"factors": [], "alerts": [], "error": str(e)}


def _build_live_deviation(factor_id: str) -> dict[str, Any]:
    """构建 /api/v1/live/factors/{id}/deviation 响应。"""
    try:
        monitor = _get_live_monitor()
        return monitor.get_factor_deviation(factor_id)
    except Exception as e:  # noqa: BLE001
        logger.error("[ui] Live 偏离查询失败: %s", e)
        return {"factor_id": factor_id, "error": str(e)}


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
    "set_data_quality_monitor",
    "get_data_quality_monitor",
]


def _safe_version() -> str:
    """安全获取 FTS 版本号（避免导入循环）。"""
    try:
        from fts import __version__ as v

        return v
    except Exception:  # noqa: BLE001
        return "?"
