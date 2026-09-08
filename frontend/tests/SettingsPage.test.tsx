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
import { SettingsPage } from "../src/pages/SettingsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("loads saved controls and sends only changed groups", async () => {
  const fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) =>
    Promise.resolve(
      new Response(
        JSON.stringify(
          init.method === "PUT"
            ? { retention: { derived_days: 7 }, ibus: { save_audio: true } }
            : {
                retention: { derived_days: 7 },
                ibus: { save_audio: false, other: "keep" },
              },
        ),
      ),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SettingsPage />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByRole("spinbutton")).toHaveValue(7));
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await screen.findByText("设置已保存");
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/settings",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({
        values: { ibus: { save_audio: true, other: "keep" } },
      }),
    }),
  );
});
