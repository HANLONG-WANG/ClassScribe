export interface Manuscript {
  job_id: string;
  has_transcript?: boolean;
  source_name: string;
  duration_samples: number;
  language: string;
  status: string;
  created_at: string;
}
export const statuses: Record<string, string> = {
  pending: "等待处理",
  cancelling: "正在取消",
  completed: "已完成",
  failed: "失败 · 可查看已有稿件",
  cancelled: "已取消",
  running: "处理中",
  paused: "已暂停",
  queued: "排队中",
  created: "待处理",
};
