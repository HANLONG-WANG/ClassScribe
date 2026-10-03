import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Segment } from "../src/api";
import { TranscriptPage } from "../src/pages/TranscriptPage";
import { useWorkbench } from "../src/store";

const player = vi.hoisted(() => ({
  create: vi.fn(),
  setTime: vi.fn(),
  play: vi.fn().mockResolvedValue(undefined),
  pause: vi.fn(),
  destroy: vi.fn(),
}));
vi.mock("wavesurfer.js", () => ({ default: { create: player.create } }));

beforeEach(() => {
  player.create.mockImplementation(() => ({
    on: (event: string, callback: () => void) => {
      if (event === "ready") callback();
    },
    getDuration: () => 600,
    setTime: player.setTime,
    play: player.play,
    pause: player.pause,
    destroy: player.destroy,
  }));
  useWorkbench.setState({
    currentJobId: "job",
    selectedSegmentId: null,
    textLayer: "smart",
    lowConfidenceOnly: false,
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function source(id: string, text: string, start = 0, end = 16000): Segment {
  return {
    id,
    job_id: "job",
    start_sample: start,
    end_sample: end,
    speaker_id: "teacher",
    speaker_name: "老师",
    language: "zh",
    raw_text: text,
    faithful_text: text,
    smart_corrected_text: text,
    user_text: null,
    auto_final_text: text,
    quality_score: 0.9,
    low_confidence: false,
    review_status: "accepted",
    timing_quality: "native",
    version: 1,
    tokens: [],
  };
}

function mount(sources: Segment[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            url.includes("/transcript?")
              ? { job_id: "job", segments: sources }
              : url.endsWith("/candidates")
                ? []
                : url.includes("/segments/")
                  ? sources.find((item) => url.endsWith("/" + item.id))
                  : url.includes("/recordings/")
                    ? { id: "recording", duration_samples: 600 * 16000 }
                    : { id: "job", job_id: "job", recording_id: "recording" },
          ),
          { status: 200 },
        ),
      ),
    ),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TranscriptPage />
    </QueryClientProvider>,
  );
}

it("groups consecutive sentences and preserves playback, selection and text-layer changes", async () => {
  const first = source("a", "第一句。");
  const second = source("b", "第二句。", 16000, 32000);
  first.faithful_text = "忠实甲。";
  second.faithful_text = "忠实乙。";
  first.user_text = "";
  mount([first, second]);
  await screen.findByRole("button", { name: /第一句/ });
  fireEvent.click(screen.getByRole("button", { name: "自然段" }));
  expect(document.querySelectorAll(".readable-paragraph")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "第二句。" }));
  await waitFor(() => {
    expect(player.setTime).toHaveBeenLastCalledWith(1);
  });
  expect(player.play).toHaveBeenCalledOnce();
  expect(useWorkbench.getState().selectedSegmentId).toBe("b");
  expect(await screen.findByRole("textbox", { name: "用户文本" })).toHaveValue(
    "第二句。",
  );
  fireEvent.click(screen.getByRole("tab", { name: "忠实" }));
  expect(screen.getByRole("button", { name: "忠实乙。" })).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "用户版" }));
  expect(
    screen.queryByRole("button", { name: "第一句。" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "空句段" })).toBeVisible();
  expect(player.create).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "句段" }));
  expect(screen.getByLabelText("句段列表")).toBeVisible();
  expect(player.destroy).not.toHaveBeenCalled();
});

it("bounds paragraph DOM and keeps a long source's original times and current reading page", async () => {
  const text = Array.from(
    { length: 250 },
    (_, index) => "句子" + String(index) + "。",
  ).join("");
  mount([source("long", text, 30 * 16000, 60 * 16000)]);
  await screen.findByRole("button", { name: /句子0/ });
  fireEvent.click(screen.getByRole("button", { name: "自然段" }));
  expect(document.querySelectorAll(".readable-paragraph")).toHaveLength(40);
  expect(
    document.querySelectorAll(".paragraph-fragment").length,
  ).toBeLessThanOrEqual(240);
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(screen.getByRole("button", { name: "句子241。" }));
  await waitFor(() => {
    expect(player.setTime).toHaveBeenLastCalledWith(30);
  });
  expect(screen.getByText("2 / 2")).toBeVisible();
  expect(useWorkbench.getState().selectedSegmentId).toBe("long");
  expect(screen.getByRole("button", { name: "句子241。" })).toHaveAttribute(
    "title",
    "回听片段 00:30.000 — 01:00.000",
  );
});

it("preserves source gaps when the low-confidence filter is enabled", async () => {
  const sources = [
    source("a", "甲。"),
    source("b", "乙。", 16000, 32000),
    source("c", "丙。", 32000, 48000),
  ];
  sources.forEach((segment, index) => {
    if (index !== 1) segment.low_confidence = true;
  });
  mount(sources);
  await screen.findByRole("button", { name: /甲/ });
  fireEvent.click(screen.getByRole("button", { name: "自然段" }));
  expect(document.querySelectorAll(".readable-paragraph")).toHaveLength(1);
  fireEvent.click(screen.getByRole("checkbox", { name: /仅低置信度/ }));
  expect(document.querySelectorAll(".readable-paragraph")).toHaveLength(2);
  expect(
    screen.queryByRole("button", { name: "乙。" }),
  ).not.toBeInTheDocument();
});
