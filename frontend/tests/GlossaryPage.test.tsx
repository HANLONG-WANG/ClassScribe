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
import { GlossaryPage } from "../src/pages/GlossaryPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function setup(fail = false) {
  let items = [
    { id: "one", name: "词典一", terms: [], version: 1 },
    { id: "two", name: "词典二", terms: [], version: 1 },
  ];
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      if (fail)
        return Promise.resolve(
          new Response(JSON.stringify({ error: { detail: "删除被拒绝" } }), {
            status: 409,
          }),
        );
      items = items.filter((item) => url !== `/api/v1/glossaries/${item.id}`);
      return Promise.resolve(new Response(JSON.stringify({ deleted: true })));
    }
    return Promise.resolve(new Response(JSON.stringify(items)));
  });
  vi.stubGlobal("fetch", fetch);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <GlossaryPage />
    </QueryClientProvider>,
  );
  return { fetch, confirm };
}

it("supports cancellation, selects a remaining glossary, and handles deletion of the last one", async () => {
  const { fetch, confirm } = setup();
  await screen.findByRole("heading", { name: "词典一" });
  confirm.mockReturnValueOnce(false);
  fireEvent.click(screen.getByRole("button", { name: "删除词典" }));
  expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(
    false,
  );
  fireEvent.click(screen.getByRole("button", { name: "删除词典" }));
  await screen.findByRole("heading", { name: "词典二" });
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("词典一"));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "删除词典" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "删除词典" }));
  await screen.findByText("创建第一个课程词典。");
});

it("shows deletion errors and keeps the glossary available", async () => {
  setup(true);
  await screen.findByRole("heading", { name: "词典一" });
  fireEvent.click(screen.getByRole("button", { name: "删除词典" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("删除被拒绝");
  expect(screen.getByRole("heading", { name: "词典一" })).toBeVisible();
  expect(screen.getByRole("button", { name: "删除词典" })).toBeEnabled();
});
