import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TranscriptsPage } from "../src/pages/TranscriptsPage";
import { useWorkbench } from "../src/store";
import type { Manuscript } from "../src/manuscripts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function historyItem(id: string, day: Date, status = "completed"): Manuscript {
  return {
    job_id: id,
    source_name: `${id}.wav`,
    duration_samples: 16000,
    language: "ja",
    status,
    created_at: day.toISOString(),
  };
}

function renderHistory(initial: Manuscript[], failIds: string[] = []) {
  let items = [...initial];
  const requests: string[] = [];
  const failures = new Set(failIds);
  const remote = vi.fn((url: string, init?: RequestInit) => {
    const path = new URL(url, "http://localhost");
    if (init?.method === "DELETE") {
      const id = path.pathname.match(/\/jobs\/([^/]+)\/local-data$/)?.[1] ?? "";
      requests.push(id);
      if (failures.delete(id))
        return Promise.resolve(
          new Response(JSON.stringify({ error: { detail: "测试删除失败" } }), {
            status: 409,
          }),
        );
      items = items.filter((item) => item.job_id !== id);
      return Promise.resolve(new Response(JSON.stringify({ deleted: true })));
    }
    const offset = Number(path.searchParams.get("offset") ?? 0);
    const status = path.searchParams.get("status");
    const matching = status
      ? items.filter((item) => item.status === status)
      : items;
    return Promise.resolve(
      new Response(
        JSON.stringify({
          items: matching.slice(offset, offset + 30),
          total: matching.length,
        }),
      ),
    );
  });
  vi.stubGlobal("fetch", remote);
  const confirmation = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TranscriptsPage />
    </QueryClientProvider>,
  );
  return { requests, confirmation, remote };
}

it("filters all history before pagination and clears hidden selections when switching status", async () => {
  const today = new Date();
  const { remote } = renderHistory([
    ...Array.from({ length: 31 }, (_, index) =>
      historyItem(`failed-${String(index)}`, today, "failed"),
    ),
    historyItem("cancelled", today, "cancelled"),
    historyItem("completed", today),
  ]);
  await screen.findByRole("checkbox", { name: "选择 failed-0.wav" });
  fireEvent.click(screen.getByRole("checkbox", { name: "选择 failed-0.wav" }));
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: /completed.wav/ });

  const filter = screen.getByRole("combobox", { name: "转录稿状态" });
  fireEvent.change(filter, { target: { value: "failed" } });
  await screen.findByRole("button", { name: /failed-0.wav/ });
  expect(screen.getByText("第 1 页")).toBeInTheDocument();
  expect(screen.getByText("已选择 0 项")).toBeInTheDocument();
  expect(screen.getByText(/「失败」状态下共 31 份稿件/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /cancelled.wav/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: /failed-30.wav/ });
  expect(screen.queryByRole("button", { name: /completed.wav/ })).toBeNull();
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  expect(
    remote.mock.calls.some(([url]) => {
      const params = new URL(url, "http://localhost").searchParams;
      return params.get("status") === "failed" && params.get("offset") === "30";
    }),
  ).toBe(true);

  fireEvent.change(filter, { target: { value: "cancelled" } });
  await screen.findByRole("button", { name: /cancelled.wav/ });
  expect(screen.getByText(/「已取消」状态下共 1 份稿件/)).toBeInTheDocument();
  expect(screen.getByText("第 1 页")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "选择 cancelled.wav" }));
  fireEvent.change(filter, { target: { value: "paused" } });
  await screen.findByText("没有符合此状态的稿件，可切换状态查看。");
  expect(screen.getByText("已选择 0 项")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "删除所选任务及录音（0）" }),
  ).toBeDisabled();

  fireEvent.change(filter, { target: { value: "" } });
  await screen.findByRole("button", { name: /failed-0.wav/ });
  expect(screen.getByText(/共 33 份稿件/)).toBeInTheDocument();
});

it("keeps the active filter and fixes pagination after deleting the last matching page", async () => {
  const today = new Date();
  const { requests } = renderHistory([
    ...Array.from({ length: 31 }, (_, index) =>
      historyItem(`failed-${String(index)}`, today, "failed"),
    ),
    historyItem("completed", today),
  ]);
  await screen.findByRole("button", { name: /failed-0.wav/ });
  fireEvent.change(screen.getByRole("combobox", { name: "转录稿状态" }), {
    target: { value: "failed" },
  });
  await screen.findByText(/「失败」状态下共 31 份稿件/);
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "选择 failed-30.wav" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "删除所选任务及录音（1）" }),
  );
  await screen.findByRole("button", { name: /failed-0.wav/ });
  expect(screen.getByText("第 1 页")).toBeInTheDocument();
  expect(screen.getByText(/「失败」状态下共 30 份稿件/)).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "转录稿状态" })).toHaveValue(
    "failed",
  );
  expect(screen.queryByRole("button", { name: /completed.wav/ })).toBeNull();
  expect(requests).toEqual(["failed-30"]);
});

it.each([
  ["completed", "已完成"],
  ["running", "处理中"],
  ["pending", "等待处理"],
  ["paused", "已暂停"],
  ["cancelling", "正在取消"],
])("filters the %s state", async (status, label) => {
  renderHistory([
    historyItem("matching", new Date(), status),
    historyItem("other", new Date(), "failed"),
  ]);
  await screen.findByRole("button", { name: /other.wav/ });
  fireEvent.change(screen.getByRole("combobox", { name: "转录稿状态" }), {
    target: { value: status },
  });
  await screen.findByText(new RegExp(`「${label}」状态下共 1 份稿件`));
  expect(
    screen.getByRole("button", { name: /matching.wav/ }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /other.wav/ })).toBeNull();
});

it("selects by day and page, skips active tasks, and honours cancelled batch confirmation", async () => {
  const today = new Date();
  const yesterday = new Date(
    today.getFullYear(),
    today.getMonth(),
    today.getDate() - 1,
    12,
  );
  const { requests, confirmation } = renderHistory([
    historyItem("today-1", today),
    historyItem("today-2", today),
    historyItem("active", today, "running"),
    historyItem("yesterday", yesterday, "failed"),
  ]);
  await screen.findByRole("button", { name: /today-1.wav/ });
  expect(
    screen.getByRole("checkbox", { name: "选择 active.wav" }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", { name: "选择今天的全部可删除任务" }),
  );
  expect(screen.getByText("已选择 2 项")).toBeInTheDocument();
  expect(
    screen.getByRole("checkbox", { name: "选择本页全部可删除任务" }),
  ).toBePartiallyChecked();
  fireEvent.click(
    screen.getByRole("checkbox", { name: "选择本页全部可删除任务" }),
  );
  expect(screen.getByText("已选择 3 项")).toBeInTheDocument();
  confirmation.mockReturnValue(false);
  fireEvent.click(
    screen.getByRole("button", { name: "删除所选任务及录音（3）" }),
  );
  expect(requests).toEqual([]);
  expect(confirmation).toHaveBeenCalledWith(
    expect.stringContaining("所选 3 个任务"),
  );
  confirmation.mockReturnValue(true);
  fireEvent.click(
    screen.getByRole("button", { name: "删除所选任务及录音（3）" }),
  );
  await screen.findByText("已删除 3 个任务及不再被引用的录音。");
  expect(requests).toEqual(["today-1", "today-2", "yesterday"]);
  expect(
    screen.getByRole("button", { name: /active.wav/ }),
  ).toBeInTheDocument();
});

it("preserves selections across pages and returns to a valid page after deletion", async () => {
  const { requests } = renderHistory(
    Array.from({ length: 31 }, (_, index) =>
      historyItem(`job-${String(index)}`, new Date()),
    ),
  );
  await screen.findByRole("checkbox", { name: "选择 job-0.wav" });
  fireEvent.click(screen.getByRole("checkbox", { name: "选择 job-0.wav" }));
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "选择 job-30.wav" }),
  );
  expect(screen.getByText("已选择 2 项")).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "删除所选任务及录音（2）" }),
  );
  await screen.findByText("已删除 2 个任务及不再被引用的录音。");
  await screen.findByRole("button", { name: /job-1.wav/ });
  expect(screen.getByText("第 1 页")).toBeInTheDocument();
  expect(requests).toEqual(["job-0", "job-30"]);
  expect(screen.getByText("已选择 0 项")).toBeInTheDocument();
});

it("continues after a delete failure and keeps only failed tasks selected for retry", async () => {
  const { requests } = renderHistory(
    [historyItem("one", new Date()), historyItem("two", new Date())],
    ["one"],
  );
  await screen.findByRole("checkbox", { name: "选择 one.wav" });
  fireEvent.click(
    screen.getByRole("checkbox", { name: "选择本页全部可删除任务" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "删除所选任务及录音（2）" }),
  );
  await screen.findByText("已删除 1 个任务及不再被引用的录音。");
  expect(requests).toEqual(["one", "two"]);
  expect(screen.getByRole("alert")).toHaveTextContent("one.wav：测试删除失败");
  expect(screen.getByText("已选择 1 项")).toBeInTheDocument();
  const retry = screen.getByRole("button", { name: "删除所选任务及录音（1）" });
  await waitFor(() => expect(retry).toBeEnabled());
  fireEvent.click(retry);
  await screen.findByText("暂无稿件，请先导入音频创建课堂任务。");
  expect(requests).toEqual(["one", "two", "one"]);
});
it("pages through history and opens the chosen older manuscript with fresh selection", async () => {
  useWorkbench.setState({
    currentJobId: "recent",
    selectedSegmentId: "old-selection",
    lowConfidenceOnly: true,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            items: [
              {
                job_id: url.includes("offset=30") ? "older" : "recent",
                source_name: url.includes("offset=30")
                  ? "旧课堂.mp4"
                  : "新课堂.mp4",
                duration_samples: 16000,
                language: "ja",
                status: "completed",
                created_at: "2026-09-08T00:00:00Z",
              },
            ],
            total: 31,
          }),
        ),
      ),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TranscriptsPage />
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: /新课堂/ });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(await screen.findByRole("button", { name: /旧课堂/ }));
  expect(useWorkbench.getState()).toMatchObject({
    currentJobId: "older",
    page: "transcript",
    selectedSegmentId: null,
    lowConfidenceOnly: false,
  });
});

it("offers scoped cleanup and confirmed task deletion from history", async () => {
  let deleted = false;
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      if (url.endsWith("/local-data")) deleted = true;
      return Promise.resolve(new Response(JSON.stringify({ deleted: true })));
    }
    return Promise.resolve(
      new Response(
        JSON.stringify({
          items: deleted
            ? []
            : [
                {
                  job_id: "job",
                  source_name: "lesson.wav",
                  duration_samples: 16000,
                  language: "ja",
                  status: "completed",
                  created_at: "2026-09-08T00:00:00Z",
                },
              ],
          total: deleted ? 0 : 1,
        }),
      ),
    );
  });
  vi.stubGlobal("fetch", fetch);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TranscriptsPage />
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: /lesson.wav/ });
  fireEvent.click(screen.getByRole("button", { name: "清理派生数据" }));
  await waitFor(() => {
    expect(
      fetch.mock.calls.some(([url]) => url.endsWith("/derived-data")),
    ).toBe(true);
  });
  fireEvent.click(screen.getByRole("button", { name: "删除任务及录音" }));
  await screen.findByText("暂无稿件，请先导入音频创建课堂任务。");
  expect(fetch.mock.calls.some(([url]) => url.endsWith("/local-data"))).toBe(
    true,
  );
});
