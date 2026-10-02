import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export function MaiSettings() {
  const client = useQueryClient();
  const [endpoint, setEndpoint] = useState("");
  const [key, setKey] = useState("");
  const status = useQuery({
    queryKey: ["mai-status"],
    queryFn: () =>
      api<{ configured: boolean; endpoint: string; offline: boolean }>(
        "/online/mai",
      ),
    retry: false,
  });
  const save = useMutation({
    mutationFn: (clear: boolean) =>
      api(
        "/online/mai",
        clear
          ? { method: "DELETE" }
          : { method: "PUT", body: JSON.stringify({ endpoint, key }) },
      ),
    onSuccess: () => {
      setKey("");
      void client.invalidateQueries({ queryKey: ["mai-status"] });
    },
  });
  return (
    <section className="panel">
      <h2>Azure MAI 在线转写</h2>
      <p>
        仅当选择 MAI 并提交任务时上传音频。Azure
        可能计费；停止请求不代表云端停止处理。
      </p>
      <p>
        凭据仅保存在当前后端进程内，重启后清除。也可使用 AZURE_SPEECH_ENDPOINT
        和 AZURE_SPEECH_KEY 环境变量。
      </p>
      {status.data?.offline && (
        <p role="status">
          离线模式已启用。使用 MAI 前请将配置中的 privacy.runtime_offline 改为
          false 并重启。
        </p>
      )}
      <p>
        {status.data?.configured
          ? `已配置：${status.data.endpoint}`
          : "尚未配置凭据"}
      </p>
      <label>
        Azure Speech Endpoint
        <input
          value={endpoint}
          onChange={(e) => {
            setEndpoint(e.target.value);
          }}
          placeholder="https://资源名.cognitiveservices.azure.com"
        />
      </label>
      <label>
        Azure Speech Key
        <input
          type="password"
          autoComplete="off"
          value={key}
          onChange={(e) => {
            setKey(e.target.value);
          }}
        />
      </label>
      <button
        disabled={save.isPending || !endpoint || !key}
        onClick={() => {
          save.mutate(false);
        }}
      >
        保存到本次运行
      </button>
      <button
        disabled={save.isPending}
        onClick={() => {
          save.mutate(true);
        }}
      >
        清除内存凭据
      </button>
      <p className="muted">清除内存凭据后，环境变量配置仍然有效。</p>
      {(save.error || status.error) && (
        <p role="alert">{save.error?.message ?? status.error?.message}</p>
      )}
    </section>
  );
}
