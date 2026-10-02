import { useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin from "wavesurfer.js/dist/plugins/regions.esm.js";
import { authHeaders, type Recording } from "../api";

export function AudioClipEditor({
  recording,
  onSubmit,
}: {
  recording: Recording;
  onSubmit: (clip?: { start_sample: number; end_sample: number }) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const player = useRef<WaveSurfer | null>(null);
  const regions = useRef<RegionsPlugin | null>(null);
  const duration = recording.duration_samples / 16000;
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(duration);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const boundary = useRef<number | null>(null);
  useEffect(() => {
    if (!container.current) return;
    const regionPlugin = RegionsPlugin.create();
    regions.current = regionPlugin;
    const wave = WaveSurfer.create({
      container: container.current,
      height: 80,
      waveColor: "#73918f",
      progressColor: "#176b62",
      url: recording.media_url,
      fetchParams: { headers: authHeaders() },
      plugins: [regionPlugin],
    });
    player.current = wave;
    wave.on("ready", () => {
      regionPlugin.addRegion({
        id: "selection",
        start: 0,
        end: duration,
        color: "rgba(30,140,120,0.2)",
        drag: true,
        resize: true,
      });
      setReady(true);
    });
    regionPlugin.on("region-updated", (region) => {
      setStart(region.start);
      setEnd(region.end);
      boundary.current = null;
      wave.pause();
    });
    wave.on("timeupdate", (time) => {
      if (boundary.current !== null && time >= boundary.current) {
        wave.pause();
        boundary.current = null;
      }
    });
    wave.on("error", () => {
      setError("音频加载失败，可使用时间输入选段");
    });
    return () => {
      player.current = null;
      regions.current = null;
      wave.destroy();
    };
  }, [recording.media_url, duration]);
  const valid =
    Number.isFinite(start) &&
    Number.isFinite(end) &&
    start >= 0 &&
    end > start &&
    end <= duration;
  function changeRange(nextStart: number, nextEnd: number) {
    setStart(nextStart);
    setEnd(nextEnd);
    boundary.current = null;
    player.current?.pause();
    if (nextStart >= 0 && nextStart < nextEnd && nextEnd <= duration) {
      regions.current
        ?.getRegions()[0]
        ?.setOptions({ start: nextStart, end: nextEnd });
    }
  }
  return (
    <div className="panel" aria-label="音频范围编辑器">
      <div ref={container} />
      <label>
        开始（秒）
        <input
          aria-label="片段开始秒"
          type="number"
          step="0.001"
          min="0"
          max={duration}
          value={start}
          onChange={(event) => {
            changeRange(Number(event.target.value), end);
          }}
        />
      </label>
      <label>
        结束（秒）
        <input
          aria-label="片段结束秒"
          type="number"
          step="0.001"
          min="0"
          max={duration}
          value={end}
          onChange={(event) => {
            changeRange(start, Number(event.target.value));
          }}
        />
      </label>
      <p>仅转写所选范围；片段从 0 秒计时，原文件保留。</p>
      {!valid && <p role="alert">请选择录音范围内有效的开始和结束时间。</p>}
      {error && <p role="alert">{error}</p>}
      <button
        type="button"
        disabled={!valid || !ready}
        onClick={() => {
          boundary.current = end;
          player.current?.setTime(start);
          void player.current?.play().catch(() => {
            setError("无法播放，请重新点击试听");
          });
        }}
      >
        试听选段
      </button>
      <button
        type="button"
        onClick={() => {
          boundary.current = null;
          player.current?.pause();
        }}
      >
        停止试听
      </button>
      <button
        type="button"
        onClick={() => {
          changeRange(0, duration);
        }}
      >
        重设范围
      </button>
      <button
        type="button"
        disabled={!valid}
        onClick={() => {
          player.current?.pause();
          onSubmit({
            start_sample: Math.round(start * 16000),
            end_sample: Math.round(end * 16000),
          });
        }}
      >
        剪辑并转写
      </button>
      <button
        type="button"
        onClick={() => {
          player.current?.pause();
          onSubmit();
        }}
      >
        取消剪辑，转写整段
      </button>
    </div>
  );
}
