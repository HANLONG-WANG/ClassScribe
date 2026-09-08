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
}));
vi.mock("wavesurfer.js", () => ({ default: { create: player.create } }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("keeps the player on text-layer changes and seeks on the full recording timeline", async () => {
  player.create.mockImplementation(() => ({
    on: (_event: string, callback: () => void) => {
      callback();
    },
    getDuration: () => 120,
    setTime: player.setTime,
    destroy: player.destroy,
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
    tokens: [],
    version: 1,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            url.includes("/transcript?")
              ? { segments: [segment] }
              : url.endsWith("/candidates")
                ? []
                : url.endsWith("/segments/s")
                  ? segment
                  : { job_id: "job", recording_id: "recording" },
          ),
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
  await waitFor(() => {
    expect(player.create).toHaveBeenCalledTimes(1);
  });
  fireEvent.click(screen.getByRole("tab", { name: "忠实" }));
  fireEvent.click(row);
  expect(player.setTime).toHaveBeenCalledWith(30);
  expect(player.create).toHaveBeenCalledTimes(1);
  expect(player.destroy).not.toHaveBeenCalled();
  const track = document.querySelector(".timeline-track > div > span");
  expect(track).toHaveStyle({ left: "25%", width: "25%" });
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
