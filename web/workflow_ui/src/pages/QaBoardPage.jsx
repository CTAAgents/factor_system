import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { QA_STATUS_ORDER, QA_STATUS_CN } from '../constants';

// 质检看板：QA 七状态分布 + 观察/暂停预警清单（手册 6.8）
export default function QaBoardPage() {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState('');
  const [at, setAt] = useState('');

  const load = useCallback(async () => {
    try {
      const b = await api.qaBoard();
      setBoard(b);
      setAt(new Date().toLocaleTimeString());
      setErr('');
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const counts = board?.counts || {};
  const total = board?.total ?? board?.factors ?? 0;
  const maxCount = Math.max(1, ...Object.values(counts).map(Number));
  const warnings = board?.obs_warning || [];

  return (
    <div className="qa-page">
      <div className="panel-head">
        <h2>因子质检看板</h2>
        <span className="muted">
          QA 七状态机 · 更新时间 {at} · 因子总数 {total}
        </span>
      </div>
      {err && <div className="error">看板加载失败：{err}</div>}

      <div className="qa-grid">
        {QA_STATUS_ORDER.map((st) => {
          const n = counts[st] || 0;
          const pct = total ? Math.round((n / total) * 100) : 0;
          return (
            <div key={st} className={`qa-card qa-${st.toLowerCase()}`}>
              <div className="qa-name">
                {QA_STATUS_CN[st]} <span className="qa-code">{st}</span>
              </div>
              <div className="qa-num">{n}</div>
              <div className="qa-bar">
                <div className="qa-fill" style={{ width: `${(n / maxCount) * 100}%` }} />
              </div>
              <div className="qa-pct">{pct}%</div>
            </div>
          );
        })}
      </div>

      <div className="block">
        <h4>
          观察 / 暂停预警（{warnings.length}）
          {board?.serving != null && (
            <span className="muted"> · 在役（核心+候选）{board.serving}</span>
          )}
        </h4>
        {warnings.length === 0 ? (
          <p className="muted">无预警因子</p>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>因子</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {warnings.map((w, i) => (
                <tr key={i}>
                  <td>{w.name}</td>
                  <td>
                    <span className={`badge b-${(w.status || '').toLowerCase()}`}>{w.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
