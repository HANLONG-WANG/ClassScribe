type StorageKind = "session" | "local";
const fallback = {
  session: new Map<string, string>(),
  local: new Map<string, string>(),
};

export function readStorage(key: string, kind: StorageKind = "session") {
  try {
    const value = (kind === "session" ? sessionStorage : localStorage).getItem(
      key,
    );
    if (value !== null) fallback[kind].set(key, value);
    else fallback[kind].delete(key);
    return value;
  } catch {
    return fallback[kind].get(key) ?? null;
  }
}

export function writeStorage(
  key: string,
  value: string,
  kind: StorageKind = "session",
) {
  fallback[kind].set(key, value);
  try {
    (kind === "session" ? sessionStorage : localStorage).setItem(key, value);
  } catch {
    /* Keep this session usable when persistence is unavailable. */
  }
}

export function removeStorage(key: string, kind: StorageKind = "session") {
  fallback[kind].delete(key);
  try {
    (kind === "session" ? sessionStorage : localStorage).removeItem(key);
  } catch {
    /* Removing in-memory state still succeeds. */
  }
}
