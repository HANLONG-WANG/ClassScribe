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

vi.mock("wavesurfer.js", () => ({
  default: {
    create: () => ({
      on: vi.fn(),
      getDuration: () => 60,
      setTime: vi.fn(),
      play: vi.fn().mockResolvedValue(undefined),
      pause: vi.fn(),
      destroy: vi.fn(),
      playPause: vi.fn().mockResolvedValue(undefined),
      skip: vi.fn(),
      setPlaybackRate: vi.fn(),
    }),
  },
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const segment = {
  id: "s",
  job_id: "job",
  start_sample: 0,
  end_sample: 16000,
  language: "ja",
  raw_text: "原始文字",
  faithful_text: "忠实文字",
  smart_corrected_text: "智能文字",
  user_text: null,
  quality_score: 0.9,
  low_confidence: false,
  tokens: [],
  version: 1,
};

function serve(provider: string) {
  const job = { status: "completed", running: 0 };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      let body: unknown;
      if (url.endsWith("/segments/s/rerun") && init?.method === "POST") {
        job.status = "running";
        body = { job_id: "job", segment_id: "s", checkpoints_queued: 6 };
      } else if (url.includes("/transcript?")) body = { segments: [segment] };
      else if (url.endsWith("/candidates")) body = [];
      else if (url.endsWith("/segments/s")) body = segment;
      else {
        // The queued job reports running once, then completes.
        if (job.status === "running" && ++job.running > 1)
          job.status = "completed";
        body = {
          job_id: "job",
          recording_id: "recording",
          status: job.status,
          options: { provider },
        };
      }
      return Promise.resolve(new Response(JSON.stringify(body)));
    }),
  );
}

function open(layer: "raw" | "smart") {
  useWorkbench.setState({
    currentJobId: "job",
    selectedSegmentId: null,
    textLayer: layer,
    lowConfidenceOnly: false,
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TranscriptPage />
    </QueryClientProvider>,
  );
}

it("shows MAI transcripts without layer tabs or a local rerun", async () => {
  serve("azure_mai");
  open("raw");
  fireEvent.click(await screen.findByRole("button", { name: /智能文字/ }));
  expect(await screen.findByText("Selected segment")).toBeVisible();
  expect(
    screen.queryByRole("tablist", { name: "文本层" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "重跑当前音频范围" }),
  ).not.toBeInTheDocument();
});

it("reports a local rerun until the job completes and refreshes the text", async () => {
  serve("local");
  open("smart");
  expect(await screen.findByRole("tablist", { name: "文本层" })).toBeVisible();
  fireEvent.click(await screen.findByRole("button", { name: /智能文字/ }));
  fireEvent.click(
    await screen.findByRole("button", { name: "重跑当前音频范围" }),
  );
  expect(
    await screen.findByText("已加入处理队列，正在重新识别这一句…"),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "重跑中…" })).toBeDisabled();
  const transcriptCalls = () =>
    vi
      .mocked(fetch)
      .mock.calls.filter(
        ([url]) => typeof url === "string" && url.includes("/transcript?"),
      ).length;
  const before = transcriptCalls();
  expect(
    await screen.findByText(
      "重跑完成，已更新这一句的识别结果。",
      {},
      { timeout: 5000 },
    ),
  ).toBeVisible();
  await waitFor(() => {
    expect(transcriptCalls()).toBeGreaterThan(before);
  });
  expect(
    screen.getByRole("button", { name: "重跑当前音频范围" }),
  ).toBeEnabled();
});
