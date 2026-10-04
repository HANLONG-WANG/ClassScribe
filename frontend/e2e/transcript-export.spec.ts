import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`transcript export settings and visible downloads at ${String(width)}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 844 });
    const manuscript = {
      job_id: "lesson",
      source_name: "日本語授業・ネパールの人口と産業.mp4",
      duration_samples: 160000,
      status: "completed",
      language: "ja",
      created_at: "2026-10-04T00:00:00Z",
      has_transcript: true,
    };
    const segment = {
      id: "segment",
      job_id: "lesson",
      start_sample: 0,
      end_sample: 160000,
      language: "ja",
      raw_text: "授業の内容です。",
      faithful_text: "授業の内容です。",
      smart_corrected_text: "授業の内容です。",
      user_text: "校对后的课堂内容。",
      quality_score: 0.9,
      low_confidence: false,
      review_status: "reviewed",
      timing_quality: "aligned",
      version: 1,
      tokens: [],
      audit: [],
    };
    const exportRequests: Record<string, unknown>[] = [];
    let failExport = true;
    let failDownload = true;
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        "/api/v1",
        "",
      );
      if (path === "/jobs")
        return route.fulfill({
          json: {
            items: Array.from({ length: 30 }, (_, index) => ({
              ...manuscript,
              job_id: index === 0 ? "lesson" : `lesson-${String(index)}`,
              source_name:
                index === 0
                  ? manuscript.source_name
                  : `课堂 ${String(index)}.wav`,
            })),
            total: 30,
          },
        });
      if (path.endsWith("/exports")) {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        exportRequests.push(body);
        if (failExport) {
          failExport = false;
          return route.fulfill({
            status: 503,
            json: { detail: "导出服务暂时不可用" },
          });
        }
        return route.fulfill({
          json: {
            id: `export-${String(exportRequests.length)}`,
            file_name: `lesson.${String(body.output_format)}`,
            format: body.output_format,
            layer: body.layer,
            view: body.view,
            size_bytes: 100,
            sha256: "a".repeat(64),
            download_url: "/api/v1/exports/file/download",
          },
        });
      }
      if (path.endsWith("/download")) {
        if (failDownload) {
          failDownload = false;
          return route.fulfill({ status: 500, body: "下载失败" });
        }
        return route.fulfill({
          contentType: "text/plain",
          body: "校对后的课堂内容。",
        });
      }
      if (path === "/jobs/lesson")
        return route.fulfill({
          json: { ...manuscript, recording_id: "recording", options: {} },
        });
      if (path === "/recordings/recording")
        return route.fulfill({ json: { ...manuscript, id: "recording" } });
      if (path === "/jobs/lesson/transcript")
        return route.fulfill({ json: { segments: [segment] } });
      if (path === "/segments/segment") return route.fulfill({ json: segment });
      if (path.endsWith("/media"))
        return route.fulfill({ status: 404, body: "" });
      return route.fulfill({ json: [] });
    });
    await page.goto("/");
    await expect(page.getByLabel("转写服务")).toHaveValue("azure_mai");
    await page
      .getByRole("navigation")
      .getByRole("button", { name: "转录稿" })
      .click();
    await page.getByRole("button", { name: /日本語授業/ }).click();
    await page.getByRole("tab", { name: "用户版" }).click();
    await page.getByRole("button", { name: "自然段", exact: true }).click();
    const exportButton = page.getByRole("button", {
      name: "导出转录稿",
      exact: true,
    });
    await exportButton.click();
    const dialog = page.getByRole("dialog", { name: "导出转录稿" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("格式")).toBeFocused();
    await expect(dialog.getByLabel("文本版本")).toHaveValue("user");
    await expect(dialog.getByLabel("组织方式")).toHaveValue(
      "readable_paragraphs",
    );
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(exportButton).toBeFocused();
    expect(exportRequests).toHaveLength(0);

    await exportButton.click();
    await dialog.getByLabel("格式").selectOption("txt");
    await dialog.getByRole("button", { name: "生成导出", exact: true }).click();
    await expect(dialog.getByRole("alert")).toHaveText(/导出服务暂时不可用/);
    await dialog.getByRole("button", { name: "生成导出", exact: true }).click();
    const dialogDownload = dialog.getByRole("button", {
      name: "下载",
      exact: true,
    });
    await expect(dialogDownload).toBeInViewport({ ratio: 1 });
    expect(exportRequests[1]).toEqual({
      output_format: "txt",
      layer: "user",
      view: "readable_paragraphs",
      traditional_chinese: false,
    });
    await dialogDownload.click();
    await expect(dialog.getByRole("alert")).toHaveText(/下载失败/);
    const downloadPromise = page.waitForEvent("download");
    await dialogDownload.click();
    expect((await downloadPromise).suggestedFilename()).toBe("lesson.txt");
    await page.screenshot({ path: testInfo.outputPath("export-dialog.png") });
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(dialog).toHaveCount(0);

    await page
      .getByRole("navigation")
      .getByRole("button", { name: "导出", exact: true })
      .click();
    await page.getByRole("checkbox", { name: /日本語授業/ }).check();
    await page.getByRole("checkbox", { name: /课堂 1\.wav/ }).check();
    await page.getByRole("button", { name: "生成导出（2 份）" }).click();
    const results = page.getByRole("region", { name: "导出结果" });
    await expect(
      results.getByRole("button", { name: "下载", exact: true }),
    ).toHaveCount(2);
    await expect(
      results.getByRole("button", { name: "下载", exact: true }).first(),
    ).toBeInViewport({ ratio: 1 });
    const batchDownload = page.waitForEvent("download");
    await results
      .getByRole("button", { name: "下载", exact: true })
      .first()
      .click();
    expect((await batchDownload).suggestedFilename()).toBe("lesson.md");
    await page.screenshot({ path: testInfo.outputPath("export-results.png") });
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
