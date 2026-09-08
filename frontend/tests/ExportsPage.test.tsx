import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ExportsPage } from "../src/pages/ExportsPage";

const manuscript = (id: string, name: string, has = true) => ({
  job_id: id,
  source_name: name,
  duration_samples: 16000,
  language: "ja",
  status: "completed",
  created_at: "2026-09-08T00:00:00Z",
  has_transcript: has,
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("keeps selection across pages and exports each manuscript independently despite a failure", async () => {
  const requests: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        requests.push({
          url,
          body: JSON.parse(
            typeof init.body === "string" ? init.body : "{}",
          ) as unknown,
        });
        if (url.includes("failed"))
          return Promise.resolve(
            new Response(JSON.stringify({ detail: "无法生成该稿件" }), {
              status: 409,
            }),
          );
        return Promise.resolve(
          new Response(
            JSON.stringify({
              id: url,
              file_name: `${url.includes("older") ? "older" : "recent"}.md`,
              format: "md",
              layer: "smart",
              size_bytes: 12,
              sha256: "a".repeat(64),
              download_url: "/download",
            }),
          ),
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            items: url.includes("offset=30")
              ? [manuscript("older", "旧课堂")]
              : [
                  manuscript("recent", "新课堂"),
                  manuscript("failed", "失败课堂"),
                  manuscript("empty", "空课堂", false),
                ],
            total: 31,
          }),
        ),
      );
    }),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ExportsPage />
    </QueryClientProvider>,
  );
  expect(
    screen.getByRole("button", { name: "生成导出（0 份）" }),
  ).toBeDisabled();
  expect(
    await screen.findByRole("checkbox", { name: /空课堂/ }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "全选本页" }));
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: /旧课堂/ }));
  fireEvent.click(screen.getByRole("button", { name: "生成导出（3 份）" }));
  await screen.findByText("导出完成：成功 2 份，失败 1 份。");
  expect(requests.map((r) => r.url)).toEqual([
    "/api/v1/jobs/recent/exports",
    "/api/v1/jobs/failed/exports",
    "/api/v1/jobs/older/exports",
  ]);
  for (const request of requests)
    expect(request.body).toEqual({
      output_format: "md",
      layer: "smart",
      view: "sentences",
      traditional_chinese: false,
    });
  expect(screen.getAllByRole("button", { name: "下载" })).toHaveLength(2);
  expect(screen.getByRole("alert")).toHaveTextContent("失败课堂");
  fireEvent.click(screen.getByRole("button", { name: "上一页" }));
  expect(await screen.findByRole("checkbox", { name: /新课堂/ })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "清空选择" }));
  expect(
    screen.getByRole("button", { name: "生成导出（0 份）" }),
  ).toBeDisabled();
});
