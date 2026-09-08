import {
  type KeyboardEvent,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import type { ModelInfo } from "../api";
import {
  capabilityLabels,
  languageLabels,
  modelGuidance,
  modelReady,
} from "../modelGuidance";

export function ModelPicker({
  models,
  value,
  onChange,
  loading = false,
  error,
}: {
  models: ModelInfo[];
  value: string;
  onChange: (id: string) => void;
  loading?: boolean;
  error?: string | undefined;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const selected = models.find((model) => model.id === value);

  useLayoutEffect(() => {
    if (!open) return;
    const anchor = trigger.current;
    const popup = panel.current;
    if (!anchor || !popup) return;
    function position() {
      if (!anchor || !popup) return;
      const viewport = window.visualViewport;
      const x = viewport?.offsetLeft ?? 0;
      const y = viewport?.offsetTop ?? 0;
      const width = viewport?.width ?? window.innerWidth;
      const height = viewport?.height ?? window.innerHeight;
      const rect = anchor.getBoundingClientRect();
      const popupWidth = Math.min(700, width - 24);
      const maximumHeight = Math.min(480, height - 24);
      const below = y + height - rect.bottom - 20;
      const above = rect.top - y - 20;
      const useBelow = below >= Math.min(300, maximumHeight) || below >= above;
      const space = useBelow ? below : above;
      const popupHeight =
        space >= 220 ? Math.min(maximumHeight, space) : maximumHeight;
      popup.style.width = `${String(popupWidth)}px`;
      popup.style.maxHeight = `${String(popupHeight)}px`;
      popup.style.left = `${String(Math.max(x + 12, Math.min(rect.right - popupWidth, x + width - popupWidth - 12)))}px`;
      const naturalHeight = Math.min(popup.scrollHeight, popupHeight);
      const top =
        space >= 220
          ? useBelow
            ? rect.bottom + 8
            : rect.top - naturalHeight - 8
          : y + (height - naturalHeight) / 2;
      popup.style.top = `${String(Math.max(y + 12, Math.min(top, y + height - naturalHeight - 12)))}px`;
    }
    position();
    (
      popup.querySelector<HTMLButtonElement>('[aria-pressed="true"]') ??
      popup.querySelector<HTMLButtonElement>("button")
    )?.focus();
    const observer = new ResizeObserver(position);
    observer.observe(popup);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    window.visualViewport?.addEventListener("resize", position);
    window.visualViewport?.addEventListener("scroll", position);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
      window.visualViewport?.removeEventListener("resize", position);
      window.visualViewport?.removeEventListener("scroll", position);
      if (anchor.isConnected) anchor.focus({ preventScroll: true });
    };
  }, [open]);

  function choose(next: string) {
    onChange(next);
    setOpen(false);
  }
  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    }
    if (event.key === "Tab") {
      const buttons = panel.current?.querySelectorAll<HTMLButtonElement>(
        "button:not(:disabled)",
      );
      const first = buttons?.[0];
      const last = buttons?.[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
  }
  return (
    <div className="model-picker">
      <span id={`${id}-label`}>主模型</span>
      <button
        ref={trigger}
        type="button"
        className="model-picker-trigger"
        aria-labelledby={`${id}-label ${id}-value`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => {
          setOpen(true);
        }}
      >
        <span id={`${id}-value`}>{selected?.name ?? "自动最佳"}</span>
        <span aria-hidden="true">▾</span>
      </button>
      {open &&
        createPortal(
          <div
            className="model-picker-overlay"
            onPointerDown={(event) => {
              if (event.target === event.currentTarget) setOpen(false);
            }}
          >
            <div
              ref={panel}
              id={id}
              className="model-picker-panel"
              role="dialog"
              aria-modal="true"
              aria-labelledby={`${id}-title`}
              onKeyDown={keyboard}
            >
              <header className="model-picker-header">
                <div>
                  <h2 id={`${id}-title`}>选择主模型</h2>
                  <p>仅显示支持当前语言的正文模型；选择后自动收起。</p>
                </div>
                <button
                  type="button"
                  aria-label="关闭模型选单"
                  onClick={() => {
                    setOpen(false);
                  }}
                >
                  ×
                </button>
              </header>
              <div className="model-picker-options">
                <button
                  type="button"
                  className="model-picker-card model-picker-auto"
                  aria-pressed={!value}
                  onClick={() => {
                    choose("");
                  }}
                >
                  <span className="model-picker-card-title">
                    <strong>自动最佳</strong>
                    <span aria-hidden="true">{!value ? "✓" : "○"}</span>
                  </span>
                  <span className="model-picker-summary">
                    根据语言、有效排名及已安装模型自动选择，适合日常转录。
                  </span>
                </button>
                {loading && <p role="status">正在读取模型…</p>}
                {error && <p role="alert">无法读取模型：{error}</p>}
                {!loading && !error && models.length === 0 && (
                  <p className="model-picker-empty">
                    当前没有已启用且支持此语言的正文模型，可调整语言或前往模型管理页查看。
                  </p>
                )}
                <div className="model-picker-grid">
                  {models.map((model) => {
                    const memory =
                      model.installation.measured_vram_mb ??
                      model.estimated_vram_mb;
                    const abilities = Object.entries(model.capabilities ?? {})
                      .filter(([, supported]) => supported)
                      .map(([key]) => capabilityLabels[key] ?? key)
                      .slice(0, 3);
                    return (
                      <button
                        type="button"
                        className="model-picker-card"
                        key={model.id}
                        aria-pressed={value === model.id}
                        onClick={() => {
                          choose(model.id);
                        }}
                      >
                        <span className="model-picker-card-title">
                          <strong>{model.name}</strong>
                          <span aria-hidden="true">
                            {value === model.id ? "✓" : "○"}
                          </span>
                        </span>
                        <span className="model-picker-meta">
                          <span data-ready={modelReady(model)}>
                            {modelReady(model) ? "已安装" : "待安装／检查"}
                          </span>
                          <span>
                            {model.languages
                              .filter((code) => code !== "auto")
                              .map((code) => languageLabels[code] ?? code)
                              .join(" · ")}
                          </span>
                        </span>
                        <span className="model-picker-summary">
                          {modelGuidance[model.id]?.purpose ??
                            "课堂正文识别候选，具体精度以本机基准为准。"}
                        </span>
                        <span className="model-picker-capabilities">
                          {[
                            model.modes?.includes("streaming")
                              ? "支持流式"
                              : "离线批量",
                            ...abilities,
                          ].join(" · ")}
                        </span>
                        <span className="model-picker-meta">
                          <span>
                            {typeof memory === "number"
                              ? `${model.installation.measured_vram_mb == null ? "预计" : "实测"}显存 ${(memory / 1024).toFixed(1)} GiB`
                              : "显存尚未报告"}
                          </span>
                          <span className="mono">
                            {model.revision.slice(0, 7)}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
