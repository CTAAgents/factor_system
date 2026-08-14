import { RUN_STATUS_TEXT, fmtT } from '../constants';

// 运行批次历史列表
export default function RunHistory({ runs, activeRunId, onSelect }) {
  return (
    <div className="run-history">
      <h3>运行批次</h3>
      {runs.length === 0 && <p className="muted">暂无批次，点击上方「创建并端到端执行」开始</p>}
      {runs.map((r) => (
        <div
          key={r.run_id}
          className={`run-item ${r.run_id === activeRunId ? 'active' : ''}`}
          onClick={() => onSelect(r.run_id)}
        >
          <span className={`dot dot-${r.status}`} />
          <div className="run-main">
            <span className="run-id">{r.run_id}</span>
            <span className="run-sub">
              {fmtT(r.created_at)} · {r.current_stage || '-'}
            </span>
          </div>
          <span className="run-status">{RUN_STATUS_TEXT[r.status] || r.status}</span>
        </div>
      ))}
    </div>
  );
}
