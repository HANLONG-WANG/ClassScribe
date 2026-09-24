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
import { TranscriptsPage } from "../src/pages/TranscriptsPage";
import { useWorkbench } from "../src/store";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it("pages through history and opens the chosen older manuscript with fresh selection", async () => {
  useWorkbench.setState({
    currentJobId: "recent",
    selectedSegmentId: "old-selection",
    lowConfidenceOnly: true,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            items: [
              {
                job_id: url.includes("offset=30") ? "older" : "recent",
                source_name: url.includes("offset=30")
                  ? "旧课堂.mp4"
                  : "新课堂.mp4",
                duration_samples: 16000,
                language: "ja",
                status: "completed",
                created_at: "2026-09-08T00:00:00Z",
              },
            ],
            total: 31,
          }),
        ),
      ),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TranscriptsPage />
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: /新课堂/ });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(await screen.findByRole("button", { name: /旧课堂/ }));
  expect(useWorkbench.getState()).toMatchObject({
    currentJobId: "older",
    page: "transcript",
    selectedSegmentId: null,
    lowConfidenceOnly: false,
  });
});

it("offers scoped cleanup and confirmed task deletion from history", async () => {
  let deleted = false;
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      if (url.endsWith("/local-data")) deleted = true;
      return Promise.resolve(new Response(JSON.stringify({ deleted: true })));
    }
    return Promise.resolve(
      new Response(
        JSON.stringify({
          items: deleted
            ? []
            : [
                {
                  job_id: "job",
                  source_name: "lesson.wav",
                  duration_samples: 16000,
                  language: "ja",
                  status: "completed",
                  created_at: "2026-09-08T00:00:00Z",
                },
              ],
          total: deleted ? 0 : 1,
        }),
      ),
    );
  });
  vi.stubGlobal("fetch", fetch);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TranscriptsPage />
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: /lesson.wav/ });
  fireEvent.click(screen.getByRole("button", { name: "清理派生数据" }));
  await waitFor(() => {
    expect(
      fetch.mock.calls.some(([url]) => url.endsWith("/derived-data")),
    ).toBe(true);
  });
  fireEvent.click(screen.getByRole("button", { name: "删除任务及录音" }));
  await screen.findByText("暂无稿件，请先导入音频创建课堂任务。");
  expect(fetch.mock.calls.some(([url]) => url.endsWith("/local-data"))).toBe(
    true,
  );
});
