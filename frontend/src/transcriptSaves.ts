import { type QueryClient } from "@tanstack/react-query";
import { create } from "zustand";

import { api, type Segment } from "./api";

interface Draft {
  text: string;
  version: number;
  status: "waiting" | "saving" | "saved" | "error";
  error?: string | undefined;
}

export const useTranscriptSaves = create<{ drafts: Record<string, Draft> }>(
  () => ({
    drafts: {},
  }),
);

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
  if (!draft || draft.status === "saved") return;
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
