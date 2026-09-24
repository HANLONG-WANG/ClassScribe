import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  sessionStorage.clear();
  vi.resetModules();
});

it("restores the page visited after a job when the workbench reloads", async () => {
  sessionStorage.clear();
  vi.resetModules();
  const { useWorkbench } = await import("../src/store");
  useWorkbench.getState().setCurrentJob("old-job");
  useWorkbench.getState().setPage("settings");

  vi.resetModules();
  const { useWorkbench: restored } = await import("../src/store");
  expect(restored.getState().page).toBe("settings");
  expect(restored.getState().currentJobId).toBe("old-job");
});
