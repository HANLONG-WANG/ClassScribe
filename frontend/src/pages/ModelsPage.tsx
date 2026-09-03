import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, useState } from "react";

import { api, type ApiObject, type ModelInfo } from "../api";

interface InstallPlan extends ApiObject {
  confirmation_token: string;
  model_id: string;
  revision: string;
  license_id: string;
  license_url: string;
  requires_terms_acceptance: boolean;
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
    mutationFn: ({ id, manifest }: { id: string; manifest: ApiObject }) =>
      api<InstallPlan>(`/models/${id}/install`, {
        method: "POST",
        body: JSON.stringify({ manifest }),
      }),
    onSuccess: (value) => {
      setPlan(value);
      setTermsAccepted(false);
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

  async function readManifest(
    model: ModelInfo,
    event: ChangeEvent<HTMLInputElement>,
  ) {
    const file = event.target.files?.item(0);
    if (!file) return;
    try {
      install.mutate({
        id: model.id,
        manifest: JSON.parse(await file.text()) as ApiObject,
      });
    } catch (error) {
      setPlan({
        confirmation_token: "",
        model_id: model.id,
        revision: model.revision,
        license_id: "",
        license_url: "",
        requires_terms_acceptance: false,
        estimated_download_bytes: 0,
        installed_size_bytes: 0,
        required_free_bytes: 0,
        available_bytes: 0,
        environment: {},
        expires_at: "",
        error: error instanceof Error ? error.message : "manifest 无效",
      });
    }
  }

  return (
    <section className="page-stack" aria-labelledby="models-title">
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
              <small>
                此页已完成磁盘与环境披露。下载确认令牌只可使用一次。
              </small>
              <label>
                健康检查录音 ID（16 kHz PCM WAV，最长 15 秒）
                <input
                  onChange={(event) => {
                    setHealthRecordingId(event.target.value);
                  }}
                  placeholder="00000000-0000-0000-0000-000000000000"
                  value={healthRecordingId}
                />
              </label>
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
                <dt>预计显存</dt>
                <dd>{model.estimated_vram_mb} MB</dd>
              </div>
              <div>
                <dt>实测显存</dt>
                <dd>{model.installation.measured_vram_mb ?? "—"}</dd>
              </div>
              <div>
                <dt>SHA-256</dt>
                <dd className="mono">
                  {model.installation.sha256?.slice(0, 12) ?? "—"}
                </dd>
              </div>
            </dl>
            <p className="ranking">
              本机排名：{JSON.stringify(model.benchmark)}
            </p>
            <div className="toolbar compact">
              <label className="file-button">
                准备安装
                <input
                  accept="application/json,.json"
                  onChange={(event) => {
                    void readManifest(model, event);
                  }}
                  type="file"
                />
              </label>
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
