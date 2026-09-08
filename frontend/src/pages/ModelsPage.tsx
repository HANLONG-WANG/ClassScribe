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
  supported_languages?: string[];
  reusing_download?: boolean;
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

const installStages: Record<string, string> = {
  downloading: "正在下载模型文件…",
  verifying: "下载已完成，正在校验模型文件…",
  checking:
    "模型文件已就绪，正在准备 Python 依赖、加载模型并运行健康检查；此步骤可能需要数分钟。",
  awaiting_health_check:
    "模型文件已保留，尚未通过健康检查。可重新选择录音和语言后重试，无需重新下载；文件将再次校验。",
  complete: "安装完成，健康检查已通过，模型可用。",
  failed: "安装未完成，请重试；如缓存文件校验失败，请先删除此版本。",
};
const busyStages = new Set(["downloading", "verifying", "checking"]);
const installationLabels: Record<string, string> = {
  not_installed: "未安装",
  downloading: "下载中",
  verifying: "文件校验中",
  checking: "准备依赖 / 健康检查中",
  awaiting_health_check: "已下载，待健康检查",
  complete: "已安装，可用",
  healthy: "已安装，可用",
  installed: "已安装",
  failed: "安装失败",
  unhealthy: "健康检查未通过",
  missing: "模型文件缺失",
};

function installBlockReason(reason: string | null) {
  if (reason === null) return "无";
  return `${installBlockReasons[reason] ?? "安装策略阻止"} (${reason})`;
}

function isInstalled(model: ModelInfo) {
  return model.install_stage
    ? model.install_stage === "complete"
    : ["healthy", "installed"].includes(model.installation.state);
}

export function ModelsPage() {
  const client = useQueryClient();
  const [plan, setPlan] = useState<InstallPlan | null>(null);
  const [healthRecordingId, setHealthRecordingId] = useState("");
  const [healthLanguage, setHealthLanguage] = useState("ja");
  const [healthTranscript, setHealthTranscript] = useState("");
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [installedName, setInstalledName] = useState("");
  const query = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelInfo[]>("/models"),
    refetchInterval: 2000,
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
      setInstalledName("");
      const supported =
        value.supported_languages ??
        query.data?.find((item) => item.id === value.model_id)?.languages ??
        [];
      setHealthLanguage((current) =>
        supported.includes("auto") || supported.includes(current)
          ? current
          : "",
      );
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
    onSuccess: (_value, installedPlan) => {
      client.setQueryData<ModelInfo[]>(["models"], (models = []) =>
        models.map((model) =>
          model.id === installedPlan.model_id
            ? {
                ...model,
                install_stage: "complete",
                installation: { ...model.installation, state: "healthy" },
              }
            : model,
        ),
      );
      setInstalledName(
        query.data?.find((item) => item.id === installedPlan.model_id)?.name ??
          installedPlan.model_id,
      );
      setPlan(null);
      setHealthRecordingId("");
      setHealthTranscript("");
      setTermsAccepted(false);
      void client.invalidateQueries({ queryKey: ["models"] });
    },
    onError: () => {
      void client.invalidateQueries({ queryKey: ["models"] });
    },
  });

  const supportedLanguages =
    plan?.supported_languages ??
    query.data?.find((item) => item.id === plan?.model_id)?.languages ??
    [];
  const languageSupported =
    supportedLanguages.includes("auto") ||
    supportedLanguages.includes(healthLanguage);
  const planStage = query.data?.find(
    (item) => item.id === plan?.model_id,
  )?.install_stage;

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
      {installedName && (
        <p className="notice" role="status">
          {installedName} 安装完成，健康检查已通过，可以开始使用。
        </p>
      )}
      {plan && (
        <div className="notice install-plan">
          <strong>安装确认</strong>
          {plan.reusing_download && (
            <p>已找到保留的模型文件，本次将复用文件并重试健康检查。</p>
          )}
          {confirm.isPending && (
            <p role="status">
              {installStages[planStage ?? ""] ?? "正在提交安装请求…"}
            </p>
          )}
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
                  disabled={confirm.isPending}
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
                  disabled={
                    uploadHealthRecording.isPending || confirm.isPending
                  }
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
                  disabled={confirm.isPending}
                  onChange={(event) => {
                    setHealthLanguage(event.target.value);
                  }}
                  value={healthLanguage}
                >
                  {!healthLanguage && (
                    <option value="">请选择支持的语言</option>
                  )}
                  {(
                    [
                      ["zh", "中文"],
                      ["ja", "日语"],
                      ["en", "英语"],
                    ] as const
                  ).map(([code, label]) => (
                    <option
                      key={code}
                      value={code}
                      disabled={
                        !supportedLanguages.includes("auto") &&
                        !supportedLanguages.includes(code)
                      }
                    >
                      {label}
                      {!supportedLanguages.includes("auto") &&
                      !supportedLanguages.includes(code)
                        ? "（此模型不支持）"
                        : ""}
                    </option>
                  ))}
                </select>
              </label>
              <small>
                此模型支持：{supportedLanguages.join("、")}
                。请选择与录音实际内容一致的语言。
              </small>
              {!languageSupported && (
                <p role="alert">
                  请选择此模型支持的健康检查语言，当前选择不能开始下载。
                </p>
              )}
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
                  !languageSupported ||
                  confirm.isError ||
                  install.isPending ||
                  (plan.requires_terms_acceptance && !termsAccepted) ||
                  confirm.isPending
                }
                onClick={() => {
                  confirm.mutate(plan);
                }}
                type="button"
              >
                {confirm.isPending
                  ? "正在安装，请查看上方进度…"
                  : "确认安装并运行推理健康检查"}
              </button>
              {confirm.isError && (
                <p role="alert">
                  {confirm.error.message}{" "}
                  请在模型卡片上重新准备安装或重试健康检查。
                </p>
              )}
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
                <dd>
                  {installationLabels[
                    model.install_stage ?? model.installation.state
                  ] ?? model.installation.state}
                </dd>
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
            {model.install_stage && installStages[model.install_stage] && (
              <p className="notice" role="status">
                {installStages[model.install_stage]}
              </p>
            )}
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
                disabled={
                  !model.installable ||
                  install.isPending ||
                  confirm.isPending ||
                  busyStages.has(model.install_stage ?? "") ||
                  isInstalled(model)
                }
                onClick={() => {
                  install.mutate(model.id);
                  confirm.reset();
                }}
                type="button"
              >
                {isInstalled(model)
                  ? "已安装"
                  : busyStages.has(model.install_stage ?? "")
                    ? "正在安装…"
                    : install.isPending && install.variables === model.id
                      ? "正在预检…"
                      : model.install_stage === "awaiting_health_check"
                        ? "重试健康检查"
                        : "准备安装"}
              </button>
              {install.isError && <p role="alert">{install.error.message}</p>}
              <button
                disabled={busyStages.has(model.install_stage ?? "")}
                onClick={() => {
                  verify.mutate(model.id);
                }}
                type="button"
              >
                验证
              </button>
              <button
                disabled={busyStages.has(model.install_stage ?? "")}
                onClick={() => {
                  rollback.mutate({ id: model.id, revision: model.revision });
                }}
                type="button"
              >
                回滚到此版
              </button>
              <button
                className="danger-button"
                disabled={busyStages.has(model.install_stage ?? "")}
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
