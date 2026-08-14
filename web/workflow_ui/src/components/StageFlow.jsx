import { STATUS_TEXT } from '../constants';

// 阶段节点流：横向卡片，展示 11 阶段 + 质检闭环及其实时状态/动作
export default function StageFlow({ stages, stageStatus, runActive, busy, onRunAction, onViewLog }) {
  return (
    <div className="flow-scroll">
      <div className="flow">
        {stages.map((s) => {
          const sr = stageStatus[s.id];
          const status = sr ? sr.status : runActive ? 'pending' : 'idle';
          const isRunning = status === 'running';
          return (
            <div key={s.id} className={`stage-node ${isRunning ? 'is-running' : ''}`}>
              <div className="node-head">
                <span className={`node-index idx-${status}`}>{s.index}</span>
                <span className="node-name" title={s.id}>
                  {s.name}
                </span>
                {sr && <span className={`badge b-${status}`}>{STATUS_TEXT[status] || status}</span>}
              </div>
              <p className="node-desc">{s.desc}</p>
              <div className="node-actions">
                {s.actions.map((a) => (
                  <div key={a.id} className="node-action">
                    <button
                      className="btn btn-sm btn-primary"
                      disabled={busy}
                      onClick={() => onRunAction(s.id, a.id)}
                      title={a.cmd ? a.cmd.join(' ') : ''}
                    >
                      ▶ {a.label}
                    </button>
                    {sr && sr.action_id === a.id && sr.log && (
                      <button className="btn btn-sm btn-ghost" onClick={() => onViewLog(sr)}>
                        日志
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
