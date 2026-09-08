import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
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
