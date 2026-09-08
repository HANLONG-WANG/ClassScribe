import { expect, test } from "@playwright/test";

function wavFixture() {
  const samples = 1600;
  const dataBytes = samples * 2;
  const buffer = Buffer.alloc(44 + dataBytes);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + dataBytes, 4);
  buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(16_000, 24);
  buffer.writeUInt32LE(32_000, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(dataBytes, 40);
  return buffer;
}

test("complete local classroom workflow is operable", async ({ page }) => {
  const wav = wavFixture();
  const writes: string[] = [];
  let segmentVersion = 1;
  let userText: string | null = null;
  let glossaries: Record<string, unknown>[] = [];
  const job = {
    job_id: "job-1",
    recording_id: "recording-1",
    status: "completed",
    stage: "completed",
    progress: 1,
    options: {},
    runtime: {
      model_id: "qwen3_asr_1_7b",
      segment_ordinal: 1,
      realtime_factor: 0.4,
      vram_mb: 4096,
    },
  };
  const segment = () => ({
    id: "segment-1",
    job_id: "job-1",
    start_sample: 0,
    end_sample: 16_000,
    speaker_id: "SPEAKER_01",
    speaker_name: "老师",
    language: "ja",
    raw_text: "自動テキスト",
    faithful_text: "自動テキスト",
    smart_corrected_text: "自动文本",
    user_text: userText,
    auto_final_text: "自动文本",
    quality_score: 0.72,
    low_confidence: true,
    review_status: userText === null ? "needs_review" : "reviewed",
    timing_quality: "aligned",
    version: segmentVersion,
    tokens: [
      {
        id: "token-1",
        start_sample: 0,
        end_sample: 16_000,
        text: "自动文本",
        confidence: 0.72,
        provenance: { model_id: "qwen3_asr_1_7b" },
      },
    ],
    audit: [],
  });
  const models = [
    {
      id: "qwen3_asr_1_7b",
      name: "Qwen3 ASR 1.7B",
      revision: "a".repeat(40),
      repository: "Qwen/Qwen3-ASR-1.7B",
      languages: ["zh", "ja", "en"],
      tasks: ["asr", "streaming"],
      enabled: true,
      experimental: false,
      estimated_vram_mb: 4800,
      installation: {
        state: "installed",
        sha256: "b".repeat(64),
        measured_vram_mb: 4500,
      },
      benchmark: { source: "local_gold" },
    },
  ];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    const method = request.method();
    const json = async (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });

    if (path === "/models" && method === "GET") return json(models);
    if (path === "/glossaries" && method === "GET") return json(glossaries);
    if (path === "/recordings" && method === "POST") {
      writes.push("recording:create");
      return json({
        id: "recording-1",
        source_name: "lesson.wav",
        duration_samples: 1600,
        sample_rate: 16_000,
        channels: 1,
        media_url: "/api/v1/recordings/recording-1/media",
      });
    }
    if (path === "/jobs" && method === "POST") {
      writes.push("job:create");
      return json(job);
    }
    if (path === "/jobs/job-1/events") {
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "",
      });
    }
    if (path === "/jobs/job-1" && method === "GET") return json(job);
    if (path === "/jobs/job-1/transcript" && method === "GET") {
      return json({
        job_id: "job-1",
        timeline: "canonical_16khz",
        segments: [segment()],
      });
    }
    if (path === "/recordings/recording-1/media") {
      return route.fulfill({
        status: 200,
        contentType: "audio/wav",
        body: wav,
      });
    }
    if (path === "/segments/segment-1" && method === "GET")
      return json(segment());
    if (path === "/segments/segment-1/candidates" && method === "GET") {
      return json([
        {
          id: "candidate-1",
          model_id: "qwen3_asr_1_7b",
          model_revision: "a".repeat(40),
          raw_text: "自动文本",
          normalized_text: "自动文本",
          confidence_raw: null,
          confidence_calibrated: 0.72,
          quality: {},
          warnings: [],
          valid: true,
          adopted: true,
        },
      ]);
    }
    if (path === "/segments/segment-1" && method === "PATCH") {
      const body = request.postDataJSON() as { text?: string };
      userText = body.text ?? userText;
      segmentVersion += 1;
      writes.push("segment:patch");
      return json(segment());
    }
    if (path === "/glossaries" && method === "POST") {
      glossaries = [
        {
          id: "glossary-1",
          name: "植物学",
          course_id: null,
          version: 1,
          terms: [],
          materials: [],
        },
      ];
      writes.push("glossary:create");
      return json(glossaries[0]);
    }
    if (path === "/glossaries/glossary-1/terms" && method === "PUT") {
      const glossary = glossaries[0] as {
        terms: Record<string, unknown>[];
        version: number;
      };
      glossary.terms = [
        {
          id: "term-1",
          canonical: "光合作用",
          reading: "こうごうせい",
          aliases: [],
          language: "ja",
          weight: 1,
          source: "manual",
          confirmed: true,
        },
      ];
      glossary.version = 2;
      writes.push("glossary:term");
      return json(glossary);
    }
    if (path === "/models/qwen3_asr_1_7b/verify" && method === "POST") {
      writes.push("model:verify");
      return json({ status: "healthy" });
    }
    if (path === "/jobs/job-1/exports" && method === "POST") {
      writes.push("export:create");
      return json({
        id: "export-1",
        job_id: "job-1",
        format: "md",
        layer: "smart",
        view: "sentences",
        file_name: "lesson.md",
        download_url: "/api/v1/exports/export-1/download",
        sha256: "c".repeat(64),
        size_bytes: 128,
      });
    }
    return json({});
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "导入一堂课" })).toBeVisible();
  await page
    .locator('input[type="file"][accept="audio/*,video/*"]')
    .setInputFiles({
      name: "lesson.wav",
      mimeType: "audio/wav",
      buffer: wav,
    });
  await page.getByRole("button", { name: "开始自动转录" }).click();
  await expect(page.getByRole("heading", { name: "课堂任务" })).toBeVisible();
  await expect(page.getByText("100%")).toBeVisible();
  await page.getByRole("button", { name: "打开转录稿" }).click();
  await expect(page.getByRole("heading", { name: "转录工作台" })).toBeVisible();
  await page.getByRole("button", { name: /自动文本/ }).click();
  const editor = page.getByRole("textbox", { name: "用户文本" });
  await editor.fill("人工校对文本");
  await expect
    .poll(() => writes.filter((item) => item === "segment:patch").length)
    .toBe(1);

  await page.getByRole("button", { name: "词典" }).click();
  await page.getByRole("textbox", { name: "新词典名称" }).fill("植物学");
  await page.getByRole("button", { name: "＋" }).click();
  await expect(page.getByRole("heading", { name: "植物学" })).toBeVisible();
  await page.getByPlaceholder("标准写法").fill("光合作用");
  await page.getByPlaceholder("读音 / reading").fill("こうごうせい");
  await page.getByRole("button", { name: "加入词典" }).click();
  await expect(page.getByText("光合作用", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "模型" }).click();
  await expect(
    page.getByRole("heading", { name: "Qwen3 ASR 1.7B" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "校验模型文件" }).click();
  await expect.poll(() => writes.includes("model:verify")).toBe(true);
  await expect(
    page.getByText("校验通过：当前启用版本的模型文件完整。"),
  ).toBeVisible();

  await page.getByRole("button", { name: "导出" }).click();
  await page.getByRole("button", { name: "生成导出" }).click();
  await expect(page.getByText("lesson.md")).toBeVisible();
  expect(writes).toEqual([
    "recording:create",
    "job:create",
    "segment:patch",
    "glossary:create",
    "glossary:term",
    "model:verify",
    "export:create",
  ]);
});

test("live IBus surface remains available when diagnostics are degraded", async ({
  page,
}) => {
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: { detail: "dictationd unavailable" } }),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: /IBus/ }).click();
  await expect(
    page.getByRole("heading", { name: "IBus 语音输入" }),
  ).toBeVisible();
  await expect(page.getByText("按住说话")).toBeVisible();
});
