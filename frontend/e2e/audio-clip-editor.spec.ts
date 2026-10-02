import { expect, test, type Page } from "@playwright/test";

type MediaState = {
  currentTime: number;
  paused: boolean;
  playbackRate: number;
  volume: number;
  dataset: Record<string, string | undefined>;
  addEventListener: (name: string, callback: () => void) => void;
};

async function prepareAudio(page: Page) {
  const samples = 12 * 16000;
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
  for (let index = 0; index < samples; index++)
    audio.writeInt16LE(
      Math.round(3000 * Math.sin((index * Math.PI) / 40)),
      44 + index * 2,
    );
  const recordings = ["lesson-a", "lesson-b"].map((id) => ({
    id,
    source_name: `${id}.wav`,
    duration_samples: samples,
    sample_rate: 16000,
    channels: 1,
    audio_qc: {},
    media_url: `/api/v1/recordings/${id}/media`,
  }));
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  await page.addInitScript((items) => {
    sessionStorage.setItem("classscribe-current-page", "upload");
    localStorage.setItem(
      "classscribe-batch-imports-v1",
      JSON.stringify(
        items.map((recording) => ({
          id: recording.id,
          name: recording.source_name,
          size: 384044,
          recording,
          status: "prepared",
          progress: 100,
          options: { prepare_only: true, provider: "local" },
        })),
      ),
    );
  }, recordings);
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    if (path.endsWith("/media"))
      return route.fulfill({ contentType: "audio/wav", body: audio });
    if (request.method() === "POST") {
      writes.push({
        path,
        body: request.postDataJSON() as Record<string, unknown>,
      });
      if (path.endsWith("/clips"))
        return route.fulfill({
          json: { ...recordings[0], id: "selected-clip" },
        });
      if (path === "/jobs")
        return route.fulfill({
          json: {
            job_id: "queued-clip",
            recording_id: "selected-clip",
            status: "queued",
          },
        });
    }
    if (path === "/models" || path === "/glossaries")
      return route.fulfill({ json: [] });
    if (path.startsWith("/recordings/"))
      return route.fulfill({
        json:
          recordings.find((item) => path.endsWith(item.id)) ?? recordings[0],
      });
    if (path === "/settings/mai")
      return route.fulfill({
        json: { configured: false, runtime_offline: true },
      });
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  const editors = page.getByRole("group", { name: "音频范围编辑器" });
  await expect(editors).toHaveCount(2);
  await expect(
    editors.nth(0).getByRole("button", { name: "试听选段" }),
  ).toBeEnabled();
  await expect(
    editors.nth(1).getByRole("button", { name: "试听选段" }),
  ).toBeEnabled();
  return { editors, writes };
}

test("drags and resizes a real waveform, zooms and submits the selected sample range", async ({
  page,
}) => {
  const { editors, writes } = await prepareAudio(page);
  const editor = editors.nth(0);
  const waveform = editor.locator(".clip-waveform");
  await waveform.scrollIntoViewIfNeeded();
  const wrapper = waveform.locator('[part="wrapper"]');
  const box = await wrapper.boundingBox();
  if (!box) throw new Error("Missing waveform bounds");
  await page.mouse.move(box.x + box.width * 0.25, box.y + 80);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.5, box.y + 80, { steps: 12 });
  await page.mouse.up();
  await expect(editor.getByLabel("片段开始时间")).toHaveValue(/00:00:03\./);
  await expect(editor.getByLabel("片段结束时间")).toHaveValue(/00:00:06\./);
  await expect(waveform.locator('[part="region selection"]')).toHaveCount(1);
  const handle = waveform.locator('[part="region-handle region-handle-left"]');
  const previousStart = await editor.getByLabel("片段开始时间").inputValue();
  const handleBox = await handle.boundingBox();
  if (!handleBox) throw new Error("Missing A handle");
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + 70);
  await page.mouse.down();
  await page.mouse.move(handleBox.x + 35, handleBox.y + 70, { steps: 8 });
  await page.mouse.up();
  await expect(editor.getByLabel("片段开始时间")).not.toHaveValue(
    previousStart,
  );
  await editor.getByRole("button", { name: "放大选段" }).click();
  await expect
    .poll(() =>
      wrapper.evaluate(
        (node: { getBoundingClientRect: () => { width: number } }) =>
          node.getBoundingClientRect().width,
      ),
    )
    .toBeGreaterThan(box.width * 2);
  await editor.getByRole("button", { name: "全貌" }).click();
  await expect
    .poll(() =>
      wrapper.evaluate(
        (node: { getBoundingClientRect: () => { width: number } }) =>
          node.getBoundingClientRect().width,
      ),
    )
    .toBeCloseTo(box.width, 0);
  await page.mouse.move(box.x + box.width / 2, box.y + 80);
  await page.keyboard.down("Control");
  await page.mouse.wheel(0, -160);
  await page.keyboard.up("Control");
  await expect
    .poll(() =>
      wrapper.evaluate(
        (node: { getBoundingClientRect: () => { width: number } }) =>
          node.getBoundingClientRect().width,
      ),
    )
    .toBeGreaterThan(box.width);
  await editor.getByRole("button", { name: "全貌" }).click();
  await editor.getByLabel("片段开始时间").fill("1.234");
  await editor.getByLabel("片段开始时间").press("Enter");
  await editor.getByLabel("片段结束时间").fill("0:04.567");
  await editor.getByLabel("片段结束时间").press("Enter");
  await editor.screenshot({ path: "/tmp/classscribe-clip-editor-desktop.png" });
  await editor.getByRole("button", { name: "转写选段" }).click();
  await expect
    .poll(() => writes.find((item) => item.path.endsWith("/clips"))?.body)
    .toMatchObject({ start_sample: 19744, end_sample: 73072 });
  await expect
    .poll(() => writes.find((item) => item.path === "/jobs")?.body)
    .toMatchObject({ recording_id: "selected-clip", provider: "local" });
});

test("plays, loops and marks real media, coordinates files and fits a narrow screen", async ({
  page,
}) => {
  const { editors } = await prepareAudio(page);
  const editor = editors.nth(0);
  const media = editor.locator("audio");
  await editor.getByLabel("片段开始时间").fill("0.2");
  await editor.getByLabel("片段结束时间").fill("0.8");
  await editor.getByRole("button", { name: "试听选段" }).click();
  await expect
    .poll(() => media.evaluate((node: MediaState) => node.currentTime))
    .toBeCloseTo(0.8, 2);
  await expect
    .poll(() => media.evaluate((node: MediaState) => node.paused))
    .toBe(true);
  await media.evaluate((node: MediaState) => {
    node.dataset.loops = "0";
    node.addEventListener("seeked", () => {
      if (Math.abs(node.currentTime - 0.2) < 0.1)
        node.dataset.loops = String(Number(node.dataset.loops) + 1);
    });
  });
  await editor.getByLabel("循环").check();
  await editor.getByRole("button", { name: "试听选段" }).click();
  await expect
    .poll(() =>
      media.evaluate((node: MediaState) => Number(node.dataset.loops)),
    )
    .toBeGreaterThanOrEqual(2);
  await editor.getByLabel("试听倍速").selectOption("1.5");
  await expect
    .poll(() => media.evaluate((node: MediaState) => node.playbackRate))
    .toBe(1.5);
  await editor.getByLabel("试听音量").fill("0.5");
  await expect
    .poll(() => media.evaluate((node: MediaState) => node.volume))
    .toBe(0.5);
  await editors
    .nth(1)
    .getByRole("button", { name: "播放", exact: true })
    .click();
  await expect
    .poll(() => media.evaluate((node: MediaState) => node.paused))
    .toBe(true);
  await expect
    .poll(() =>
      editors
        .nth(1)
        .locator("audio")
        .evaluate((node: MediaState) => node.paused),
    )
    .toBe(false);
  await editors
    .nth(1)
    .getByRole("button", { name: "暂停", exact: true })
    .click();
  await editor.getByRole("button", { name: "全选" }).click();
  await editor.focus();
  await editor.press("ArrowRight");
  await editor.press("i");
  await expect(editor.getByLabel("片段开始时间")).toHaveValue(/00:00:05\./);
  await editor.press("ArrowRight");
  await editor.press("o");
  await expect(editor.getByLabel("片段结束时间")).toHaveValue(/00:00:10\./);
  await editor.getByLabel("片段开始时间").focus();
  await editor.getByLabel("片段开始时间").press("Space");
  await expect
    .poll(() => media.evaluate((node: MediaState) => node.paused))
    .toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  await editor.screenshot({ path: "/tmp/classscribe-clip-editor-mobile.png" });
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (
            globalThis as unknown as {
              document: { documentElement: { scrollWidth: number } };
            }
          ).document.documentElement.scrollWidth,
      ),
    )
    .toBeLessThanOrEqual(390);
});
