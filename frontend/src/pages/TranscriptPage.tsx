import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";

import {
  api,
  authHeaders,
  type Candidate,
  formatSamples,
  type Job,
  type Segment,
  type Transcript,
} from "../api";
import { type TextLayer, useWorkbench } from "../store";

function segmentText(segment: Segment, layer: TextLayer) {
  if (layer === "raw") return segment.raw_text;
  if (layer === "faithful") return segment.faithful_text;
  if (layer === "smart") return segment.smart_corrected_text;
  return segment.user_text ?? segment.smart_corrected_text;
}

function Waveform({
  job,
  duration,
  onReady,
}: {
  job: Job;
  duration: number;
  onReady: (wave: WaveSurfer) => void;
}) {
  const target = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (target.current === null) return;
    const wave = WaveSurfer.create({
      container: target.current,
      url: `/api/v1/recordings/${job.recording_id}/media`,
      fetchParams: { headers: authHeaders() },
      height: 104,
      waveColor: "#95a59d",
      progressColor: "#d36135",
      cursorColor: "#17211d",
      normalize: true,
    });
    wave.on("ready", () => {
      onReady(wave);
    });
    return () => {
      wave.destroy();
    };
  }, [job.recording_id, onReady]);
  return (
    <div
      aria-label={`音频波形，共 ${formatSamples(duration)}`}
      className="waveform"
      ref={target}
    />
  );
}

export function TranscriptPage() {
  const jobId = useWorkbench((state) => state.currentJobId);
  const selectedId = useWorkbench((state) => state.selectedSegmentId);
  const selectSegment = useWorkbench((state) => state.selectSegment);
  const layer = useWorkbench((state) => state.textLayer);
  const setLayer = useWorkbench((state) => state.setTextLayer);
  const lowOnly = useWorkbench((state) => state.lowConfidenceOnly);
  const setLowOnly = useWorkbench((state) => state.setLowConfidenceOnly);
  const wave = useRef<WaveSurfer | null>(null);
  const client = useQueryClient();

  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>(`/jobs/${String(jobId)}`),
    enabled: jobId !== null,
  });
  const transcript = useQuery({
    queryKey: ["transcript", jobId, lowOnly],
    queryFn: () =>
      api<Transcript>(
        `/jobs/${String(jobId)}/transcript?low_confidence_only=${String(lowOnly)}`,
      ),
    enabled: jobId !== null,
  });
  const detail = useQuery({
    queryKey: ["segment", selectedId],
    queryFn: () => api<Segment>(`/segments/${String(selectedId)}`),
    enabled: selectedId !== null,
  });
  const candidates = useQuery({
    queryKey: ["candidates", selectedId],
    queryFn: () =>
      api<Candidate[]>(`/segments/${String(selectedId)}/candidates`),
    enabled: selectedId !== null,
  });

  const invalidateSegment = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["segment", selectedId] }),
      client.invalidateQueries({ queryKey: ["transcript", jobId] }),
    ]);
  };
  const patch = useMutation({
    mutationFn: (value: {
      version: number;
      text?: string;
      speaker_name?: string;
    }) =>
      api<Segment>(`/segments/${String(selectedId)}`, {
        method: "PATCH",
        body: JSON.stringify({ ...value, layer: "user" }),
      }),
    onSuccess: invalidateSegment,
  });
  const history = useMutation({
    mutationFn: ({
      action,
      version,
    }: {
      action: "undo" | "redo";
      version: number;
    }) =>
      api<Segment>(`/segments/${String(selectedId)}/${action}`, {
        method: "POST",
        body: JSON.stringify({ version }),
      }),
    onSuccess: invalidateSegment,
  });
  const rerun = useMutation({
    mutationFn: () =>
      api(`/segments/${String(selectedId)}/rerun`, {
        method: "POST",
        body: "{}",
      }),
  });
  const adopt = useMutation({
    mutationFn: (candidateId: string) =>
      api(`/segments/${String(selectedId)}/adopt-candidate`, {
        method: "POST",
        body: JSON.stringify({
          candidate_id: candidateId,
          version: detail.data?.version,
        }),
      }),
    onSuccess: invalidateSegment,
  });

  if (jobId === null) return <p className="empty-state">请先创建课堂任务。</p>;
  if (!job.data || !transcript.data)
    return <p className="loading">正在加载可追溯转录稿…</p>;
  const segments = transcript.data.segments;
  const duration = Math.max(1, ...segments.map((item) => item.end_sample));

  function seek(segment: Segment) {
    selectSegment(segment.id);
    wave.current?.seekTo(segment.start_sample / duration);
  }

  return (
    <section
      className="page-stack transcript-page"
      aria-labelledby="transcript-title"
    >
      <header className="page-header">
        <div>
          <p className="eyebrow">Canonical 16 kHz timeline</p>
          <h1 id="transcript-title">转录工作台</h1>
          <p>每句绑定真实音频范围；自动结果可直接导出，校对是可选增强。</p>
        </div>
        <label className="toggle">
          <input
            checked={lowOnly}
            onChange={(event) => {
              setLowOnly(event.target.checked);
            }}
            type="checkbox"
          />
          <span />
          仅低置信度
        </label>
      </header>
      <div className="panel timeline-panel">
        <Waveform
          duration={duration}
          job={job.data}
          onReady={(instance) => {
            wave.current = instance;
          }}
        />
        <TimelineTrack
          label="说话人"
          segments={segments}
          duration={duration}
          value={(item) => item.speaker_name ?? item.speaker_id ?? "未知"}
        />
        <TimelineTrack
          label="语言"
          segments={segments}
          duration={duration}
          value={(item) => item.language.toUpperCase()}
        />
      </div>
      <div className="layer-tabs" role="tablist" aria-label="文本层">
        {(["raw", "faithful", "smart", "user"] as const).map((value) => (
          <button
            aria-selected={layer === value}
            className={layer === value ? "active" : ""}
            key={value}
            onClick={() => {
              setLayer(value);
            }}
            role="tab"
            type="button"
          >
            {
              {
                raw: "原始",
                faithful: "忠实",
                smart: "智能纠正",
                user: "用户版",
              }[value]
            }
          </button>
        ))}
      </div>
      <div className="transcript-layout">
        <div className="segment-list" aria-label="句段列表">
          {segments.length === 0 && (
            <p className="empty-state">该筛选下没有句段。</p>
          )}
          {segments.map((segment) => (
            <button
              className={`segment-row ${selectedId === segment.id ? "selected" : ""}`}
              key={segment.id}
              onClick={() => {
                seek(segment);
              }}
              type="button"
            >
              <span className="segment-time">
                {formatSamples(segment.start_sample)}
                <br />
                {formatSamples(segment.end_sample)}
              </span>
              <span className="speaker-chip">
                {segment.speaker_name ?? segment.speaker_id ?? "Speaker ?"}
              </span>
              <span className="segment-copy">
                {segmentText(segment, layer) || <em>空句段</em>}
              </span>
              <span
                className={`confidence ${segment.low_confidence ? "low" : ""}`}
              >
                {segment.quality_score === null
                  ? "—"
                  : `${String(Math.round(segment.quality_score * 100))}%`}
              </span>
            </button>
          ))}
        </div>
        <SegmentInspector
          adopt={(id) => {
            adopt.mutate(id);
          }}
          candidates={candidates.data ?? []}
          history={(action, version) => {
            history.mutate({ action, version });
          }}
          onPatch={(value) => {
            patch.mutate(value);
          }}
          onRerun={() => {
            rerun.mutate();
          }}
          savePending={patch.isPending}
          segment={detail.data}
        />
      </div>
    </section>
  );
}

function TimelineTrack({
  label,
  segments,
  duration,
  value,
}: {
  label: string;
  segments: Segment[];
  duration: number;
  value: (segment: Segment) => string;
}) {
  return (
    <div className="timeline-track">
      <strong>{label}</strong>
      <div>
        {segments.map((segment) => (
          <span
            key={segment.id}
            style={{
              left: `${String((segment.start_sample / duration) * 100)}%`,
              width: `${String(Math.max(0.5, ((segment.end_sample - segment.start_sample) / duration) * 100))}%`,
            }}
          >
            {value(segment)}
          </span>
        ))}
      </div>
    </div>
  );
}

function SegmentInspector({
  segment,
  candidates,
  onPatch,
  history,
  onRerun,
  adopt,
  savePending,
}: {
  segment: Segment | undefined;
  candidates: Candidate[];
  onPatch: (value: {
    version: number;
    text?: string;
    speaker_name?: string;
  }) => void;
  history: (action: "undo" | "redo", version: number) => void;
  onRerun: () => void;
  adopt: (id: string) => void;
  savePending: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [speaker, setSpeaker] = useState("");
  const timer = useRef<number | null>(null);
  useEffect(() => {
    setDraft(segment?.user_text ?? segment?.smart_corrected_text ?? "");
    setSpeaker(segment?.speaker_name ?? "");
  }, [
    segment?.id,
    segment?.speaker_name,
    segment?.smart_corrected_text,
    segment?.user_text,
  ]);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  if (!segment)
    return (
      <aside className="inspector panel">
        <p className="muted">选择一句以查看候选、质量与来源。</p>
      </aside>
    );
  function scheduleSave(text: string) {
    setDraft(text);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      onPatch({ version: segment?.version ?? 1, text });
    }, 700);
  }
  return (
    <aside className="inspector panel">
      <div className="inspector-heading">
        <div>
          <p className="eyebrow">Selected segment</p>
          <strong>
            {formatSamples(segment.start_sample)} –{" "}
            {formatSamples(segment.end_sample)}
          </strong>
        </div>
        <span>{savePending ? "保存中…" : "已自动保存"}</span>
      </div>
      <label>
        <span>说话人姓名</span>
        <div className="inline-field">
          <input
            onChange={(event) => {
              setSpeaker(event.target.value);
            }}
            value={speaker}
          />
          <button
            onClick={() => {
              onPatch({ version: segment.version, speaker_name: speaker });
            }}
            type="button"
          >
            保存
          </button>
        </div>
      </label>
      <label>
        <span>用户文本</span>
        <textarea
          onChange={(event) => {
            scheduleSave(event.target.value);
          }}
          rows={7}
          value={draft}
        />
      </label>
      <div className="toolbar compact">
        <button
          onClick={() => {
            history("undo", segment.version);
          }}
          type="button"
        >
          撤销
        </button>
        <button
          onClick={() => {
            history("redo", segment.version);
          }}
          type="button"
        >
          重做
        </button>
        <button onClick={onRerun} type="button">
          重跑当前音频范围
        </button>
      </div>
      <details open>
        <summary>模型候选 · {candidates.length}</summary>
        {candidates.map((candidate) => (
          <article className="candidate-card" key={candidate.id}>
            <div>
              <strong>{candidate.model_id}</strong>
              <span>
                {candidate.confidence_calibrated === null
                  ? "未校准"
                  : `${String(Math.round(candidate.confidence_calibrated * 100))}%`}
              </span>
            </div>
            <p>{candidate.normalized_text}</p>
            <small>
              {Object.keys(candidate.quality).join(" · ") || "无质量警告"}
            </small>
            <button
              disabled={!candidate.valid || candidate.adopted}
              onClick={() => {
                adopt(candidate.id);
              }}
              type="button"
            >
              {candidate.adopted ? "已采用" : "采用候选"}
            </button>
          </article>
        ))}
      </details>
      <details>
        <summary>Token provenance · {segment.tokens.length}</summary>
        <div className="token-cloud">
          {segment.tokens.map((token) => (
            <span key={token.id} title={JSON.stringify(token.provenance)}>
              {token.text}
              <small>{token.confidence?.toFixed(2) ?? "—"}</small>
            </span>
          ))}
        </div>
      </details>
      <details>
        <summary>审计历史</summary>
        <pre>{JSON.stringify(segment.audit ?? [], null, 2)}</pre>
      </details>
    </aside>
  );
}
