import { type QueryClient } from "@tanstack/react-query";
import { create } from "zustand";

import { api, type Segment } from "./api";

interface Draft {
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
    const saved = JSON.parse(
      sessionStorage.getItem(storageKey) ?? "{}",
    ) as Record<string, Draft>;
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
    sessionStorage.setItem(
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
  if (previous?.conflict) {
    update(segment.id, { ...previous, text });
    return;
  }
  update(segment.id, {
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
    const latest = useTranscriptSaves.getState().drafts[id] ?? draft;
    const changed = latest.text !== draft.text;
    update(id, {
      text: latest.text,
      version: saved.version,
      status: changed ? "waiting" : "saved",
    });
    client.setQueryData(["segment", id], saved);
    void client.invalidateQueries({ queryKey: ["transcript"] });
  } catch (error) {
    const latest = useTranscriptSaves.getState().drafts[id] ?? draft;
    update(id, {
      ...latest,
      status: "error",
      error: error instanceof Error ? error.message : "保存失败",
      conflict:
        error instanceof Error && "status" in error && error.status === 409,
    });
  } finally {
    running.delete(id);
  }
  if (
    useTranscriptSaves.getState().drafts[id]?.status === "waiting" &&
    !timers.has(id)
  ) {
    void flushTranscriptSave(id, client);
  }
}

export async function loadLatestTranscriptDraft(id: string) {
  const draft = useTranscriptSaves.getState().drafts[id];
  if (!draft) return;
  try {
    const latest = await api<Segment>(`/segments/${id}`);
    update(id, {
      ...draft,
      conflict: true,
      status: "error",
      serverVersion: latest.version,
      serverText: latest.user_text ?? latest.smart_corrected_text,
      error: "版本冲突：请比较服务器最新版与当前草稿，再选择保存或放弃。",
    });
  } catch (error) {
    update(id, {
      ...draft,
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
  let latest: Segment;
  try {
    latest = await api<Segment>(`/segments/${id}`);
  } catch (error) {
    update(id, {
      ...draft,
      error: error instanceof Error ? error.message : "读取最新版失败",
    });
    return;
  }
  if (latest.version !== draft.serverVersion) {
    update(id, {
      ...draft,
      serverVersion: latest.version,
      serverText: latest.user_text ?? latest.smart_corrected_text,
      error: "服务器内容再次变化，请重新比较后再保存。",
    });
    return;
  }
  update(id, {
    ...draft,
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
