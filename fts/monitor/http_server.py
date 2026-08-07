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

版本: 动态读取自 fts.__version__
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

  .family-dist-section { margin-bottom: 24px; }
  .family-bar { display: flex; align-items: center; gap: 8px; padding: 6px 0; cursor: pointer; }
  .family-bar:hover { opacity: 0.8; }
  .family-bar .tag { font-size: 12px; min-width: 80px; font-weight: 600; }
  .family-bar .bar-track { flex: 1; height: 20px; background: rgba(255,255,255,0.05);
                           border-radius: 4px; overflow: hidden; position: relative; }
  .family-bar .bar-fill { height: 100%; border-radius: 4px; transition: width 0.5s ease;
                          display: flex; align-items: center; padding-left: 6px; }
  .family-bar .bar-fill span { font-size: 11px; color: #fff; text-shadow: 0 1px 2px rgba(0,0,0,0.5); }
  .family-bar .bar-stats { font-size: 11px; color: var(--muted); min-width: 120px; text-align: right; }

  .family-filter { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .family-filter .chip { font-size: 11px; padding: 4px 10px; border-radius: 12px;
                         border: 1px solid var(--border); cursor: pointer; color: var(--muted);
                         background: transparent; transition: all 0.2s; }
  .family-filter .chip:hover { border-color: var(--blue); color: var(--text); }
  .family-filter .chip.active { background: var(--blue); color: #fff; border-color: var(--blue); }
  .family-filter .chip.all { border-color: var(--muted); }
  .family-filter .chip.all.active { background: var(--muted); color: #fff; border-color: var(--muted); }

  .section-title { font-size: 16px; font-weight: 600; margin-bottom: 12px;
                   display: flex; justify-content: space-between; align-items: center; }
  .factor-table { width: 100%; border-collapse: collapse; }
  .factor-table th { text-align: left; font-size: 12px; color: var(--muted);
                     padding: 8px 12px; border-bottom: 1px solid var(--border);
                     position: sticky; top: 0; background: var(--card); z-index: 1; }
  .factor-table td { font-size: 13px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
  .factor-table tr.clickable { cursor: pointer; }
  .factor-table tr.clickable:hover td { background: rgba(59,130,246,0.08); }

  .family-header { background: rgba(59,130,246,0.08) !important; }
  .family-header td { padding: 8px 12px; font-weight: 700; font-size: 14px;
                      border-bottom: 2px solid var(--blue); }
  .family-header .fam-count { font-size: 12px; font-weight: 400; color: var(--muted);
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

  .fam-color { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }

  footer { text-align: center; color: var(--muted); font-size: 12px; margin-top: 40px;
           padding-top: 16px; border-top: 1px solid var(--border); }

  @media (max-width: 768px) {
    .grid-4 { grid-template-columns: repeat(2, 1fr); }
    .loop-grid { grid-template-columns: 1fr; }
    .detail-grid { grid-template-columns: 1fr; }
    .family-bar .bar-stats { display: none; }
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

  <div class="section-title">家族分布 <span style="font-size:12px;color:var(--muted)" id="familySummaryNote"></span></div>
  <div id="familyDistSection" class="family-dist-section"></div>

  <div class="family-filter" id="familyFilter"></div>

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

  <footer>FTS v<span id="footerVersion">--</span> · 每 10 秒自动刷新</footer>
</div>

<script>
const FAMILY_COLORS = {
  momentum: '#22c55e', trend: '#22c55e',
  carry: '#3b82f6', roll: '#3b82f6',
  volatility: '#f97316', vol: '#f97316',
  value: '#a855f7', quality: '#ec4899',
  macro: '#06b6d4', sentiment: '#eab308',
  basis: '#14b8a6', structure: '#8b5cf6',
  spread: '#f43f5e', seasonal: '#f59e0b',
  other: '#94a3b8', default: '#3b82f6',
};

function getFamilyColor(family) {
  return FAMILY_COLORS[family] || FAMILY_COLORS.default;
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

let activeFamilyFilter = null;
let cachedFactors = [];

function renderFamilyDistribution(family_dist, family_summary, maxCount) {
  const section = document.getElementById('familyDistSection');
  const maxC = maxCount || 1;
  const totalFamilies = Object.keys(family_dist).length;
  let html = '';
  for (const fam of Object.keys(family_dist)) {
    const count = family_dist[fam];
    const pct = (count / maxC * 100).toFixed(0);
    const color = getFamilyColor(fam);
    const summary = (family_summary || []).find(s => s.family === fam);
    const avgIc = summary ? summary.avg_ic.toFixed(4) : '--';
    const avgSharpe = summary ? summary.avg_sharpe.toFixed(2) : '--';
    const isActive = activeFamilyFilter === fam;
    html += '<div class="family-bar" onclick="toggleFamilyFilter(\''+fam+'\')" style="opacity:'+(activeFamilyFilter && !isActive ? 0.4 : 1)+'">'
      + '<div class="tag"><span class="fam-color" style="background:'+color+'"></span>'+fam+'</div>'
      + '<div class="bar-track"><div class="bar-fill" style="width:'+pct+'%;background:'+color+'"><span>'+count+'</span></div></div>'
      + '<div class="bar-stats">IC: '+avgIc+' · Sharpe: '+avgSharpe+'</div></div>';
  }
  section.innerHTML = html;
  document.getElementById('familySummaryNote').textContent = '共 '+totalFamilies+' 个家族';
}

function toggleFamilyFilter(family) {
  activeFamilyFilter = (activeFamilyFilter === family) ? null : family;
  renderFamilyFilterChips();
  renderFactorTable(cachedFactors);
}

function renderFamilyFilterChips() {
  const filter = document.getElementById('familyFilter');
  const families = Object.keys(cachedFactors.reduce(function(acc, f) { acc[f.family] = true; return acc; }, {}));
  let html = '<span class="chip all'+(activeFamilyFilter ? '' : ' active')+'" onclick="activeFamilyFilter=null;renderFamilyFilterChips();renderFactorTable(cachedFactors)">全部</span>';
  for (var i = 0; i < families.length; i++) {
    var fam = families[i];
    html += '<span class="chip'+(activeFamilyFilter === fam ? ' active' : '')+'" onclick="toggleFamilyFilter(\''+fam+'\')">'+fam+'</span>';
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
    + '<div class="detail-item"><span class="dl">家族</span><span class="dv" style="color:'+getFamilyColor(f.family)+'">'+sanitize(f.family)+'</span></div>'
    + '<div class="detail-item"><span class="dl">状态</span><span class="dv"><span class="status-tag '+statusClass+'">'+status+'</span></span></div>'
    + '<div class="detail-section-title">评估指标</div>'
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
  var filtered = activeFamilyFilter ? factors.filter(function(f) { return f.family === activeFamilyFilter; }) : factors;
  if (filtered.length === 0) {
    fBody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px">暂无 elite 因子</td></tr>';
    document.getElementById('factorSummary').textContent = '0 个';
    return;
  }
  document.getElementById('factorSummary').textContent = '共 '+filtered.length+' 个';
  var currentFamily = '';
  var html = '';
  var detailHtml = '';
  for (var i = 0; i < filtered.length; i++) {
    var f = filtered[i];
    if (f.family !== currentFamily) {
      currentFamily = f.family;
      var color = getFamilyColor(currentFamily);
      var famCount = filtered.filter(function(x) { return x.family === currentFamily; }).length;
      html += '<tr class="family-header"><td colspan="8">'
        + '<span class="fam-color" style="background:'+color+'"></span>'+currentFamily
        + '<span class="fam-count">'+famCount+' 个因子</span></td></tr>';
    }
    var sharpeVal = parseFloat(f.sharpe);
    var sharpeClass = sharpeVal >= 3 ? 'green' : (sharpeVal >= 1 ? '' : 'yellow');
    var icVal = parseFloat(f.ic);
    var icClass = icVal >= 0.05 ? 'green' : (icVal >= 0.03 ? '' : 'yellow');
    var ddVal = parseFloat(f.max_drawdown);
    var ddClass = ddVal < 0.1 ? 'green' : (ddVal < 0.2 ? '' : 'red');
    html += '<tr class="clickable" onclick="toggleDetail(\''+f.factor_id+'\')">'
      + '<td><span class="expand-icon" data-expand="'+f.factor_id+'">▶</span></td>'
      + '<td style="font-weight:500">'+sanitize(f.name)+'</td>'
      + '<td>'+sanitize(f.generation)+'</td>'
      + '<td class="dv '+icClass+'">'+sanitize(f.ic)+'</td>'
      + '<td class="dv '+sharpeClass+'">'+sanitize(f.sharpe)+'</td>'
      + '<td class="dv '+ddClass+'">'+sanitize(f.max_drawdown)+'</td>'
      + '<td>'+sanitize(f.turnover)+'</td>'
      + '<td>'+sanitize(f.source)+'</td></tr>';
    detailHtml += '<tr class="detail-row" id="detail-'+f.factor_id+'"><td colspan="8" class="detail-cell">'+buildDetailHtml(f)+'</td></tr>';
  }
  fBody.innerHTML = html + detailHtml;
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

    var familyDist = data.family_distribution || {};
    var maxCount = 1;
    for (var k in familyDist) { if (familyDist[k] > maxCount) maxCount = familyDist[k]; }

    try {
      var factorsResp = await fetchJSON('/api/factors');
      var flist = factorsResp.factors || [];
      var familySummary = factorsResp.family_summary || [];
      renderFamilyDistribution(familyDist, familySummary, maxCount);
      cachedFactors = flist;
      renderFamilyFilterChips();
      renderFactorTable(flist);
    } catch (e) {
      var fBody = document.getElementById('factorBody');
      fBody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px">加载失败</td></tr>';
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

        # 从 DuckDB 查询精英因子计数和家族分布
        elite_count = 0
        overload_count = 0
        retired_count = 0
        family_dist_map: dict[str, int] = {}
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

                    # 家族分布（仅精英因子）
                    fam_rows = _conn.execute("""
                        SELECT family, COUNT(*) as cnt
                        FROM factor_catalog
                        WHERE is_elite = TRUE AND status != 'deleted'
                        GROUP BY family
                        ORDER BY cnt DESC
                    """).fetchall()
                    for fam_row in fam_rows:
                        fam = str(fam_row[0] or "other")
                        family_dist_map[fam] = int(fam_row[1])
                finally:
                    _conn.close()
        except Exception:  # noqa: BLE001
            logger.warning("[ui] DuckDB 查询失败，回退到 JSON 文件统计")
            # 降级到 JSON 文件
            elite_dir = root / "memory" / "knowledge" / "factors" / "futures_elite"
            if elite_dir.exists():
                elite_count = len(list(elite_dir.glob("*.json")))
            overload_dir = root / "memory" / "knowledge" / "factors" / "overloaded"
            if overload_dir.exists():
                overload_count = len(list(overload_dir.glob("*.json")))
            retired_dir = root / "memory" / "knowledge" / "factors" / "retired"
            if retired_dir.exists():
                retired_count = len(list(retired_dir.glob("*.json")))
            if elite_dir.exists():
                for fp in elite_dir.glob("*.json"):
                    if fp.stem.startswith("_"):
                        continue
                    try:
                        raw = json.loads(fp.read_text(encoding="utf-8"))
                        fam = raw.get("family") or "other"
                        family_dist_map[str(fam)] = family_dist_map.get(str(fam), 0) + 1
                    except Exception:  # noqa: BLE001
                        continue

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
            "family_distribution": dict(sorted(family_dist_map.items(), key=lambda x: -x[1])),
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
        """构建 /api/factors 响应，包含家族分类和详细信息。

        数据源优先级: DuckDB → JSON 文件降级。
        """
        try:
            from ..factor_engine.factor_db.schema import DATABASE_PATH as _db_path
            import duckdb as _duckdb

            if _db_path.exists():
                return self._build_factor_list_from_duckdb(_db_path)
        except Exception:  # noqa: BLE001
            logger.warning("[ui] DuckDB 因子查询失败，降级到 JSON 文件")
        return self._build_factor_list_json_fallback()

    def _build_factor_list_from_duckdb(self, db_path: Path) -> dict:
        """从 DuckDB 查询精英因子列表（含家族分类和详细信息）。"""
        import duckdb as _duckdb

        conn = _duckdb.connect(str(db_path), read_only=True)
        try:
            # 检测可用表，构建动态 JOIN
            tables = {
                r[0] for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
            }
            has_evaluations = "factor_evaluations" in tables
            has_quality_scores = "factor_quality_scores" in tables

            select_cols = """
                fc.factor_id, fc.name, fc.generation, fc.source,
                fc.family, fc.market, fc.status,
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
                ORDER BY fc.family, fc.sharpe DESC
            """
            result = conn.execute(sql).fetchall()

            cols = [desc[0] for desc in result.description]

            factors: list[dict] = []
            family_dist: dict[str, int] = {}
            family_stats: dict[str, dict[str, float]] = {}

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

                family = str(row_dict.get("family") or "other")
                family_dist[family] = family_dist.get(family, 0) + 1
                ic_val = float(row_dict.get("ic", 0) or 0)
                sharpe_val = float(row_dict.get("sharpe", 0) or 0)

                if family not in family_stats:
                    family_stats[family] = {"ic_sum": 0.0, "sharpe_sum": 0.0, "count": 0}
                family_stats[family]["ic_sum"] += ic_val
                family_stats[family]["sharpe_sum"] += sharpe_val
                family_stats[family]["count"] += 1

                factors.append({
                    "factor_id": str(row_dict.get("factor_id", "")),
                    "name": str(row_dict.get("name", "")),
                    "generation": str(row_dict.get("generation", "?")),
                    "ic": f"{ic_val:.4f}",
                    "sharpe": f"{sharpe_val:.2f}",
                    "source": str(row_dict.get("source", "?")),
                    "family": family,
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
                })

            # 按家族因子数排序，组内按 Sharpe 降序
            family_order = sorted(family_dist.keys(), key=lambda f: -family_dist[f])
            factors.sort(key=lambda f: (
                family_order.index(f["family"]) if f["family"] in family_order else 999,
                -float(f["sharpe"]),
            ))

            # 构建家族汇总
            family_summary_list: list[dict] = []
            for fam in family_order:
                st = family_stats.get(fam, {"ic_sum": 0, "sharpe_sum": 0, "count": 1})
                cnt = st["count"]
                family_summary_list.append({
                    "family": fam,
                    "count": cnt,
                    "avg_ic": round(st["ic_sum"] / cnt, 4) if cnt else 0,
                    "avg_sharpe": round(st["sharpe_sum"] / cnt, 2) if cnt else 0,
                })

            return {
                "factors": factors,
                "count": len(factors),
                "family_distribution": dict(sorted(family_dist.items(), key=lambda x: -x[1])),
                "family_summary": family_summary_list,
                "source": "duckdb",
            }
        finally:
            conn.close()

    def _build_factor_list_json_fallback(self) -> dict:
        """降级方案：从 JSON 文件读取精英因子数据。"""
        import json as _json

        elite_dir = Path.cwd() / "memory" / "knowledge" / "factors" / "futures_elite"
        factors: list[dict] = []
        family_dist: dict[str, int] = {}
        family_stats: dict[str, dict[str, float]] = {}

        if elite_dir.exists():
            for fp in sorted(elite_dir.glob("*.json"), reverse=True)[:200]:
                if fp.stem.startswith("_"):
                    continue
                try:
                    raw = _json.loads(fp.read_text(encoding="utf-8"))
                    bt = raw.get("evaluation", {}).get("level_1_backtest", {})
                    eco = raw.get("economic_logic", {})
                    qs = raw.get("quality_score", {})
                    family = raw.get("family") or "other"
                    family = str(family) if not isinstance(family, str) else family

                    family_dist[family] = family_dist.get(family, 0) + 1
                    ic_val = float(bt.get("ic", 0))
                    sharpe_val = float(bt.get("sharpe", 0))
                    if family not in family_stats:
                        family_stats[family] = {"ic_sum": 0.0, "sharpe_sum": 0.0, "count": 0}
                    family_stats[family]["ic_sum"] += ic_val
                    family_stats[family]["sharpe_sum"] += sharpe_val
                    family_stats[family]["count"] += 1

                    factors.append({
                        "factor_id": raw.get("factor_id", fp.stem),
                        "name": raw.get("name", fp.stem),
                        "generation": raw.get("generation", "?"),
                        "ic": f"{ic_val:.4f}",
                        "sharpe": f"{sharpe_val:.2f}",
                        "source": raw.get("source", "?"),
                        "family": family,
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
                        } if qs and qs.get("total_score") else None,
                        "status": str(raw.get("status", "active")),
                    })
                except Exception:  # noqa: BLE001
                    continue

        family_order = sorted(family_dist.keys(), key=lambda f: -family_dist[f])
        factors.sort(key=lambda f: (
            family_order.index(f["family"]) if f["family"] in family_order else 999,
            -float(f["sharpe"]),
        ))

        family_summary_list: list[dict] = []
        for fam in family_order:
            st = family_stats.get(fam, {"ic_sum": 0, "sharpe_sum": 0, "count": 1})
            cnt = st["count"]
            family_summary_list.append({
                "family": fam,
                "count": cnt,
                "avg_ic": round(st["ic_sum"] / cnt, 4) if cnt else 0,
                "avg_sharpe": round(st["sharpe_sum"] / cnt, 2) if cnt else 0,
            })

        return {
            "factors": factors,
            "count": len(factors),
            "family_distribution": dict(sorted(family_dist.items(), key=lambda x: -x[1])),
            "family_summary": family_summary_list,
            "source": "json_fallback",
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
            lines.extend([
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
            ])

        return "\n".join(lines)

    def _build_data_source_metrics(self) -> str:
        """构建数据源特定指标。"""
        lines = [
            "# HELP fts_circuit_open 数据源熔断器是否开启",
            "# TYPE fts_circuit_open gauge",
            "fts_circuit_open 0",
            "",
            "# HELP fts_data_source_success_rate 数据源成功率",
            "# TYPE fts_data_source_success_rate gauge",
            "fts_data_source_success_rate 1.0",
            "",
        ]
        dq_monitor = get_data_quality_monitor()
        if dq_monitor is not None:
            try:
                snapshot = dq_monitor.get_metrics_snapshot()
                valid = 1.0 if snapshot.get("market_data_valid", True) else 0.0
                lines.append(f"fts_data_source_success_rate {valid}")
            except Exception:  # noqa: BLE001
                pass
        return "\n".join(lines)

    def do_GET(self):  # noqa: N802
        path = self.path.rstrip("/")

        if path == "" or path == "/":
            self._respond_html(DASHBOARD_HTML)

        elif path == "/api/status":
            self._respond_json(self._build_status())

        elif path == "/api/factors":
            self._respond_json(self._build_factor_list())

        elif path == "/metrics":
            self._respond_metrics(self._build_metrics())

        elif path == "/metrics/data-sources":
            self._respond_metrics(self._build_data_source_metrics())

        elif path == "/health":
            self._respond_json({
                "status": "ok",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })

        elif path == "/api/v1/risk/status":
            self._respond_json(_build_risk_status())

        elif path == "/api/v1/live/factors":
            self._respond_json(_build_live_factors())

        elif path.startswith("/api/v1/live/factors/") and path.endswith("/deviation"):
            factor_id = path[len("/api/v1/live/factors/"):-len("/deviation")]
            self._respond_json(_build_live_deviation(factor_id))

        else:
            self._respond_json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        """处理 POST 请求（信号提交）。"""
        path = self.path.rstrip("/")

        if path == "/api/v1/signal/submit":
            self._handle_signal_submit()
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
            self._respond_json({
                "approved": False,
                "violations": result.get("blocking_violations", []),
            }, 403)
            return

        # 3. 模拟成交
        try:
            from ..risk import SimulatedTradeAdapter

            adapter = _get_sim_adapter()
            if not adapter.is_connected():
                adapter.connect({})
            order = adapter.submit_signal(signal)
        except Exception as e:  # noqa: BLE001
            logger.exception("[ui] 模拟成交失败")
            self._respond_json({"approved": False, "error": str(e)}, 500)
            return

        self._respond_json({"approved": True, "order": order})


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
        return {"total_equity": 1_000_000.0, "balance": 1_000_000.0,
                "peak_equity": 1_000_000.0, "daily_pnl": 0.0,
                "position_value": 0.0}


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
        metrics_registry.record_risk_check(
            check_name, "passed" if passed else "blocked"
        )


def _build_risk_status() -> dict[str, Any]:
    """构建 /api/v1/risk/status 响应。"""
    try:
        risk = _get_risk_manager()
        account = _sim_account_status()
        positions = _sim_positions()
        # 空信号探测
        result = risk.check(
            {"signal_id": "probe", "signals": [], "timestamp": ""},
            account, positions,
        )
        violations = [c for c in result.get("checks", [])
                      if not c.get("passed", False)]
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
