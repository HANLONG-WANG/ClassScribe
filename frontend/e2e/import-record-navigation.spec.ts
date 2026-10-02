import { expect, test } from "@playwright/test";

test("deletes a single retained record and opens each imported task's own transcript", async ({
  page,
}) => {
  const samples = 16000;
  const audio = Buffer.alloc(44 + samples * 2);
  audio.write("RIFF", 0);
  audio.writeUInt32LE(audio.length - 8, 4);
  audio.write("WAVEfmt ", 8);
  audio.writeUInt32LE(16, 16);
  audio.writeUInt16LE(1, 20);
  audio.writeUInt16LE(1, 22);
  audio.writeUInt32LE(16000, 24);
  audio.writeUInt32LE(32000, 28);
  audio.writeUInt16LE(2, 32);
  audio.writeUInt16LE(16, 34);
  audio.write("data", 36);
  audio.writeUInt32LE(samples * 2, 40);
  const tasks = [
    {
      job_id: "job-active",
      recording_id: "recording-active",
      source_name: "正在转录.wav",
      status: "running",
      text: "正在转录课堂的正文",
    },
    {
      job_id: "job-completed",
      recording_id: "recording-completed",
      source_name: "已完成课堂.wav",
      status: "completed",
      text: "已完成课堂的正文",
    },
    {
      job_id: "job-retained",
      recording_id: "recording-retained",
      source_name: "移除记录.wav",
      status: "pending",
      text: "保留任务的正文",
    },
  ].map((task) => ({
    ...task,
    progress: task.status === "completed" ? 1 : 0.5,
    stage: "asr",
    options: { provider: "local" },
    events: [],
  }));
  const requests: { path: string; method: string }[] = [];
  await page.addInitScript((tasks) => {
    if (!sessionStorage.getItem("classscribe-current-page"))
      sessionStorage.setItem("classscribe-current-page", "upload");
    if (!localStorage.getItem("classscribe-batch-imports-v1"))
      localStorage.setItem(
        "classscribe-batch-imports-v1",
        JSON.stringify(
          tasks.map((task) => ({
            id: task.recording_id,
            name: task.source_name,
            jobId: task.job_id,
            status: "done",
            options: {},
            size: 32044,
            progress: 100,
          })),
        ),
      );
  }, tasks);
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    requests.push({ path, method: route.request().method() });
    if (path === "/models" || path === "/glossaries")
      return route.fulfill({ json: [] });
    if (path === "/queue")
      return route.fulfill({
        json: {
          paused: false,
          items: tasks.filter((task) => task.status !== "completed"),
        },
      });
    if (path.endsWith("/media"))
      return route.fulfill({ contentType: "audio/wav", body: audio });
    const task = tasks.find(
      (task) =>
        path === `/jobs/${task.job_id}` ||
        path === `/jobs/${task.job_id}/transcript` ||
        path === `/recordings/${task.recording_id}`,
    );
    if (task && path.endsWith("/transcript"))
      return route.fulfill({
        json: {
          segments: [
            {
              id: `segment-${task.job_id}`,
              job_id: task.job_id,
              start_sample: 0,
              end_sample: 16000,
              raw_text: task.text,
              faithful_text: task.text,
              smart_corrected_text: task.text,
              auto_final_text: task.text,
              user_text: null,
              language: "zh",
              speaker_id: "speaker",
              speaker_name: "老师",
              timing_quality: "native",
              low_confidence: false,
              review_status: "reviewed",
              version: 1,
              tokens: [],
              audit: [],
            },
          ],
        },
      });
    if (task && path.startsWith("/recordings/"))
      return route.fulfill({
        json: {
          id: task.recording_id,
          source_name: task.source_name,
          duration_samples: samples,
          sample_rate: 16000,
          channels: 1,
          audio_qc: {},
          media_url: `/api/v1/recordings/${task.recording_id}/media`,
        },
      });
    if (task) return route.fulfill({ json: task });
    if (path.includes("/candidates")) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /删除 .* 的导入记录/ }),
  ).toHaveCount(3);
  await page
    .getByRole("button", { name: "删除 移除记录.wav 的导入记录" })
    .click();
  await expect(
    page.getByRole("button", { name: "移除记录.wav", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("button", { name: /删除 .* 的导入记录/ }),
  ).toHaveCount(2);

  await page.getByRole("button", { name: "正在转录.wav", exact: true }).click();
  await expect(page.getByRole("heading", { name: "转录队列" })).toBeVisible();
  const selected = page.locator('article[aria-current="true"]');
  await expect(selected).toContainText("正在转录.wav");
  await expect(selected).toBeFocused();
  await expect(page.getByText("移除记录.wav", { exact: true })).toBeVisible();
  await selected.getByRole("button", { name: "查看转录稿" }).click();
  await expect(
    page.getByText("正在转录课堂的正文", { exact: true }),
  ).toBeVisible();
  expect(
    requests.some((request) => request.path === "/jobs/job-active/transcript"),
  ).toBe(true);

  await page.locator("nav").getByRole("button", { name: /导入/ }).click();
  await page
    .getByRole("button", { name: "已完成课堂.wav", exact: true })
    .click();
  await expect(selected).toContainText("已完成课堂.wav");
  await expect(selected).toContainText("已完成 · 已离开当前队列");
  await selected.getByRole("button", { name: "查看转录稿" }).click();
  await expect(
    page.getByText("已完成课堂的正文", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("正在转录课堂的正文", { exact: true }),
  ).toHaveCount(0);
  expect(
    requests.some(
      (request) => request.path === "/jobs/job-completed/transcript",
    ),
  ).toBe(true);
  expect(requests.some((request) => request.method === "DELETE")).toBe(false);
});
