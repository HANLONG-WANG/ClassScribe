export function formatClipTime(seconds: number): string {
  const milliseconds = Math.max(0, Math.round(seconds * 1000));
  const hours = Math.floor(milliseconds / 3_600_000);
  const minutes = Math.floor((milliseconds % 3_600_000) / 60_000);
  const remainder = (milliseconds % 60_000) / 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
}

export function parseClipTime(text: string): number | null {
  const parts = text.trim().split(":");
  if (
    parts.length > 3 ||
    !parts.every((part, index) =>
      (index === parts.length - 1 ? /^\d+(?:\.\d{1,3})?$/ : /^\d+$/).test(part),
    )
  )
    return null;
  const values = parts.map(Number);
  if (values.some((value) => !Number.isFinite(value))) return null;
  if (parts.length > 1 && (values.at(-1) ?? 0) >= 60) return null;
  if (parts.length === 3 && (values[1] ?? 0) >= 60) return null;
  const seconds = values.reduce((total, value) => total * 60 + value, 0);
  return Number.isFinite(seconds) ? seconds : null;
}
