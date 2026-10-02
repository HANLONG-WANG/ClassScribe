import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QueuePage } from "../src/pages/QueuePage";
import { useWorkbench } from "../src/store";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useWorkbench.setState({ page: "upload", currentJobId: null });
});

it("highlights the chosen queue task and opens the transcript for that exact job", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            paused: false,
            items: [
              {
                job_id: "a",
                source_name: "数学.wav",
                status: "running",
                progress: 0.5,
              },
              {
                job_id: "b",
                source_name: "日语.wav",
                status: "paused",
                progress: 0.2,
              },
            ],
          }),
        ),
      ),
    ),
  );
  useWorkbench.getState().setCurrentJob("b");
  useWorkbench.getState().setPage("queue");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <QueuePage />
    </QueryClientProvider>,
  );
  const name = await screen.findByText("日语.wav");
  const card = name.closest("article");
  if (!card) throw new Error("Missing selected queue card");
  expect(card).toHaveAttribute("aria-current", "true");
  expect(card).toHaveFocus();
  fireEvent.click(within(card).getByRole("button", { name: "查看转录稿" }));
  expect(useWorkbench.getState().currentJobId).toBe("b");
  expect(useWorkbench.getState().page).toBe("transcript");
});

it("locates a completed task outside the active queue and opens its own transcript", async () => {
  const fetcher = vi.fn((path: string) =>
    Promise.resolve(
      new Response(
        JSON.stringify(
          path.endsWith("/queue")
            ? { paused: false, items: [] }
            : path.endsWith("/jobs/archived")
              ? {
                  job_id: "archived",
                  recording_id: "original",
                  status: "completed",
                }
              : { id: "original", source_name: "已完成.wav" },
        ),
      ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  useWorkbench.getState().setCurrentJob("archived");
  useWorkbench.getState().setPage("queue");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <QueuePage />
    </QueryClientProvider>,
  );
  const name = await screen.findByText("已完成.wav");
  const card = name.closest("article");
  if (!card) throw new Error("Missing completed task card");
  expect(card).toHaveAttribute("aria-current", "true");
  expect(within(card).getByText(/已完成 · 已离开当前队列/)).toBeInTheDocument();
  fireEvent.click(within(card).getByRole("button", { name: "查看转录稿" }));
  expect(useWorkbench.getState().currentJobId).toBe("archived");
  expect(useWorkbench.getState().page).toBe("transcript");
  expect(fetcher).toHaveBeenCalledWith(
    "/api/v1/recordings/original",
    expect.anything(),
  );
});
it("shows waiting order and sends persistent pause/reorder controls", async () => {
  const fetcher = vi.fn(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          paused: false,
          items: [
            {
              job_id: "a",
              source_name: "数学.wav",
              status: "pending",
              progress: 0,
            },
            {
              job_id: "b",
              source_name: "日语.wav",
              status: "pending",
              progress: 0,
            },
          ],
        }),
      ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <QueuePage />
    </QueryClientProvider>,
  );
  await screen.findByText("数学.wav");
  const buttons = screen.getAllByRole("button", { name: "上移" });
  expect(buttons[0]).toBeDisabled();
  if (!buttons[1]) throw new Error("missing second course");
  fireEvent.click(buttons[1]);
  await waitFor(() => {
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/queue/reorder",
      expect.objectContaining({
        body: JSON.stringify({ job_ids: ["b", "a"] }),
      }),
    );
  });
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "暂停整个队列" })).toBeEnabled();
  });
  fireEvent.click(screen.getByRole("button", { name: "暂停整个队列" }));
  await waitFor(() => {
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/queue/pause",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
