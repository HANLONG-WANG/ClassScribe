import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";

interface IBusStatus {
  dictationd?: {
    available: boolean;
    current?: { state?: string; message?: string };
    config?: {
      language?: string;
      confirmation?: string;
      activation?: string;
      model_id?: string;
    };
    available_models?: string[];
    error?: string;
  };
  portal?: { available?: boolean; diagnostic?: string };
  ibus_input_source_fallback?: boolean;
}

interface WorkerStatus {
  enabled: boolean;
  stage: string;
  message: string;
  active: boolean;
  loaded_models?: string[];
  idle_unload_seconds: number;
  prewarm_on_startup: boolean;
}

const workerStages: Record<string, string> = {
  unloaded: "模型未加载",
  disabled: "IBus 已关闭",
  queued: "预热已排队",
  checking: "检查模型与依赖",
  verifying: "校验模型文件",
  loading: "加载模型中",
  ready: "模型已就绪",
  active: "语音输入中",
  failed: "准备失败",
};

const languageNames: Record<string, string> = {
  auto: "自动 / 混合",
  zh: "中文",
  ja: "日本語",
  en: "English",
};

const modeNames: Record<string, string> = {
  fast: "快速",
  balanced: "平衡",
  accuracy: "最高精度",
};

export function IBusPage() {
  const client = useQueryClient();
  const workers = useQuery({
    queryKey: ["ibus-workers"],
    queryFn: () => api<WorkerStatus>("/ibus/workers"),
    refetchInterval: 1000,
  });
  const prepare = useMutation({
    mutationFn: () =>
      api<WorkerStatus>("/ibus/workers/prepare", {
        method: "POST",
        body: "{}",
      }),
    onSuccess: (value) => {
      client.setQueryData(["ibus-workers"], value);
    },
  });
  const release = useMutation({
    mutationFn: () =>
      api<WorkerStatus>("/ibus/workers/release", {
        method: "POST",
        body: "{}",
      }),
    onSuccess: (value) => {
      client.setQueryData(["ibus-workers"], value);
    },
  });
  const workerBusy = ["queued", "checking", "verifying", "loading"].includes(
    workers.data?.stage ?? "",
  );
  const loadedModels = workers.data?.loaded_models ?? [];
  const status = useQuery({
    queryKey: ["ibus-status"],
    queryFn: () => api<IBusStatus>("/ibus/status"),
    refetchInterval: 1000,
  });
  const value = status.data;
  const daemonAvailable = value?.dictationd?.available === true;
  const config = value?.dictationd?.config ?? {};
  const currentState = value?.dictationd?.current?.state ?? "idle";

  return (
    <section className="page-stack" aria-labelledby="ibus-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">Fedora dictation</p>
          <h1 id="ibus-title">IBus 语音输入</h1>
          <p>专用输入源与 Wayland 全局快捷键共享同一个本地 dictationd。</p>
        </div>
        <span
          className={`status-pill ${daemonAvailable ? "status-completed" : "status-pending"}`}
        >
          {daemonAvailable ? currentState : "守护进程不可用"}
        </span>
      </header>
      <div className="panel worker-readiness">
        <h2>
          {workerStages[workers.data?.stage ?? ""] ?? "正在读取模型准备状态…"}
        </h2>
        <p role={workers.data?.stage === "failed" ? "alert" : "status"}>
          {workers.data?.message ?? workers.error?.message ?? "读取中…"}
        </p>
        <p>
          默认在首次语音输入时加载模型，期间会显示准备状态。也可以提前预热默认语言和档位；预热会占用内存。
        </p>
        {workers.data && (
          <small>
            空闲 {workers.data.idle_unload_seconds}{" "}
            秒后自动释放；课堂任务需要计算资源时也会释放。启动预热：
            {workers.data.prewarm_on_startup ? "开启" : "关闭"}。
          </small>
        )}
        {loadedModels.length > 0 && <p>已加载：{loadedModels.join("、")}</p>}
        <div className="toolbar compact">
          <button
            type="button"
            disabled={
              !workers.data?.enabled ||
              workers.data.active ||
              workerBusy ||
              prepare.isPending ||
              release.isPending
            }
            onClick={() => {
              release.reset();
              prepare.mutate();
            }}
          >
            {workerBusy || prepare.isPending ? "正在准备…" : "预热默认模型"}
          </button>
          <button
            type="button"
            disabled={
              !workers.data ||
              workers.data.active ||
              release.isPending ||
              prepare.isPending ||
              (!workerBusy && workers.data.stage !== "ready")
            }
            onClick={() => {
              prepare.reset();
              release.mutate();
            }}
          >
            {release.isPending
              ? "正在释放…"
              : workerBusy
                ? "取消准备并释放"
                : "释放模型"}
          </button>
        </div>
        {(prepare.error || release.error) && (
          <p role="alert">{prepare.error?.message ?? release.error?.message}</p>
        )}
      </div>
      <div className="panel ibus-console">
        <div className="mic-orb" aria-hidden="true">
          <span />
        </div>
        <h2>
          {config.activation === "toggle" ? "单击开始 / 结束" : "按住说话"}
        </h2>
        <p>临时文本进入 preedit；松开后才提交。取消不会写入当前应用。</p>
        <div className="metric-grid">
          <article>
            <span>语言</span>
            <strong>
              {languageNames[config.language ?? "auto"] ?? config.language}
            </strong>
          </article>
          <article>
            <span>模式</span>
            <strong>
              {modeNames[config.confirmation ?? "balanced"] ??
                config.confirmation}
            </strong>
          </article>
          <article>
            <span>模型</span>
            <strong>
              {config.model_id === "auto_best"
                ? "自动最佳"
                : (config.model_id ?? "—")}
            </strong>
          </article>
        </div>
        <div className="notice" role="status">
          {value?.portal?.diagnostic ??
            value?.dictationd?.error ??
            "正在读取 GlobalShortcuts 与 dictationd 状态…"}
          {value?.ibus_input_source_fallback === true &&
          value.portal?.available !== true
            ? "；仍可从系统输入源选择 ClassScribe Voice。"
            : ""}
        </div>
        {value?.dictationd?.current?.message ? (
          <pre>{JSON.stringify(value.dictationd.current, null, 2)}</pre>
        ) : null}
      </div>
    </section>
  );
}
