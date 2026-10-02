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
import { MaiSettings } from "../src/components/MaiSettings";
import { UploadPage } from "../src/pages/UploadPage";

const batch = vi.hoisted(() => vi.fn());
vi.mock("../src/batchImports", () => ({
  addImports: batch,
  useBatchImports: () => [],
  retryImport: vi.fn(),
  stopImports: vi.fn(),
  clearCompletedImports: vi.fn(),
  reconcileImports: vi.fn(),
  submitPreparedImport: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
  localStorage.clear();
  batch.mockReset();
});

it("sends credentials only to the backend and clears the input after saving", async () => {
  const fetch = vi.fn((_url: string, init?: RequestInit) =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          configured: init?.method === "PUT",
          endpoint: "https://eastus.api.cognitive.microsoft.com",
          offline: false,
        }),
      ),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  const persist = vi.spyOn(Storage.prototype, "setItem");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MaiSettings />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByLabelText("Azure Speech Endpoint"), {
    target: { value: "https://eastus.api.cognitive.microsoft.com" },
  });
  fireEvent.change(screen.getByLabelText("Azure Speech Key"), {
    target: { value: "private-test-key" },
  });
  fireEvent.click(screen.getByText("保存到本次运行"));
  await waitFor(() =>
    expect(screen.getByLabelText("Azure Speech Key")).toHaveValue(""),
  );
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/online/mai",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({
        endpoint: "https://eastus.api.cognitive.microsoft.com",
        key: "private-test-key",
      }),
    }),
  );
  expect(persist).not.toHaveBeenCalled();
});

it("requires explicit cloud consent and does not send local model options to MAI", () => {
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
  if (!input) throw new Error("missing file input");
  fireEvent.change(input, {
    target: { files: [new File(["audio"], "lesson.wav")] },
  });
  fireEvent.change(screen.getByLabelText("转写服务"), {
    target: { value: "azure_mai" },
  });
  const submit = screen.getByRole("button", { name: "加入转录队列" });
  expect(submit).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /我同意/ }));
  fireEvent.click(submit);
  expect(batch).toHaveBeenCalledWith(
    expect.anything(),
    expect.objectContaining({
      provider: "azure_mai",
      model_selection: "auto_best",
      primary_model_id: null,
      accuracy_mode: "balanced",
    }),
  );
});
