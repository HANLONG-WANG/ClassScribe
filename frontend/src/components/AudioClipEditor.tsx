import { useCallback, useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin, {
  type Region,
} from "wavesurfer.js/dist/plugins/regions.esm.js";
import TimelinePlugin from "wavesurfer.js/dist/plugins/timeline.esm.js";
import { formatClipTime, parseClipTime } from "../audioClipTime";
import { authHeaders, type Recording } from "../api";

export function AudioClipEditor(props: {
  recording: Recording;
  onSubmit: (clip?: { start_sample: number; end_sample: number }) => void;
}) {
  return (
    <AudioClipWorkbench
      key={`${props.recording.id}:${props.recording.media_url}:${String(props.recording.duration_samples)}`}
      {...props}
    />
  );
}

let activeClipPlayer: WaveSurfer | null = null;

function AudioClipWorkbench({
  recording,
  onSubmit,
}: {
  recording: Recording;
  onSubmit: (clip?: { start_sample: number; end_sample: number }) => void;
}) {
  const editor = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const media = useRef<HTMLAudioElement>(null);
  const player = useRef<WaveSurfer | null>(null);
  const selection = useRef<Region | null>(null);
  const range = useRef({ start: 0, end: recording.duration_samples / 16000 });
  const preview = useRef<{ start: number; end: number } | null>(null);
  const looping = useRef(false);
  const updatingRegion = useRef(false);
  const settling = useRef(false);
  const zoom = useRef(0);
  const duration = recording.duration_samples / 16000;
  const [startText, setStartText] = useState(formatClipTime(0));
  const [endText, setEndText] = useState(formatClipTime(duration));
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [loadingError, setLoadingError] = useState("");
  const [error, setError] = useState("");
  const [loop, setLoop] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [volume, setVolume] = useState(0.8);
  const start = parseClipTime(startText);
  // The visible millisecond timecode can round beyond the last audio sample.
  const end =
    endText === formatClipTime(duration) ? duration : parseClipTime(endText);
  const valid =
    start !== null &&
    end !== null &&
    start >= 0 &&
    end <= duration &&
    Math.round(end * 16000) > Math.round(start * 16000);

  const zoomBy = useCallback(
    (factor: number, ratio = 0.5) => {
      const wave = player.current;
      if (!wave || !wave.getDecodedData() || duration <= 0) return;
      const width = wave.getWidth();
      const fit = width / duration;
      const previous = Math.max(fit, zoom.current);
      const anchor = (wave.getScroll() + ratio * width) / previous;
      const next = Math.min(
        width / Math.min(0.25, duration),
        Math.max(fit, previous * factor),
      );
      zoom.current = next;
      wave.zoom(next);
      wave.setScrollTime(Math.max(0, anchor - (ratio * width) / next));
    },
    [duration],
  );

  useEffect(() => {
    if (!container.current || !media.current) return;
    const host = container.current;
    const regionPlugin = RegionsPlugin.create();
    const wave = WaveSurfer.create({
      container: host,
      media: media.current,
      height: 160,
      waveColor: "#86a595",
      progressColor: "#2f6a50",
      cursorColor: "#d36135",
      cursorWidth: 2,
      autoCenter: false,
      url: recording.media_url,
      fetchParams: { headers: authHeaders() },
      plugins: [
        regionPlugin,
        TimelinePlugin.create({
          height: 26,
          formatTimeCallback: (time) =>
            formatClipTime(time).replace(/\.000$/, ""),
          style: { color: "#65716b", fontSize: "11px" },
        }),
      ],
    });
    player.current = wave;
    wave.setVolume(0.8);
    const syncRegion = (region: Region) => {
      if (updatingRegion.current) return;
      const nextStart = Math.max(0, Math.min(duration, region.start));
      const nextEnd = Math.max(nextStart, Math.min(duration, region.end));
      range.current = { start: nextStart, end: nextEnd };
      setStartText(formatClipTime(nextStart));
      setEndText(formatClipTime(nextEnd));
      preview.current = null;
      wave.pause();
      setError("");
    };
    regionPlugin.on("region-created", (region) => {
      selection.current?.remove();
      selection.current = region;
      syncRegion(region);
    });
    regionPlugin.on("region-update", syncRegion);
    regionPlugin.on("region-updated", syncRegion);
    wave.on("ready", () => {
      regionPlugin.addRegion({
        id: "selection",
        start: 0,
        end: duration,
        color: "rgba(47,106,80,0.14)",
        drag: false,
        resize: true,
        minLength: Math.min(0.01, duration),
      });
      setReady(true);
    });
    const disableDragSelection = regionPlugin.enableDragSelection(
      {
        id: "selection",
        color: "rgba(47,106,80,0.14)",
        drag: false,
        resize: true,
        minLength: Math.min(0.01, duration),
      },
      4,
    );
    wave.on("play", () => {
      if (activeClipPlayer !== wave) activeClipPlayer?.pause();
      activeClipPlayer = wave;
      setPlaying(true);
    });
    wave.on("pause", () => {
      setPlaying(false);
    });
    const completePreview = () => {
      const bounds = preview.current;
      if (!bounds || settling.current) return;
      settling.current = true;
      if (looping.current && activeClipPlayer === wave) {
        wave.setTime(bounds.start);
        void wave.play().catch(() => {
          if (player.current === wave) {
            preview.current = null;
            setError("无法播放，请重新点击试听");
          }
        });
      } else {
        preview.current = null;
        wave.pause();
        wave.setTime(bounds.end);
      }
      settling.current = false;
    };
    wave.on("timeupdate", (time) => {
      setCurrentTime(Math.min(duration, time));
      if (preview.current && wave.isPlaying() && time >= preview.current.end)
        completePreview();
    });
    wave.on("finish", () => {
      if (preview.current) completePreview();
      else setPlaying(false);
    });
    wave.on("interaction", () => {
      preview.current = null;
    });
    wave.on("error", () => {
      preview.current = null;
      wave.pause();
      setReady(false);
      setLoadingError("音频加载失败，可使用时间输入选择范围并转写。");
    });
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const rect = host.getBoundingClientRect();
      const ratio = Math.max(
        0,
        Math.min(1, (event.clientX - rect.left) / rect.width),
      );
      zoomBy(event.deltaY < 0 ? 1.4 : 1 / 1.4, ratio);
    };
    host.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      host.removeEventListener("wheel", onWheel);
      disableDragSelection();
      preview.current = null;
      if (activeClipPlayer === wave) activeClipPlayer = null;
      player.current = null;
      selection.current = null;
      wave.pause();
      wave.destroy();
    };
  }, [recording.media_url, duration, zoomBy]);

  function updateRange(nextStart: number, nextEnd: number, normalize = true) {
    preview.current = null;
    player.current?.pause();
    range.current = { start: nextStart, end: nextEnd };
    if (normalize) {
      setStartText(formatClipTime(nextStart));
      setEndText(formatClipTime(nextEnd));
    }
    updatingRegion.current = true;
    selection.current?.setOptions({ start: nextStart, end: nextEnd });
    updatingRegion.current = false;
    setError("");
  }
  function editTime(side: "start" | "end", text: string) {
    if (side === "start") setStartText(text);
    else setEndText(text);
    preview.current = null;
    player.current?.pause();
    const nextStart = parseClipTime(side === "start" ? text : startText);
    const nextEndText = side === "end" ? text : endText;
    const nextEnd =
      nextEndText === formatClipTime(duration)
        ? duration
        : parseClipTime(nextEndText);
    if (
      nextStart !== null &&
      nextEnd !== null &&
      nextStart >= 0 &&
      nextEnd <= duration &&
      Math.round(nextStart * 16000) < Math.round(nextEnd * 16000)
    ) {
      updateRange(nextStart, nextEnd, false);
    }
  }
  function normalizeTime() {
    if (valid) updateRange(start, end);
  }
  function mark(side: "start" | "end") {
    const time = Math.min(
      duration,
      Math.max(0, player.current?.getCurrentTime() ?? currentTime),
    );
    const nextStart = side === "start" ? time : range.current.start;
    const nextEnd = side === "end" ? time : range.current.end;
    if (Math.round(nextStart * 16000) >= Math.round(nextEnd * 16000)) {
      setError(
        side === "start"
          ? "A 起点必须早于 B 终点。"
          : "B 终点必须晚于 A 起点。",
      );
      return;
    }
    updateRange(nextStart, nextEnd);
  }
  function playAudio(fromSelection = false) {
    const wave = player.current;
    if (!wave || !ready) return;
    if (fromSelection) {
      if (!valid) return;
      preview.current = { start, end };
      wave.setTime(start);
    } else if (preview.current) {
      const bounds = preview.current;
      if (
        wave.getCurrentTime() < bounds.start ||
        wave.getCurrentTime() >= bounds.end
      )
        wave.setTime(bounds.start);
    } else if (wave.getCurrentTime() >= duration) wave.setTime(0);
    if (activeClipPlayer !== wave) activeClipPlayer?.pause();
    activeClipPlayer = wave;
    setError("");
    void wave.play().catch(() => {
      if (player.current === wave) {
        preview.current = null;
        setError("无法播放，请重新点击试听");
      }
    });
  }
  function togglePlayback() {
    if (player.current?.isPlaying()) player.current.pause();
    else playAudio();
  }
  function fitSelection() {
    const wave = player.current;
    if (!wave || !ready || !valid) return;
    const span = Math.min(duration, Math.max(0.5, (end - start) * 1.15));
    const left = Math.max(
      0,
      Math.min(duration - span, (start + end - span) / 2),
    );
    zoom.current = wave.getWidth() / span;
    wave.zoom(zoom.current);
    wave.setScrollTime(left);
  }
  function fitAll() {
    const wave = player.current;
    if (!wave || !ready) return;
    zoom.current = 0;
    wave.zoom(0);
    wave.setScrollTime(0);
  }
  function submit(whole: boolean) {
    preview.current = null;
    player.current?.pause();
    if (whole) onSubmit();
    else if (valid)
      onSubmit({
        start_sample: Math.round(start * 16000),
        end_sample: Math.round(end * 16000),
      });
  }

  return (
    <div
      className="clip-editor"
      aria-label="音频范围编辑器"
      role="group"
      tabIndex={0}
      ref={editor}
      aria-keyshortcuts="Space I O ArrowLeft ArrowRight"
      onPointerDownCapture={(event) => {
        if (
          event.target instanceof HTMLElement &&
          !event.target.closest(
            "input, select, textarea, button, [contenteditable=true]",
          )
        ) {
          editor.current?.focus({ preventScroll: true });
        }
      }}
      onKeyDown={(event) => {
        if (
          event.ctrlKey ||
          event.metaKey ||
          event.altKey ||
          !ready ||
          (event.target instanceof HTMLElement &&
            event.target.closest(
              "input, select, textarea, [contenteditable=true]",
            ))
        )
          return;
        const key = event.key.toLowerCase();
        if (key === " " && !(event.target instanceof HTMLButtonElement)) {
          event.preventDefault();
          togglePlayback();
        } else if (key === "i" || key === "o") {
          event.preventDefault();
          mark(key === "i" ? "start" : "end");
        } else if (key === "arrowleft" || key === "arrowright") {
          event.preventDefault();
          preview.current = null;
          const wave = player.current;
          if (wave)
            wave.setTime(
              Math.max(
                0,
                Math.min(
                  duration,
                  wave.getCurrentTime() + (key === "arrowleft" ? -5 : 5),
                ),
              ),
            );
        }
      }}
    >
      <div className="clip-editor-heading">
        <div>
          <h3>试听与选段</h3>
          <span className="muted">总时长 {formatClipTime(duration)}</span>
        </div>
        <div className="clip-zoom-controls" aria-label="波形缩放">
          <button
            type="button"
            disabled={!ready}
            aria-label="缩小波形"
            onClick={() => {
              zoomBy(1 / 1.6);
            }}
          >
            −
          </button>
          <button
            type="button"
            disabled={!ready}
            aria-label="放大波形"
            onClick={() => {
              zoomBy(1.6);
            }}
          >
            ＋
          </button>
          <button
            type="button"
            disabled={!ready || !valid}
            onClick={fitSelection}
          >
            放大选段
          </button>
          <button type="button" disabled={!ready} onClick={fitAll}>
            全貌
          </button>
        </div>
      </div>
      <div className="clip-waveform" ref={container} aria-label="音频波形" />
      <audio ref={media} hidden preload="auto" />
      {!ready && !loadingError && (
        <p className="clip-loading" role="status">
          正在加载音频波形…
        </p>
      )}
      <p className="clip-hint">
        单击定位 · 拖动选段 · 拖动 A / B 调整边界 · Ctrl + 滚轮缩放
      </p>
      <div className="clip-transport">
        <button
          type="button"
          className="clip-play"
          disabled={!ready}
          onClick={togglePlayback}
        >
          {playing ? "暂停" : "播放"}
        </button>
        <button
          type="button"
          disabled={!ready || !valid}
          onClick={() => {
            playAudio(true);
          }}
        >
          试听选段
        </button>
        <label className="clip-loop">
          <input
            type="checkbox"
            checked={loop}
            onChange={(event) => {
              looping.current = event.target.checked;
              setLoop(event.target.checked);
            }}
          />
          循环
        </label>
        <output className="clip-clock" aria-label="当前播放时间">
          {formatClipTime(currentTime)}
        </output>
        <label className="clip-speed">
          倍速
          <select
            aria-label="试听倍速"
            value={speed}
            onChange={(event) => {
              const value = Number(event.target.value);
              setSpeed(value);
              player.current?.setPlaybackRate(value, true);
            }}
          >
            {[0.5, 0.75, 1, 1.25, 1.5, 2].map((value) => (
              <option key={value} value={value}>
                {value.toFixed(value === 1 || value === 2 ? 1 : 2)}×
              </option>
            ))}
          </select>
        </label>
        <label className="clip-volume">
          音量
          <input
            aria-label="试听音量"
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={volume}
            onChange={(event) => {
              const value = Number(event.target.value);
              setVolume(value);
              player.current?.setVolume(value);
            }}
          />
        </label>
      </div>
      <div className="clip-boundaries">
        {(["start", "end"] as const).map((side) => (
          <div className="clip-boundary" key={side}>
            <label>
              <span className="clip-boundary-letter">
                {side === "start" ? "A" : "B"}
              </span>
              <input
                aria-label={side === "start" ? "片段开始时间" : "片段结束时间"}
                aria-invalid={!valid}
                title="支持 时:分:秒.毫秒、分:秒.毫秒 或秒数；回车应用"
                type="text"
                spellCheck={false}
                value={side === "start" ? startText : endText}
                onChange={(event) => {
                  editTime(side, event.target.value);
                }}
                onBlur={normalizeTime}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    normalizeTime();
                  }
                }}
              />
            </label>
            <button
              type="button"
              disabled={!ready}
              aria-label={
                side === "start"
                  ? "将当前播放位置设为 A 起点"
                  : "将当前播放位置设为 B 终点"
              }
              onClick={() => {
                mark(side);
              }}
            >
              设为当前
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => {
            updateRange(0, duration);
          }}
        >
          全选
        </button>
      </div>
      {!valid && (
        <p className="error-callout" role="alert">
          请输入录音范围内有效的 A / B 时间，起点必须早于终点。支持 00:12:34.500
          或秒数。
        </p>
      )}
      {(loadingError || error) && (
        <p className="error-callout" role="alert">
          {loadingError || error}
        </p>
      )}
      <div className="clip-footer">
        <div>
          <p className="clip-selection-summary">
            {valid
              ? `选段 ${formatClipTime(start)} → ${formatClipTime(end)} · 时长 ${formatClipTime(end - start)}`
              : "请检查选段时间"}
          </p>
          <p className="clip-hint">片段从 0 秒计时，原文件保留。</p>
        </div>
        <div className="clip-submit-controls">
          <button
            type="button"
            disabled={!valid}
            className="clip-submit"
            onClick={() => {
              submit(false);
            }}
          >
            转写选段
          </button>
          <button
            type="button"
            onClick={() => {
              submit(true);
            }}
          >
            转写整段
          </button>
        </div>
      </div>
      <p className="clip-shortcuts">
        快捷键：空格 播放 / 暂停 · I 起点 · O 终点 · ← / → 移动 5 秒
      </p>
    </div>
  );
}
