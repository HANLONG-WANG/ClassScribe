import { create } from "zustand";
import {
  api,
  authHeaders,
  filenameHeaders,
  type ApiObject,
  type Job,
  type Recording,
} from "./api";

export interface ImportItem {
  id: string;
  name: string;
  size: number;
  file?: File | undefined;
  options: ApiObject;
  status: "waiting" | "uploading" | "submitting" | "done" | "error";
  progress: number;
  error?: string | undefined;
  jobId?: string;
}
const storageKey = "classscribe-batch-imports-v1";
function restored(): ImportItem[] {
  try {
    const saved = JSON.parse(
      localStorage.getItem(storageKey) ?? "[]",
    ) as ImportItem[];
    return saved.map((item) => ({
      ...item,
      file: undefined,
      status: item.status === "done" ? "done" : "error",
      error:
        item.status === "done"
          ? undefined
          : "导入尚未完成，点击重试；未上传的文件需要重新选择。",
    }));
  } catch {
    return [];
  }
}
export const useBatchImports = create<{ items: ImportItem[] }>()(() => ({
  items: restored(),
}));
useBatchImports.subscribe(({ items }) => {
  try {
    localStorage.setItem(
      storageKey,
      JSON.stringify(items.map((item) => ({ ...item, file: undefined }))),
    );
  } catch {
    /* Uploads continue when browser storage is full. */
  }
});
function update(id: string, patch: Partial<ImportItem>) {
  useBatchImports.setState(({ items }) => ({
    items: items.map((item) => (item.id === id ? { ...item, ...patch } : item)),
  }));
}
let active: Promise<void> | null = null;
let controller: AbortController | null = null;

function upload(item: ImportItem, signal: AbortSignal): Promise<Recording> {
  return new Promise((resolve, reject) => {
    if (!item.file) {
      reject(new Error("请重新选择原文件后重试"));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/v1/recordings/${item.id}/upload`);
    const headers = new Headers(authHeaders(true));
    Object.entries(filenameHeaders(item.name)).forEach(([key, value]) => {
      headers.set(key, value);
    });
    headers.forEach((value, key) => {
      xhr.setRequestHeader(key, value);
    });
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    const abort = () => {
      xhr.abort();
    };
    signal.addEventListener("abort", abort, { once: true });
    xhr.onloadend = () => {
      signal.removeEventListener("abort", abort);
    };
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable)
        update(item.id, {
          progress: Math.round((event.loaded / event.total) * 100),
        });
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as Recording);
        } catch {
          reject(new Error("上传响应无效，请重试"));
        }
      } else
        reject(
          new Error(
            `上传或媒体校验失败（${String(xhr.status)}），请检查文件后重试`,
          ),
        );
    };
    xhr.onerror = () => {
      reject(new Error("上传连接中断，请重试"));
    };
    xhr.onabort = () => {
      reject(new Error("已停止上传，可重新选择文件后重试"));
    };
    if (signal.aborted) {
      reject(new Error("已停止上传"));
      return;
    }
    xhr.send(item.file);
  });
}
async function drain() {
  for (;;) {
    const item = useBatchImports
      .getState()
      .items.find((entry) => entry.status === "waiting");
    if (!item) return;
    controller = new AbortController();
    const signal = controller.signal;
    try {
      update(item.id, { status: "uploading", error: undefined });
      // A stable recording ID recovers uploads whose success response was lost.
      const recording = await api<Recording>(`/recordings/${item.id}`).catch(
        () => upload(item, signal),
      );
      if (signal.aborted) throw new Error("已停止入队，可重试");
      update(item.id, { status: "submitting", progress: 100 });
      const job = await api<Job>("/jobs", {
        method: "POST",
        body: JSON.stringify({
          ...item.options,
          recording_id: recording.id,
          submission_key: item.id,
        }),
      });
      update(item.id, { status: "done", jobId: job.job_id, file: undefined });
    } catch (error) {
      update(item.id, {
        status: "error",
        error: error instanceof Error ? error.message : "导入失败",
      });
    } finally {
      controller = null;
    }
  }
}
function start() {
  if (active) return;
  active = drain().finally(() => {
    active = null;
  });
}
export function addImports(files: File[], options: ApiObject) {
  useBatchImports.setState(({ items }) => ({
    items: [
      ...items,
      ...files.map((file): ImportItem => ({
        id: crypto.randomUUID(),
        file,
        name: file.name,
        size: file.size,
        options: { ...options },
        status: "waiting",
        progress: 0,
      })),
    ],
  }));
  start();
}
export function retryImport(id: string, file?: File) {
  const item = useBatchImports
    .getState()
    .items.find((entry) => entry.id === id);
  if (!item || item.status !== "error") return;
  if (file && (file.name !== item.name || file.size !== item.size)) {
    update(id, { error: "请选择与原记录同名、同大小的文件" });
    return;
  }
  update(id, { status: "waiting", file: file ?? item.file, error: undefined });
  start();
}
export function stopImports() {
  useBatchImports.setState(({ items }) => ({
    items: items.map((item) =>
      item.status === "waiting"
        ? { ...item, status: "error", error: "已停止，可重试" }
        : item,
    ),
  }));
  controller?.abort();
}
