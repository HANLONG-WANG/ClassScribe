import { useMutation, useQuery } from "@tanstack/react-query";
import { type ChangeEvent, type SyntheticEvent, useState } from "react";

import {
  api,
  type Glossary,
  type Job,
  type ModelInfo,
  uploadRecording,
} from "../api";
import { useWorkbench } from "../store";

type Language = "zh" | "ja" | "en" | "auto_mixed";
type Accuracy = "fast" | "balanced" | "highest" | "strict_single";

async function inspectMedia(file: File) {
  const context = new AudioContext();
  try {
    const buffer = await context.decodeAudioData(await file.arrayBuffer());
    return {
      durationSamples: Math.round(buffer.duration * 16000),
      channels: buffer.numberOfChannels,
      sampleRate: buffer.sampleRate,
    };
  } finally {
    void context.close();
  }
}

export function UploadPage() {
  const setCurrentJob = useWorkbench((state) => state.setCurrentJob);
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState<Language>("auto_mixed");
  const [accuracy, setAccuracy] = useState<Accuracy>("balanced");
  const [speakerCount, setSpeakerCount] = useState("auto");
  const [glossaryId, setGlossaryId] = useState("");
  const [primaryModel, setPrimaryModel] = useState("");
  const [outputs, setOutputs] = useState(["json", "md", "srt", "vtt"]);
  const [includeFaithful, setIncludeFaithful] = useState(true);
  const [includeSmart, setIncludeSmart] = useState(true);
  const [includeSpeakers, setIncludeSpeakers] = useState(true);

  const glossaries = useQuery({
    queryKey: ["glossaries"],
    queryFn: () => api<Glossary[]>("/glossaries"),
  });
  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelInfo[]>("/models"),
  });
  const create = useMutation({
    mutationFn: async () => {
      if (file === null) throw new Error("请选择音频或视频文件");
      const metadata = await inspectMedia(file);
      const recording = await uploadRecording(file, metadata);
      return api<Job>("/jobs", {
        method: "POST",
        body: JSON.stringify({
          recording_id: recording.id,
          language,
          glossary_id: glossaryId || null,
          speaker_count: speakerCount,
          model_selection: primaryModel ? "manual_primary" : "auto_best",
          primary_model_id: primaryModel || null,
          accuracy_mode: accuracy,
          outputs,
          include_faithful: includeFaithful,
          include_smart: includeSmart,
          include_speakers: includeSpeakers,
          include_subtitles: outputs.includes("srt") || outputs.includes("vtt"),
        }),
      });
    },
    onSuccess: (job) => {
      setCurrentJob(job.job_id);
    },
  });

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.item(0) ?? null);
  }

  function toggleOutput(format: string) {
    setOutputs((current) =>
      current.includes(format)
        ? current.filter((item) => item !== format)
        : [...current, format],
    );
  }

  function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!create.isPending) create.mutate();
  }

  return (
    <section className="page-stack" aria-labelledby="upload-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">Classroom workspace</p>
          <h1 id="upload-title">导入一堂课</h1>
          <p>文件留在本机。任务会自动完成到导出，不要求先进入校对页。</p>
        </div>
        <span className="privacy-badge">离线推理</span>
      </header>

      <form className="panel upload-form" onSubmit={submit}>
        <label className={`drop-zone ${file ? "has-file" : ""}`}>
          <input accept="audio/*,video/*" onChange={chooseFile} type="file" />
          <span className="drop-icon">＋</span>
          <strong>{file?.name ?? "拖放音频或视频，或点此选择"}</strong>
          <small>
            {file
              ? `${(file.size / 1_048_576).toFixed(1)} MB`
              : "最长约 90 分钟"}
          </small>
        </label>

        <div className="form-grid">
          <label>
            <span>语言</span>
            <select
              value={language}
              onChange={(event) => {
                setLanguage(event.target.value as Language);
              }}
            >
              <option value="auto_mixed">自动 / 混合</option>
              <option value="zh">中文</option>
              <option value="ja">日本語</option>
              <option value="en">English</option>
            </select>
          </label>
          <label>
            <span>课程词典</span>
            <select
              value={glossaryId}
              onChange={(event) => {
                setGlossaryId(event.target.value);
              }}
            >
              <option value="">不使用</option>
              {(glossaries.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>说话人数</span>
            <select
              value={speakerCount}
              onChange={(event) => {
                setSpeakerCount(event.target.value);
              }}
            >
              <option value="auto">自动</option>
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="3-4">3–4</option>
              <option value="5+">5+</option>
            </select>
          </label>
          <label>
            <span>主模型</span>
            <select
              value={primaryModel}
              onChange={(event) => {
                setPrimaryModel(event.target.value);
              }}
            >
              <option value="">自动最佳</option>
              {(models.data ?? [])
                .filter((item) => item.enabled)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.revision.slice(0, 7)}
                  </option>
                ))}
            </select>
          </label>
        </div>

        <fieldset>
          <legend>处理模式</legend>
          <div className="choice-row">
            {(["fast", "balanced", "highest", "strict_single"] as const).map(
              (mode) => (
                <label className="choice-card" key={mode}>
                  <input
                    checked={accuracy === mode}
                    name="accuracy"
                    onChange={() => {
                      setAccuracy(mode);
                    }}
                    type="radio"
                  />
                  <span>
                    {
                      {
                        fast: "快速",
                        balanced: "平衡",
                        highest: "最高精度",
                        strict_single: "严格单模型",
                      }[mode]
                    }
                  </span>
                </label>
              ),
            )}
          </div>
        </fieldset>

        <fieldset>
          <legend>自动输出</legend>
          <div className="choice-row compact">
            {["txt", "md", "json", "srt", "vtt", "csv"].map((format) => (
              <label key={format}>
                <input
                  checked={outputs.includes(format)}
                  onChange={() => {
                    toggleOutput(format);
                  }}
                  type="checkbox"
                />{" "}
                {format.toUpperCase()}
              </label>
            ))}
          </div>
          <div className="choice-row compact">
            <label>
              <input
                checked={includeFaithful}
                onChange={(event) => {
                  setIncludeFaithful(event.target.checked);
                }}
                type="checkbox"
              />{" "}
              忠实版
            </label>
            <label>
              <input
                checked={includeSmart}
                onChange={(event) => {
                  setIncludeSmart(event.target.checked);
                }}
                type="checkbox"
              />{" "}
              智能纠正版
            </label>
            <label>
              <input
                checked={includeSpeakers}
                onChange={(event) => {
                  setIncludeSpeakers(event.target.checked);
                }}
                type="checkbox"
              />{" "}
              说话人
            </label>
          </div>
        </fieldset>

        {accuracy === "strict_single" && !primaryModel && (
          <p className="error-callout">严格单模型测试需要指定主模型。</p>
        )}
        {create.error && (
          <p className="error-callout">{create.error.message}</p>
        )}
        <button
          className="primary-button"
          disabled={
            !file ||
            outputs.length === 0 ||
            create.isPending ||
            (accuracy === "strict_single" && !primaryModel)
          }
          type="submit"
        >
          {create.isPending ? "正在安全导入…" : "开始自动转录"}
        </button>
      </form>
    </section>
  );
}
