import { useQuery } from "@tanstack/react-query";
import { type ChangeEvent, type SyntheticEvent, useState } from "react";

import { api, type Glossary, type ModelInfo } from "../api";
import {
  addImports,
  useBatchImports,
  retryImport,
  stopImports,
} from "../batchImports";
import { useWorkbench } from "../store";
import { bodyModel } from "../modelGuidance";
import { ClassroomModelGuide } from "./ClassroomModelGuide";
import { ModelPicker } from "./ModelPicker";

type Language = "zh" | "ja" | "en" | "auto_mixed";
type Accuracy = "fast" | "balanced" | "highest" | "strict_single";

export function UploadPage() {
  const setPage = useWorkbench((state) => state.setPage);
  const [files, setFiles] = useState<File[]>([]);
  const imports = useBatchImports((state) => state.items);
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
  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setFiles((current) => [
      ...current,
      ...Array.from(event.target.files ?? []),
    ]);
    event.target.value = "";
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
    if (!files.length) return;
    addImports(files, {
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
    });
    setFiles([]);
  }

  return (
    <section className="page-stack" aria-labelledby="upload-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">Classroom workspace</p>
          <h1 id="upload-title">批量导入课堂</h1>
          <p>文件留在本机。任务会自动完成到导出，不要求先进入校对页。</p>
        </div>
        <span className="privacy-badge">离线推理</span>
      </header>

      <p>
        已入队的课堂在关闭浏览器后继续处理；尚未上传完成的文件需要保持此浏览器开启。
      </p>
      <button
        type="button"
        onClick={() => {
          setPage("queue");
        }}
      >
        查看转录队列
      </button>
      <form className="panel upload-form" onSubmit={submit}>
        <label
          className={`drop-zone ${files.length ? "has-file" : ""}`}
          onDragOver={(event) => {
            event.preventDefault();
          }}
          onDrop={(event) => {
            event.preventDefault();
            setFiles((current) => [
              ...current,
              ...Array.from(event.dataTransfer.files),
            ]);
          }}
        >
          <input
            accept="audio/*,video/*"
            onChange={chooseFile}
            type="file"
            multiple
          />
          <span className="drop-icon">＋</span>
          <strong>
            {files.length
              ? `已选择 ${String(files.length)} 个文件`
              : "拖放音频或视频，或点此多选"}
          </strong>
          <small>按列表顺序逐个上传；本批次共用下方设置。</small>
        </label>
        <ol className="batch-files">
          {files.map((file, index) => (
            <li key={`${String(index)}-${file.name}`}>
              <span>
                {file.name} · {(file.size / 1048576).toFixed(1)} MB
              </span>
              <button
                type="button"
                disabled={index === 0}
                onClick={() => {
                  setFiles((current) => {
                    const next = [...current];
                    const item = next[index];
                    const previous = next[index - 1];
                    if (item && previous) {
                      next[index - 1] = item;
                      next[index] = previous;
                    }
                    return next;
                  });
                }}
              >
                上移
              </button>
              <button
                type="button"
                onClick={() => {
                  setFiles((current) => current.filter((_, i) => i !== index));
                }}
              >
                移除
              </button>
            </li>
          ))}
        </ol>

        <div className="form-grid">
          <label>
            <span>语言</span>
            <select
              value={language}
              onChange={(event) => {
                const next = event.target.value as Language;
                setLanguage(next);
                const selected = models.data?.find(
                  (item) => item.id === primaryModel,
                );
                if (
                  selected &&
                  !(next === "auto_mixed" ? ["zh", "ja", "en"] : [next]).every(
                    (code) => bodyModel(selected, code),
                  )
                )
                  setPrimaryModel("");
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
          <ModelPicker
            value={primaryModel}
            onChange={setPrimaryModel}
            loading={models.isPending}
            error={models.error?.message}
            models={(models.data ?? []).filter((item) =>
              (language === "auto_mixed"
                ? ["zh", "ja", "en"]
                : [language]
              ).every((code) => bodyModel(item, code)),
            )}
          />
        </div>

        <fieldset>
          <legend>处理模式</legend>
          <div className="choice-row">
            {(["fast", "balanced", "highest", "strict_single"] as const).map(
              (mode) => (
                <label className="choice-card" key={mode}>
                  <input
                    aria-label={
                      {
                        fast: "快速",
                        balanced: "平衡",
                        highest: "最高精度",
                        strict_single: "严格单模型",
                      }[mode]
                    }
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
                  <small>
                    {mode === "strict_single"
                      ? "指定正文模型，不调用备用 ASR；辅助模型仍使用。"
                      : "按语言选择主模型，质量异常时调用备用模型复核。"}
                  </small>
                </label>
              ),
            )}
          </div>
        </fieldset>

        <ClassroomModelGuide
          models={models.data ?? []}
          language={language}
          primaryModel={primaryModel}
          strict={accuracy === "strict_single"}
        />

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
        <button
          className="primary-button"
          disabled={
            !files.length ||
            outputs.length === 0 ||
            (accuracy === "strict_single" && !primaryModel)
          }
          type="submit"
        >
          加入转录队列
        </button>
      </form>
      {imports.length > 0 && (
        <div className="panel page-stack">
          <h2>导入进度</h2>
          <button type="button" onClick={stopImports}>
            停止未完成的导入
          </button>
          {imports.map((item) => (
            <div key={item.id} className="batch-import-row">
              <strong>{item.name}</strong>
              <span>
                {
                  {
                    waiting: "待上传",
                    uploading: "上传 / 校验中",
                    submitting: "正在入队",
                    done: "已入队",
                    error: "导入失败",
                  }[item.status]
                }{" "}
                · {item.progress}%
              </span>
              {item.error && <p role="alert">{item.error}</p>}
              {item.status === "error" && (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      retryImport(item.id);
                    }}
                  >
                    重试此文件
                  </button>
                  <label>
                    重新选择原文件
                    <input
                      type="file"
                      accept="audio/*,video/*"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) retryImport(item.id, file);
                      }}
                    />
                  </label>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
