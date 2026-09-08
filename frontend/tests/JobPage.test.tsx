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
