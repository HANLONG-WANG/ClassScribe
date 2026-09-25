import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TranscriptPage } from "../src/pages/TranscriptPage";
import { useWorkbench } from "../src/store";

const player = vi.hoisted(() => ({
  create: vi.fn(),
  destroy: vi.fn(),
  setTime: vi.fn(),
  playPause: vi.fn().mockResolvedValue(undefined),
  skip: vi.fn(),
  setPlaybackRate: vi.fn(),
}));
vi.mock("wavesurfer.js", () => ({ default: { create: player.create } }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("keeps the player on text-layer changes and seeks on the full recording timeline", async () => {
  player.create.mockImplementation(() => ({
    on: (event: string, callback: () => void) => {
      if (event === "ready") callback();
    },
    getDuration: () => 120,
    setTime: player.setTime,
    destroy: player.destroy,
    playPause: player.playPause,
    skip: player.skip,
    setPlaybackRate: player.setPlaybackRate,
  }));
  useWorkbench.setState({
    currentJobId: "job",
    selectedSegmentId: null,
    textLayer: "smart",
    lowConfidenceOnly: true,
  });
  const segment = {
    id: "s",
    start_sample: 30 * 16000,
    end_sample: 60 * 16000,
    language: "en",
    smart_corrected_text: "Hello",
    faithful_text: "Hello",
    quality_score: 0.5,
    low_confidence: true,
    tokens: [],
    version: 1,
  };
  const other = {
    ...segment,
    id: "other",
    start_sample: 0,
    end_sample: 30 * 16000,
    smart_corrected_text: "Earlier",
    low_confidence: false,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            url.endsWith("/undo") && init?.method === "POST"
              ? { error: { detail: "没有可撤销的历史" } }
              : url.includes("low_confidence_only=true")
                ? { segments: [segment] }
                : url.includes("/transcript?")
                  ? { segments: [other, segment] }
                  : url.endsWith("/candidates")
                    ? [
                        {
                          id: "c",
                          model_id: "asr",
                          model_revision: "a".repeat(40),
                          raw_text: "raw candidate",
                          normalized_text: "Hello",
                          confidence_raw: 0.4,
                          confidence_calibrated: 0.5,
                          decode_config: { beam: 3 },
                          inference_metrics: { latency_ms: 8 },
                          quality: {},
                          warnings: [],
                          valid: true,
                          adopted: false,
                        },
                      ]
                    : url.endsWith("/segments/s")
                      ? segment
                      : { job_id: "job", recording_id: "recording" },
          ),
          { status: url.endsWith("/undo") ? 409 : 200 },
        ),
      ),
    ),
  );
  const view = render(
    <QueryClientProvider client={new QueryClient()}>
      <TranscriptPage />
    </QueryClientProvider>,
  );
  const row = await screen.findByRole("button", { name: /Hello/ });
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(
        ([url]) => typeof url === "string" && url.includes("/transcript?"),
      ),
  ).toHaveLength(1);
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/jobs/job/transcript?include_tokens=false",
    expect.anything(),
  );
  await waitFor(() => {
    expect(player.create).toHaveBeenCalledTimes(1);
  });
  fireEvent.click(screen.getByRole("tab", { name: "忠实" }));
  fireEvent.click(row);
  expect(player.setTime).toHaveBeenCalledWith(30);
  expect(player.create).toHaveBeenCalledTimes(1);
  expect(player.destroy).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "播放" }));
  expect(player.playPause).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "前进 10 秒" }));
  expect(player.skip).toHaveBeenCalledWith(10);
  fireEvent.change(screen.getByRole("combobox", { name: "播放速度" }), {
    target: { value: "1.5" },
  });
  expect(player.setPlaybackRate).toHaveBeenCalledWith(1.5);
  const tracks = document.querySelectorAll(".timeline-track > div > span");
  expect(tracks).toHaveLength(4);
  expect(tracks[1]).toHaveStyle({ left: "25%", width: "25%" });
  expect(
    screen.queryByRole("button", { name: /Earlier/ }),
  ).not.toBeInTheDocument();
  fireEvent.click(await screen.findByText("完整候选证据"));
  expect(screen.getByText(`完整 revision：${"a".repeat(40)}`)).toBeVisible();
  expect(screen.getByText(/raw candidate/)).toBeVisible();
  expect(screen.getByText(/beam/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "撤销" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "没有可撤销的历史",
  );
  fireEvent.click(screen.getByRole("checkbox", { name: /仅低置信度/ }));
  await waitFor(() => {
    expect(player.create).toHaveBeenCalledTimes(1);
  });
  const textarea = await screen.findByRole("textbox", { name: "用户文本" });
  fireEvent.change(textarea, { target: { value: "Saved after leaving" } });
  expect(screen.getByText("等待保存…")).toBeInTheDocument();
  view.unmount();
  await waitFor(() => {
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/segments/s",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          version: 1,
          text: "Saved after leaving",
          layer: "user",
        }),
      }),
    );
  });
});

it("opens a long transcript before loading its recording waveform", async () => {
  player.create.mockImplementation(() => ({
    on: vi.fn(),
    destroy: player.destroy,
  }));
  useWorkbench.setState({
    currentJobId: "long-job",
    selectedSegmentId: null,
    lowConfidenceOnly: false,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            url.includes("/transcript?")
              ? {
                  segments: [
                    {
                      id: "long-segment",
                      start_sample: 0,
                      end_sample: 60 * 60 * 16000,
                      language: "en",
                      smart_corrected_text: "Long recording",
                      faithful_text: "Long recording",
                      quality_score: 0.9,
                      low_confidence: false,
                      tokens: [],
                      version: 1,
                    },
                  ],
                }
              : { job_id: "long-job", recording_id: "recording" },
          ),
          { status: 200 },
        ),
      ),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TranscriptPage />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByRole("button", { name: /Long recording/ }),
  ).toBeVisible();
  expect(player.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "加载音频波形" }));
  await waitFor(() => {
    expect(player.create).toHaveBeenCalledOnce();
  });
});
