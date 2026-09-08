import { expect, test } from "@playwright/test";

test("activity survives refresh and stops on completion at desktop and mobile widths", async ({
  page,
}, testInfo) => {
  const activity = {
    run_id: "run-1",
    checkpoint_id: "checkpoint-1",
    checkpoint_key: "primary_asr",
    attempt: 1,
    stage: "transcription",
    operation: "verify_model",
    started_at: new Date().toISOString(),
    progress_at: new Date().toISOString(),
    model_id: "qwen3_asr_1_7b",
    model_name: "Qwen3 ASR 1.7B",
    device: "auto",
    language: "ja",
    segment_ordinal: 38,
    segment_total: 126,
    start_sample: 1122 * 16000,
    end_sample: 1146 * 16000,
    completed: 2.1 * 1024 ** 3,
    total: 3.5 * 1024 ** 3,
    unit: "bytes",
    files_completed: 4,
    files_total: 7,
  };
  const job = {
    job_id: "job-progress",
    recording_id: "rec-1",
    status: "running",
    stage: "transcription",
    progress: 0.4,
    options: {},
    activity,
    stage_activity: { transcription: [activity] },
    events: [
      {
        sequence: 1,
        job_id: "job-progress",
        kind: "activity",
        occurred_at: new Date().toISOString(),
        payload: activity,
      },
    ],
  };
  await page.addInitScript(() => {
    sessionStorage.setItem("classscribe-current-job", "job-progress");
  });
  await page.route("**/api/v1/jobs/job-progress", (route) =>
    route.fulfill({ json: job }),
  );
  await page.route("**/api/v1/jobs/job-progress/events", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: ": keepalive\n\n",
    }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "正在校验模型文件" }),
  ).toBeVisible();
  await expect(page.getByText(/已校验 2.10 GiB/)).toBeVisible();
  await expect(page.locator("details pre")).not.toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("job-progress-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "正在校验模型文件" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => {
      const browser = globalThis as unknown as {
        document: { documentElement: { scrollWidth: number } };
        innerWidth: number;
      };
      return browser.document.documentElement.scrollWidth <= browser.innerWidth;
    }),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("job-progress-mobile.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(page.getByText(/已校验 2.10 GiB/)).toBeVisible();
  job.status = "completed";
  job.stage = "completed";
  job.progress = 1;
  await expect(page.getByRole("heading", { name: "任务已完成" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "正在校验模型文件" }),
  ).not.toBeVisible();
});
