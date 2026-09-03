import { useQuery } from "@tanstack/react-query";

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
