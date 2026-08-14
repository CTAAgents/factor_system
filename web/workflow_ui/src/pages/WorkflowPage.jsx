import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import StageFlow from '../components/StageFlow';
import RunHistory from '../components/RunHistory';
import StageDetailModal from '../components/StageDetailModal';
import { RUN_STATUS_TEXT } from '../constants';

// WorkFlow 看板：阶段节点流 / 端到端运行 / 单动作执行 / 批次历史
export default function WorkflowPage() {
  const [stages, setStages] = useState([]);
  const [runs, setRuns] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [startStage, setStartStage] = useState('s1');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [modal, setModal] = useState(null);
  const timerRef = useRef(null);

  const loadStages = useCallback(async () => {
    try {
      setStages(await api.stages());
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      const r = await api.runs();
      setRuns(r.runs || []);
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  const loadDetail = useCallback(async (runId) => {
    if (!runId) {
      setDetail(null);
      return;
    }
    try {
      setDetail(await api.run(runId));
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    loadStages();
    loadRuns();
  }, [loadStages, loadRuns]);

  // 选中批次：3s 轮询直到批次结束
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (!activeRunId) return undefined;
    loadDetail(activeRunId);
    timerRef.current = setInterval(async () => {
      try {
        const d = await api.run(activeRunId);
        setDetail(d);
        if (d.run && d.run.status !== 'running') clearInterval(timerRef.current);
      } catch {
        /* 轮询失败静默，下轮重试 */
      }
    }, 3000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [activeRunId, loadDetail]);

  const run = detail?.run ?? null;

  const stageStatus = useMemo(() => {
    const m = {};
    if (detail) {
      for (const sr of detail.stage_runs || []) m[sr.stage_id] = sr; // 后写覆盖，取最新
    }
    return m;
  }, [detail]);

  const startEndToEnd = async () => {
    setBusy(true);
    setErr('');
    try {
      const { run_id } = await api.createRun(startStage);
      await api.runAll(run_id, startStage);
      setActiveRunId(run_id);
      await loadDetail(run_id);
    } catch (e) {
      setErr(String(e.message || e));
    }
    setBusy(false);
  };

  const runSingle = async (stageId, actionId) => {
    setBusy(true);
    setErr('');
    try {
      let runId = activeRunId;
      if (!runId) {
        const { run_id } = await api.createRun(startStage);
        runId = run_id;
        setActiveRunId(runId);
      }
      await api.runAction(runId, stageId, actionId);
      await loadDetail(runId);
    } catch (e) {
      setErr(String(e.message || e));
    }
    setBusy(false);
  };

  return (
    <div className="workflow-page">
      <div className="toolbar">
        <label className="tool-label">
          起始阶段
          <select value={startStage} onChange={(e) => setStartStage(e.target.value)}>
            {stages.map((s) => (
              <option key={s.id} value={s.id}>
                {s.index}. {s.name}
              </option>
            ))}
          </select>
        </label>
        <button className="btn btn-primary" disabled={busy} onClick={startEndToEnd}>
          ▶ 创建并端到端执行
        </button>
        <button
          className="btn"
          disabled={busy}
          onClick={async () => {
            setErr('');
            await loadRuns();
            await loadDetail(activeRunId);
          }}
        >
          ⟳ 刷新
        </button>
        {run && (
          <span className={`run-banner b-${run.status}`}>
            {run.run_id} · {RUN_STATUS_TEXT[run.status] || run.status} · 当前阶段 {run.current_stage || '-'}
          </span>
        )}
      </div>

      {err && <div className="error">{err}</div>}

      <div className="main-grid">
        <div className="flow-panel">
          <div className="panel-head">
            <h2>端到端工作流</h2>
            <span className="muted">11 阶段 + 质检闭环（CTA 手册 v1.3）</span>
          </div>
          <StageFlow
            stages={stages}
            stageStatus={stageStatus}
            runActive={Boolean(run)}
            busy={busy}
            onRunAction={runSingle}
            onViewLog={(sr) => setModal(sr)}
          />
        </div>
        <aside className="side-panel">
          <RunHistory
            runs={runs}
            activeRunId={activeRunId}
            onSelect={(id) => {
              setActiveRunId(id);
              loadDetail(id);
            }}
          />
        </aside>
      </div>

      <StageDetailModal
        stageRun={modal}
        stage={modal ? stages.find((s) => s.id === modal.stage_id) : null}
        onClose={() => setModal(null)}
      />
    </div>
  );
}
