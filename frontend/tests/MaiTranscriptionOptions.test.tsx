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
  batch.mockReset();
  sessionStorage.clear();
  localStorage.clear();
});

function renderUpload() {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            url.endsWith("/glossaries")
              ? [{ id: "course-1", name: "课程术语" }]
              : [],
          ),
        ),
      ),
    ),
  );
  const result = render(
    <QueryClientProvider client={new QueryClient()}>
      <UploadPage />
    </QueryClientProvider>,
  );
  const picker = result.container.querySelector('input[type="file"]');
  if (!picker) throw new Error("missing file picker");
  fireEvent.change(picker, {
    target: { files: [new File(["audio"], "lesson.wav")] },
  });
  fireEvent.change(screen.getByLabelText("转写服务"), {
    target: { value: "azure_mai" },
  });
  return result;
}

it("sends all MAI controls with the chosen glossary and hides the local speaker count", async () => {
  renderUpload();
  expect(screen.queryByLabelText("说话人数")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("语言")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("MAI 转写语言"), {
    target: { value: "fr" },
  });
  fireEvent.change(screen.getByLabelText("转写风格"), {
    target: { value: "clean" },
  });
  fireEvent.change(screen.getByLabelText("时间戳粒度"), {
    target: { value: "segment" },
  });
  fireEvent.change(screen.getByLabelText("脏话处理"), {
    target: { value: "Tags" },
  });
  fireEvent.click(
    screen.getByRole("checkbox", { name: "区分说话人（自动识别）" }),
  );
  fireEvent.change(screen.getByLabelText("额外术语提示"), {
    target: { value: " GPU \nmanual term\nGPU\n" },
  });
  fireEvent.change(screen.getByLabelText("术语提示强度"), {
    target: { value: "1.55" },
  });
  await waitFor(() =>
    expect(
      screen.getByRole("option", { name: "课程术语" }),
    ).toBeInTheDocument(),
  );
  fireEvent.change(screen.getByLabelText("课程词典"), {
    target: { value: "course-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "加入转录队列" }));
  expect(batch).toHaveBeenCalledWith(
    expect.anything(),
    expect.objectContaining({
      provider: "azure_mai",
      language: "auto_mixed",
      glossary_id: "course-1",
      mai_options: {
        locale: "fr",
        transcribe_style: "clean",
        timestamps: "segment",
        diarization: true,
        profanity_filter_mode: "Tags",
        phrases: ["GPU", "manual term"],
        phrase_biasing_weight: 1.55,
      },
    }),
  );
  expect(batch.mock.calls[0]?.[1]).not.toHaveProperty("speaker_count");
});

it("disables subtitle output without timestamps and restores it for local processing", () => {
  renderUpload();
  fireEvent.click(screen.getByRole("checkbox", { name: "SRT" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "VTT" }));
  fireEvent.change(screen.getByLabelText("时间戳粒度"), {
    target: { value: "none" },
  });
  expect(screen.getByRole("checkbox", { name: "SRT" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "SRT" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "VTT" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "加入转录队列" }));
  expect(batch).toHaveBeenCalledWith(
    expect.anything(),
    expect.objectContaining({
      outputs: ["txt", "md"],
      include_subtitles: false,
    }),
  );
  expect(batch.mock.calls[0]?.[1]).toMatchObject({
    mai_options: { timestamps: "none" },
  });
  fireEvent.change(screen.getByLabelText("转写服务"), {
    target: { value: "local" },
  });
  expect(screen.getByLabelText("说话人数")).toBeInTheDocument();
  expect(screen.queryByLabelText("转写风格")).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "SRT" })).toBeEnabled();
  expect(screen.getByRole("checkbox", { name: "SRT" })).toBeChecked();
});

it("defaults to the requested MAI options and TXT/MD exports", () => {
  renderUpload();
  expect(screen.getByLabelText("转写服务")).toHaveValue("azure_mai");
  expect(screen.getByLabelText("课程词典")).toHaveValue("");
  expect(screen.getByLabelText("MAI 转写语言")).toHaveValue("ja");
  expect(screen.getByLabelText("转写风格")).toHaveValue("clean");
  expect(screen.getByLabelText("时间戳粒度")).toHaveValue("word");
  expect(screen.getByLabelText("脏话处理")).toHaveValue("None");
  expect(screen.getByLabelText("额外术语提示")).toHaveValue("");
  expect(screen.getByLabelText("术语提示强度")).toHaveValue(null);
  for (const name of ["TXT", "MD", "忠实版", "智能纠正版", "说话人"]) {
    expect(screen.getByRole("checkbox", { name })).toBeChecked();
  }
  for (const name of ["JSON", "SRT", "VTT", "CSV", "区分说话人（自动识别）"]) {
    expect(screen.getByRole("checkbox", { name })).not.toBeChecked();
  }
  fireEvent.click(screen.getByRole("button", { name: "加入转录队列" }));
  expect(batch.mock.calls[0]?.[1]).toMatchObject({
    language: "ja",
    outputs: ["txt", "md"],
    mai_options: {
      transcribe_style: "clean",
      timestamps: "word",
      diarization: false,
      locale: "ja",
      profanity_filter_mode: "None",
      phrases: [],
      phrase_biasing_weight: null,
    },
  });
});

it("restores MAI settings and keeps them out of local submissions", () => {
  const first = renderUpload();
  fireEvent.change(screen.getByLabelText("转写风格"), {
    target: { value: "clean" },
  });
  fireEvent.change(screen.getByLabelText("MAI 转写语言"), {
    target: { value: "yue" },
  });
  fireEvent.change(screen.getByLabelText("额外术语提示"), {
    target: { value: "课程词\n" },
  });
  first.unmount();
  renderUpload();
  expect(screen.getByLabelText("转写风格")).toHaveValue("clean");
  expect(screen.getByLabelText("MAI 转写语言")).toHaveValue("yue");
  expect(screen.getByLabelText("额外术语提示")).toHaveValue("课程词\n");
  fireEvent.change(screen.getByLabelText("转写服务"), {
    target: { value: "local" },
  });
  fireEvent.click(screen.getByRole("button", { name: "加入转录队列" }));
  expect(batch.mock.calls[0]?.[1]).not.toHaveProperty("mai_options");
  expect(batch.mock.calls[0]?.[1]).toHaveProperty("speaker_count", "auto");
});

it("blocks overlong manual hints before adding an import", () => {
  renderUpload();
  fireEvent.change(screen.getByLabelText("额外术语提示"), {
    target: { value: "x".repeat(201) },
  });
  expect(screen.getByRole("alert")).toBeVisible();
  expect(screen.getByRole("button", { name: "加入转录队列" })).toBeDisabled();
  expect(batch).not.toHaveBeenCalled();
});
