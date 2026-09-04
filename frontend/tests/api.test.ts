import { beforeEach, describe, expect, it, vi } from "vitest";

describe("API authentication bootstrap", () => {
  beforeEach(() => {
    vi.resetModules();
    sessionStorage.clear();
    document.head.replaceChildren();
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
});
