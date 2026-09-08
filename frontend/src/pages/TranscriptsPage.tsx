import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, formatSamples } from "../api";
import { useWorkbench } from "../store";

interface Manuscript {
  job_id: string;
  source_name: string;
  duration_samples: number;
  language: string;
  status: string;
  created_at: string;
}
const statuses: Record<string, string> = {
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

export function TranscriptsPage() {
  const [offset, setOffset] = useState(0);
  const setCurrentJob = useWorkbench((state) => state.setCurrentJob);
  const setPage = useWorkbench((state) => state.setPage);
  const query = useQuery({
    queryKey: ["manuscripts", offset],
    queryFn: () =>
      api<{ items: Manuscript[]; total: number }>(
        `/jobs?limit=30&offset=${String(offset)}`,
      ),
  });
  return (
    <section className="page-stack" aria-labelledby="manuscripts-title">
      <header className="page-header">
        <div>
          <h1 id="manuscripts-title">历史转录稿</h1>
          <p>选择一份稿件，查看转写内容、播放音频或继续校对。</p>
        </div>
        <button
          type="button"
          onClick={() => {
            setPage("upload");
          }}
        >
          导入新音频
        </button>
      </header>
      {query.isPending && <p role="status">正在加载历史稿件…</p>}
      {query.isError && (
        <div role="alert">
          <p>{query.error.message}</p>
          <button
            type="button"
            onClick={() => {
              void query.refetch();
            }}
          >
            重新加载
          </button>
        </div>
      )}
      {query.data && (
        <>
          <p className="muted">
            共 {query.data.total} 份稿件 · 按创建时间从新到旧排列
          </p>
          {query.data.items.length === 0 && (
            <p className="empty-state">暂无稿件，请先导入音频创建课堂任务。</p>
          )}
          <div className="manuscript-list">
            {query.data.items.map((item) => (
              <button
                className="panel manuscript-card"
                key={item.job_id}
                type="button"
                onClick={() => {
                  setCurrentJob(item.job_id);
                  setPage("transcript");
                }}
              >
                <strong>{item.source_name}</strong>
                <span>
                  {new Date(item.created_at).toLocaleString()} ·{" "}
                  {formatSamples(item.duration_samples)} ·{" "}
                  {(
                    { ja: "日语", zh: "中文", en: "英语" } as Record<
                      string,
                      string
                    >
                  )[item.language] ?? item.language}
                </span>
                <span>{statuses[item.status] ?? item.status} · 查看稿件 →</span>
              </button>
            ))}
          </div>
          <div className="toolbar">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => {
                setOffset(Math.max(0, offset - 30));
              }}
            >
              上一页
            </button>
            <span>第 {Math.floor(offset / 30) + 1} 页</span>
            <button
              type="button"
              disabled={offset + 30 >= query.data.total}
              onClick={() => {
                setOffset(offset + 30);
              }}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
