import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api, authHeaders, type ExportArtifact } from "../api";
import { useWorkbench } from "../store";

export function ExportsPage() {
  const jobId = useWorkbench((state) => state.currentJobId);
  const [format, setFormat] = useState("md");
  const [layer, setLayer] = useState("smart");
  const [view, setView] = useState("sentences");
  const [artifacts, setArtifacts] = useState<ExportArtifact[]>([]);
  const create = useMutation({
    mutationFn: () =>
      api<ExportArtifact>(`/jobs/${String(jobId)}/exports`, {
        method: "POST",
        body: JSON.stringify({
          output_format: format,
          layer,
          view,
          traditional_chinese: false,
        }),
      }),
    onSuccess: (artifact) => {
      setArtifacts((current) => [artifact, ...current]);
    },
  });

  async function download(artifact: ExportArtifact) {
    const response = await fetch(artifact.download_url, {
      headers: authHeaders(),
    });
    if (!response.ok) throw new Error("导出下载失败");
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(await response.blob());
    anchor.download = artifact.file_name;
    anchor.click();
    URL.revokeObjectURL(anchor.href);
  }

  return (
    <section className="page-stack" aria-labelledby="exports-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">Atomic local artifacts</p>
          <h1 id="exports-title">导出</h1>
          <p>所有文件按 ID 获取并验证哈希；不会暴露本机路径。</p>
        </div>
      </header>
      {jobId === null ? (
        <p className="empty-state">请先创建课堂任务。</p>
      ) : (
        <div className="panel export-builder">
          <div className="form-grid">
            <label>
              <span>格式</span>
              <select
                value={format}
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
                onChange={(event) => {
                  setView(event.target.value);
                }}
              >
                <option value="sentences">逐句</option>
                <option value="readable_paragraphs">段落</option>
              </select>
            </label>
          </div>
          <button
            className="primary-button"
            disabled={create.isPending}
            onClick={() => {
              create.mutate();
            }}
            type="button"
          >
            {create.isPending ? "正在原子写入…" : "生成导出"}
          </button>
          {create.error && (
            <p className="error-callout">{create.error.message}</p>
          )}
        </div>
      )}
      <div className="artifact-list">
        {artifacts.map((artifact) => (
          <article className="panel" key={artifact.id}>
            <div>
              <strong>{artifact.file_name}</strong>
              <small>
                {artifact.format.toUpperCase()} · {artifact.layer} ·{" "}
                {artifact.size_bytes} bytes
              </small>
            </div>
            <span className="mono">SHA {artifact.sha256.slice(0, 12)}</span>
            <button
              onClick={() => {
                void download(artifact);
              }}
              type="button"
            >
              下载
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}
