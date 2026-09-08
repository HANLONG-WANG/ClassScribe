import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  api,
  formatSamples,
  type Job,
  type JobActivity,
  type PipelineEvent,
  streamJobEvents,
} from "../api";
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
  const [connection, setConnection] = useState<{
    jobId: string | null;
    connected: boolean;
  }>({ jobId: null, connected: false });
  const connected = connection.jobId === jobId && connection.connected;
  const [now, setNow] = useState(Date.now());
  const [lastEvent, setLastEvent] = useState<PipelineEvent | null>(null);
  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>(`/jobs/${String(jobId)}`),
    enabled: jobId !== null,
    refetchInterval: (query) =>
      ["completed", "cancelled", "failed"].includes(
        query.state.data?.status ?? "",
      )
        ? false
        : connected
          ? 10000
          : 1500,
  });
  const control = useMutation({
    mutationFn: (action: "pause" | "resume" | "cancel" | "retry") =>
      api<Job>(`/jobs/${String(jobId)}/${action}`, {
        method: "POST",
        body: "{}",
      }),
    onSuccess: (job) => client.setQueryData(["job", jobId], job),
  });

  const terminal = ["completed", "cancelled", "failed"].includes(
    query.data?.status ?? "",
  );
  useEffect(() => {
    if (terminal) return;
    const timer = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [terminal]);

  useEffect(() => {
    if (jobId === null || terminal) return;
    const controller = new AbortController();
    let refresh: ReturnType<typeof setTimeout> | undefined;
    void streamJobEvents(
      jobId,
      (event) => {
        setLastEvent(event);
        if (refresh === undefined)
          refresh = setTimeout(
            () => {
              refresh = undefined;
              void client.invalidateQueries({ queryKey: ["job", jobId] });
            },
            event.kind === "activity" ? 500 : 0,
          );
      },
      controller.signal,
      (value) => {
        setConnection({ jobId, connected: value });
      },
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
      clearTimeout(refresh);
    };
  }, [client, jobId, terminal]);

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
    [...(job.events ?? [])]
      .reverse()
      .find((event) => event.kind === "job_paused")?.payload.reason ===
      "ibus_preempted_at_safe_segment_boundary";
  const activity = job.status === "running" ? job.activity : null;

  return (
    <section className="page-stack" aria-labelledby="job-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">Persistent pipeline</p>
          <h1 id="job-title">课堂任务</h1>
          <p className="mono muted">{job.job_id}</p>
        </div>
        <span className={`status-pill status-${job.status}`}>
          {{
            running: "运行中",
            pending: "等待调度",
            paused: "已暂停",
            completed: "已完成",
            failed: "失败",
            cancelled: "已取消",
            cancelling: "取消中",
          }[job.status] ?? job.status}
        </span>
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
        <p className="muted">
          流程完成度 · 按已完成检查点计算，阶段内部进度单独显示。
        </p>
      </div>
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
      <CurrentActivity
        activity={activity ?? null}
        status={job.status}
        now={now}
        connected={connected && !terminal}
        polling={!query.isError}
        terminal={terminal}
      />
      <div className="panel job-flow">
        <h2>总体流程</h2>
        <ol className="job-stages">
          {stages.map(([id, label], index) => {
            const count = job.stage_counts?.[id];
            const done =
              completed ||
              (count
                ? count.total > 0 && count.completed === count.total
                : index < activeIndex);
            const active = !terminal && index === activeIndex;
            return (
              <li
                key={id}
                className={done ? "done" : active ? "active" : ""}
                aria-current={active ? "step" : undefined}
              >
                <details>
                  <summary>
                    {label}
                    <span>
                      {done ? "已完成" : active ? "当前阶段" : "等待"}
                      {count && count.total > 0
                        ? ` · ${String(count.completed)} / ${String(count.total)}`
                        : ""}
                    </span>
                  </summary>
                  <p>
                    首次加载：等待资源 → 校验文件 → 准备环境 →
                    加载模型；后续片段复用模型，切换或结束时释放。
                  </p>
                  <p className="muted">
                    每个片段独立保存；暂停或重启后，从已保存的检查点继续。
                  </p>
                  {job.stage_activity?.[id]?.slice(-8).map((step, n) => (
                    <p className="muted" key={n}>
                      {new Date(step.started_at).toLocaleTimeString()} ·{" "}
                      {operationLabel(step.operation).replace(/^正在/, "")}
                      {step.model_id ? ` · ${step.model_id}` : ""}
                      {step.operation === "verify_model" &&
                      step.completed !== undefined &&
                      step.total !== undefined
                        ? ` · ${amount(step.completed, "bytes")} / ${amount(step.total, "bytes")}`
                        : ""}
                      {terminal ? " · 已结束" : " · 活动记录"}
                    </p>
                  ))}
                  {activity?.stage === id && (
                    <p>
                      当前：{operationLabel(activity.operation)} · 第{" "}
                      {activity.attempt} 次检查点尝试
                    </p>
                  )}
                </details>
              </li>
            );
          })}
        </ol>
      </div>
      {job.error_detail && (
        <p className="error-callout">
          {job.error_code}: {job.error_detail}
        </p>
      )}
      {(job.events?.length ?? 0) > 0 && (
        <section className="panel job-recent" aria-label="最近活动">
          <h2>实时详情</h2>
          <div className="job-events">
            {readableEvents(job.events ?? []).map((event) => (
              <p key={event.sequence}>
                <time>{new Date(event.occurred_at).toLocaleTimeString()}</time>{" "}
                {eventLabel(event)}
              </p>
            ))}
          </div>
        </section>
      )}
      {(lastEvent?.job_id === job.job_id || activity || job.runtime) && (
        <details className="panel job-technical">
          <summary>技术详情</summary>
          <pre>
            {JSON.stringify(
              {
                activity,
                runtime: job.runtime,
                activity_history: job.stage_activity,
                event: lastEvent?.job_id === job.job_id ? lastEvent : null,
              },
              null,
              2,
            )}
          </pre>
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

const operationLabels: Record<string, string> = {
  model_error: "模型处理失败",
  reuse_model: "复用已加载模型",
  model_retained: "模型已保留，等待下一片段",
  restore_window_result: "恢复已完成窗口的结果",
  restore_review_result: "读取已保存的复核结果",
  quality_secondary: "第二模型复核",
  quality_tertiary: "第三模型复核",
  waiting_resource: "等待计算资源",
  verify_model: "正在校验模型文件",
  prepare_environment: "正在准备运行环境",
  load_model: "正在加载模型",
  process_audio: "正在处理音频",
  audio_processed: "音频处理完成",
  release_model: "正在释放模型",
  model_released: "模型已释放",
  normalize_audio: "正在标准化音频",
  normalize_audio_master: "准备音频标准化",
  upload_validate: "正在校验音频",
  audio_qc: "正在分析音频质量",
  vad: "正在检测语音",
  lid: "正在识别语言",
  lid_window: "正在识别语言窗口",
  lid_window_completed: "语言窗口完成",
  moss_structure: "准备说话人结构分析",
  structure_window: "正在分析结构窗口",
  structure_attempt: "正在分析说话人结构",
  speaker_analysis: "正在分析说话人",
  structure_window_completed: "结构窗口完成",
  primary_asr: "准备正文识别",
  transcribe_segment: "正在识别正文",
  quality_and_review: "正在自动复核",
  review: "正在调用备用模型复核",
  terminology: "正在修正术语",
  punctuation: "正在处理标点",
  forced_alignment: "正在对齐片段",
  final_validation: "正在验证片段",
  automatic_exports: "准备导出",
  export: "正在导出文件",
};
function operationLabel(operation: string) {
  return operationLabels[operation] ?? operation;
}
function elapsed(now: number, at?: string) {
  return at ? Math.max(0, Math.floor((now - Date.parse(at)) / 1000)) : 0;
}
function amount(value: number, unit?: string) {
  return unit === "bytes"
    ? `${(value / 1024 ** 3).toFixed(2)} GiB`
    : unit === "seconds"
      ? `${value.toFixed(1)} 秒`
      : String(value);
}
function CurrentActivity({
  activity: a,
  status,
  now,
  connected,
  polling,
  terminal,
}: {
  activity?: JobActivity | null;
  status: string;
  now: number;
  connected: boolean;
  polling: boolean;
  terminal: boolean;
}) {
  const age = elapsed(now, a?.progress_at);
  const duration = elapsed(now, a?.started_at);
  const statusLabels: Record<string, string> = {
    completed: "任务已完成",
    failed: "任务失败",
    cancelled: "任务已取消",
    cancelling: "正在取消，等待安全边界",
    paused: "任务已暂停",
    pending: "等待调度",
  };
  return (
    <section className="panel current-activity" aria-label="当前操作">
      <p className="eyebrow">当前操作</p>
      <h2>
        {a
          ? operationLabel(a.operation)
          : (statusLabels[status] ?? "等待新的活动快照")}
      </h2>
      {a && (
        <>
          <p>
            所属阶段：{stages.find(([id]) => id === a.stage)?.[1] ?? a.stage}
            {a.model_id ? ` · ${a.model_name ?? a.model_id}` : ""}
            {a.language
              ? ` · ${{ ja: "日语", zh: "中文", en: "英语", auto: "自动识别" }[a.language] ?? a.language}`
              : ""}
            {a.device
              ? ` · 设备：${a.device === "auto" ? "自动选择（尚未确认设备）" : a.device}`
              : ""}
          </p>
          {a.segment_ordinal !== undefined && (
            <p>
              当前片段：第 {a.segment_ordinal} / {a.segment_total} 个
            </p>
          )}
          {a.window_ordinal !== undefined && (
            <p>
              当前窗口：第 {a.window_ordinal} / {a.window_total} 个 · 已完成{" "}
              {a.windows_completed ?? 0} 个
            </p>
          )}
          {a.start_sample !== undefined && a.end_sample !== undefined && (
            <p>
              音频位置：{formatSamples(a.start_sample).split(".")[0]}–
              {formatSamples(a.end_sample).split(".")[0]}
            </p>
          )}
          {a.completed !== undefined && a.total !== undefined && (
            <div>
              <p>
                {a.unit === "bytes" ? "已校验" : "已处理"}{" "}
                {amount(a.completed, a.unit)} / {amount(a.total, a.unit)}
                {a.files_total !== undefined
                  ? ` · 文件 ${String(a.files_completed ?? 0)} / ${String(a.files_total)}`
                  : ""}
              </p>
              {a.total > 0 && (
                <progress
                  aria-label="当前操作进度"
                  value={a.completed}
                  max={a.total}
                />
              )}
            </div>
          )}
          {a.object && <p className="mono muted">当前对象：{a.object}</p>}
          {[
            "process_audio",
            "load_model",
            "prepare_environment",
            "release_model",
          ].includes(a.operation) && (
            <p className="notice">
              {a.operation === "process_audio"
                ? "模型处理中，暂不提供内部百分比"
                : "此操作暂不提供内部百分比"}
            </p>
          )}
          {a.reason && <p>原因：{reasonLabel(a.reason)}</p>}
          {a.fallback && <p className="notice">正在执行说话人分析回退</p>}
          {a.retry && (
            <p className="notice">正在重试 · 第 {a.model_attempt} 次</p>
          )}
          <div className="activity-health">
            <p>本次操作已运行：{duration} 秒</p>
            <p>最近实际进度更新：{age} 秒前</p>
            <p>
              模型进程：
              {a.process_checked_at && elapsed(now, a.process_checked_at) < 5
                ? a.process_alive
                  ? "存活"
                  : "未运行"
                : "暂无实时观测"}
            </p>
          </div>
          {age >= 30 && (
            <p className="notice">
              已 {age} 秒没有新的实际进展；连接或进程存活不代表计算有进展。
            </p>
          )}
          {a.timeout_seconds !== undefined && duration > a.timeout_seconds && (
            <p className="error-callout">
              已超过本次操作超时限制，等待执行器返回状态。
            </p>
          )}
        </>
      )}
      <p className="muted">
        {terminal
          ? "任务已结束，实时监听已停止"
          : connected
            ? "SSE 连接正常"
            : polling
              ? "SSE 正在连接 / 重连，快照轮询可用"
              : "服务连接异常，正在重试"}
      </p>
    </section>
  );
}
function eventLabel(event?: PipelineEvent) {
  if (!event) return "";
  const p = event.payload;
  if (event.kind === "activity") {
    const segment =
      typeof p.segment_ordinal === "number"
        ? ` · 第 ${String(p.segment_ordinal)} 段`
        : "";
    const window =
      typeof p.window_ordinal === "number"
        ? ` · 第 ${String(p.window_ordinal)} 个窗口`
        : "";
    return `${operationLabel(String(p.operation))}${segment}${window}${typeof p.model_id === "string" ? ` · ${p.model_id}` : ""}`;
  }
  const labels: Record<string, string> = {
    checkpoint_started: "检查点开始",
    checkpoint_completed: "检查点完成",
    checkpoint_failed: "检查点失败",
    checkpoint_yielded: "已到安全边界，保存进度并让出资源",
    job_completed: "任务已完成",
    job_cancelled: "任务已取消",
    job_paused: "任务暂停",
    preemption_requested: "IBus 请求 GPU，等待安全边界",
    job_resumed: "任务继续",
    job_cancelling: "请求取消任务",
    job_shutdown_yield: "服务停止，任务进度已保存",
  };
  const segment =
    typeof p.segment_ordinal === "number"
      ? ` · 第 ${String(p.segment_ordinal)} 段`
      : "";
  return `${labels[event.kind] ?? event.kind}${segment}${typeof p.checkpoint_key === "string" ? ` · ${operationLabel(p.checkpoint_key)}` : ""}`;
}
function readableEvents(events: PipelineEvent[]) {
  return events
    .filter(
      (event, index) =>
        event.kind !== "runtime_telemetry" &&
        (index === events.length - 1 ||
          eventLabel(event) !== eventLabel(events[index + 1])),
    )
    .slice(-6)
    .reverse();
}

function reasonLabel(reason: string) {
  const labels: Record<string, string> = {
    low_unified_quality: "综合质量偏低",
    repetition_or_hallucination: "疑似重复或幻觉",
    script_anomaly: "文字系统异常",
    insufficient_time_coverage: "音频覆盖不足",
    suspected_terminology_error: "疑似术语错误",
    structure_text_divergence: "与结构识别文本差异较大",
    low_snr_or_far_field: "信噪比偏低或远场录音",
    high_value_token_conflict: "关键信息冲突",
    both_candidates_low_quality: "两个候选结果质量均偏低",
    homophone_spelling_conflict: "同音词拼写冲突",
    content_or_silence_conflict: "语音内容与静音判断冲突",
  };
  return reason
    .split(", ")
    .map((item) => labels[item] ?? item)
    .join("、");
}
