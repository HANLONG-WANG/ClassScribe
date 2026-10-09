import { expect, test } from "@playwright/test";

test.use({ timezoneId: "Asia/Tokyo" });

for (const width of [1440, 390]) {
  test(`history date groups and confirmed batch deletion at ${String(width)}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const date = (daysAgo: number) =>
      new Date(Date.now() - daysAgo * 86_400_000).toISOString();
    let items = [
      {
        job_id: "today-1",
        source_name: "日本語授業・今日の講義と授業のまとめ.mp4",
        status: "completed",
        created_at: date(0),
      },
      {
        job_id: "today-2",
        source_name: "课堂录音_第二节_未完成但可以删除的较长文件名.wav",
        status: "failed",
        created_at: date(0),
      },
      {
        job_id: "active",
        source_name: "正在转写的课堂.wav",
        status: "running",
        created_at: date(0),
      },
      {
        job_id: "yesterday",
        source_name: "昨天的课堂.mp3",
        status: "completed",
        created_at: date(1),
      },
      {
        job_id: "older-1",
        source_name: "更早的录音一.wav",
        status: "completed",
        created_at: date(30),
      },
      {
        job_id: "older-2",
        source_name: "更早的录音二.wav",
        status: "cancelled",
        created_at: date(30),
      },
    ].map((item) => ({
      ...item,
      language: "ja",
      duration_samples: 16000 * 60 * 45,
    }));
    const deleted: string[] = [];
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        "/api/v1",
        "",
      );
      if (path === "/jobs")
        return route.fulfill({ json: { items, total: items.length } });
      if (route.request().method() === "DELETE") {
        const id = path.match(/^\/jobs\/([^/]+)\/local-data$/)?.[1];
        if (!id) return route.fulfill({ status: 404, json: {} });
        deleted.push(id);
        items = items.filter((item) => item.job_id !== id);
        return route.fulfill({ json: { deleted: true } });
      }
      return route.fulfill({ json: [] });
    });
    await page.goto("/");
    await page
      .getByRole("navigation")
      .getByRole("button", { name: "转录稿" })
      .click();
    await expect(page.getByRole("heading", { name: /^今天/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /^昨天/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "更久以前" })).toBeVisible();
    await expect(
      page.getByRole("checkbox", { name: "选择 正在转写的课堂.wav" }),
    ).toBeDisabled();
    await page
      .getByRole("checkbox", { name: "选择今天的全部可删除任务" })
      .check();
    await expect(page.getByText("已选择 2 项")).toBeVisible();
    await page.locator(".history-selection-toolbar").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath("history-selection.png"),
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
    const dialogEvent = page.waitForEvent("dialog");
    const click = page
      .getByRole("button", { name: "删除所选任务及录音（2）" })
      .click();
    const dialog = await dialogEvent;
    expect(dialog.message()).toContain("所选 2 个任务");
    expect(dialog.message()).toContain("原文件会保留");
    await dialog.accept();
    await click;
    await expect(
      page.getByText("已删除 2 个任务及不再被引用的录音。"),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /日本語授業/ })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole("button", { name: /正在转写的课堂.wav/ }),
    ).toBeVisible();
    expect(deleted).toEqual(["today-1", "today-2"]);
  });
}
