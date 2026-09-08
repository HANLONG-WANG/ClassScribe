import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TranscriptsPage } from "../src/pages/TranscriptsPage";
import { useWorkbench } from "../src/store";

afterEach(() => {
  cleanup();
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
