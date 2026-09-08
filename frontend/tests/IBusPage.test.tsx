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
import { IBusPage } from "../src/pages/IBusPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function setup(enabled = true) {
  let stage = enabled ? "unloaded" : "disabled";
  const value = () => ({
    enabled,
    stage,
    active: false,
    loaded_models: [],
    message: stage === "queued" ? "后台准备已开始" : "模型尚未加载",
    idle_unload_seconds: 60,
    prewarm_on_startup: false,
  });
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith("/prepare") && init?.method === "POST") stage = "queued";
    if (url.endsWith("/release") && init?.method === "POST") stage = "unloaded";
    return Promise.resolve(
      new Response(JSON.stringify(url.endsWith("/status") ? {} : value())),
    );
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <IBusPage />
    </QueryClientProvider>,
  );
  return fetch;
}
it("shows cold startup and allows explicit prewarm and cancellation", async () => {
  const fetch = setup();
  expect(
    await screen.findByRole("heading", { name: "模型未加载" }),
  ).toBeVisible();
  expect(screen.getByText(/空闲 60/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "预热默认模型" }));
  expect(
    await screen.findByRole("heading", { name: "预热已排队" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "正在准备…" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "取消准备并释放" }));
  await screen.findByRole("heading", { name: "模型未加载" });
  await waitFor(() => {
    expect(
      fetch.mock.calls
        .filter(([, init]) => init?.method === "POST")
        .map(([url]) => url),
    ).toEqual(["/api/v1/ibus/workers/prepare", "/api/v1/ibus/workers/release"]);
  });
});
it("disables prewarming when IBus is off", async () => {
  setup(false);
  await screen.findByRole("heading", { name: "IBus 已关闭" });
  expect(screen.getByRole("button", { name: "预热默认模型" })).toBeDisabled();
});
