import { expect, test } from "@playwright/test";

test("plays real audio within the selected sentence and replaces the stop boundary", async ({
  page,
}) => {
  const samples = 3 * 16000;
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
  const segments = [
    {
      id: "first",
      start_sample: 3200,
      end_sample: 11200,
      text: "First sentence",
    },
    {
      id: "second",
      start_sample: 16000,
      end_sample: 24000,
      text: "Second sentence",
    },
  ].map((item) => ({
    ...item,
    job_id: "playback",
    language: "en",
    raw_text: item.text,
    faithful_text: item.text,
    smart_corrected_text: item.text,
    timing_quality: "native",
    version: 1,
    low_confidence: false,
    tokens: [],
  }));
  await page.addInitScript(() => {
    sessionStorage.setItem("classscribe-current-job", "playback");
    sessionStorage.setItem("classscribe-current-page", "transcript");
  });
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/media"))
      return route.fulfill({ contentType: "audio/wav", body: audio });
    if (url.pathname.endsWith("/transcript"))
      return route.fulfill({ json: { segments } });
    if (url.pathname.includes("/segments/"))
      return route.fulfill({
        json: url.pathname.endsWith("/candidates")
          ? []
          : segments.find((s) => url.pathname.endsWith(s.id)),
      });
    if (url.pathname.includes("/recordings/"))
      return route.fulfill({
        json: {
          id: "clip",
          duration_samples: samples,
          parent_recording_id: "parent",
          source_start_sample: 9600000,
          source_end_sample: 9648000,
        },
      });
    return route.fulfill({
      json: {
        job_id: "playback",
        recording_id: "clip",
        status: "completed",
        options: { provider: "azure_mai" },
      },
    });
  });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "播放", exact: true }),
  ).toBeEnabled();
  await expect(page.getByText(/来源录音片段/)).toContainText("10:00.000");
  const media = page.locator("audio");
  await media.evaluate(
    (node: {
      dataset: Record<string, string | undefined>;
      addEventListener: (name: string, callback: () => void) => void;
    }) => {
      node.dataset.playCount = "0";
      node.addEventListener("play", () => {
        node.dataset.playCount = String(Number(node.dataset.playCount) + 1);
      });
    },
  );
  await page.getByRole("button", { name: /First sentence/ }).click();
  await expect(media).toHaveAttribute("data-play-count", "1");
  await page.getByRole("button", { name: /Second sentence/ }).click();
  await expect(media).toHaveAttribute("data-play-count", "2");
  await expect(
    page.getByRole("button", { name: "播放", exact: true }),
  ).toBeVisible();
  await expect
    .poll(() =>
      media.evaluate((node: { currentTime: number }) => node.currentTime),
    )
    .toBeCloseTo(1.5, 1);
  await page.getByRole("button", { name: /First sentence/ }).click();
  await expect(media).toHaveAttribute("data-play-count", "3");
  await expect(
    page.getByRole("button", { name: "播放", exact: true }),
  ).toBeVisible();
  await expect
    .poll(() =>
      media.evaluate((node: { currentTime: number }) => node.currentTime),
    )
    .toBeCloseTo(0.7, 1);
});
