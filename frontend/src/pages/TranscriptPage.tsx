import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import WaveSurfer from "wavesurfer.js";

import {
  api,
  authHeaders,
  type Candidate,
  formatSamples,
  type Job,
  type Recording,
  type Segment,
  type Transcript,
} from "../api";
import { type TextLayer, useWorkbench } from "../store";
import { buildReadableParagraphs } from "../readability";
import { scheduleTranscriptSave, useTranscriptSaves } from "../transcriptSaves";
import { TranscriptExportDialog } from "../components/TranscriptExportDialog";

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
  request,
}: {
  job: Job;
  duration: number;
  onReady: (wave: WaveSurfer | null) => void;
  request: { start: number; end: number; play: boolean } | null;
}) {
  const target = useRef<HTMLDivElement>(null);
  const instance = useRef<WaveSurfer | null>(null);
  const endAt = useRef<number | null>(null);
  const generation = useRef(0);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loadRequested, setLoadRequested] = useState(
    duration <= 30 * 60 * 16000,
  );
  useEffect(() => {
    if (request) setLoadRequested(true);
  }, [request]);
  useEffect(() => {
    const wave = instance.current;
    if (!request || !ready || !wave) return;
    const current = ++generation.current;
    endAt.current = null;
    wave.pause();
    const end = Math.min(request.end, wave.getDuration());
    if (
      !Number.isFinite(request.start) ||
      !Number.isFinite(end) ||
      request.start < 0 ||
      end <= request.start
    ) {
      setError("该句段缺少可用时间戳，无法回听。");
      return;
    }
    setError(null);
    wave.setTime(request.start);
    if (request.play) {
      endAt.current = end;
      void wave.play().catch((reason: unknown) => {
        if (generation.current !== current || instance.current !== wave) return;
        endAt.current = null;
        setError(reason instanceof Error ? reason.message : "播放失败，请重试");
      });
    }
  }, [request, ready]);
  useEffect(() => {
    if (!loadRequested || target.current === null) return;
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
    instance.current = wave;
    wave.on("ready", () => {
      setReady(true);
      onReady(wave);
    });
    wave.on("play", () => {
      setPlaying(true);
    });
    wave.on("pause", () => {
      setPlaying(false);
    });
    wave.on("finish", () => {
      setPlaying(false);
    });
    wave.on("timeupdate", (seconds) => {
      setPosition(seconds);
      if (endAt.current !== null && seconds >= endAt.current) {
        const end = endAt.current;
        endAt.current = null;
        wave.pause();
        wave.setTime(end);
      }
    });
    wave.on("interaction", () => {
      endAt.current = null;
      generation.current++;
    });
    wave.on("error", (reason) => {
      setError(reason.message);
      setReady(false);
    });
    return () => {
      endAt.current = null;
      instance.current = null;
      onReady(null);
      wave.destroy();
    };
  }, [job.recording_id, loadRequested, onReady]);
  return (
    <>
      {!loadRequested && (
        <button
          type="button"
          onClick={() => {
            setLoadRequested(true);
          }}
        >
          加载音频波形
        </button>
      )}
      <div
        aria-label={`音频波形，共 ${formatSamples(duration)}`}
        className="waveform"
        ref={target}
      />
      <div className="toolbar audio-controls" aria-label="音频播放控制">
        <button
          type="button"
          disabled={!ready}
          onClick={() => {
            setError(null);
            endAt.current = null;
            generation.current++;
            void instance.current?.playPause().catch((reason: unknown) => {
              setError(
                reason instanceof Error ? reason.message : "播放失败，请重试",
              );
            });
          }}
        >
          {playing ? "暂停" : "播放"}
        </button>
        <button
          type="button"
          disabled={!ready}
          onClick={() => {
            endAt.current = null;
            instance.current?.skip(-10);
          }}
        >
          后退 10 秒
        </button>
        <button
          type="button"
          disabled={!ready}
          onClick={() => {
            endAt.current = null;
            instance.current?.skip(10);
          }}
        >
          前进 10 秒
        </button>
        <span>
          {formatSamples(Math.round(position * 16000))} /{" "}
          {formatSamples(duration)}
        </span>
        <label>
          播放速度{" "}
          <select
            defaultValue="1"
            disabled={!ready}
            onChange={(event) => {
              instance.current?.setPlaybackRate(Number(event.target.value));
            }}
          >
            {[0.5, 0.75, 1, 1.25, 1.5, 2].map((rate) => (
              <option key={rate} value={rate}>
                {rate}×
              </option>
            ))}
          </select>
        </label>
        {loadRequested && !ready && !error && (
          <span role="status">音频加载中…</span>
        )}
      </div>
      <p className="muted">
        点击句段立即回听，到句尾自动停止。点击波形可自由定位。
      </p>
      {error && <p role="alert">音频不可播放：{error}</p>}
    </>
  );
}

export function TranscriptPage() {
  const jobId = useWorkbench((state) => state.currentJobId);
  const setPage = useWorkbench((state) => state.setPage);
  const selectedId = useWorkbench((state) => state.selectedSegmentId);
  const selectSegment = useWorkbench((state) => state.selectSegment);
  const layer = useWorkbench((state) => state.textLayer);
  const setLayer = useWorkbench((state) => state.setTextLayer);
  const lowOnly = useWorkbench((state) => state.lowConfidenceOnly);
  const setLowOnly = useWorkbench((state) => state.setLowConfidenceOnly);
  const wave = useRef<WaveSurfer | null>(null);
  const [playback, setPlayback] = useState<{
    jobId: string | null;
    start: number;
    end: number;
    play: boolean;
  } | null>(null);
  const client = useQueryClient();
  const [mediaDuration, setMediaDuration] = useState(0);
  const [exportOpen, setExportOpen] = useState(false);
  const [readingView, setReadingView] = useState<"sentences" | "paragraphs">(
    "sentences",
  );
  const onWaveReady = useCallback((instance: WaveSurfer | null) => {
    wave.current = instance;
    if (instance)
      setMediaDuration(Math.max(1, Math.round(instance.getDuration() * 16000)));
    else setMediaDuration(0);
  }, []);

  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: ({ signal }) => api<Job>(`/jobs/${String(jobId)}`, { signal }),
    enabled: jobId !== null,
  });
  const transcript = useQuery({
    queryKey: ["transcript", jobId, "summary"],
    queryFn: ({ signal }) =>
      api<Transcript>(
        `/jobs/${String(jobId)}/transcript?include_tokens=false`,
        { signal },
      ),
    enabled: jobId !== null,
  });
  const recording = useQuery({
    queryKey: ["recording", job.data?.recording_id],
    queryFn: ({ signal }) =>
      api<Recording>(`/recordings/${String(job.data?.recording_id)}`, {
        signal,
      }),
    enabled: Boolean(job.data?.recording_id),
  });
  const detail = useQuery({
    queryKey: ["segment", selectedId],
    queryFn: ({ signal }) =>
      api<Segment>(`/segments/${String(selectedId)}`, { signal }),
    enabled: selectedId !== null,
  });
  const candidates = useQuery({
    queryKey: ["candidates", selectedId],
    queryFn: ({ signal }) =>
      api<Candidate[]>(`/segments/${String(selectedId)}/candidates`, {
        signal,
      }),
    enabled: selectedId !== null,
  });

  const invalidateSegment = async (segment: Segment) => {
    client.setQueryData(["segment", segment.id], segment);
    await Promise.all([
      client.invalidateQueries({ queryKey: ["segment", segment.id] }),
      client.invalidateQueries({ queryKey: ["transcript", segment.job_id] }),
    ]);
  };
  const patch = useMutation({
    mutationFn: ({
      id,
      ...value
    }: {
      id: string;
      version: number;
      text?: string;
      speaker_name?: string;
    }) =>
      api<Segment>(`/segments/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ ...value, layer: "user" }),
      }),
    onSuccess: invalidateSegment,
  });
  const history = useMutation({
    mutationFn: ({
      action,
      version,
      id,
    }: {
      action: "undo" | "redo";
      version: number;
      id: string;
    }) =>
      api<Segment>(`/segments/${id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ version }),
      }),
    onSuccess: invalidateSegment,
  });
  const rerun = useMutation({
    mutationFn: (id: string) =>
      api(`/segments/${id}/rerun`, {
        method: "POST",
        body: "{}",
      }),
  });
  const adopt = useMutation({
    mutationFn: ({
      id,
      candidateId,
      version,
    }: {
      id: string;
      candidateId: string;
      version: number;
    }) =>
      api<Segment>(`/segments/${id}/adopt-candidate`, {
        method: "POST",
        body: JSON.stringify({
          candidate_id: candidateId,
          version,
        }),
      }),
    onSuccess: invalidateSegment,
  });

  if (jobId === null) return <p className="empty-state">请先创建课堂任务。</p>;
  if (job.isError || transcript.isError)
    return (
      <p role="alert">{job.error?.message ?? transcript.error?.message}</p>
    );
  if (!job.data || !transcript.data)
    return <p className="loading">正在加载可追溯转录稿…</p>;
  const fullSegments = transcript.data.segments;
  const segments = lowOnly
    ? fullSegments.filter((segment) => segment.low_confidence)
    : fullSegments;
  const duration = Math.max(
    1,
    mediaDuration || recording.data?.duration_samples || 0,
    fullSegments.reduce((end, segment) => Math.max(end, segment.end_sample), 0),
  );

  function seek(segment: Segment) {
    selectSegment(segment.id);
    setPlayback({
      jobId,
      start: segment.start_sample / 16000,
      end: segment.end_sample / 16000,
      play: true,
    });
  }

  return (
    <section
      className="page-stack transcript-page"
      aria-labelledby="transcript-title"
    >
      <button
        className="back-to-manuscripts"
        type="button"
        onClick={() => {
          setPage("transcripts");
        }}
      >
        ← 全部转录稿
      </button>
      <header className="page-header">
        <div>
          <p className="eyebrow">
            {recording.data?.source_name ?? "课堂转录稿"}
          </p>
          <h1 id="transcript-title">转录工作台</h1>
          <p>每句绑定真实音频范围；自动结果可直接导出，校对是可选增强。</p>
        </div>
        <div className="toolbar transcript-header-actions">
          <button
            className="primary-button"
            type="button"
            disabled={fullSegments.length === 0}
            onClick={() => {
              setExportOpen(true);
            }}
          >
            导出转录稿
          </button>
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
        </div>
      </header>
      {exportOpen && (
        <TranscriptExportDialog
          key={jobId}
          jobId={jobId}
          sourceName={recording.data?.source_name ?? "课堂转录稿"}
          initialLayer={layer === "raw" ? "faithful" : layer}
          initialView={
            readingView === "paragraphs" ? "readable_paragraphs" : "sentences"
          }
          onClose={() => {
            setExportOpen(false);
          }}
        />
      )}
      <div className="panel timeline-panel">
        {recording.data?.parent_recording_id && (
          <p className="muted">
            来源录音片段：
            {formatSamples(recording.data.source_start_sample ?? 0)} —{" "}
            {formatSamples(recording.data.source_end_sample ?? 0)}
            。下方时间从片段起点计算。
          </p>
        )}
        <Waveform
          key={job.data.recording_id}
          duration={duration}
          job={job.data}
          onReady={onWaveReady}
          request={playback?.jobId === jobId ? playback : null}
        />
        <button
          type="button"
          disabled={!selectedId}
          onClick={() => {
            const segment = fullSegments.find((item) => item.id === selectedId);
            if (segment)
              setPlayback({
                jobId,
                start: segment.start_sample / 16000,
                end: segment.end_sample / 16000,
                play: false,
              });
          }}
        >
          仅定位所选句段
        </button>
        {fullSegments.some(
          (segment) => segment.timing_quality === "invalid",
        ) && (
          <p role="status">
            部分文本缺少有效时间戳，无法回听，也不会写入字幕；全文仍保留。
          </p>
        )}
        <TimelineTrack
          label="说话人"
          segments={fullSegments}
          duration={duration}
          value={(item) => item.speaker_name ?? item.speaker_id ?? "未知"}
        />
        <TimelineTrack
          label="语言"
          segments={fullSegments}
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
      <div className="reading-view" role="group" aria-label="转录稿视图">
        <button
          type="button"
          aria-pressed={readingView === "sentences"}
          onClick={() => {
            setReadingView("sentences");
          }}
        >
          句段
        </button>
        <button
          type="button"
          aria-pressed={readingView === "paragraphs"}
          onClick={() => {
            setReadingView("paragraphs");
          }}
        >
          自然段
        </button>
        {readingView === "paragraphs" && (
          <span className="muted">点击文字可回听并校对对应片段。</span>
        )}
      </div>
      <div className="transcript-layout">
        {readingView === "paragraphs" ? (
          <ParagraphList
            key={`${jobId}:${layer}:${String(lowOnly)}`}
            segments={fullSegments}
            layer={layer}
            lowOnly={lowOnly}
            selectedId={selectedId}
            onSelect={seek}
          />
        ) : (
          <div className="segment-list" aria-label="句段列表">
            {segments.length === 0 && (
              <p className="empty-state">该筛选下没有句段。</p>
            )}
            <SegmentList
              segments={segments}
              selectedId={selectedId}
              renderRow={(segment) => (
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
                    {recording.data?.source_start_sample != null &&
                      segment.timing_quality !== "invalid" && (
                        <small>
                          原录音{" "}
                          {formatSamples(
                            recording.data.source_start_sample +
                              segment.start_sample,
                          )}
                        </small>
                      )}
                  </span>
                  <span className="speaker-chip">
                    {segment.speaker_name ?? segment.speaker_id ?? "Speaker ?"}
                  </span>
                  <span className="segment-copy">
                    {segmentText(segment, layer) || <em>空句段</em>}
                    {segment.repetition_warning && (
                      <span className="anomaly-tag">
                        {segment.repetition_warning.all_candidates
                          ? "所有候选均疑似重复"
                          : "候选疑似重复"}{" "}
                        · 待复核
                      </span>
                    )}
                  </span>
                  <span
                    className={`confidence ${segment.low_confidence ? "low" : ""}`}
                  >
                    {segment.quality_score === null
                      ? "—"
                      : `${String(Math.round(segment.quality_score * 100))}%`}
                  </span>
                </button>
              )}
            />
          </div>
        )}
        <SegmentInspector
          adopt={(id) => {
            if (detail.data)
              adopt.mutate({
                id: detail.data.id,
                candidateId: id,
                version: detail.data.version,
              });
          }}
          candidates={candidates.data ?? []}
          history={(action, version) => {
            if (detail.data)
              history.mutate({ id: detail.data.id, action, version });
          }}
          onPatch={(value) => {
            if (detail.data) patch.mutate({ ...value, id: detail.data.id });
          }}
          saveError={patch.error?.message}
          historyError={
            history.error
              ? `${history.variables.action === "redo" ? "重做" : "撤销"}失败：${history.error.message}`
              : undefined
          }
          onRerun={() => {
            if (detail.data) rerun.mutate(detail.data.id);
          }}
          savePending={patch.isPending}
          segment={detail.data?.job_id === jobId ? detail.data : undefined}
        />
      </div>
    </section>
  );
}

const rowHeight = 116;
function SegmentList({
  segments,
  selectedId,
  renderRow,
}: {
  segments: Segment[];
  selectedId: string | null;
  renderRow: (segment: Segment) => ReactNode;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(600);
  const virtual = segments.length > 200;
  useEffect(() => {
    const target = viewport.current;
    if (!virtual || !target) return;
    const resize = () => {
      setHeight(target.clientHeight || 600);
    };
    resize();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(resize);
    observer.observe(target);
    return () => {
      observer.disconnect();
    };
  }, [virtual]);
  useEffect(() => {
    const target = viewport.current;
    if (!virtual || !target) return;
    const index = segments.findIndex((segment) => segment.id === selectedId);
    if (index < 0) return;
    const top = index * rowHeight;
    if (top < target.scrollTop || top + rowHeight > target.scrollTop + height) {
      target.scrollTop = top;
      setScrollTop(top);
    }
  }, [virtual, selectedId, segments, height]);
  if (!virtual) return <>{segments.map(renderRow)}</>;
  const start = Math.max(
    0,
    Math.min(segments.length - 1, Math.floor(scrollTop / rowHeight) - 4),
  );
  const end = Math.min(
    segments.length,
    Math.ceil((scrollTop + height) / rowHeight) + 4,
  );
  return (
    <div
      ref={viewport}
      className="virtual-segment-list"
      tabIndex={0}
      aria-label="滚动句段列表"
      onScroll={(event) => {
        setScrollTop(event.currentTarget.scrollTop);
      }}
    >
      <div
        style={{ height: segments.length * rowHeight, position: "relative" }}
      >
        {segments.slice(start, end).map((segment, index) => (
          <div
            key={segment.id}
            className="virtual-segment-row"
            style={{ top: (start + index) * rowHeight, height: rowHeight }}
          >
            {renderRow(segment)}
          </div>
        ))}
      </div>
    </div>
  );
}

function ParagraphList({
  segments,
  layer,
  lowOnly,
  selectedId,
  onSelect,
}: {
  segments: Segment[];
  layer: TextLayer;
  lowOnly: boolean;
  selectedId: string | null;
  onSelect: (segment: Segment) => void;
}) {
  const paragraphs = useMemo(
    () =>
      buildReadableParagraphs(
        segments,
        (segment) => segmentText(segment, layer),
        (segment) => !lowOnly || segment.low_confidence,
      ),
    [segments, layer, lowOnly],
  );
  const pageSize = 40;
  const [requestedPage, setRequestedPage] = useState(0);
  const page = Math.min(
    requestedPage,
    Math.max(0, Math.ceil(paragraphs.length / pageSize) - 1),
  );
  useEffect(() => {
    if (!selectedId) return;
    const index = paragraphs.findIndex((parts) =>
      parts.some((part) => part.source.id === selectedId),
    );
    if (index >= 0)
      setRequestedPage((current) => {
        const visible = paragraphs.slice(
          current * pageSize,
          (current + 1) * pageSize,
        );
        return visible.some((parts) =>
          parts.some((part) => part.source.id === selectedId),
        )
          ? current
          : Math.floor(index / pageSize);
      });
  }, [paragraphs, selectedId]);

  return (
    <div className="paragraph-list" aria-label="自然段列表">
      {paragraphs.length === 0 && (
        <p className="empty-state">该筛选下没有句段。</p>
      )}
      {paragraphs.slice(page * pageSize, (page + 1) * pageSize).map((parts) => {
        const first = parts[0];
        if (!first) return null;
        return (
          <article
            className="readable-paragraph"
            key={first.source.id + ":" + String(first.start)}
          >
            <div className="paragraph-meta">
              <span className="speaker-chip">
                {first.source.speaker_name ??
                  first.source.speaker_id ??
                  "Speaker ?"}
              </span>
            </div>
            <p className="paragraph-copy">
              {parts.map((part) => (
                <span key={part.source.id + ":" + String(part.start)}>
                  {part.separator}
                  <button
                    type="button"
                    className={
                      "paragraph-fragment" +
                      (selectedId === part.source.id ? " selected" : "") +
                      (part.source.low_confidence ? " low-confidence" : "")
                    }
                    title={
                      part.source.timing_quality === "invalid"
                        ? "该片段缺少有效时间戳"
                        : "回听片段 " +
                          formatSamples(part.source.start_sample) +
                          " — " +
                          formatSamples(part.source.end_sample)
                    }
                    onClick={() => {
                      onSelect(part.source);
                    }}
                  >
                    {part.text || <em>空句段</em>}
                  </button>
                  {part.source.repetition_warning && (
                    <span className="anomaly-tag">候选疑似重复 · 待复核</span>
                  )}
                </span>
              ))}
            </p>
          </article>
        );
      })}
      {paragraphs.length > pageSize && (
        <nav className="paragraph-pages" aria-label="自然段分页">
          <button
            type="button"
            disabled={page === 0}
            onClick={() => {
              setRequestedPage(page - 1);
            }}
          >
            上一页
          </button>
          <span>
            {page + 1} / {Math.ceil(paragraphs.length / pageSize)}
          </span>
          <button
            type="button"
            disabled={(page + 1) * pageSize >= paragraphs.length}
            onClick={() => {
              setRequestedPage(page + 1);
            }}
          >
            下一页
          </button>
        </nav>
      )}
    </div>
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
  const ranges =
    segments.length <= 256
      ? segments.map((segment) => ({
          start: segment.start_sample,
          end: segment.end_sample,
          text: value(segment),
        }))
      : (() => {
          const bins = new Map<number, Set<string>>();
          for (const segment of segments) {
            const first = Math.max(
              0,
              Math.floor((segment.start_sample / duration) * 256),
            );
            const last = Math.min(
              255,
              Math.ceil((segment.end_sample / duration) * 256) - 1,
            );
            for (let index = first; index <= last; index++) {
              const values = bins.get(index) ?? new Set<string>();
              values.add(value(segment));
              bins.set(index, values);
            }
          }
          return [...bins.entries()]
            .sort(([a], [b]) => a - b)
            .map(([index, values]) => ({
              start: (index * duration) / 256,
              end: ((index + 1) * duration) / 256,
              text: [...values].join(" / "),
            }));
        })();
  return (
    <div className="timeline-track">
      <strong>{label}</strong>
      <div>
        {ranges.map((range, index) => (
          <span
            key={index}
            title={range.text}
            style={{
              left: `${String((range.start / duration) * 100)}%`,
              width: `${String(((range.end - range.start) / duration) * 100)}%`,
            }}
          >
            {range.text}
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
  saveError,
  historyError,
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
  saveError: string | undefined;
  historyError: string | undefined;
}) {
  const client = useQueryClient();
  const savedDraft = useTranscriptSaves((state) =>
    segment ? state.drafts[segment.id] : undefined,
  );
  const draft =
    savedDraft?.status !== "saved" && savedDraft
      ? savedDraft.text
      : (segment?.user_text ?? segment?.smart_corrected_text ?? "");
  const [speaker, setSpeaker] = useState("");
  useEffect(() => {
    setSpeaker(segment?.speaker_name ?? "");
  }, [segment?.id, segment?.speaker_name]);
  if (!segment)
    return (
      <aside className="inspector panel">
        <p className="muted">选择一句以查看候选、质量与来源。</p>
      </aside>
    );
  const textBusy = savedDraft !== undefined && savedDraft.status !== "saved";
  return (
    <aside className="inspector panel">
      {segment.repetition_warning && (
        <div className="anomaly-details" role="note">
          <strong>
            {segment.repetition_warning.all_candidates
              ? "所有可用候选均疑似重复"
              : "疑似重复"}
          </strong>
          <p>已保留候选文字，请结合音频复核。模型间一致不会自动解除异常。</p>
          {segment.repetition_warning.candidates.map((candidate, index) => (
            <p key={`${candidate.model_id}-${String(index)}`}>
              {candidate.model_id}：
              {candidate.fragment ? `重复片段「${candidate.fragment}」；` : ""}
              {candidate.issues
                .map(
                  (issue) =>
                    (
                      ({
                        repeated_3_to_10_token_ngram: "短语大量重复",
                        shortest_loop_period_detected: "连续循环重复",
                        decode_prefix_novelty_stalled: "输出停滞",
                        abnormally_compressible_repetition: "重复内容占比异常",
                        repeated_sentence: "整句反复出现",
                      }) as Record<string, string>
                    )[issue] ?? issue,
                )
                .join("、")}
            </p>
          ))}
        </div>
      )}
      <div className="inspector-heading">
        <div>
          <p className="eyebrow">Selected segment</p>
          <strong>
            {formatSamples(segment.start_sample)} –{" "}
            {formatSamples(segment.end_sample)}
          </strong>
        </div>
        <span>
          {saveError || savedDraft?.status === "error"
            ? "保存失败，草稿已保留"
            : savePending || savedDraft?.status === "saving"
              ? "保存中…"
              : savedDraft?.status === "waiting"
                ? "等待保存…"
                : "已自动保存"}
        </span>
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
            disabled={textBusy || savePending}
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
            scheduleTranscriptSave(segment, event.target.value, client);
          }}
          rows={7}
          value={draft}
        />
      </label>
      {saveError && <p role="alert">{saveError}</p>}
      {savedDraft?.conflict && savedDraft.serverVersion !== undefined && (
        <div className="error-callout" role="status">
          <strong>版本冲突：请比较并编辑上方草稿。</strong>
          <p>
            服务器最新版（v{savedDraft.serverVersion}）：
            {savedDraft.serverText || "空句段"}
          </p>
          <p>确认后可在页面顶部保存草稿，或放弃并重新加载。</p>
        </div>
      )}
      {historyError && <p role="alert">{historyError}</p>}
      <div className="toolbar compact">
        <button
          disabled={textBusy || savePending}
          onClick={() => {
            history("undo", segment.version);
          }}
          type="button"
        >
          撤销
        </button>
        <button
          disabled={textBusy || savePending}
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
            <details>
              <summary>完整候选证据</summary>
              <p className="mono">完整 revision：{candidate.model_revision}</p>
              <p>原始候选：{candidate.raw_text}</p>
              <p>原始置信度：{candidate.confidence_raw ?? "未提供"}</p>
              <p>校准置信度：{candidate.confidence_calibrated ?? "未校准"}</p>
              <p>
                解码参数：
                <code>{JSON.stringify(candidate.decode_config ?? {})}</code>
              </p>
              <p>
                推理指标：
                <code>{JSON.stringify(candidate.inference_metrics ?? {})}</code>
              </p>
            </details>
            <small>
              {Object.keys(candidate.quality).join(" · ") || "无质量警告"}
            </small>
            <button
              disabled={
                textBusy || savePending || !candidate.valid || candidate.adopted
              }
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
