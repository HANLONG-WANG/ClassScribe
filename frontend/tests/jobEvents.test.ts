import { TextDecoderStream } from "node:stream/web";
import { afterEach, expect, it, vi } from "vitest";
import { streamJobEvents, type PipelineEvent } from "../src/api";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function frame(sequence: number, kind: string) {
  return `data: ${JSON.stringify({ sequence, kind, job_id: "job-1", occurred_at: "", payload: {} })}\n\n`;
}
it("reconnects with the last sequence, drops replayed events and stops after completion", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("TextDecoderStream", TextDecoderStream);
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(frame(5, "activity")))
    .mockResolvedValueOnce(
      new Response(frame(5, "activity") + frame(6, "job_completed")),
    );
  vi.stubGlobal("fetch", fetch);
  const events: PipelineEvent[] = [];
  const connection = vi.fn();
  const controller = new AbortController();
  const done = streamJobEvents(
    "job-1",
    (event) => {
      events.push(event);
      if (event.kind === "job_completed") controller.abort();
    },
    controller.signal,
    connection,
  );
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(1000);
  await done;
  expect(events.map((event) => event.sequence)).toEqual([5, 6]);
  const options = fetch.mock.calls[1]?.[1] as RequestInit;
  expect(new Headers(options.headers).get("Last-Event-ID")).toBe("5");
  expect(connection).toHaveBeenCalledWith(false);
  await vi.advanceTimersByTimeAsync(30000);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("accepts a reset sequence after the server restarts", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("TextDecoderStream", TextDecoderStream);
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(new Response(frame(100, "activity")))
      .mockResolvedValueOnce(
        new Response(frame(0, "stream_reset") + frame(1, "job_completed")),
      ),
  );
  const events: PipelineEvent[] = [];
  const controller = new AbortController();
  const done = streamJobEvents(
    "job-1",
    (event) => {
      events.push(event);
      if (event.kind === "job_completed") controller.abort();
    },
    controller.signal,
  );
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(1000);
  await done;
  expect(events.map((event) => event.sequence)).toEqual([100, 0, 1]);
});

it("does not let a historical terminal event stop a retried task stream", async () => {
  vi.stubGlobal("TextDecoderStream", TextDecoderStream);
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          frame(9, "job_completed") +
            frame(10, "checkpoint_started") +
            frame(11, "activity"),
        ),
      ),
  );
  const controller = new AbortController();
  const events: PipelineEvent[] = [];
  await streamJobEvents(
    "job-1",
    (event) => {
      events.push(event);
      if (event.sequence === 11) controller.abort();
    },
    controller.signal,
  );
  expect(events.map((event) => event.sequence)).toEqual([9, 10, 11]);
});

it("reconnects when an open connection stops delivering heartbeats", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("TextDecoderStream", TextDecoderStream);
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(new ReadableStream()))
    .mockResolvedValueOnce(new Response(frame(1, "activity")));
  vi.stubGlobal("fetch", fetch);
  const controller = new AbortController();
  const connection = vi.fn();
  const done = streamJobEvents(
    "job-1",
    () => {
      controller.abort();
    },
    controller.signal,
    connection,
  );
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(11000);
  await done;
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(connection).toHaveBeenCalledWith(false);
});
