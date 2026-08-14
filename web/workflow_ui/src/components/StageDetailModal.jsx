import { STATUS_TEXT, fmtT } from '../constants';

// 阶段动作详情弹窗：状态元信息 / 结构化产物 / 运行日志
export default function StageDetailModal({ stageRun, stage, onClose }) {
  if (!stageRun) return null;
  const stageName = stage ? stage.name : stageRun.stage_id;
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>
            {stageName} / {stageRun.action_id}
          </h3>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="meta-row">
          <span>
            状态 <b className={`b-${stageRun.status}`}>{STATUS_TEXT[stageRun.status] || stageRun.status}</b>
          </span>
          <span>
            退出码 <b>{stageRun.exit_code ?? '-'}</b>
          </span>
          <span>开始 {fmtT(stageRun.started_at)}</span>
          <span>结束 {fmtT(stageRun.ended_at)}</span>
        </div>
        {stageRun.output != null && stageRun.output !== '' && (
          <div className="block">
            <h4>结构化产物</h4>
            <pre className="output">{JSON.stringify(stageRun.output, null, 2)}</pre>
          </div>
        )}
        <div className="block">
          <h4>运行日志</h4>
          <pre className="log">{stageRun.log || '（空）'}</pre>
        </div>
      </div>
    </div>
  );
}
