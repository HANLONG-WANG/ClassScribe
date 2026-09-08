import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`import progress, manuscript exports and repetition warnings at ${String(width)}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 960 });
    await page.addInitScript(() => {
      localStorage.setItem(
        "classscribe-batch-imports-v1",
        JSON.stringify([
          {
            id: "done",
            name: "日本語授業・ネパールの人口と産業.mp4",
            size: 1024,
            status: "done",
            progress: 100,
            options: {},
          },
          {
            id: "failed",
            name: "课堂录音_第二节_需要重新选择的较长文件名.wav",
            size: 2048,
            status: "error",
            progress: 35,
            options: {},
          },
        ]),
      );
    });
    const manuscript = {
      job_id: "one",
      source_name: "日本語授業・ネパールの人口と産業.mp4",
      duration_samples: 160000,
      status: "completed",
      language: "ja",
      created_at: "2026-09-08T14:15:00Z",
      has_transcript: true,
    };
    const segment = {
      id: "segment",
      job_id: "one",
      start_sample: 0,
      end_sample: 160000,
      language: "ja",
      raw_text: "説明です。説明です。説明です。説明です。",
      faithful_text: "説明です。説明です。説明です。説明です。",
      smart_corrected_text: "説明です。説明です。説明です。説明です。",
      user_text: null,
      quality_score: 0.3,
      low_confidence: true,
      review_status: "needs_review",
      timing_quality: "structure",
      version: 1,
      tokens: [],
      audit: [],
      repetition_warning: {
        all_candidates: true,
        candidates: [
          {
            model_id: "qwen3_asr_0_6b",
            fragment: "説明です",
            issues: ["shortest_loop_period_detected"],
          },
          {
            model_id: "qwen3_asr_1_7b",
            fragment: "説明です",
            issues: ["repeated_sentence"],
          },
        ],
      },
    };
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        "/api/v1",
        "",
      );
      if (path === "/jobs")
        return route.fulfill({
          json: {
            items: [
              manuscript,
              { ...manuscript, job_id: "two", source_name: "第二节课堂.wav" },
            ],
            total: 2,
          },
        });
      if (path.endsWith("/exports"))
        return route.fulfill({
          json: {
            id: path,
            file_name: path.includes("one") ? "one.md" : "two.md",
            format: "md",
            layer: "smart",
            size_bytes: 100,
            sha256: "a".repeat(64),
            download_url: "/download",
          },
        });
      if (path === "/jobs/one")
        return route.fulfill({
          json: {
            job_id: "one",
            recording_id: "recording",
            status: "completed",
            options: {},
          },
        });
      if (path === "/recordings/recording")
        return route.fulfill({ json: { ...manuscript, id: "recording" } });
      if (path === "/jobs/one/transcript")
        return route.fulfill({ json: { segments: [segment] } });
      if (path === "/segments/segment") return route.fulfill({ json: segment });
      if (path.endsWith("/media"))
        return route.fulfill({ status: 404, body: "" });
      return route.fulfill({ json: [] });
    });
    await page.goto("/");
    await expect(
      page.getByRole("button", { name: "查看转录队列" }),
    ).toHaveCount(0);
    await page.locator(".import-progress").scrollIntoViewIfNeeded();
    await expect(page.getByRole("heading", { name: "导入进度" })).toBeVisible();
    await expect(page.getByRole("progressbar")).toHaveCount(2);
    await page.screenshot({
      path: testInfo.outputPath("imports.png"),
      fullPage: true,
    });
    await page
      .getByRole("navigation")
      .getByRole("button", { name: /导出/ })
      .click();
    await expect(
      page.getByRole("button", { name: "生成导出（0 份）" }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "全选本页" }).click();
    await page.getByRole("button", { name: "生成导出（2 份）" }).click();
    await expect(page.getByRole("button", { name: "下载" })).toHaveCount(2);
    await page.screenshot({
      path: testInfo.outputPath("exports.png"),
      fullPage: true,
    });
    await page
      .getByRole("navigation")
      .getByRole("button", { name: /转录稿/ })
      .click();
    await page.getByRole("button", { name: /日本語授業/ }).click();
    await page.locator(".anomaly-tag").click();
    await expect(
      page.getByText("所有可用候选均疑似重复", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/qwen3_asr_0_6b：重复片段/)).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("repetition.png"),
      fullPage: true,
    });
    expect(
      await page.evaluate(() => {
        const browser = globalThis as unknown as {
          document: { documentElement: { scrollWidth: number } };
          innerWidth: number;
        };
        return (
          browser.document.documentElement.scrollWidth <= browser.innerWidth
        );
      }),
    ).toBe(true);
  });
}
