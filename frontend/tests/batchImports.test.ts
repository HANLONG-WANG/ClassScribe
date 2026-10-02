import { afterEach, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
const remote = vi.hoisted(() => vi.fn());
vi.mock("../src/api", () => ({
  api: remote,
  authHeaders: () => ({}),
  filenameHeaders: () => ({}),
}));
import {
  addImports,
  clearCompletedImports,
  reconcileImports,
  removeImport,
  retryImport,
  useBatchImports,
} from "../src/batchImports";

afterEach(() => {
  remote.mockReset();
  useBatchImports.setState({ items: [] });
  localStorage.clear();
});
it("submits files sequentially, continues after an error, and reuses the submission key", async () => {
  let release: (() => void) | undefined;
  let submissions = 0;
  remote.mockImplementation(async (path: string) => {
    if (path.startsWith("/recordings/")) return { id: path.split("/").at(-1) };
    submissions++;
    if (submissions === 1) {
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      throw new Error("first failed");
    }
    return { job_id: "job" };
  });
  addImports([new File(["a"], "a.wav"), new File(["b"], "b.wav")], {
    language: "ja",
  });
  await waitFor(() => {
    expect(submissions).toBe(1);
  });
  expect(useBatchImports.getState().items[1]?.status).toBe("waiting");
  release?.();
  await waitFor(() => {
    expect(useBatchImports.getState().items.map((item) => item.status)).toEqual(
      ["error", "done"],
    );
  });
  const failed = useBatchImports.getState().items[0];
  if (!failed) throw new Error("missing fixture");
  retryImport(failed.id);
  await waitFor(() => {
    expect(useBatchImports.getState().items[0]?.status).toBe("done");
  });
  const calls = remote.mock.calls.filter(([path]) => path === "/jobs");
  expect(calls[0]?.[1]).toEqual(calls[2]?.[1]);
  expect(localStorage.getItem("classscribe-batch-imports-v1")).not.toContain(
    '"file":',
  );
});

it("removes completed entries missing on the server and allows clearing retained history", async () => {
  useBatchImports.setState({
    items: [
      {
        id: "old",
        name: "old.wav",
        size: 1,
        options: {},
        status: "done",
        progress: 100,
        jobId: "missing",
      },
      {
        id: "new",
        name: "new.wav",
        size: 1,
        options: {},
        status: "done",
        progress: 100,
        jobId: "present",
      },
    ],
  });
  remote.mockImplementation((path: string) => {
    if (path === "/jobs/missing")
      throw Object.assign(new Error("missing"), { status: 404 });
    return Promise.resolve({ job_id: "present" });
  });
  await reconcileImports();
  expect(useBatchImports.getState().items.map((item) => item.id)).toEqual([
    "new",
  ]);
  clearCompletedImports();
  expect(useBatchImports.getState().items).toEqual([]);
});

it("removes only the chosen local record and persists the removal without deleting server data", () => {
  useBatchImports.setState({
    items: [
      {
        id: "remove",
        name: "remove.wav",
        size: 1,
        options: {},
        status: "done",
        progress: 100,
        jobId: "server-job",
      },
      {
        id: "keep",
        name: "keep.wav",
        size: 1,
        options: {},
        status: "prepared",
        progress: 100,
      },
    ],
  });
  removeImport("remove");
  expect(useBatchImports.getState().items.map((item) => item.id)).toEqual([
    "keep",
  ]);
  expect(localStorage.getItem("classscribe-batch-imports-v1")).not.toContain(
    "server-job",
  );
  expect(remote).not.toHaveBeenCalled();
});

it("aborts only a removed active import, rejects its late result and continues the next file", async () => {
  let release: ((value: { id: string }) => void) | undefined;
  let signal: AbortSignal | undefined;
  let held = false;
  remote.mockImplementation((path: string, init?: RequestInit) => {
    if (path.startsWith("/recordings/")) {
      if (!held) {
        held = true;
        signal = init?.signal ?? undefined;
        return new Promise<{ id: string }>((resolve) => {
          release = resolve;
        });
      }
      return Promise.resolve({ id: path.split("/").at(-1) });
    }
    return Promise.resolve({ job_id: "kept-job" });
  });
  addImports([new File(["a"], "a.wav"), new File(["b"], "b.wav")], {
    language: "ja",
  });
  const [first, second] = useBatchImports.getState().items;
  if (!first || !second) throw new Error("Missing imports");
  await waitFor(() => {
    expect(release).toBeDefined();
  });
  removeImport(first.id);
  expect(signal?.aborted).toBe(true);
  release?.({ id: first.id });
  await waitFor(() => {
    expect(useBatchImports.getState().items[0]?.status).toBe("done");
  });
  expect(useBatchImports.getState().items.map((item) => item.id)).toEqual([
    second.id,
  ]);
  const jobs = remote.mock.calls.filter(([path]) => path === "/jobs");
  expect(jobs).toHaveLength(1);
  const request = jobs[0]?.[1] as RequestInit | undefined;
  if (typeof request?.body !== "string")
    throw new Error("Missing JSON request body");
  expect(JSON.parse(request.body) as unknown).toMatchObject({
    recording_id: second.id,
  });
});

it("removes a waiting file without interrupting the current import", async () => {
  let release: ((value: { id: string }) => void) | undefined;
  let signal: AbortSignal | undefined;
  remote.mockImplementation((path: string, init?: RequestInit) => {
    if (path.startsWith("/recordings/")) {
      signal = init?.signal ?? undefined;
      return new Promise<{ id: string }>((resolve) => {
        release = resolve;
      });
    }
    return Promise.resolve({ job_id: "first-job" });
  });
  addImports([new File(["a"], "a.wav"), new File(["b"], "b.wav")], {});
  const [first, second] = useBatchImports.getState().items;
  if (!first || !second) throw new Error("Missing imports");
  removeImport(second.id);
  expect(signal?.aborted).toBe(false);
  release?.({ id: first.id });
  await waitFor(() => {
    expect(useBatchImports.getState().items[0]?.status).toBe("done");
  });
  expect(remote.mock.calls.filter(([path]) => path === "/jobs")).toHaveLength(
    1,
  );
  expect(useBatchImports.getState().items.map((item) => item.id)).toEqual([
    first.id,
  ]);
});
