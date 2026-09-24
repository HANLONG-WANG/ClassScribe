import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, authHeaders, type ApiObject, setApiToken } from "../api";
import { useAuthStatus } from "../authStatus";
import { useBatchImports } from "../batchImports";
import { useTranscriptSaves } from "../transcriptSaves";

const deleteAllPhrase = "DELETE ALL CLASSSCRIBE DATA";

export function SettingsPage() {
  const client = useQueryClient();
  const authInvalid = useAuthStatus((state) => state.invalid);
  const [token, setToken] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [localCleared, setLocalCleared] = useState(false);
  const [retentionEdit, setRetentionDays] = useState<number | undefined>();
  const [audioEdit, setSaveDictationAudio] = useState<boolean | undefined>();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<Record<string, ApiObject>>("/settings"),
  });
  const settingsData = authInvalid || localCleared ? undefined : settings.data;
  const clearAll = useMutation({
    mutationFn: () =>
      api<{ restart_required: boolean }>("/local-data/clear", {
        method: "POST",
        body: JSON.stringify({ confirmation: deleteConfirmation }),
      }),
    onSuccess: () => {
      setLocalCleared(true);
      setDeleteConfirmation("");
      setApiToken("");
      useBatchImports.setState({ items: [] });
      useTranscriptSaves.setState({ drafts: {} });
      localStorage.removeItem("classscribe-batch-imports-v1");
      for (const key of [
        "classscribe-transcript-drafts-v1",
        "classscribe-current-job",
        "classscribe-current-page",
        "classscribe-upload-draft-v1",
        "classscribe-token",
      ])
        sessionStorage.removeItem(key);
    },
  });
  const retentionDays =
    retentionEdit ??
    (typeof settingsData?.retention?.derived_days === "number"
      ? settingsData.retention.derived_days
      : 30);
  const saveDictationAudio =
    audioEdit ?? settingsData?.ibus?.save_audio === true;
  const diagnostics = useQuery({
    queryKey: ["diagnostics"],
    queryFn: () => api<ApiObject>("/diagnostics"),
  });
  const save = useMutation({
    mutationFn: () =>
      api<Record<string, ApiObject>>("/settings", {
        method: "PUT",
        body: JSON.stringify({
          values: {
            ...(retentionEdit !== undefined
              ? {
                  retention: {
                    ...settingsData?.retention,
                    derived_days: retentionDays,
                  },
                }
              : {}),
            ...(audioEdit !== undefined
              ? {
                  ibus: {
                    ...settingsData?.ibus,
                    save_audio: saveDictationAudio,
                  },
                }
              : {}),
          },
        }),
      }),
    onSuccess: (value) => {
      client.setQueryData(["settings"], value);
      setRetentionDays(undefined);
      setSaveDictationAudio(undefined);
    },
  });

  async function downloadDiagnostics() {
    const response = await fetch("/api/v1/diagnostics/bundle", {
      headers: authHeaders(),
    });
    if (!response.ok) throw new Error("诊断包下载失败");
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(await response.blob());
    anchor.download = "classscribe-diagnostics.zip";
    anchor.click();
    URL.revokeObjectURL(anchor.href);
  }

  return (
    <section className="page-stack" aria-labelledby="settings-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">Local control plane</p>
          <h1 id="settings-title">设置与诊断</h1>
          <p>正文默认不进入日志；听写原始音频默认不保存。</p>
        </div>
      </header>
      <div className="settings-grid">
        <article className="panel">
          <h2>API 访问</h2>
          <label>
            <span>本机 API token</span>
            <div className="inline-field">
              <input
                autoComplete="off"
                onChange={(event) => {
                  setToken(event.target.value);
                }}
                placeholder="仅保存在当前浏览器会话"
                type="password"
                value={token}
              />
              <button
                disabled={localCleared}
                onClick={() => {
                  setApiToken(token);
                  void client.invalidateQueries();
                }}
                type="button"
              >
                应用
              </button>
            </div>
          </label>
          <small>所有写请求同时携带 bearer 与 CSRF token。</small>
          {localCleared && (
            <p role="status">本地数据已清除。请重启 ClassScribe 后再使用。</p>
          )}
          <h2>数据生命周期</h2>
          <label>
            <span>派生缓存保留天数</span>
            <input
              disabled={!settingsData || save.isPending}
              min="1"
              onChange={(event) => {
                setRetentionDays(event.target.valueAsNumber);
              }}
              type="number"
              value={retentionDays}
            />
          </label>
          <label className="toggle-line">
            <input
              disabled={!settingsData || save.isPending}
              checked={saveDictationAudio}
              onChange={(event) => {
                setSaveDictationAudio(event.target.checked);
              }}
              type="checkbox"
            />
            保存 IBus 原始音频（默认关闭）
          </label>
          <button
            className="primary-button"
            disabled={
              !settingsData ||
              save.isPending ||
              !Number.isInteger(retentionDays) ||
              retentionDays < 1
            }
            onClick={() => {
              save.mutate();
            }}
            type="button"
          >
            保存设置
          </button>
          {save.error && <p role="alert">{save.error.message}</p>}
          {settings.error && <p role="alert">{settings.error.message}</p>}
          {save.isSuccess && <p role="status">设置已保存</p>}
          <pre>{JSON.stringify(settingsData ?? {}, null, 2)}</pre>
          <h2>清除全部本地数据</h2>
          <p>
            将清空 ClassScribe
            的设置、录音、模型缓存、任务和导出。执行后需重启应用。
          </p>
          <label>
            输入确认短语 <code>{deleteAllPhrase}</code>
            <input
              aria-label="全量清除确认短语"
              value={deleteConfirmation}
              onChange={(event) => {
                setDeleteConfirmation(event.target.value);
              }}
            />
          </label>
          <button
            className="danger-button"
            type="button"
            disabled={
              localCleared ||
              clearAll.isPending ||
              deleteConfirmation !== deleteAllPhrase
            }
            onClick={() => {
              clearAll.mutate();
            }}
          >
            {clearAll.isPending ? "正在清除…" : "清除全部本地数据"}
          </button>
          {clearAll.error && (
            <p role="alert">清除失败：{clearAll.error.message}</p>
          )}
        </article>
        <article className="panel diagnostic-panel">
          <div className="card-top">
            <h2>本机诊断</h2>
            <button
              disabled={authInvalid || localCleared}
              onClick={() => {
                void diagnostics.refetch();
              }}
              type="button"
            >
              刷新
            </button>
            <button
              disabled={authInvalid || localCleared}
              onClick={() => {
                void downloadDiagnostics();
              }}
              type="button"
            >
              下载脱敏诊断包
            </button>
          </div>
          {diagnostics.isLoading ? (
            <p className="loading">正在检查系统…</p>
          ) : (
            <pre>
              {JSON.stringify(
                (authInvalid ? undefined : diagnostics.data) ?? {
                  error: diagnostics.error?.message,
                },
                null,
                2,
              )}
            </pre>
          )}
        </article>
      </div>
    </section>
  );
}
