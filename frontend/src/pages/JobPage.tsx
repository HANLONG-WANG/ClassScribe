import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type Job, type PipelineEvent, streamJobEvents } from "../api";
import { useWorkbench } from "../store";

const stages = [
  ["audio_import", "音频校验"],
  ["audio_qc", "质量分析"],
  ["vad", "VAD"],
  ["lid", "LID"],
  ["structure", "结构"],
  ["transcription", "主 ASR"],
  ["quality", "自动复核"],
  ["postprocess", "术语 / 标点"],
  ["alignment", "对齐"],
  ["export", "导出"],
] as const;

export function JobPage() {
  const jobId = useWorkbench((state) => state.currentJobId);
  const setPage = useWorkbench((state) => state.setPage);
  const client = useQueryClient();
  const [lastEvent, setLastEvent] = useState<PipelineEvent | null>(null);
  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>(`/jobs/${String(jobId)}`),
    enabled: jobId !== null,
    refetchInterval: 1500,
  });
  const control = useMutation({
    mutationFn: (action: "pause" | "resume" | "cancel" | "retry") =>
      api<Job>(`/jobs/${String(jobId)}/${action}`, {
        method: "POST",
        body: "{}",
      }),
    onSuccess: (job) => client.setQueryData(["job", jobId], job),
  });

  useEffect(() => {
    if (jobId === null) return;
    const controller = new AbortController();
    void streamJobEvents(
      jobId,
      (event) => {
        setLastEvent(event);
        void client.invalidateQueries({ queryKey: ["job", jobId] });
      },
      controller.signal,
    ).catch((error: unknown) => {
      if (!controller.signal.aborted)
        setLastEvent({
          sequence: 0,
          job_id: jobId,
          kind: "stream_error",
          occurred_at: new Date().toISOString(),
          payload: {
            detail: error instanceof Error ? error.message : "SSE disconnected",
          },
        });
    });
    return () => {
      controller.abort();
    };
  }, [client, jobId]);

  if (jobId === null) return <EmptyJob />;
  if (query.isLoading) return <p className="loading">正在读取任务…</p>;
  if (!query.data)
    return (
      <p className="error-callout">{query.error?.message ?? "任务不存在"}</p>
    );
  const job = query.data;
  const completed = job.status === "completed";
  const activeIndex = completed
    ? stages.length
    : stages.findIndex(([stage]) => stage === job.stage);
  const stageLabel = completed
    ? "已完成"
    : (stages[activeIndex]?.[1] ??
      { created: "等待调度", completed: "已完成" }[job.stage] ??
      job.stage);
  const progressPercent = completed
    ? 100
    : Math.max(0, Math.min(100, Math.round(job.progress * 100)));
  const pausedByIbus =
    job.status === "paused" &&
    lastEvent?.job_id === job.job_id &&
    lastEvent.kind === "job_paused" &&
    lastEvent.payload.reason === "ibus_preempted_at_safe_segment_boundary";

  return (
    <section className="page-stack" aria-labelledby="job-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">Persistent pipeline</p>
          <h1 id="job-title">课堂任务</h1>
          <p className="mono muted">{job.job_id}</p>
        </div>
        <span className={`status-pill status-${job.status}`}>{job.status}</span>
      </header>
      {pausedByIbus && (
        <div className="notice">
          IBus 正在使用 GPU；课堂批处理已在安全片段边界暂停。
        </div>
      )}
      <div className="panel progress-panel">
        <div className="progress-heading">
          <strong>{stageLabel}</strong>
          <span>{progressPercent}%</span>
        </div>
        <div
          className="progress-track"
          role="progressbar"
          aria-label="任务进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progressPercent}
          aria-valuetext={stageLabel}
        >
          <span style={{ width: `${String(progressPercent)}%` }} />
        </div>
        <ol className="stage-rail">
          {stages.map(([id, label], index) => (
            <li
              aria-current={index === activeIndex ? "step" : undefined}
              className={
                index < activeIndex
                  ? "done"
                  : index === activeIndex
                    ? "active"
                    : ""
              }
              key={id}
            >
              <i />
              {label}
            </li>
          ))}
        </ol>
      </div>
      <div className="metric-grid">
        <article>
          <span>当前模型</span>
          <strong>{job.runtime?.model_id ?? "等待调度"}</strong>
        </article>
        <article>
          <span>片段</span>
          <strong>{job.runtime?.segment_ordinal ?? "—"}</strong>
        </article>
        <article>
          <span>实时系数</span>
          <strong>{job.runtime?.realtime_factor?.toFixed(2) ?? "—"}</strong>
        </article>
        <article>
          <span>显存</span>
          <strong>
            {job.runtime?.vram_mb
              ? `${String(Math.round(job.runtime.vram_mb))} MB`
              : "—"}
          </strong>
        </article>
      </div>
      {job.error_detail && (
        <p className="error-callout">
          {job.error_code}: {job.error_detail}
        </p>
      )}
      <div className="toolbar">
        {job.status === "paused" ? (
          <button
            onClick={() => {
              control.mutate("resume");
            }}
            type="button"
          >
            继续
          </button>
        ) : (
          <button
            disabled={job.status !== "running"}
            onClick={() => {
              control.mutate("pause");
            }}
            type="button"
          >
            暂停
          </button>
        )}
        <button
          className="danger-button"
          disabled={["completed", "cancelled"].includes(job.status)}
          onClick={() => {
            control.mutate("cancel");
          }}
          type="button"
        >
          取消
        </button>
        <button
          disabled={!["failed", "pending"].includes(job.status)}
          onClick={() => {
            control.mutate("retry");
          }}
          type="button"
        >
          重试失败点
        </button>
        <button
          className="primary-button"
          disabled={job.status !== "completed"}
          onClick={() => {
            setPage("transcript");
          }}
          type="button"
        >
          打开转录稿
        </button>
        <button
          disabled={job.status !== "completed"}
          onClick={() => {
            setPage("exports");
          }}
          type="button"
        >
          直接导出
        </button>
      </div>
      {lastEvent && (
        <details className="panel">
          <summary>最新流水线事件 · {lastEvent.kind}</summary>
          <pre>{JSON.stringify(lastEvent.payload, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}

function EmptyJob() {
  const setPage = useWorkbench((state) => state.setPage);
  return (
    <section className="empty-state">
      <h1>还没有课堂任务</h1>
      <p>先导入一段录音，流水线会持久化每个阶段。</p>
      <button
        className="primary-button"
        onClick={() => {
          setPage("upload");
        }}
        type="button"
      >
        去导入
      </button>
    </section>
  );
}
