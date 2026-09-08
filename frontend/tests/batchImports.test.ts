import { afterEach, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
const remote = vi.hoisted(() => vi.fn());
vi.mock("../src/api", () => ({
  api: remote,
  authHeaders: () => ({}),
  filenameHeaders: () => ({}),
}));
import { addImports, retryImport, useBatchImports } from "../src/batchImports";

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
