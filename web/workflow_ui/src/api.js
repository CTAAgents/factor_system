// 与后端 fts.monitor.http_server WorkFlow API 对接
async function http(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      msg = JSON.parse(await res.text()).error || msg;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json();
}

export const api = {
  stages: () => http('/api/workflow/stages'),

  runs: () => http('/api/workflow/runs'),

  run: (runId) => http(`/api/workflow/runs/${runId}`),

  qaBoard: () => http('/api/workflow/qa/board'),

  createRun: (startedStage = 's1') =>
    http('/api/workflow/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ started_stage: startedStage })
    }),

  runAll: (runId, startStage, actionId = null) =>
    http(`/api/workflow/runs/${runId}/run_all`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_stage: startStage, action_id: actionId })
    }),

  runAction: (runId, stageId, actionId) =>
    http(`/api/workflow/runs/${runId}/stage/${stageId}/action/${actionId}/run`, {
      method: 'POST'
    })
};
