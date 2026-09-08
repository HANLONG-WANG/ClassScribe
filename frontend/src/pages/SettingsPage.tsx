import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, authHeaders, type ApiObject, setApiToken } from "../api";

export function SettingsPage() {
  const client = useQueryClient();
  const [token, setToken] = useState("");
  const [retentionEdit, setRetentionDays] = useState<number | undefined>();
  const [audioEdit, setSaveDictationAudio] = useState<boolean | undefined>();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<Record<string, ApiObject>>("/settings"),
  });
  const retentionDays =
    retentionEdit ??
    (typeof settings.data?.retention?.derived_days === "number"
      ? settings.data.retention.derived_days
      : 30);
  const saveDictationAudio =
    audioEdit ?? settings.data?.ibus?.save_audio === true;
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
                    ...settings.data?.retention,
                    derived_days: retentionDays,
                  },
                }
              : {}),
            ...(audioEdit !== undefined
              ? {
                  ibus: {
                    ...settings.data?.ibus,
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
          <h2>数据生命周期</h2>
          <label>
            <span>派生缓存保留天数</span>
            <input
              disabled={!settings.data || save.isPending}
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
              disabled={!settings.data || save.isPending}
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
              !settings.data ||
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
          <pre>{JSON.stringify(settings.data ?? {}, null, 2)}</pre>
        </article>
        <article className="panel diagnostic-panel">
          <div className="card-top">
            <h2>本机诊断</h2>
            <button
              onClick={() => {
                void diagnostics.refetch();
              }}
              type="button"
            >
              刷新
            </button>
            <button
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
                diagnostics.data ?? { error: diagnostics.error?.message },
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
