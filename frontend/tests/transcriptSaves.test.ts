import { QueryClient } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";

import { type Segment } from "../src/api";
import {
  scheduleTranscriptSave,
  useTranscriptSaves,
  flushTranscriptSave,
  loadLatestTranscriptDraft,
  saveRebasedTranscriptDraft,
} from "../src/transcriptSaves";

const segment = { id: "a", version: 1 } as Segment;
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  useTranscriptSaves.setState({ drafts: {} });
  sessionStorage.clear();
});

it("keeps pending writes outside page lifetime and targets the original segment", async () => {
  vi.useFakeTimers();
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ ...segment, version: 2 })),
    );
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient();
  scheduleTranscriptSave(segment, "latest edit", client);
  expect(useTranscriptSaves.getState().drafts.a?.status).toBe("waiting");
  await vi.advanceTimersByTimeAsync(700);
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/segments/a",
    expect.objectContaining({
      body: JSON.stringify({ version: 1, text: "latest edit", layer: "user" }),
    }),
  );
  expect(useTranscriptSaves.getState().drafts.a?.status).toBe("saved");
});

it("retains a failed draft for retry", async () => {
  vi.useFakeTimers();
  const fetch = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ ...segment, version: 2 })),
    );
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient();
  scheduleTranscriptSave(segment, "keep me", client);
  await vi.advanceTimersByTimeAsync(700);
  expect(useTranscriptSaves.getState().drafts.a).toMatchObject({
    status: "error",
    text: "keep me",
  });
  await flushTranscriptSave("a", client);
  expect(useTranscriptSaves.getState().drafts.a?.status).toBe("saved");
});

it("serializes edits made during a save using the returned version", async () => {
  vi.useFakeTimers();
  let finish: (value: Response) => void = () => {};
  const fetch = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ ...segment, version: 3 })),
    );
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient();
  scheduleTranscriptSave(segment, "first", client);
  await vi.advanceTimersByTimeAsync(700);
  scheduleTranscriptSave(segment, "second", client);
  await vi.advanceTimersByTimeAsync(700);
  expect(fetch).toHaveBeenCalledTimes(1);
  finish(new Response(JSON.stringify({ ...segment, version: 2 })));
  await vi.advanceTimersByTimeAsync(0);
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/v1/segments/a",
    expect.objectContaining({
      body: JSON.stringify({ version: 2, text: "second", layer: "user" }),
    }),
  );
});

it("restores an unsaved draft after reload and resolves a version conflict explicitly", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.method === "PATCH") {
      if (typeof init.body !== "string") throw new Error("expected JSON body");
      const version = (JSON.parse(init.body) as { version: number }).version;
      return Promise.resolve(
        version === 1
          ? new Response(JSON.stringify({ error: { detail: "版本已变化" } }), {
              status: 409,
            })
          : new Response(JSON.stringify({ ...segment, version: 3 })),
      );
    }
    return Promise.resolve(
      new Response(
        JSON.stringify({ ...segment, version: 2, user_text: "server update" }),
      ),
    );
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient();
  scheduleTranscriptSave(segment, "my unsaved draft", client);
  await vi.advanceTimersByTimeAsync(700);
  expect(useTranscriptSaves.getState().drafts.a).toMatchObject({
    conflict: true,
    text: "my unsaved draft",
  });
  expect(sessionStorage.getItem("classscribe-transcript-drafts-v1")).toContain(
    "my unsaved draft",
  );

  vi.resetModules();
  const { useTranscriptSaves: restored } =
    await import("../src/transcriptSaves");
  expect(restored.getState().drafts.a).toMatchObject({
    status: "error",
    text: "my unsaved draft",
  });

  await loadLatestTranscriptDraft("a");
  expect(useTranscriptSaves.getState().drafts.a).toMatchObject({
    serverVersion: 2,
    serverText: "server update",
  });
  await saveRebasedTranscriptDraft("a", client);
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/v1/segments/a",
    expect.objectContaining({
      method: "PATCH",
      body: JSON.stringify({
        version: 2,
        text: "my unsaved draft",
        layer: "user",
      }),
    }),
  );
  expect(useTranscriptSaves.getState().drafts.a?.status).toBe("saved");
  expect(
    sessionStorage.getItem("classscribe-transcript-drafts-v1"),
  ).not.toContain("my unsaved draft");
});
