import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("API authentication bootstrap", () => {
  beforeEach(() => {
    vi.resetModules();
    sessionStorage.clear();
    document.head.replaceChildren();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the runtime-injected token and removes it from the DOM", async () => {
    const meta = document.createElement("meta");
    meta.name = "classscribe-api-token";
    meta.content = "runtime-token";
    document.head.append(meta);

    const { authHeaders } = await import("../src/api");

    expect(new Headers(authHeaders()).get("Authorization")).toBe(
      "Bearer runtime-token",
    );
    expect(
      document.querySelector('meta[name="classscribe-api-token"]'),
    ).toBeNull();
  });

  it.each(["", "__CLASSSCRIBE_API_TOKEN__"])(
    "falls back to the session token when the injected value is %j",
    async (injectedToken) => {
      sessionStorage.setItem("classscribe-token", "session-token");
      const meta = document.createElement("meta");
      meta.name = "classscribe-api-token";
      meta.content = injectedToken;
      document.head.append(meta);

      const { authHeaders } = await import("../src/api");

      expect(new Headers(authHeaders(true)).get("Authorization")).toBe(
        "Bearer session-token",
      );
      expect(
        new Headers(authHeaders(true)).get("X-ClassScribe-CSRF-Token"),
      ).toBe("session-token");
    },
  );

  it("redacts credentials and local paths from API error details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "MODEL_HEALTH_CHECK_FAILED",
                detail:
                  "Authorization: Bearer browser-secret hf_abcdefghijklmnop failed at /home/private-user/models/revision/config.json",
              },
            }),
            {
              status: 409,
              headers: { "Content-Type": "application/json" },
            },
          ),
        ),
      ),
    );
    const { api } = await import("../src/api");

    let message = "";
    try {
      await api("/models/example/install", { method: "POST", body: "{}" });
    } catch (error) {
      message = error instanceof Error ? error.message : String(error);
    }
    expect(message).toContain("[REDACTED]");
    expect(message).toContain("[LOCAL_PATH]");
    expect(message).not.toContain("Authorization");
    expect(message).not.toContain("browser-secret");
    expect(message).not.toContain("hf_abcdefghijklmnop");
    expect(message).not.toContain(
      "/home/private-user/models/revision/config.json",
    );
  });

  it("shows validation details returned by framework 422 responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({ detail: "材料编码无效, 请使用 UTF-8." }),
            { status: 422, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );
    const { api } = await import("../src/api");
    await expect(
      api("/glossaries/id/documents", { method: "POST" }),
    ).rejects.toThrow("材料编码无效, 请使用 UTF-8.");
  });

  it("marks an invalid API token and clears the warning after a successful request", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { detail: "invalid token" } }), {
          status: 403,
        }),
      )
      .mockResolvedValueOnce(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("../src/api");
    const { useAuthStatus } = await import("../src/authStatus");
    await expect(api("/settings")).rejects.toThrow("invalid token");
    expect(useAuthStatus.getState().invalid).toBe(true);
    await api("/settings");
    expect(useAuthStatus.getState().invalid).toBe(false);
  });
});

describe("recording upload filenames", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each(["录音 2.wav", "授業 🎙️.wav", "100% + %E5.wav", "lecture.wav"])(
    "uploads %s through browser-safe headers",
    async (name) => {
      const fetchMock = vi.fn(() => Promise.resolve(new Response("{}")));
      vi.stubGlobal("fetch", fetchMock);
      const { uploadRecording } = await import("../src/api");
      const file = new File(["audio"], name, { type: "audio/wav" });
      await uploadRecording(file, {
        durationSamples: 16000,
        channels: 1,
        sampleRate: 16000,
      });
      const [, init] = fetchMock.mock.calls[0] as unknown as [
        string,
        RequestInit,
      ];
      const headers = new Headers(init.headers);
      expect(headers.get("X-ClassScribe-Filename-Encoding")).toBe(
        "utf-8-percent",
      );
      expect(
        decodeURIComponent(headers.get("X-ClassScribe-Filename") ?? ""),
      ).toBe(name);
      expect(init.body).toBe(file);
    },
  );
});
