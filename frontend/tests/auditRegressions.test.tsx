import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { type ModelInfo, type Segment, api, setApiToken } from "../src/api";
import * as apiModule from "../src/api";
import {
  discardTranscriptDraft,
  flushTranscriptSave,
  loadLatestTranscriptDraft,
  saveRebasedTranscriptDraft,
  scheduleTranscriptSave,
  useTranscriptSaves,
} from "../src/transcriptSaves";
import { useWorkbench } from "../src/store";
import { GlossaryPage } from "../src/pages/GlossaryPage";
import { JobPage } from "../src/pages/JobPage";
import { QueuePage } from "../src/pages/QueuePage";
import { SettingsPage } from "../src/pages/SettingsPage";
import { TranscriptPage } from "../src/pages/TranscriptPage";
import { App } from "../src/App";
import { ModelsPage } from "../src/pages/ModelsPage";
import { addImports, stopImports, useBatchImports } from "../src/batchImports";
import { readStorage, writeStorage } from "../src/storage";
vi.mock("wavesurfer.js", () => ({
  default: { create: () => ({ on: () => {}, destroy: () => {} }) },
}));

const client = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });
function mount(node: React.ReactNode, queryClient = client()) {
  return render(
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>,
  );
}
function deferred() {
  let finish!: (response: Response) => void;
  const promise = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  return { promise, finish };
}
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
const segment = { id: "draft", job_id: "job-a", version: 1 } as Segment;
afterEach(() => {
  cleanup();
  for (const id of Object.keys(useTranscriptSaves.getState().drafts))
    discardTranscriptDraft(id, client());
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setApiToken("");
  sessionStorage.clear();
  useBatchImports.setState({ items: [] });
});

it.each([false, true])(
  "keeps input made during a latest-version read, failure=%s",
  async (failure) => {
    useTranscriptSaves.setState({
      drafts: {
        draft: { text: "before", version: 1, status: "error", conflict: true },
      },
    });
    const request = deferred();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => request.promise),
    );
    const pending = loadLatestTranscriptDraft("draft");
    scheduleTranscriptSave(segment, "typed during read", client());
    request.finish(
      json(
        failure
          ? { detail: "offline" }
          : { id: "draft", job_id: "job-a", version: 2, user_text: "server" },
        failure ? 503 : 200,
      ),
    );
    await pending;
    expect(useTranscriptSaves.getState().drafts.draft?.text).toBe(
      "typed during read",
    );
  },
);

it("does not resurrect a discarded draft after a read completes", async () => {
  useTranscriptSaves.setState({
    drafts: {
      draft: { text: "before", version: 1, status: "error", conflict: true },
    },
  });
  const request = deferred();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => request.promise),
  );
  const pending = loadLatestTranscriptDraft("draft");
  discardTranscriptDraft("draft", client());
  request.finish(json({ ...segment, version: 2 }));
  await pending;
  expect(useTranscriptSaves.getState().drafts.draft).toBeUndefined();
});

it("rebases the latest edited text rather than the pre-request snapshot", async () => {
  useTranscriptSaves.setState({
    drafts: {
      draft: {
        text: "before",
        version: 1,
        status: "error",
        conflict: true,
        serverVersion: 2,
      },
    },
  });
  const request = deferred();
  const fetch = vi
    .fn()
    .mockImplementationOnce(() => request.promise)
    .mockResolvedValueOnce(json({ ...segment, version: 3 }));
  vi.stubGlobal("fetch", fetch);
  const pending = saveRebasedTranscriptDraft("draft", client());
  scheduleTranscriptSave(segment, "typed during confirmation", client());
  request.finish(json({ ...segment, version: 2 }));
  await pending;
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/v1/segments/draft",
    expect.objectContaining({
      body: JSON.stringify({
        version: 2,
        text: "typed during confirmation",
        layer: "user",
      }),
    }),
  );
  expect(useTranscriptSaves.getState().drafts.draft).toMatchObject({
    text: "typed during confirmation",
    status: "saved",
    jobId: "job-a",
  });
});

it("ignores a completed save for a discarded draft", async () => {
  vi.useFakeTimers();
  const request = deferred();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => request.promise),
  );
  const queryClient = client();
  scheduleTranscriptSave(segment, "before", queryClient);
  const pending = flushTranscriptSave("draft", queryClient);
  discardTranscriptDraft("draft", queryClient);
  request.finish(json({ ...segment, version: 2 }));
  await pending;
  expect(useTranscriptSaves.getState().drafts.draft).toBeUndefined();
});

it("closes an editor when switching glossaries and changes language with one atomic PATCH", async () => {
  const term = {
    id: "term-a",
    canonical: "term",
    reading: "reading",
    aliases: [],
    language: "ja",
    weight: 1,
    source: "manual",
    confirmed: true,
  };
  const glossaries = [
    { id: "a", name: "Glossary A", version: 1, terms: [term], materials: [] },
    { id: "b", name: "Glossary B", version: 1, terms: [], materials: [] },
  ];
  const writes: { url: string; method: string | undefined; body: unknown }[] =
    [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (init?.method) {
        writes.push({
          url,
          method: init.method,
          body: JSON.parse(
            typeof init.body === "string" ? init.body : "{}",
          ) as unknown,
        });
        return Promise.resolve(
          json({ ...glossaries[0], terms: [{ ...term, language: "en" }] }),
        );
      }
      return Promise.resolve(json(glossaries));
    }),
  );
  mount(<GlossaryPage />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑 term" }));
  fireEvent.click(screen.getByRole("button", { name: /Glossary B/ }));
  expect(
    screen.queryByRole("button", { name: "保存词条" }),
  ).not.toBeInTheDocument();
  expect(writes).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: /Glossary A/ }));
  fireEvent.click(screen.getByRole("button", { name: "编辑 term" }));
  const form = document.querySelector(".term-edit-form") as HTMLElement;
  fireEvent.change(within(form).getByRole("combobox", { name: "语言" }), {
    target: { value: "en" },
  });
  fireEvent.click(within(form).getByRole("button", { name: "保存词条" }));
  await waitFor(() => {
    expect(writes).toHaveLength(1);
  });
  expect(writes[0]).toMatchObject({
    url: "/api/v1/glossaries/a/terms/term-a",
    method: "PATCH",
    body: { language: "en" },
  });
});

it("stores a late control response under the response job identity", async () => {
  vi.spyOn(apiModule, "streamJobEvents").mockResolvedValue(undefined);
  useWorkbench.setState({ currentJobId: "a" });
  const job = (id: string, status: string) => ({
    job_id: id,
    recording_id: id,
    status,
    stage: status,
    progress: 0,
    options: {},
  });
  const request = deferred();
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? request.promise
        : Promise.resolve(
            json(
              job(
                url.endsWith("/a") ? "a" : "b",
                url.endsWith("/a") ? "failed" : "completed",
              ),
            ),
          ),
    ),
  );
  const queryClient = client();
  mount(<JobPage />, queryClient);
  fireEvent.click(await screen.findByRole("button", { name: "重试失败点" }));
  act(() => {
    useWorkbench.setState({ currentJobId: "b" });
  });
  await screen.findByText("b", { selector: ".mono.muted" });
  request.finish(json(job("a", "pending")));
  await waitFor(() => {
    expect(
      queryClient.getQueryData<{ status: string }>(["job", "a"])?.status,
    ).toBe("pending");
  });
  expect(
    queryClient.getQueryData<{ job_id: string }>(["job", "b"])?.job_id,
  ).toBe("b");
});

it.each([true, false])(
  "opens a draft's original job, stored job identity=%s",
  async (storedJob) => {
    vi.stubGlobal("scrollTo", vi.fn());
    useWorkbench.setState({
      currentJobId: "job-b",
      page: "settings",
      selectedSegmentId: null,
    });
    useTranscriptSaves.setState({
      drafts: {
        draft: {
          text: "unsaved",
          version: 1,
          status: "error",
          ...(storedJob ? { jobId: "job-a" } : {}),
        },
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve(
          json(
            url.includes("/transcript?")
              ? { segments: [] }
              : url.endsWith("/candidates")
                ? []
                : url.endsWith("/segments/draft")
                  ? {
                      ...segment,
                      tokens: [],
                      start_sample: 0,
                      end_sample: 16000,
                      smart_corrected_text: "server",
                      speaker_name: null,
                    }
                  : url.endsWith("/jobs/job-a")
                    ? {
                        job_id: "job-a",
                        recording_id: "rec-a",
                        status: "completed",
                        options: {},
                      }
                    : url.endsWith("/recordings/rec-a")
                      ? {
                          duration_samples: 4000 * 16000,
                          source_name: "A recording",
                        }
                      : { retention: { derived_days: 7 } },
          ),
        ),
      ),
    );
    mount(<App />);
    fireEvent.click(screen.getByRole("button", { name: "打开草稿" }));
    await waitFor(() => {
      expect(useWorkbench.getState()).toMatchObject({
        currentJobId: "job-a",
        selectedSegmentId: "draft",
        page: "transcript",
      });
    });
    expect(
      await screen.findByRole("textbox", { name: "用户文本" }),
    ).toHaveValue("unsaved");
  },
);

it("shows fractional queue progress as a percentage", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        json({
          paused: false,
          items: [
            { job_id: "a", source_name: "A", status: "running", progress: 0.4 },
          ],
        }),
      ),
    ),
  );
  mount(<QueuePage />);
  expect(await screen.findByText(/正在转录.*40%/)).toBeInTheDocument();
});

it("bounds DOM size for thousands of segments and scrolls to a restored selection", async () => {
  const segments = Array.from({ length: 4000 }, (_, index) => ({
    ...segment,
    id: `s-${String(index)}`,
    start_sample: index * 16000,
    end_sample: (index + 1) * 16000,
    language: "en",
    tokens: [],
    quality_score: 0.9,
    low_confidence: false,
    smart_corrected_text: `line ${String(index)}`,
    faithful_text: "text",
    speaker_name: null,
  }));
  useWorkbench.setState({
    currentJobId: "job-a",
    selectedSegmentId: null,
    lowConfidenceOnly: false,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        json(
          url.includes("/transcript?")
            ? { segments }
            : url.includes("/segments/")
              ? url.endsWith("/candidates")
                ? []
                : segments[3000]
              : url.includes("/recordings/")
                ? { duration_samples: 4000 * 16000 }
                : { job_id: "job-a", recording_id: "rec" },
        ),
      ),
    ),
  );
  mount(<TranscriptPage />);
  await screen.findByRole("button", { name: /line 0/ });
  expect(document.querySelectorAll(".segment-row").length).toBeLessThan(20);
  expect(
    document.querySelectorAll(".timeline-track span").length,
  ).toBeLessThanOrEqual(512);
  act(() => {
    useWorkbench.setState({ selectedSegmentId: "s-3000" });
  });
  expect(
    await screen.findByRole("button", { name: /line 3000/ }),
  ).toBeInTheDocument();
  expect(document.querySelectorAll(".segment-row").length).toBeLessThan(20);
});

it("times out stalled requests and forwards explicit cancellation", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
    () => new Promise<Response>(() => {}),
  );
  vi.stubGlobal("fetch", fetch);
  const pending = api("/stalled", { timeoutMs: 25 });
  const checked = expect(pending).rejects.toThrow(/超时/);
  await vi.advanceTimersByTimeAsync(25);
  await checked;
  expect(fetch.mock.calls[0]?.[1].signal?.aborted).toBe(true);
  const controller = new AbortController();
  const cancelled = api("/cancelled", { signal: controller.signal });
  const cancelledCheck = expect(cancelled).rejects.toThrow(/取消/);
  controller.abort();
  await cancelledCheck;
});

it("keeps navigation and token application usable when browser storage is blocked", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("blocked", "SecurityError");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("full", "QuotaExceededError");
  });
  writeStorage("fallback-test", "value");
  expect(readStorage("fallback-test")).toBe("value");
  expect(() => {
    setApiToken("temporary-token");
  }).not.toThrow();
  expect(() => {
    useWorkbench.getState().setCurrentJob("fallback-job");
  }).not.toThrow();
  expect(useWorkbench.getState().currentJobId).toBe("fallback-job");
});

it("displays download failures and allows cancelling a stalled diagnostics download", async () => {
  let fail = true;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      url.endsWith("/bundle")
        ? fail
          ? Promise.resolve(json({}, 503))
          : new Promise<Response>(() => {})
        : Promise.resolve(json({ retention: { derived_days: 7 } })),
    ),
  );
  mount(<SettingsPage />);
  fireEvent.click(screen.getByRole("button", { name: "下载脱敏诊断包" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("下载失败");
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "下载脱敏诊断包" }));
  fireEvent.click(await screen.findByRole("button", { name: "取消下载" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("取消");
});

it("cancels a query's underlying fetch when its page unmounts", async () => {
  let signal: AbortSignal | null | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit) => {
      if (url.endsWith("/settings")) {
        signal = init.signal;
        return new Promise<Response>(() => {});
      }
      return Promise.resolve(json({}));
    }),
  );
  const view = mount(<SettingsPage />);
  await waitFor(() => {
    expect(signal).toBeTruthy();
  });
  view.unmount();
  expect(signal?.aborted).toBe(true);
});

it("persists the displayed retention default when saving previously empty settings", async () => {
  const fetch = vi.fn((_url: string, init?: RequestInit) =>
    Promise.resolve(
      json(init?.method === "PUT" ? { retention: { derived_days: 30 } } : {}),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  mount(<SettingsPage />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "保存设置" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await screen.findByText("设置已保存");
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/settings",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ values: { retention: { derived_days: 30 } } }),
    }),
  );
});

it("shows explicit cache cleanup results and failures without deleting model weights", async () => {
  const model: ModelInfo = {
    id: "model",
    name: "Model",
    revision: "a".repeat(40),
    repository: "owner/model",
    languages: ["en"],
    tasks: ["asr"],
    enabled: true,
    experimental: false,
    manifest_available: true,
    manifest_sha256: null,
    estimated_download_bytes: 1,
    installed_size_bytes: 1,
    remote_code_file_count: 0,
    component_source_count: 0,
    component_sources: [],
    worker_implemented: true,
    installable: true,
    install_block_reason: null,
    estimated_vram_mb: 1,
    installation: { state: "healthy" },
    benchmark: {},
    environment_cache: {
      versions: 4,
      damaged: 1,
      size_bytes: 1024 ** 3,
      in_use: 1,
    },
  };
  let fail = false;
  const fetch = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      url.endsWith("/cache/cleanup") && init?.method === "POST"
        ? json(
            fail ? { detail: "busy" } : { removed_count: 2, skipped_in_use: 1 },
            fail ? 409 : 200,
          )
        : json([model]),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  mount(<ModelsPage />);
  await screen.findByRole("heading", { name: "Model" });
  fireEvent.click(screen.getByRole("button", { name: "清理旧运行环境" }));
  expect(await screen.findByText(/已清理 2 个旧环境/)).toHaveTextContent(
    "跳过 1 个使用中的环境",
  );
  expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(
    false,
  );
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "清理旧运行环境" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "缓存清理失败：busy",
  );
});

it("bounds upload lifetime and preserves a timed-out file for retry", async () => {
  const requests: FakeUpload[] = [];
  class FakeUpload {
    upload = { onprogress: null };
    timeout = 0;
    ontimeout: (() => void) | null = null;
    onloadend: (() => void) | null = null;
    onabort: (() => void) | null = null;
    constructor() {
      requests.push(this);
    }
    open() {}
    setRequestHeader() {}
    send() {}
    abort() {
      this.onabort?.();
      this.onloadend?.();
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeUpload);
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(json({ detail: "not found" }, 404))),
  );
  addImports([new File(["audio"], "timeout.wav")], {});
  await waitFor(() => {
    expect(requests).toHaveLength(1);
  });
  expect(requests[0]?.timeout).toBe(30 * 60_000);
  requests[0]?.ontimeout?.();
  requests[0]?.onloadend?.();
  await waitFor(() => {
    expect(useBatchImports.getState().items[0]?.status).toBe("error");
  });
  expect(useBatchImports.getState().items[0]?.error).toContain("超时");
  expect(useBatchImports.getState().items[0]?.file).toBeInstanceOf(File);
});

it("stops an in-flight submission and keeps its stable retry identity", async () => {
  let submissionSignal: AbortSignal | null | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit) => {
      if (init.method === "POST") {
        submissionSignal = init.signal;
        return new Promise<Response>(() => {});
      }
      return Promise.resolve(json({ id: url.split("/").at(-1) }));
    }),
  );
  addImports([new File(["audio"], "cancel.wav")], {});
  await waitFor(() => {
    expect(useBatchImports.getState().items[0]?.status).toBe("submitting");
  });
  const id = useBatchImports.getState().items[0]?.id;
  stopImports();
  await waitFor(() => {
    expect(useBatchImports.getState().items[0]?.status).toBe("error");
  });
  expect(submissionSignal?.aborted).toBe(true);
  expect(useBatchImports.getState().items[0]?.id).toBe(id);
});
