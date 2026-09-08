import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
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

function healthWavFile() {
  const buffer = new ArrayBuffer(46);
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  for (const [offset, value] of [
    [0, "RIFF"],
    [8, "WAVE"],
    [12, "fmt "],
    [36, "data"],
  ] as const) {
    for (let index = 0; index < value.length; index += 1) {
      bytes[offset + index] = value.charCodeAt(index);
    }
  }
  view.setUint32(4, 38, true);
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16_000, true);
  view.setUint32(28, 32_000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  view.setUint32(40, 2, true);
  const file = new File([buffer], "uploaded-health.wav", { type: "audio/wav" });
  Object.defineProperty(file, "arrayBuffer", {
    value: () => Promise.resolve(buffer),
  });
  return file;
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
        if (url.endsWith("/queue"))
          return Promise.resolve(
            new Response(JSON.stringify({ paused: false, items: [] })),
          );
        if (
          url.endsWith("/models") ||
          url.endsWith("/glossaries") ||
          url.endsWith("/recordings")
        ) {
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
      screen.getByRole("heading", { name: "批量导入课堂" }),
    ).toBeInTheDocument();
    expect(screen.getByText("离线推理")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "语言" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "加入转录队列" })).toBeDisabled();
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
    fireEvent.click(screen.getByRole("button", { name: /任务队列$/ }));
    expect(
      screen.getByRole("heading", { name: "转录队列" }),
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

  it("renders manifest, worker, installability, and enabled as separate model states", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : input.url;
        const body = url.endsWith("/models")
          ? [
              {
                id: "four-state-fixture",
                name: "Four-state fixture",
                revision: "a".repeat(40),
                repository: "owner/model",
                languages: ["en"],
                tasks: ["asr"],
                enabled: true,
                experimental: false,
                manifest_available: true,
                manifest_sha256: "b".repeat(64),
                estimated_download_bytes: 1,
                installed_size_bytes: 1,
                remote_code_file_count: 2,
                component_source_count: 0,
                component_sources: [],
                worker_implemented: false,
                installable: false,
                install_block_reason: "worker_not_implemented",
                estimated_vram_mb: 1,
                installation: { state: "not_installed" },
                benchmark: {},
              },
              {
                id: "expert-enablement-fixture",
                name: "Expert enablement fixture",
                revision: "c".repeat(40),
                repository: "owner/expert-model",
                languages: ["en"],
                tasks: ["asr"],
                enabled: false,
                experimental: false,
                manifest_available: true,
                manifest_sha256: "d".repeat(64),
                estimated_download_bytes: 1,
                installed_size_bytes: 1,
                remote_code_file_count: 0,
                component_source_count: 0,
                component_sources: [],
                worker_implemented: true,
                installable: false,
                install_block_reason: "registry_policy_disabled",
                estimated_vram_mb: 1,
                installation: { state: "not_installed" },
                benchmark: {},
              },
            ]
          : url.endsWith("/recordings")
            ? []
            : {};
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );
    const app = renderApp();
    fireEvent.click(screen.getByRole("button", { name: /模型$/ }));

    const heading = await screen.findByRole("heading", {
      name: "Four-state fixture",
    });
    const card = heading.closest("article");
    expect(card).not.toBeNull();
    const content = within(card as HTMLElement);
    expect(content.getByText("发布 Manifest")).toBeInTheDocument();
    expect(content.getByText("可用")).toBeInTheDocument();
    expect(content.getByText("Worker 实现")).toBeInTheDocument();
    expect(content.getByText("未实现")).toBeInTheDocument();
    expect(content.getByText("普通安装")).toBeInTheDocument();
    expect(content.getByText("已阻塞")).toBeInTheDocument();
    expect(content.getByText("自动候选")).toBeInTheDocument();
    expect(content.getByText("已启用")).toBeInTheDocument();
    expect(
      content.getByText("Manifest SHA-256").nextElementSibling,
    ).toHaveTextContent("b".repeat(64));
    expect(content.getByText("预计下载").nextElementSibling).toHaveTextContent(
      "0.00 GiB",
    );
    expect(content.getByText("安装大小").nextElementSibling).toHaveTextContent(
      "0.00 GiB",
    );
    expect(
      content.getByText("Remote code 文件").nextElementSibling,
    ).toHaveTextContent("2");
    expect(
      content.getByText("安装阻塞原因").nextElementSibling,
    ).toHaveTextContent("当前版本未实现此模型 Worker (worker_not_implemented)");
    expect(content.getByRole("button", { name: "准备安装" })).toBeDisabled();
    expect(app.container.querySelector('input[type="file"]')).toBeNull();

    const expertHeading = screen.getByRole("heading", {
      name: "Expert enablement fixture",
    });
    const expertCard = expertHeading.closest("article");
    expect(expertCard).not.toBeNull();
    const expertContent = within(expertCard as HTMLElement);
    expect(
      expertContent.getByText(
        "此模型只能在独立专家启用流程完成后安装；普通安装不会更改注册表策略。",
      ),
    ).toBeInTheDocument();
    expect(
      expertContent.getByRole("button", { name: "准备安装" }),
    ).toBeDisabled();
  });

  it("requests an install plan by model ID without a client manifest", async () => {
    const installRequests: RequestInit[] = [];
    const confirmRequests: RequestInit[] = [];
    const uploadRequests: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : input.url;
        if (url.endsWith("/models/direct-install-fixture/install/confirm")) {
          confirmRequests.push(init ?? {});
          return Promise.resolve(
            new Response("{}", {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        if (url.endsWith("/models/direct-install-fixture/install")) {
          installRequests.push(init ?? {});
          return Promise.resolve(
            new Response(
              JSON.stringify({
                confirmation_token: "confirmation",
                model_id: "direct-install-fixture",
                revision: "a".repeat(40),
                license_id: "Apache-2.0",
                license_url: "https://example.com/license",
                requires_terms_acceptance: true,
                estimated_download_bytes: 1,
                installed_size_bytes: 1,
                required_free_bytes: 2,
                available_bytes: 3,
                environment: { python: "3.12" },
                component_sources: [
                  {
                    repository: "upstream/component",
                    revision: "e".repeat(40),
                    relationship: "derived",
                    license_id: "CC-BY-4.0",
                    license_url: "https://example.com/component-license",
                    requires_terms_acceptance: false,
                    files: [
                      {
                        installed_path: "config.json",
                        source_path: "source-config.json",
                        source_sha256: "f".repeat(64),
                        source_size_bytes: 1,
                      },
                    ],
                  },
                ],
                expires_at: "2026-09-05T00:00:00Z",
              }),
              { status: 202, headers: { "Content-Type": "application/json" } },
            ),
          );
        }
        if (url.endsWith("/recordings") && init?.method === "POST") {
          uploadRequests.push(init);
          return Promise.resolve(
            new Response(
              JSON.stringify({
                id: "00000000-0000-0000-0000-000000000002",
                source_name: "uploaded-health.wav",
                duration_samples: 1,
                sample_rate: 16_000,
                channels: 1,
                audio_qc: { health_wav_eligible: true },
                media_url:
                  "/api/v1/recordings/00000000-0000-0000-0000-000000000002/media",
              }),
              { status: 201, headers: { "Content-Type": "application/json" } },
            ),
          );
        }
        const body = url.endsWith("/models")
          ? [
              {
                id: "direct-install-fixture",
                name: "Direct install fixture",
                revision: "a".repeat(40),
                repository: "owner/model",
                languages: ["en"],
                tasks: ["alignment"],
                enabled: true,
                experimental: false,
                manifest_available: true,
                manifest_sha256: "b".repeat(64),
                estimated_download_bytes: 1,
                installed_size_bytes: 1,
                remote_code_file_count: 0,
                component_source_count: 1,
                component_sources: [],
                worker_implemented: true,
                installable: true,
                install_block_reason: null,
                estimated_vram_mb: 1,
                installation: { state: "not_installed" },
                benchmark: {},
              },
            ]
          : url.endsWith("/recordings")
            ? [
                {
                  id: "00000000-0000-0000-0000-000000000001",
                  source_name: "health.wav",
                  duration_samples: 3_200,
                  sample_rate: 16_000,
                  channels: 1,
                  audio_qc: { health_wav_eligible: true },
                  media_url:
                    "/api/v1/recordings/00000000-0000-0000-0000-000000000001/media",
                },
              ]
            : {};
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: /模型$/ }));
    fireEvent.click(await screen.findByRole("button", { name: "准备安装" }));

    expect(await screen.findByText("安装确认")).toBeInTheDocument();
    expect(screen.getByText("组件来源与许可证：")).toBeInTheDocument();
    expect(screen.getByText("upstream/component")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CC-BY-4.0" })).toHaveAttribute(
      "href",
      "https://example.com/component-license",
    );
    expect(screen.getByText(`revision ${"e".repeat(40)}`)).toBeInTheDocument();
    expect(installRequests).toHaveLength(1);
    expect(installRequests[0]?.method).toBe("POST");
    expect(installRequests[0]?.body).toBe("{}");

    const healthRecording = screen.getByRole("combobox", {
      name: "健康检查录音",
    });
    expect(
      await screen.findByRole("option", { name: "health.wav · 3200 samples" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("上传健康 WAV"), {
      target: { files: [healthWavFile()] },
    });
    await vi.waitFor(() => {
      expect(healthRecording).toHaveValue(
        "00000000-0000-0000-0000-000000000002",
      );
    });
    expect(uploadRequests).toHaveLength(1);
    expect(
      new Headers(uploadRequests[0]?.headers).get("X-ClassScribe-Filename"),
    ).toBe("uploaded-health.wav");
    expect(screen.queryByText(/\/home\/|\/tmp\//u)).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "录音语言" }), {
      target: { value: "en" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: /精确文字/ }), {
      target: { value: "exact alignment transcript" },
    });
    fireEvent.click(
      screen.getByRole("checkbox", { name: /我已阅读并接受上游模型访问条款/ }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "确认安装并运行推理健康检查" }),
    );

    await vi.waitFor(() => {
      expect(confirmRequests).toHaveLength(1);
    });
    expect(confirmRequests[0]?.method).toBe("POST");
    const confirmBody = confirmRequests[0]?.body;
    expect(typeof confirmBody).toBe("string");
    if (typeof confirmBody !== "string") {
      throw new TypeError("confirm request body must be JSON text");
    }
    expect(JSON.parse(confirmBody)).toEqual({
      confirmation_token: "confirmation",
      health_recording_id: "00000000-0000-0000-0000-000000000002",
      health_language: "en",
      health_transcript: "exact alignment transcript",
      terms_accepted: true,
    });
  });
});
