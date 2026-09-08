import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useWorkbench } from "../store";

interface QueueItem {
  job_id: string;
  source_name: string;
  status: string;
  progress: number;
  stage: string;
  error_detail: string | null;
}
interface QueueSnapshot {
  paused: boolean;
  items: QueueItem[];
}
const statuses: Record<string, string> = {
  pending: "等待转录",
  running: "正在转录",
  paused: "已暂停",
  failed: "失败",
  cancelling: "正在取消",
};
export function QueuePage() {
  const client = useQueryClient();
  const setJob = useWorkbench((state) => state.setCurrentJob);
  const setPage = useWorkbench((state) => state.setPage);
  const query = useQuery({
    queryKey: ["queue"],
    queryFn: () => api<QueueSnapshot>("/queue"),
    refetchInterval: 1500,
  });
  const change = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      api(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["queue"] });
    },
  });
  const pending =
    query.data?.items.filter((item) => item.status === "pending") ?? [];
  return (
    <section className="page-stack" aria-labelledby="queue-title">
      <header className="page-header">
        <div>
          <h1 id="queue-title">转录队列</h1>
          <p>每次处理一节课，完成并释放资源后自动开始下一节。</p>
        </div>
        <button
          type="button"
          onClick={() => {
            setPage("upload");
          }}
        >
          添加课堂
        </button>
      </header>
      {query.isPending && <p role="status">正在加载队列…</p>}
      {query.error && <p role="alert">{query.error.message}</p>}
      {change.error && <p role="alert">{change.error.message}</p>}
      {query.data && (
        <>
          <div className="toolbar">
            <strong>
              {query.data.paused
                ? "队列已暂停，当前课堂将在安全位置停止"
                : "队列自动运行"}
            </strong>
            <button
              type="button"
              disabled={change.isPending}
              onClick={() => {
                change.mutate({
                  path: `/queue/${query.data.paused ? "resume" : "pause"}`,
                });
              }}
            >
              {query.data.paused ? "恢复整个队列" : "暂停整个队列"}
            </button>
            <button
              type="button"
              onClick={() => {
                setPage("transcripts");
              }}
            >
              查看已完成稿件
            </button>
          </div>
          <p>
            等待 {pending.length}{" "}
            节课。暂停单课会让后续课堂继续；恢复或重试的课堂加入队尾。
          </p>
          {query.data.items.length === 0 && (
            <p className="empty-state">
              队列已清空，可添加新的课堂或查看历史稿件。
            </p>
          )}
          {query.data.items.map((item) => {
            const index = pending.findIndex(
              (entry) => entry.job_id === item.job_id,
            );
            return (
              <article className="panel queue-card" key={item.job_id}>
                <strong>{item.source_name}</strong>
                <p>
                  {statuses[item.status] ?? item.status}
                  {index >= 0 ? ` · 等待第 ${String(index + 1)} 位` : ""} ·{" "}
                  {Math.round(item.progress)}%
                </p>
                {item.error_detail && <p role="alert">{item.error_detail}</p>}
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() => {
                      setJob(item.job_id);
                    }}
                  >
                    查看任务
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setJob(item.job_id);
                      setPage("transcript");
                    }}
                  >
                    查看稿件
                  </button>
                  {["pending", "running", "paused", "failed"].includes(
                    item.status,
                  ) && (
                    <button
                      type="button"
                      disabled={change.isPending}
                      onClick={() => {
                        change.mutate({
                          path: `/jobs/${item.job_id}/${item.status === "paused" ? "resume" : item.status === "failed" ? "retry" : "pause"}`,
                        });
                      }}
                    >
                      {item.status === "paused"
                        ? "恢复到队尾"
                        : item.status === "failed"
                          ? "重试"
                          : "暂停此课"}
                    </button>
                  )}
                  {item.status !== "failed" && (
                    <button
                      type="button"
                      disabled={
                        change.isPending || item.status === "cancelling"
                      }
                      onClick={() => {
                        change.mutate({ path: `/jobs/${item.job_id}/cancel` });
                      }}
                    >
                      取消此课
                    </button>
                  )}
                  {index >= 0 && (
                    <button
                      type="button"
                      disabled={change.isPending || index === 0}
                      onClick={() => {
                        const ids = pending.map((entry) => entry.job_id);
                        const item = ids[index];
                        const previous = ids[index - 1];
                        if (item && previous) {
                          ids[index - 1] = item;
                          ids[index] = previous;
                        }
                        change.mutate({
                          path: "/queue/reorder",
                          body: { job_ids: ids },
                        });
                      }}
                    >
                      上移
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </>
      )}
    </section>
  );
}
