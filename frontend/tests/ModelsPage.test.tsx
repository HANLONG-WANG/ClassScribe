import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ModelsPage } from "../src/pages/ModelsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup(initialStage = "not_installed") {
  let stage = initialStage;
  let finish: (response: Response) => void = () => {
    throw new Error("No pending install");
  };
  let prepareCount = 0;
  const model = {
    id: "model",
    name: "English model",
    revision: "a".repeat(40),
    languages: ["en"],
    tasks: ["asr"],
    enabled: true,
    experimental: false,
    manifest_available: true,
    worker_implemented: true,
    installable: true,
    install_block_reason: null,
    installation: { state: "not_installed" },
    benchmark: {},
  };
  const json = (value: unknown, status = 200) =>
    new Response(JSON.stringify(value), { status });
  const fetch = vi.fn((url: string) => {
    if (url.endsWith("/install/confirm")) {
      stage = "downloading";
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }
    if (url.endsWith("/install")) {
      prepareCount++;
      return Promise.resolve(
        json({
          confirmation_token: `token-${String(prepareCount)}`,
          model_id: "model",
          supported_languages: ["en"],
          reusing_download: stage === "awaiting_health_check",
          estimated_download_bytes: 0,
          installed_size_bytes: 1,
          required_free_bytes: 1,
          environment: {},
          expires_at: "2026-09-09T00:00:00Z",
          component_sources: [],
          license_id: "Apache-2.0",
          license_url: "https://example.com",
        }),
      );
    }
    if (url.endsWith("/recordings"))
      return Promise.resolve(
        json([
          {
            id: "audio",
            source_name: "health.wav",
            duration_samples: 1600,
            sample_rate: 16000,
            channels: 1,
            audio_qc: { health_wav_eligible: true },
          },
        ]),
      );
    return Promise.resolve(json([{ ...model, install_stage: stage }]));
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ModelsPage />
    </QueryClientProvider>,
  );
  return {
    fetch,
    client,
    setStage: (next: string) => {
      stage = next;
    },
    finish: (success: boolean) => {
      stage = success ? "complete" : "awaiting_health_check";
      finish(
        json(
          success ? { installed: true } : { error: { detail: "依赖安装失败" } },
          success ? 200 : 409,
        ),
      );
    },
  };
}

async function selectAudio() {
  await screen.findByRole("option", { name: "health.wav · 1600 samples" });
  fireEvent.change(screen.getByRole("combobox", { name: "健康检查录音" }), {
    target: { value: "audio" },
  });
}

it("blocks unsupported language and shows progress followed by installed status", async () => {
  const state = setup();
  fireEvent.click(await screen.findByRole("button", { name: "准备安装" }));
  await selectAudio();
  expect(
    screen.getByRole("option", { name: "日语（此模型不支持）" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "确认安装并运行推理健康检查" }),
  ).toBeDisabled();
  expect(
    state.fetch.mock.calls.some(([url]) => url.endsWith("/install/confirm")),
  ).toBe(false);
  fireEvent.change(screen.getByRole("combobox", { name: "录音语言" }), {
    target: { value: "en" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "确认安装并运行推理健康检查" }),
  );
  await waitFor(() => {
    expect(
      state.fetch.mock.calls.some(([url]) => url.endsWith("/install/confirm")),
    ).toBe(true);
  });
  await act(() => state.client.invalidateQueries({ queryKey: ["models"] }));
  expect(
    (await screen.findAllByText("正在下载模型文件…")).length,
  ).toBeGreaterThan(0);
  state.setStage("checking");
  await act(() => state.client.invalidateQueries({ queryKey: ["models"] }));
  expect(
    (await screen.findAllByText(/正在准备 Python 依赖/)).length,
  ).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "正在安装…" })).toBeDisabled();
  await act(async () => {
    state.finish(true);
    await Promise.resolve();
  });
  expect(
    await screen.findByText(
      "English model 安装完成，健康检查已通过，可以开始使用。",
    ),
  ).toBeVisible();
  expect(await screen.findByRole("button", { name: "已安装" })).toBeDisabled();
});

it("offers retry for retained files and reports a failed health check", async () => {
  const state = setup("awaiting_health_check");
  fireEvent.click(await screen.findByRole("button", { name: "重试健康检查" }));
  expect(
    await screen.findByText(
      "已找到保留的模型文件，本次将复用文件并重试健康检查。",
    ),
  ).toBeVisible();
  await selectAudio();
  fireEvent.change(screen.getByRole("combobox", { name: "录音语言" }), {
    target: { value: "en" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "确认安装并运行推理健康检查" }),
  );
  await waitFor(() => {
    expect(
      state.fetch.mock.calls.some(([url]) => url.endsWith("/install/confirm")),
    ).toBe(true);
  });
  await act(async () => {
    state.finish(false);
    await Promise.resolve();
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("依赖安装失败");
  expect(
    screen.getByRole("button", { name: "确认安装并运行推理健康检查" }),
  ).toBeDisabled();
  fireEvent.click(await screen.findByRole("button", { name: "重试健康检查" }));
  await waitFor(() =>
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
  );
  expect(
    state.fetch.mock.calls.filter(([url]) => url.endsWith("/install")),
  ).toHaveLength(2);
});
