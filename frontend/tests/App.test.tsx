import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { useWorkbench } from "../src/store";

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

describe("classroom workbench", () => {
  beforeEach(() => {
    useWorkbench.setState({
      page: "upload",
      currentJobId: null,
      selectedSegmentId: null,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : input.url;
        if (url.endsWith("/models") || url.endsWith("/glossaries")) {
          return Promise.resolve(
            new Response("[]", {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        if (url.endsWith("/settings")) {
          return Promise.resolve(
            new Response("{}", {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        if (url.endsWith("/ibus/status")) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                dictationd: {
                  available: true,
                  current: { state: "listening" },
                  config: {
                    language: "ja",
                    confirmation: "accuracy",
                    activation: "hold",
                    model_id: "nemotron_3_5_asr_streaming_0_6b",
                  },
                  available_models: ["nemotron_3_5_asr_streaming_0_6b"],
                },
                portal: {
                  available: true,
                  diagnostic: "GlobalShortcuts registered",
                },
                ibus_input_source_fallback: true,
              }),
              { status: 200, headers: { "Content-Type": "application/json" } },
            ),
          );
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({ service: "classscribe-core", privacy: "local" }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("starts at the complete upload workflow and states the local boundary", () => {
    renderApp();
    expect(
      screen.getByRole("heading", { name: "导入一堂课" }),
    ).toBeInTheDocument();
    expect(screen.getByText("离线推理")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "语言" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始自动转录" })).toBeDisabled();
  });

  it("navigates to both classroom and live IBus product surfaces", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: /IBus$/ }));
    expect(
      screen.getByRole("heading", { name: "IBus 语音输入" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/取消不会写入当前应用/)).toBeInTheDocument();
    expect(await screen.findByText("listening")).toBeInTheDocument();
    expect(screen.getByText("日本語")).toBeInTheDocument();
    expect(screen.getByText("最高精度")).toBeInTheDocument();
    expect(screen.getByText("GlobalShortcuts registered")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /任务$/ }));
    expect(
      screen.getByRole("heading", { name: "还没有课堂任务" }),
    ).toBeInTheDocument();
  });

  it("exposes model, glossary, export, and diagnostics navigation", () => {
    renderApp();
    for (const [button, heading] of [
      ["模型", "模型管理"],
      ["词典", "课程词典"],
      ["导出", "导出"],
      ["设置", "设置与诊断"],
    ] as const) {
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(`${button}$`) }),
      );
      expect(
        screen.getByRole("heading", { name: heading }),
      ).toBeInTheDocument();
    }
  });
});
