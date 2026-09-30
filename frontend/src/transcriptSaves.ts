import { type QueryClient } from "@tanstack/react-query";
import { create } from "zustand";

import { api, type Segment } from "./api";
import { readStorage, writeStorage } from "./storage";

interface Draft {
  jobId?: string | undefined;
  text: string;
  version: number;
  status: "waiting" | "saving" | "saved" | "error";
  error?: string | undefined;
  conflict?: boolean | undefined;
  serverVersion?: number | undefined;
  serverText?: string | undefined;
}

const storageKey = "classscribe-transcript-drafts-v1";
function restoreDrafts(): Record<string, Draft> {
  try {
    const saved = JSON.parse(readStorage(storageKey) ?? "{}") as Record<
      string,
      Draft
    >;
    return Object.fromEntries(
      Object.entries(saved)
        .filter(
          ([, draft]) =>
            typeof draft.text === "string" && Number.isInteger(draft.version),
        )
        .map(([id, draft]) => [
          id,
          {
            ...draft,
            status: "error" as const,
            error: draft.error ?? "刷新前的编辑尚未保存，草稿已恢复。",
          },
        ]),
    );
  } catch {
    return {};
  }
}

export const useTranscriptSaves = create<{ drafts: Record<string, Draft> }>(
  () => ({
    drafts: restoreDrafts(),
  }),
);
useTranscriptSaves.subscribe(({ drafts }) => {
  try {
    writeStorage(
      storageKey,
      JSON.stringify(
        Object.fromEntries(
          Object.entries(drafts).filter(
            ([, draft]) => draft.status !== "saved",
          ),
        ),
      ),
    );
  } catch {
    /* Editing remains available when browser storage is full. */
  }
});

const timers = new Map<string, ReturnType<typeof setTimeout>>();
const running = new Set<string>();
const generations = new Map<string, symbol>();

function draftGeneration(id: string) {
  let generation = generations.get(id);
  if (!generation) {
    generation = Symbol(id);
    generations.set(id, generation);
  }
  return generation;
}

function currentDraft(id: string, generation: symbol) {
  return generations.get(id) === generation
    ? useTranscriptSaves.getState().drafts[id]
    : undefined;
}

function update(id: string, draft: Draft) {
  useTranscriptSaves.setState((state) => ({
    drafts: { ...state.drafts, [id]: draft },
  }));
}

export function scheduleTranscriptSave(
  segment: Segment,
  text: string,
  client: QueryClient,
) {
  const previous = useTranscriptSaves.getState().drafts[segment.id];
  if (!previous) generations.set(segment.id, Symbol(segment.id));
  if (previous?.conflict) {
    update(segment.id, { ...previous, text });
    return;
  }
  update(segment.id, {
    jobId: segment.job_id,
    text,
    version:
      previous && previous.status !== "saved"
        ? previous.version
        : Math.max(previous?.version ?? 0, segment.version),
    status: "waiting",
  });
  clearTimeout(timers.get(segment.id));
  timers.set(
    segment.id,
    setTimeout(() => {
      timers.delete(segment.id);
      void flushTranscriptSave(segment.id, client);
    }, 700),
  );
}

export async function flushTranscriptSave(id: string, client: QueryClient) {
  if (running.has(id)) return;
  const draft = useTranscriptSaves.getState().drafts[id];
  if (!draft || draft.status === "saved" || draft.conflict) return;
  clearTimeout(timers.get(id));
  timers.delete(id);
  running.add(id);
  const generation = draftGeneration(id);
  update(id, { ...draft, status: "saving", error: undefined });
  try {
    const saved = await api<Segment>(`/segments/${id}`, {
      method: "PATCH",
      body: JSON.stringify({
        version: draft.version,
        text: draft.text,
        layer: "user",
      }),
    });
    const latest = currentDraft(id, generation);
    if (!latest) return;
    const changed = latest.text !== draft.text;
    update(id, {
      jobId: latest.jobId,
      text: latest.text,
      version: saved.version,
      status: changed ? "waiting" : "saved",
    });
    client.setQueryData(["segment", id], saved);
    void client.invalidateQueries({ queryKey: ["transcript"] });
  } catch (error) {
    const latest = currentDraft(id, generation);
    if (!latest) return;
    update(id, {
      ...latest,
      status: "error",
      error: error instanceof Error ? error.message : "保存失败",
      conflict:
        error instanceof Error && "status" in error && error.status === 409,
    });
  } finally {
    running.delete(id);
    if (
      useTranscriptSaves.getState().drafts[id]?.status === "waiting" &&
      !timers.has(id)
    ) {
      void flushTranscriptSave(id, client);
    }
  }
}

export async function loadLatestTranscriptDraft(id: string) {
  const draft = useTranscriptSaves.getState().drafts[id];
  if (!draft) return;
  const generation = draftGeneration(id);
  try {
    const latest = await api<Segment>(`/segments/${id}`);
    const current = currentDraft(id, generation);
    if (!current) return;
    update(id, {
      ...current,
      jobId: latest.job_id,
      conflict: true,
      status: "error",
      serverVersion: latest.version,
      serverText: latest.user_text ?? latest.smart_corrected_text,
      error: "版本冲突：请比较服务器最新版与当前草稿，再选择保存或放弃。",
    });
  } catch (error) {
    const current = currentDraft(id, generation);
    if (!current) return;
    update(id, {
      ...current,
      status: "error",
      error: error instanceof Error ? error.message : "读取最新版失败",
    });
  }
}

export async function saveRebasedTranscriptDraft(
  id: string,
  client: QueryClient,
) {
  const draft = useTranscriptSaves.getState().drafts[id];
  if (!draft || draft.serverVersion === undefined) return;
  const generation = draftGeneration(id);
  let latest: Segment;
  try {
    latest = await api<Segment>(`/segments/${id}`);
  } catch (error) {
    const current = currentDraft(id, generation);
    if (!current) return;
    update(id, {
      ...current,
      error: error instanceof Error ? error.message : "读取最新版失败",
    });
    return;
  }
  const current = currentDraft(id, generation);
  if (!current || current.serverVersion !== draft.serverVersion) return;
  if (latest.version !== draft.serverVersion) {
    update(id, {
      ...current,
      serverVersion: latest.version,
      serverText: latest.user_text ?? latest.smart_corrected_text,
      error: "服务器内容再次变化，请重新比较后再保存。",
    });
    return;
  }
  update(id, {
    ...current,
    jobId: latest.job_id,
    version: latest.version,
    conflict: false,
    serverVersion: undefined,
    serverText: undefined,
    status: "waiting",
    error: undefined,
  });
  await flushTranscriptSave(id, client);
}

export function discardTranscriptDraft(id: string, client: QueryClient) {
  generations.delete(id);
  clearTimeout(timers.get(id));
  timers.delete(id);
  useTranscriptSaves.setState(({ drafts }) => {
    const next = Object.fromEntries(
      Object.entries(drafts).filter(([key]) => key !== id),
    );
    return { drafts: next };
  });
  void client.invalidateQueries({ queryKey: ["segment", id] });
  void client.invalidateQueries({ queryKey: ["transcript"] });
}
