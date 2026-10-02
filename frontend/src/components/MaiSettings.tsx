import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

interface MaiStatus {
  configured: boolean;
  endpoint: string;
}

export function MaiSettings() {
  const client = useQueryClient();
  const [endpointEdit, setEndpoint] = useState<string>();
  const [key, setKey] = useState("");
  const status = useQuery({
    queryKey: ["mai-status"],
    queryFn: () => api<MaiStatus>("/online/mai"),
    retry: false,
  });
  const endpoint = endpointEdit ?? status.data?.endpoint ?? "";
  const save = useMutation({
    mutationFn: (clear: boolean) =>
      api<MaiStatus>(
        "/online/mai",
        clear
          ? { method: "DELETE" }
          : { method: "PUT", body: JSON.stringify({ endpoint, key }) },
      ),
    onSuccess: (value) => {
      setKey("");
      setEndpoint(undefined);
      client.setQueryData(["mai-status"], value);
    },
  });
  return (
    <article
      className="panel mai-settings"
      aria-labelledby="mai-settings-title"
    >
      <h2 id="mai-settings-title">Azure MAI 在线转写</h2>
      <p className="muted">
        选择 Azure MAI 并提交任务后，音频和所选术语将上传至 Azure。
        服务可能计费；停止本地请求不保证云端停止处理。
      </p>
      <p role="status" className="mai-credential-status">
        {status.isPending
          ? "正在读取凭据状态…"
          : status.data?.configured
            ? "凭据已配置"
            : "尚未配置凭据"}
      </p>
      <div className="form-grid">
        <label>
          <span>Azure Speech Endpoint</span>
          <input
            autoComplete="off"
            value={endpoint}
            onChange={(event) => {
              setEndpoint(event.target.value);
            }}
            placeholder="https://资源名.cognitiveservices.azure.com"
          />
        </label>
        <label>
          <span>Azure Speech Key</span>
          <input
            type="password"
            autoComplete="off"
            value={key}
            onChange={(event) => {
              setKey(event.target.value);
            }}
            placeholder={
              status.data?.configured
                ? "已保存；输入新 Key 可更新"
                : "输入 Azure Speech Key"
            }
          />
        </label>
      </div>
      <small>凭据保存在本机，重启后仍然有效。</small>
      <div className="mai-settings-actions">
        <button
          className="primary-button"
          type="button"
          disabled={
            status.isPending ||
            save.isPending ||
            !endpoint.trim() ||
            !key.trim()
          }
          onClick={() => {
            save.mutate(false);
          }}
        >
          {save.isPending && !save.variables ? "正在保存…" : "保存凭据"}
        </button>
        <button
          type="button"
          disabled={status.isPending || save.isPending}
          onClick={() => {
            save.mutate(true);
          }}
        >
          {save.isPending && save.variables ? "正在清除…" : "清除凭据"}
        </button>
      </div>
      <small>
        也支持 Azure Speech 环境变量；清除保存的凭据后，环境变量配置仍然有效。
      </small>
      {save.isSuccess && (
        <p role="status">
          {save.variables ? "已清除保存的凭据" : "凭据已保存"}
        </p>
      )}
      {(save.error || status.error) && (
        <p role="alert">{save.error?.message ?? status.error?.message}</p>
      )}
    </article>
  );
}
