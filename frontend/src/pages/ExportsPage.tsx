import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, authHeaders, formatSamples, type ExportArtifact } from "../api";
import { type Manuscript, statuses } from "../manuscripts";

export function ExportsPage() {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Map<string, Manuscript>>(new Map());
  const [failures, setFailures] = useState<string[]>([]);
  const [downloadError, setDownloadError] = useState("");
  const [progress, setProgress] = useState({ completed: 0, total: 0 });
  const query = useQuery({
    queryKey: ["manuscripts", offset],
    queryFn: () =>
      api<{ items: Manuscript[]; total: number }>(
        `/jobs?limit=30&offset=${String(offset)}`,
      ),
  });
  function toggle(item: Manuscript, checked: boolean) {
    setSelected((current) => {
      const next = new Map(current);
      if (checked) next.set(item.job_id, item);
      else next.delete(item.job_id);
      return next;
    });
  }
  const [format, setFormat] = useState("md");
  const [layer, setLayer] = useState("smart");
  const [view, setView] = useState("sentences");
  const [artifacts, setArtifacts] = useState<
    (ExportArtifact & { source_name: string })[]
  >([]);
  const create = useMutation({
    mutationFn: async (manuscripts: Manuscript[]) => {
      setFailures([]);
      setProgress({ completed: 0, total: manuscripts.length });
      for (const [index, manuscript] of manuscripts.entries()) {
        try {
          const artifact = await api<ExportArtifact>(
            `/jobs/${manuscript.job_id}/exports`,
            {
              method: "POST",
              body: JSON.stringify({
                output_format: format,
                layer,
                view,
                traditional_chinese: false,
              }),
            },
          );
          setArtifacts((current) => [
            { ...artifact, source_name: manuscript.source_name },
            ...current,
          ]);
        } catch (error) {
          setFailures((current) => [
            ...current,
            `${manuscript.source_name}：${error instanceof Error ? error.message : "导出失败"}`,
          ]);
        }
        setProgress({ completed: index + 1, total: manuscripts.length });
      }
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
          <p className="eyebrow">Manuscript export</p>
          <h1 id="exports-title">导出</h1>
          <p>选择稿件和导出格式，每份稿件生成一个独立文件。</p>
        </div>
      </header>
      <div className="panel export-builder">
        <div className="form-grid">
          <label>
            <span>格式</span>
            <select
              disabled={create.isPending}
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
              disabled={create.isPending}
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
              disabled={create.isPending}
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
          disabled={create.isPending || selected.size === 0}
          onClick={() => {
            create.mutate([...selected.values()]);
          }}
          type="button"
        >
          {create.isPending
            ? `正在导出 ${String(progress.completed)}/${String(progress.total)}…`
            : `生成导出（${String(selected.size)} 份）`}
        </button>
        {create.error && (
          <p className="error-callout">{create.error.message}</p>
        )}
      </div>
      <div className="page-stack" aria-label="选择导出稿件">
        <div className="toolbar export-selection-toolbar">
          <strong>历史转录稿 · 已选 {selected.size} 份</strong>
          <button
            type="button"
            disabled={
              create.isPending ||
              !query.data?.items.some((item) => item.has_transcript !== false)
            }
            onClick={() => {
              for (const item of query.data?.items ?? [])
                if (item.has_transcript !== false) toggle(item, true);
            }}
          >
            全选本页
          </button>
          <button
            type="button"
            disabled={create.isPending || selected.size === 0}
            onClick={() => {
              setSelected(new Map());
            }}
          >
            清空选择
          </button>
        </div>
        {query.isPending && <p role="status">正在加载历史稿件…</p>}
        {query.isError && (
          <div className="error-callout" role="alert">
            {query.error.message}
            <button
              type="button"
              onClick={() => {
                void query.refetch();
              }}
            >
              重新加载
            </button>
          </div>
        )}
        {query.data && (
          <>
            <p className="muted">
              共 {query.data.total} 份稿件 · 选择会跨页保留
            </p>
            {query.data.items.length === 0 && (
              <p className="empty-state">暂无稿件，请先导入音频。</p>
            )}
            <div className="manuscript-list">
              {query.data.items.map((item) => (
                <label
                  className={`panel manuscript-card export-manuscript ${selected.has(item.job_id) ? "selected" : ""}`}
                  key={item.job_id}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(item.job_id)}
                    disabled={create.isPending || item.has_transcript === false}
                    onChange={(event) => {
                      toggle(item, event.target.checked);
                    }}
                  />
                  <div>
                    <strong>{item.source_name}</strong>
                    <span>
                      {new Date(item.created_at).toLocaleString()} ·{" "}
                      {formatSamples(item.duration_samples)} ·{" "}
                      {(
                        {
                          ja: "日语",
                          zh: "中文",
                          en: "英语",
                          auto_mixed: "自动 / 混合",
                        } as Record<string, string>
                      )[item.language] ?? item.language}
                    </span>
                    <span>
                      {statuses[item.status] ?? item.status}
                      {item.has_transcript === false
                        ? " · 暂无可导出的正文"
                        : ""}
                    </span>
                  </div>
                </label>
              ))}
            </div>
            <div className="toolbar">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => {
                  setOffset(Math.max(0, offset - 30));
                }}
              >
                上一页
              </button>
              <span>第 {Math.floor(offset / 30) + 1} 页</span>
              <button
                type="button"
                disabled={offset + 30 >= query.data.total}
                onClick={() => {
                  setOffset(offset + 30);
                }}
              >
                下一页
              </button>
            </div>
          </>
        )}
      </div>
      {progress.total > 0 && !create.isPending && (
        <p role="status">
          导出完成：成功 {progress.total - failures.length} 份，失败{" "}
          {failures.length} 份。
        </p>
      )}
      {failures.map((failure, index) => (
        <p className="error-callout" role="alert" key={index}>
          {failure}
        </p>
      ))}
      {downloadError && (
        <p className="error-callout" role="alert">
          {downloadError}
        </p>
      )}
      <div className="artifact-list">
        {artifacts.map((artifact) => (
          <article className="panel" key={artifact.id}>
            <div>
              <strong>{artifact.source_name}</strong>
              <small>{artifact.file_name}</small>
              <small>
                {artifact.format.toUpperCase()} · {artifact.layer} ·{" "}
                {artifact.size_bytes} bytes
              </small>
            </div>
            <span className="mono">SHA {artifact.sha256.slice(0, 12)}</span>
            <button
              onClick={() => {
                setDownloadError("");
                void download(artifact).catch((error: unknown) => {
                  setDownloadError(
                    error instanceof Error ? error.message : "下载失败",
                  );
                });
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
