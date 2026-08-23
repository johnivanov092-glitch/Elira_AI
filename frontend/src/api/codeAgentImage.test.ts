import { afterEach, describe, expect, it, vi } from "vitest";

describe("fetchCodeAgentImage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("loads through the authenticated backend proxy, never from the remote URL", async () => {
    vi.stubEnv("VITE_ELIRA_API_TOKEN", "lan-token");
    let requestedUrl = "";
    let requestedHeaders = new Headers();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      requestedUrl = String(url);
      requestedHeaders = new Headers(init?.headers);
      return new Response(new Blob(["image"], { type: "image/png" }), {
        status: 200,
        headers: { "content-type": "image/png" },
      });
    });

    const { fetchCodeAgentImage } = await import("./codeAgent");
    const blob = await fetchCodeAgentImage("https://images.example/pangu.png");

    expect(requestedUrl).toContain("/api/code-agent/image?url=");
    expect(requestedUrl).not.toBe("https://images.example/pangu.png");
    expect(requestedHeaders.get("Authorization")).toBe("Bearer lan-token");
    expect(blob.type).toBe("image/png");
  });
});
