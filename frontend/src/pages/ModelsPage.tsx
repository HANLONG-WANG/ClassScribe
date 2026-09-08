import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, useState } from "react";

import {
  api,
  type ApiObject,
  type ModelComponentSource,
  type ModelInfo,
  type Recording,
  uploadRecording,
} from "../api";
import { inspectHealthWav } from "../healthWav";

interface InstallPlan extends ApiObject {
  confirmation_token: string;
  model_id: string;
  revision: string;
  license_id: string;
  license_url: string;
  requires_terms_acceptance: boolean;
  component_sources: ModelComponentSource[];
  estimated_download_bytes: number;
  installed_size_bytes: number;
  required_free_bytes: number;
  available_bytes: number;
  environment: ApiObject;
  expires_at: string;
}

function bytes(value: number | null | undefined) {
  if (value === null || value === undefined) return "尚未报告";
  return `${(value / 1_073_741_824).toFixed(2)} GiB`;
}

const installBlockReasons: Record<string, string> = {
  manifest_unavailable: "发布包未包含此模型 Manifest",
  registry_policy_disabled: "注册表策略未允许普通安装",
  worker_lock_unavailable_or_mismatched: "Worker 依赖锁缺失或不匹配",
  worker_not_implemented: "当前版本未实现此模型 Worker",
};

function installBlockReason(reason: string | null) {
  if (reason === null) return "无";
  return `${installBlockReasons[reason] ?? "安装策略阻止"} (${reason})`;
}

export function ModelsPage() {
  const client = useQueryClient();
  const [plan, setPlan] = useState<InstallPlan | null>(null);
  const [healthRecordingId, setHealthRecordingId] = useState("");
  const [healthLanguage, setHealthLanguage] = useState("ja");
  const [healthTranscript, setHealthTranscript] = useState("");
  const [termsAccepted, setTermsAccepted] = useState(false);
  const query = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelInfo[]>("/models"),
  });
  const recordings = useQuery({
    queryKey: ["recordings"],
    queryFn: () => api<Recording[]>("/recordings"),
    enabled: plan !== null,
  });
  const verify = useMutation({
    mutationFn: (id: string) =>
      api(`/models/${id}/verify`, { method: "POST", body: "{}" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["models"] }),
  });
  const remove = useMutation({
    mutationFn: ({ id, revision }: { id: string; revision: string }) =>
      api(`/models/${id}?revision=${encodeURIComponent(revision)}`, {
        method: "DELETE",
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["models"] }),
  });
  const rollback = useMutation({
    mutationFn: ({ id, revision }: { id: string; revision: string }) =>
      api(`/models/${id}/rollback`, {
        method: "POST",
        body: JSON.stringify({ revision }),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["models"] }),
  });
  const install = useMutation({
    mutationFn: (id: string) =>
      api<InstallPlan>(`/models/${id}/install`, {
        method: "POST",
        body: "{}",
      }),
    onSuccess: (value) => {
      setPlan(value);
      setTermsAccepted(false);
    },
  });
  const uploadHealthRecording = useMutation({
    mutationFn: async (file: File) => {
      const recording = await uploadRecording(
        file,
        await inspectHealthWav(file),
      );
      if (recording.audio_qc.health_wav_eligible !== true) {
        throw new Error("服务端未确认该文件符合健康 WAV 合同");
      }
      return recording;
    },
    onSuccess: (recording) => {
      client.setQueryData<Recording[]>(["recordings"], (current = []) => [
        recording,
        ...current.filter((item) => item.id !== recording.id),
      ]);
      setHealthRecordingId(recording.id);
    },
  });
  const confirm = useMutation({
    mutationFn: (value: InstallPlan) =>
      api(`/models/${value.model_id}/install/confirm`, {
        method: "POST",
        body: JSON.stringify({
          confirmation_token: value.confirmation_token,
          health_recording_id: healthRecordingId,
          health_language: healthLanguage,
          health_transcript: healthTranscript || null,
          terms_accepted: termsAccepted,
        }),
      }),
    onSuccess: () => {
      setPlan(null);
      setHealthRecordingId("");
      setHealthTranscript("");
      setTermsAccepted(false);
      void client.invalidateQueries({ queryKey: ["models"] });
    },
  });

  function chooseHealthRecording(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file !== undefined) {
      uploadHealthRecording.mutate(file);
    }
    event.target.value = "";
  }

  return (
    <section className="page-stack models-page" aria-labelledby="models-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">Pinned & isolated</p>
          <h1 id="models-title">模型管理</h1>
          <p>
            安装是唯一允许联网的操作；推理只解析经过哈希验证的本地 revision。
          </p>
        </div>
      </header>
      {plan && (
        <div className="notice install-plan">
          <strong>安装确认</strong>
          {plan.error ? (
            <p>
              {typeof plan.error === "string" ? plan.error : "manifest 无效"}
            </p>
          ) : (
            <>
              <p>
                下载 {bytes(plan.estimated_download_bytes)} · 安装后{" "}
                {bytes(plan.installed_size_bytes)} · 需要空间{" "}
                {bytes(plan.required_free_bytes)}
              </p>
              <p className="mono">
                环境：{JSON.stringify(plan.environment)} · 确认于{" "}
                {new Date(plan.expires_at).toLocaleTimeString()} 前有效
              </p>
              <p>
                许可证：{" "}
                <a href={plan.license_url} rel="noreferrer" target="_blank">
                  {plan.license_id}
                </a>
                {plan.requires_terms_acceptance
                  ? " · 需要接受上游访问条款"
                  : ""}
              </p>
              {plan.component_sources.length > 0 && (
                <div>
                  <p>组件来源与许可证：</p>
                  <ul>
                    {plan.component_sources.map((source) => (
                      <li key={source.repository}>
                        <span className="mono">{source.repository}</span> ·{" "}
                        {source.relationship === "copied" ? "复制" : "派生"} ·{" "}
                        <a
                          href={source.license_url}
                          rel="noreferrer"
                          target="_blank"
                        >
                          {source.license_id}
                        </a>
                        {source.requires_terms_acceptance
                          ? " · 需要接受该组件上游条款"
                          : ""}
                        <br />
                        <small className="mono">
                          revision {source.revision}
                        </small>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <small>
                此页已完成磁盘与环境披露。下载确认令牌只可使用一次。
              </small>
              <label>
                健康检查录音
                <select
                  onChange={(event) => {
                    setHealthRecordingId(event.target.value);
                  }}
                  value={healthRecordingId}
                >
                  <option value="">请选择已有短 WAV</option>
                  {(recordings.data ?? [])
                    .filter(
                      (recording) =>
                        recording.audio_qc.health_wav_eligible === true &&
                        recording.duration_samples > 0 &&
                        recording.duration_samples <= 15 * 16_000 &&
                        recording.sample_rate === 16_000 &&
                        recording.channels === 1,
                    )
                    .map((recording) => (
                      <option key={recording.id} value={recording.id}>
                        {recording.source_name} · {recording.duration_samples}{" "}
                        samples
                      </option>
                    ))}
                </select>
              </label>
              <label className="file-button">
                {uploadHealthRecording.isPending
                  ? "正在上传健康 WAV…"
                  : "上传健康 WAV"}
                <input
                  accept="audio/wav,.wav"
                  disabled={uploadHealthRecording.isPending}
                  onChange={chooseHealthRecording}
                  type="file"
                />
              </label>
              {uploadHealthRecording.isError && (
                <p role="alert">{uploadHealthRecording.error.message}</p>
              )}
              <label>
                录音语言
                <select
                  onChange={(event) => {
                    setHealthLanguage(event.target.value);
                  }}
                  value={healthLanguage}
                >
                  <option value="zh">中文</option>
                  <option value="ja">日语</option>
                  <option value="en">英语</option>
                </select>
              </label>
              <label>
                精确文字（强制对齐模型必填）
                <input
                  maxLength={500}
                  onChange={(event) => {
                    setHealthTranscript(event.target.value);
                  }}
                  value={healthTranscript}
                />
              </label>
              {plan.requires_terms_acceptance && (
                <label>
                  <input
                    checked={termsAccepted}
                    onChange={(event) => {
                      setTermsAccepted(event.target.checked);
                    }}
                    type="checkbox"
                  />
                  我已阅读并接受上游模型访问条款
                </label>
              )}
              <button
                disabled={
                  !healthRecordingId ||
                  (plan.requires_terms_acceptance && !termsAccepted) ||
                  confirm.isPending
                }
                onClick={() => {
                  confirm.mutate(plan);
                }}
                type="button"
              >
                {confirm.isPending
                  ? "正在下载并实测…"
                  : "确认安装并运行推理健康检查"}
              </button>
              {confirm.isError && <p>{confirm.error.message}</p>}
            </>
          )}
        </div>
      )}
      <div className="model-grid">
        {(query.data ?? []).map((model) => (
          <article className="panel model-card" key={model.id}>
            <div className="card-top">
              <div>
                <span
                  className="status-dot"
                  data-status={model.installation.state}
                />
                <h2>{model.name}</h2>
              </div>
              <span>{model.experimental ? "实验" : "稳定"}</span>
            </div>
            <p className="mono">
              {model.id} · {model.revision.slice(0, 12)}
            </p>
            <div className="tag-row">
              {model.languages.map((item) => (
                <span key={item}>{item}</span>
              ))}
              {model.tasks.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </div>
            <dl>
              <div>
                <dt>安装状态</dt>
                <dd>{model.installation.state}</dd>
              </div>
              <div>
                <dt>发布 Manifest</dt>
                <dd>{model.manifest_available ? "可用" : "缺失"}</dd>
              </div>
              <div>
                <dt>Worker 实现</dt>
                <dd>{model.worker_implemented ? "已实现" : "未实现"}</dd>
              </div>
              <div>
                <dt>普通安装</dt>
                <dd>{model.installable ? "可安装" : "已阻塞"}</dd>
              </div>
              <div>
                <dt>自动候选</dt>
                <dd>{model.enabled ? "已启用" : "未启用"}</dd>
              </div>
              <div>
                <dt>Manifest SHA-256</dt>
                <dd className="mono">{model.manifest_sha256 ?? "—"}</dd>
              </div>
              <div>
                <dt>预计下载</dt>
                <dd>{bytes(model.estimated_download_bytes)}</dd>
              </div>
              <div>
                <dt>安装大小</dt>
                <dd>{bytes(model.installed_size_bytes)}</dd>
              </div>
              <div>
                <dt>Remote code 文件</dt>
                <dd>{model.remote_code_file_count ?? "—"}</dd>
              </div>
              <div>
                <dt>外部组件来源</dt>
                <dd>{model.component_source_count ?? "—"}</dd>
              </div>
              <div>
                <dt>安装阻塞原因</dt>
                <dd id={`${model.id}-install-block-reason`}>
                  {installBlockReason(model.install_block_reason)}
                </dd>
              </div>
              <div>
                <dt>预计显存</dt>
                <dd>{model.estimated_vram_mb} MB</dd>
              </div>
              <div>
                <dt>实测显存</dt>
                <dd>{model.installation.measured_vram_mb ?? "—"}</dd>
              </div>
              <div>
                <dt>已安装 SHA-256</dt>
                <dd className="mono">
                  {model.installation.sha256?.slice(0, 12) ?? "—"}
                </dd>
              </div>
            </dl>
            <p className="ranking">
              本机排名：{JSON.stringify(model.benchmark)}
            </p>
            {!model.enabled &&
              model.manifest_available &&
              model.worker_implemented && (
                <p
                  className="notice"
                  id={`${model.id}-expert-enablement-required`}
                >
                  此模型只能在独立专家启用流程完成后安装；普通安装不会更改注册表策略。
                </p>
              )}
            <div className="toolbar compact">
              <button
                aria-describedby={
                  model.installable
                    ? undefined
                    : [
                        `${model.id}-install-block-reason`,
                        !model.enabled &&
                        model.manifest_available &&
                        model.worker_implemented
                          ? `${model.id}-expert-enablement-required`
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" ")
                }
                disabled={!model.installable || install.isPending}
                onClick={() => {
                  install.mutate(model.id);
                }}
                type="button"
              >
                {install.isPending ? "正在预检…" : "准备安装"}
              </button>
              {install.isError && <p role="alert">{install.error.message}</p>}
              <button
                onClick={() => {
                  verify.mutate(model.id);
                }}
                type="button"
              >
                验证
              </button>
              <button
                onClick={() => {
                  rollback.mutate({ id: model.id, revision: model.revision });
                }}
                type="button"
              >
                回滚到此版
              </button>
              <button
                className="danger-button"
                onClick={() => {
                  remove.mutate({ id: model.id, revision: model.revision });
                }}
                type="button"
              >
                删除
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
