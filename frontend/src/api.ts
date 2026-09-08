export type ApiObject = Record<string, unknown>;

const tokenMeta = document.querySelector<HTMLMetaElement>(
  'meta[name="classscribe-api-token"]',
);
const injectedToken = tokenMeta?.content.trim();
let apiToken =
  (injectedToken === "__CLASSSCRIBE_API_TOKEN__" ? "" : injectedToken) ||
  sessionStorage.getItem("classscribe-token")?.trim() ||
  "";
tokenMeta?.remove();

export function setApiToken(token: string) {
  apiToken = token;
  sessionStorage.setItem("classscribe-token", token);
}

export function authHeaders(write = false): HeadersInit {
  const headers: Record<string, string> = {};
  if (apiToken) {
    headers.Authorization = `Bearer ${apiToken}`;
    if (write) headers["X-ClassScribe-CSRF-Token"] = apiToken;
  }
  return headers;
}

function publicErrorDetail(detail: string) {
  return detail
    .replace(/\bauthorization\s*[:=]\s*bearer\s+\S+/giu, "[REDACTED]")
    .replace(/\bbearer\s+\S+/giu, "[REDACTED]")
    .replace(/\bhf_[a-z0-9]{8,}\b/giu, "[REDACTED]")
    .replace(/(^|[\s="'(])\/(?:[^/\s]+\/)+[^/\s]*/gu, "$1[LOCAL_PATH]")
    .replace(/\b[A-Z]:\\(?:[^\\\s]+\\)+[^\\\s]*/giu, "[LOCAL_PATH]");
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method?.toUpperCase() ?? "GET";
  const write = ["POST", "PUT", "PATCH", "DELETE"].includes(method);
  const headers = new Headers(authHeaders(write));
  if (init.body !== undefined && typeof init.body === "string") {
    headers.set("Content-Type", "application/json");
  }
  new Headers(init.headers).forEach((value, key) => {
    headers.set(key, value);
  });
  const response = await fetch(`/api/v1${path}`, { ...init, headers });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      error?: { detail?: string };
    };
    throw new Error(
      payload.error?.detail
        ? publicErrorDetail(payload.error.detail)
        : `${String(response.status)} ${response.statusText}`,
    );
  }
  return (await response.json()) as T;
}

export function filenameHeaders(filename: string): Record<string, string> {
  return {
    "X-ClassScribe-Filename": encodeURIComponent(filename),
    "X-ClassScribe-Filename-Encoding": "utf-8-percent",
  };
}

export async function uploadRecording(
  file: File,
  metadata: { durationSamples: number; channels: number; sampleRate: number },
) {
  return api<Recording>("/recordings", {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      ...filenameHeaders(file.name),
      "X-ClassScribe-Duration-Samples": String(metadata.durationSamples),
      "X-ClassScribe-Channels": String(metadata.channels),
      "X-ClassScribe-Sample-Rate": String(metadata.sampleRate),
    },
    body: file,
  });
}

export interface Recording {
  id: string;
  source_name: string;
  duration_samples: number;
  sample_rate: number;
  channels: number;
  audio_qc: ApiObject;
  media_url: string;
}

export interface JobActivity {
  run_id: string;
  checkpoint_id: string;
  checkpoint_key: string;
  attempt: number;
  stage: string;
  operation: string;
  started_at: string;
  progress_at: string;
  model_id?: string;
  model_name?: string;
  device?: string;
  language?: string;
  completed?: number;
  total?: number;
  unit?: string;
  files_completed?: number;
  files_total?: number;
  object?: string;
  segment_ordinal?: number;
  segment_total?: number;
  window_ordinal?: number;
  window_total?: number;
  windows_completed?: number;
  start_sample?: number;
  end_sample?: number;
  reason?: string;
  retry?: boolean;
  fallback?: boolean;
  model_attempt?: number;
  process_alive?: boolean;
  process_checked_at?: string;
  timeout_seconds?: number;
}

export interface Job {
  job_id: string;
  recording_id: string;
  status: string;
  stage: string;
  progress: number;
  current_checkpoint?: string | null;
  current_segment_id?: string | null;
  error_code?: string | null;
  error_detail?: string | null;
  activity?: JobActivity | null;
  events?: PipelineEvent[];
  stage_activity?: Record<string, JobActivity[]>;
  stage_counts?: Record<string, { completed: number; total: number }>;
  runtime?: {
    model_id?: string;
    segment_ordinal?: number;
    realtime_factor?: number;
    vram_mb?: number;
  };
  options: ApiObject;
}

export interface Token {
  id: string;
  start_sample: number;
  end_sample: number;
  text: string;
  confidence: number | null;
  provenance: ApiObject;
}

export interface Segment {
  id: string;
  job_id: string;
  start_sample: number;
  end_sample: number;
  speaker_id: string | null;
  speaker_name: string | null;
  language: "zh" | "ja" | "en";
  raw_text: string;
  faithful_text: string;
  smart_corrected_text: string;
  user_text: string | null;
  auto_final_text: string;
  quality_score: number | null;
  low_confidence: boolean;
  review_status: string;
  timing_quality: string;
  version: number;
  tokens: Token[];
  audit?: ApiObject[];
}

export interface Transcript {
  job_id: string;
  timeline: string;
  segments: Segment[];
}

export interface Candidate {
  id: string;
  model_id: string;
  model_revision: string;
  raw_text: string;
  normalized_text: string;
  confidence_raw: number | null;
  confidence_calibrated: number | null;
  quality: ApiObject;
  warnings: ApiObject[];
  valid: boolean;
  adopted: boolean;
}

export interface ModelComponentSourceFile {
  installed_path: string;
  source_path: string;
  source_sha256: string;
  source_size_bytes: number;
}

export interface ModelComponentSource {
  repository: string;
  revision: string;
  relationship: "copied" | "derived";
  license_id: string;
  license_url: string;
  requires_terms_acceptance: boolean;
  files: ModelComponentSourceFile[];
}

export interface ModelInfo {
  modes?: string[];
  capabilities?: Record<string, boolean>;
  safe_window_seconds?: number;
  known_defects?: string[];
  disable_reason?: string | null;
  install_stage?: string;
  id: string;
  name: string;
  revision: string;
  repository: string;
  languages: string[];
  tasks: string[];
  enabled: boolean;
  experimental: boolean;
  manifest_available: boolean;
  manifest_sha256: string | null;
  estimated_download_bytes: number | null;
  installed_size_bytes: number | null;
  remote_code_file_count: number | null;
  component_source_count: number | null;
  component_sources: ModelComponentSource[] | null;
  worker_implemented: boolean;
  installable: boolean;
  install_block_reason: string | null;
  estimated_vram_mb: number;
  installation: {
    state: string;
    sha256?: string;
    measured_vram_mb?: number | null;
  };
  benchmark: ApiObject;
}

export interface GlossaryTerm {
  id: string;
  canonical: string;
  reading: string;
  aliases: string[];
  language: string;
  weight: number;
  source: string;
  confirmed: boolean;
}

export interface Glossary {
  id: string;
  name: string;
  course_id: string | null;
  version: number;
  terms: GlossaryTerm[];
  materials: ApiObject[];
}

export interface ExportArtifact {
  id: string;
  job_id: string;
  format: string;
  layer: string;
  view: string;
  file_name: string;
  download_url: string;
  sha256: string;
  size_bytes: number;
}

export interface PipelineEvent {
  sequence: number;
  job_id: string;
  kind: string;
  occurred_at: string;
  payload: ApiObject;
}

export async function streamJobEvents(
  jobId: string,
  onEvent: (event: PipelineEvent) => void,
  signal: AbortSignal,
  onConnection?: (connected: boolean) => void,
) {
  const aborted = () => signal.aborted;
  let sequence = 0;
  let delay = 1000;
  while (!aborted()) {
    let reader: ReadableStreamDefaultReader<string> | undefined;
    const connection = new AbortController();
    const abort = () => {
      connection.abort();
    };
    signal.addEventListener("abort", abort, { once: true });
    const connectTimer = setTimeout(abort, 10000);
    try {
      const headers = new Headers(authHeaders());
      headers.set("Last-Event-ID", String(sequence));
      const response = await fetch(`/api/v1/jobs/${jobId}/events`, {
        headers,
        signal: connection.signal,
      });
      clearTimeout(connectTimer);
      if (!response.ok || response.body === null)
        throw new Error("SSE disconnected");
      reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let pending = "";
      while (!aborted()) {
        const watchdog = setTimeout(() => {
          onConnection?.(false);
          void reader?.cancel().catch(() => undefined);
        }, 10000);
        let result: ReadableStreamReadResult<string>;
        try {
          result = await reader.read();
        } finally {
          clearTimeout(watchdog);
        }
        const { done, value } = result;
        if (done) break;
        onConnection?.(true);
        delay = 1000;
        pending += value;
        const blocks = pending.split("\n\n");
        pending = blocks.pop() ?? "";
        for (const block of blocks) {
          const data = block
            .split("\n")
            .find((line) => line.startsWith("data: "))
            ?.slice(6);
          if (data !== undefined) {
            const event = JSON.parse(data) as PipelineEvent;
            if (event.job_id !== jobId) continue;
            if (event.kind === "stream_reset") {
              sequence = 0;
              onEvent(event);
              continue;
            }
            if (event.sequence <= sequence) continue;
            sequence = event.sequence;
            onEvent(event);
          }
        }
      }
    } catch {
      if (aborted()) return;
      // The snapshot query remains a fallback while SSE reconnects.
    } finally {
      clearTimeout(connectTimer);
      signal.removeEventListener("abort", abort);
      connection.abort();
      await reader?.cancel().catch(() => undefined);
      reader?.releaseLock();
    }
    if (aborted()) return;
    onConnection?.(false);
    await new Promise<void>((resolve) => {
      const finish = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", finish);
        resolve();
      };
      const timer = setTimeout(finish, delay);
      signal.addEventListener("abort", finish, { once: true });
    });
    delay = Math.min(delay * 2, 15000);
  }
}

export function formatSamples(samples: number) {
  const milliseconds = Math.round((samples * 1000) / 16000);
  const seconds = Math.floor(milliseconds / 1000);
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}.${String(milliseconds % 1000).padStart(3, "0")}`;
}
