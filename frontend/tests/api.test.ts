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
});
