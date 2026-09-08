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
import { QueuePage } from "../src/pages/QueuePage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
