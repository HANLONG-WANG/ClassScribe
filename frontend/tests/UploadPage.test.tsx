import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
const batch = vi.hoisted(() => vi.fn());
vi.mock("../src/batchImports", () => ({
  addImports: batch,
  useBatchImports: () => [],
  retryImport: vi.fn(),
  stopImports: vi.fn(),
}));
import { UploadPage } from "../src/pages/UploadPage";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  batch.mockReset();
});
it("accepts multiple files, reorders them, and freezes one shared settings snapshot", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response("[]"))),
  );
  const { container } = render(
    <QueryClientProvider client={new QueryClient()}>
      <UploadPage />
    </QueryClientProvider>,
  );
  const input = container.querySelector('input[type="file"]');
  if (!input) throw new Error("missing file picker");
  expect(input).toHaveAttribute("multiple");
  const a = new File(["a"], "math.wav"),
    b = new File(["b"], "japanese.mp4");
  fireEvent.change(input, { target: { files: [a, b] } });
  const up = screen.getAllByRole("button", { name: "上移" })[1];
  if (!up) throw new Error("missing reorder button");
  fireEvent.click(up);
  fireEvent.click(screen.getByRole("button", { name: "加入转录队列" }));
  expect(batch).toHaveBeenCalledWith(
    [b, a],
    expect.objectContaining({
      language: "auto_mixed",
      accuracy_mode: "balanced",
    }),
  );
});
