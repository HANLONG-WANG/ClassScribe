import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../src/api";
import type { Job } from "../src/api";
import { JobPage } from "../src/pages/JobPage";
import { useWorkbench } from "../src/store";

beforeEach(() => {
  useWorkbench.setState({ currentJobId: "job-1" });
  vi.spyOn(api, "streamJobEvents").mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useWorkbench.setState({ currentJobId: null });
});
function setup(status: string, stage: string, progress: number) {
  let job: Job = {
    job_id: "job-1",
    recording_id: "recording-1",
    status,
    stage,
    progress,
    options: {},
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(job)))),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <JobPage />
    </QueryClientProvider>,
  );
  return async (next: Partial<Job>) => {
    job = { ...job, ...next };
    await act(() => client.invalidateQueries({ queryKey: ["job", "job-1"] }));
  };
}

it.each([false, true])(
  "requires consent before manually resending an online request (uncertain: %s)",
  async (uncertain) => {
    const update = setup("failed", "transcription", 0.4);
    await screen.findByText("主 ASR", { selector: "strong" });
    await update({
      online_retry: {
        attempt_id: "attempt-1",
        requires_confirmation: true,
        result_uncertain: uncertain,
        retry_at: null,
        retry_after_seconds: 0,
        recovers_saved_response: false,
      },
    });
    await screen.findByRole("button", { name: "重试 MAI 请求" });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetch = vi.mocked(globalThis.fetch);
    fetch.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "重试 MAI 请求" }));
    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining(uncertain ? "结果无法确认" : "再次计费"),
    );
    expect(fetch).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "重试 MAI 请求" }));
    await waitFor(() => {
      expect(fetch).toHaveBeenCalled();
    });
    expect(fetch.mock.calls[0]?.[0]).toContain("/jobs/job-1/retry");
    const body = fetch.mock.calls[0]?.[1]?.body;
    if (typeof body !== "string") throw new Error("missing retry body");
    expect(JSON.parse(body)).toEqual({
      confirm_resend: true,
      expected_attempt_id: "attempt-1",
    });
  },
);

it("enforces retry cooldown locally and enables the button when the wait ends", async () => {
  const update = setup("failed", "transcription", 0.4);
  await screen.findByText("主 ASR", { selector: "strong" });
  vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
  try {
    await update({
      online_retry: {
        attempt_id: "attempt-1",
        requires_confirmation: true,
        result_uncertain: false,
        retry_at: new Date(Date.now() + 3000).toISOString(),
        retry_after_seconds: 3,
        recovers_saved_response: false,
      },
    });
    expect(
      await screen.findByRole("button", { name: /等待.*秒后重试/ }),
    ).toBeDisabled();
    await act(async () => {
      vi.advanceTimersByTime(4000);
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "重试 MAI 请求" })).toBeEnabled();
  } finally {
    vi.useRealTimers();
  }
});

it("shows rejected retry errors and disables duplicate clicks while submitting", async () => {
  setup("failed", "transcription", 0.4);
  await screen.findByText("主 ASR", { selector: "strong" });
  let complete: ((response: Response) => void) | undefined;
  vi.mocked(globalThis.fetch).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "重试失败点" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "重试失败点" })).toBeDisabled(),
  );
  await act(async () => {
    complete?.(
      new Response(JSON.stringify({ detail: "请求记录已变化，请刷新后重试" }), {
        status: 409,
      }),
    );
    await Promise.resolve();
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("请求记录已变化");
});

it("shows persisted upstream error codes, messages, request IDs and the response body", async () => {
  const update = setup("failed", "transcription", 0.4);
  await screen.findByText("主 ASR", { selector: "strong" });
  await update({
    online_error: {
      http_status: 503,
      service_error_code: "diarization_unavailable",
      service_error_message: "Speaker diarization service is unavailable",
      request_id: "azure-request-1",
      retry_after_seconds: 30,
      response_body: '{"error":{"code":"diarization_unavailable"}}',
      response_truncated: false,
      response_read_error: null,
      captured_at: "2026-10-09T03:29:44Z",
    },
  });
  const summary = await screen.findByText("上游错误详情");
  const details = summary.closest("details");
  if (!details) throw new Error("missing diagnostics");
  details.open = true;
  expect(within(details).getByText(/HTTP 503/)).toBeVisible();
  expect(
    within(details).getByText("Speaker diarization service is unavailable"),
  ).toBeVisible();
  expect(within(details).getByText(/azure-request-1/)).toBeVisible();
  expect(within(details).getByText(/服务建议等待 30 秒/)).toBeVisible();
  expect(details.querySelector("pre")?.textContent).toContain(
    "diarization_unavailable",
  );
});
it("shows completed status and marks every pipeline step complete when reopening a finished job", async () => {
  setup("completed", "completed", 1);
  expect(
    await screen.findByText("已完成", { selector: "strong" }),
  ).toBeVisible();
  const progress = screen.getByRole("progressbar", { name: "任务进度" });
  expect(progress).toHaveAttribute("aria-valuenow", "100");
  expect(progress).toHaveAttribute("aria-valuetext", "已完成");
  const steps = within(screen.getByRole("list")).getAllByRole("listitem");
  expect(steps).toHaveLength(10);
  for (const step of steps) {
    expect(step).toHaveClass("done");
    expect(step).not.toHaveClass("active");
    expect(step).not.toHaveAttribute("aria-current");
  }
});
it("updates the current phase when an exporting job becomes complete", async () => {
  const update = setup("running", "export", 0.95);
  expect(await screen.findByText("导出", { selector: "strong" })).toBeVisible();
  expect(screen.getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "95",
  );
  await update({ status: "completed", stage: "completed", progress: 1 });
  expect(
    await screen.findByText("已完成", { selector: "strong" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "打开转录稿" })).toBeEnabled();
});
it.each([
  ["created", "等待调度"],
  ["future_stage", "future_stage"],
])("does not falsely label %s as audio validation", async (stage, label) => {
  setup("pending", stage, 0);
  expect(
    await screen.findByText(label, { selector: ".progress-heading strong" }),
  ).toBeVisible();
  expect(
    screen.queryByText("音频校验", { selector: "strong" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  for (const step of within(screen.getByRole("list")).getAllByRole(
    "listitem",
  )) {
    expect(step).not.toHaveClass("active");
  }
});

it("restores real verification counters and clears the operation on completion", async () => {
  const update = setup("running", "transcription", 0.4);
  await screen.findByText("主 ASR", { selector: "strong" });
  await update({
    activity: {
      run_id: "attempt-1",
      checkpoint_id: "cp-1",
      checkpoint_key: "primary_asr",
      attempt: 1,
      stage: "transcription",
      operation: "verify_model",
      started_at: new Date().toISOString(),
      progress_at: new Date().toISOString(),
      model_id: "qwen3_asr_1_7b",
      completed: 2.1 * 1024 ** 3,
      total: 3.5 * 1024 ** 3,
      unit: "bytes",
      files_completed: 4,
      files_total: 7,
      segment_ordinal: 38,
      segment_total: 126,
    },
  });
  expect(await screen.findByText("正在校验模型文件")).toBeVisible();
  expect(screen.getByText(/已校验 2.10 GiB \/ 3.50 GiB/)).toBeVisible();
  expect(screen.getByRole("progressbar", { name: "任务进度" })).toHaveAttribute(
    "aria-valuenow",
    "40",
  );
  await update({ status: "completed", stage: "completed", progress: 1 });
  expect(await screen.findByText("任务已完成")).toBeVisible();
  expect(screen.queryByText("正在校验模型文件")).not.toBeInTheDocument();
});

it("distinguishes a live model from actual progress and uses no invented percentage", async () => {
  const update = setup("running", "structure", 0.3);
  await screen.findByText("结构", { selector: "strong" });
  await update({
    activity: {
      run_id: "attempt-2",
      checkpoint_id: "cp-2",
      checkpoint_key: "moss_structure",
      attempt: 2,
      stage: "structure",
      operation: "process_audio",
      started_at: new Date(Date.now() - 90000).toISOString(),
      progress_at: new Date(Date.now() - 60000).toISOString(),
      process_checked_at: new Date().toISOString(),
      process_alive: true,
      window_ordinal: 3,
      window_total: 20,
      windows_completed: 2,
    },
  });
  expect(
    await screen.findByText("模型处理中，暂不提供内部百分比"),
  ).toBeVisible();
  expect(screen.getByText("模型进程：存活")).toBeVisible();
  expect(screen.getByText(/没有新的实际进展/)).toBeVisible();
  expect(screen.getAllByRole("progressbar")).toHaveLength(1);
  await update({ status: "paused" });
  expect(await screen.findByText("任务已暂停")).toBeVisible();
  expect(screen.queryByText(/本次操作已运行/)).not.toBeInTheDocument();
});

it("explains model reuse without showing another load or verification percentage", async () => {
  const update = setup("running", "transcription", 0.4);
  await screen.findByText("主 ASR", { selector: "strong" });
  await update({
    activity: {
      run_id: "resident-1",
      checkpoint_id: "cp-38",
      checkpoint_key: "primary_asr",
      attempt: 1,
      stage: "transcription",
      operation: "reuse_model",
      started_at: new Date().toISOString(),
      progress_at: new Date().toISOString(),
      model_id: "qwen3_asr_1_7b",
      device: "cuda:0",
      segment_ordinal: 38,
      segment_total: 126,
    },
  });
  expect(
    await screen.findByRole("heading", { name: "复用已加载模型" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "正在加载模型" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("progressbar", { name: "当前操作进度" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "任务进度" })).toHaveAttribute(
    "aria-valuenow",
    "40",
  );
});
