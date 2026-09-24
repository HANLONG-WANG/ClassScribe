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
  clearCompletedImports: vi.fn(),
  reconcileImports: vi.fn(),
}));
import { UploadPage } from "../src/pages/UploadPage";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  batch.mockReset();
  sessionStorage.clear();
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

it("keeps selected files when clearing the native picker", () => {
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
  let selected: File[] = [new File(["audio"], "lesson.wav")];
  Object.defineProperty(input, "files", {
    configurable: true,
    get: () => selected,
  });
  Object.defineProperty(input, "value", {
    configurable: true,
    get: () => "",
    set: () => {
      selected = [];
    },
  });
  fireEvent.change(input);
  expect(screen.getByText(/lesson\.wav/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "加入转录队列" })).toBeEnabled();
});

it("restores import settings after leaving and returning to the page", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response("[]"))),
  );
  const client = new QueryClient();
  const first = render(
    <QueryClientProvider client={client}>
      <UploadPage />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByRole("combobox", { name: "语言" }), {
    target: { value: "en" },
  });
  fireEvent.click(screen.getByRole("radio", { name: "最高精度" }));
  first.unmount();
  render(
    <QueryClientProvider client={client}>
      <UploadPage />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("combobox", { name: "语言" })).toHaveValue("en");
  expect(screen.getByRole("radio", { name: "最高精度" })).toBeChecked();
});
