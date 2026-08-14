// 共享常量：阶段动作状态文案 / QA 七状态顺序
export const STATUS_TEXT = {
  idle: '未运行',
  pending: '待执行',
  running: '执行中',
  success: '成功',
  failed: '失败',
  skipped: '跳过',
  aborted: '已中止'
};

export const RUN_STATUS_TEXT = {
  running: '执行中',
  success: '成功',
  failed: '失败',
  aborted: '已中止'
};

export const QA_STATUS_ORDER = [
  'DRAFT',
  'PENDING_QA',
  'CORE',
  'CANDIDATE',
  'OBSERVATION',
  'SUSPENDED',
  'RETIRED'
];

export const QA_STATUS_CN = {
  DRAFT: '草稿',
  PENDING_QA: '待质检',
  CORE: '核心',
  CANDIDATE: '候选',
  OBSERVATION: '观察',
  SUSPENDED: '暂停',
  RETIRED: '退役'
};

export function fmtT(iso) {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  } catch {
    return iso;
  }
}
