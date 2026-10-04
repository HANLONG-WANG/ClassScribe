import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, downloadFile, type ExportArtifact } from "../api";
import { type TextLayer } from "../store";
import { useTranscriptSaves } from "../transcriptSaves";

export function TranscriptExportDialog({
  jobId,
  sourceName,
  initialLayer,
  initialView,
  onClose,
}: {
  jobId: string;
  sourceName: string;
  initialLayer: Exclude<TextLayer, "raw">;
  initialView: "sentences" | "readable_paragraphs";
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const [format, setFormat] = useState("md");
  const [layer, setLayer] = useState<string>(initialLayer);
  const [view, setView] = useState<string>(initialView);
  const unsaved = useTranscriptSaves((state) =>
    Object.values(state.drafts).some(
      (draft) => draft.jobId === jobId && draft.status !== "saved",
    ),
  );

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) {
      dialog.showModal();
      dialog.querySelector("select")?.focus();
    }
    return () => {
      controllerRef.current?.abort();
    };
  }, []);

  const create = useMutation({
    mutationFn: async () => {
      const controller = new AbortController();
      controllerRef.current = controller;
      return api<ExportArtifact>(`/jobs/${jobId}/exports`, {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          output_format: format,
          layer,
          view,
          traditional_chinese: false,
        }),
      });
    },
  });
  const download = useMutation({
    mutationFn: async (artifact: ExportArtifact) => {
      const controller = new AbortController();
      controllerRef.current = controller;
      await downloadFile(
        artifact.download_url,
        artifact.file_name,
        controller.signal,
      );
    },
  });
  const busy = create.isPending || download.isPending;

  return (
    <dialog
      className="panel transcript-export-dialog"
      ref={dialogRef}
      aria-labelledby="transcript-export-title"
      aria-describedby="transcript-export-description"
      onClose={onClose}
    >
      <form
        className="page-stack"
        onSubmit={(event) => {
          event.preventDefault();
          if (busy || (layer === "user" && unsaved)) return;
          download.reset();
          create.mutate();
        }}
      >
        <div className="dialog-heading">
          <div>
            <p className="eyebrow">Manuscript export</p>
            <h2 id="transcript-export-title">导出转录稿</h2>
          </div>
          <button
            type="button"
            aria-label="关闭导出设置"
            onClick={() => {
              dialogRef.current?.close();
            }}
          >
            ✕
          </button>
        </div>
        <p className="muted" id="transcript-export-description">
          {sourceName} · 导出完整转录稿
        </p>
        <div className="form-grid">
          <label>
            <span>格式</span>
            <select
              value={format}
              disabled={busy}
              onChange={(event) => {
                setFormat(event.target.value);
              }}
            >
              {["txt", "md", "json", "srt", "vtt", "csv"].map((item) => (
                <option key={item} value={item}>
                  {item.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>文本版本</span>
            <select
              value={layer}
              disabled={busy}
              onChange={(event) => {
                setLayer(event.target.value);
              }}
            >
              <option value="faithful">忠实版</option>
              <option value="smart">智能纠正版</option>
              <option value="user">用户版</option>
            </select>
          </label>
          <label>
            <span>组织方式</span>
            <select
              value={view}
              disabled={busy}
              onChange={(event) => {
                setView(event.target.value);
              }}
            >
              <option value="sentences">逐句</option>
              <option value="readable_paragraphs">段落</option>
            </select>
          </label>
        </div>
        {layer === "user" && unsaved && (
          <p className="error-callout" role="status">
            用户文本尚未保存，请先完成保存后再导出。
          </p>
        )}
        {create.error && (
          <p className="error-callout" role="alert">
            {create.error.message}
          </p>
        )}
        {download.error && (
          <p className="error-callout" role="alert">
            {download.error.message}
          </p>
        )}
        {create.data && (
          <div className="export-dialog-result" role="status">
            <div>
              <strong>导出已生成</strong>
              <small>{create.data.file_name}</small>
            </div>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                download.mutate(create.data);
              }}
            >
              {download.isPending ? "正在下载…" : "下载"}
            </button>
          </div>
        )}
        <div className="toolbar dialog-actions">
          <button
            type="button"
            onClick={() => {
              dialogRef.current?.close();
            }}
          >
            关闭
          </button>
          <button
            className="primary-button"
            type="submit"
            disabled={busy || (layer === "user" && unsaved)}
          >
            {create.isPending ? "正在生成…" : "生成导出"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
