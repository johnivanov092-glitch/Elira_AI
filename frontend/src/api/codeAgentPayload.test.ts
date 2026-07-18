import { afterEach, describe, expect, it, vi } from "vitest";
import { streamCodeAgent } from "./codeAgent";
import type { ResourceRef } from "./resources";

// An empty SSE response so streamCodeAgent's reader completes immediately; we
// only care about the REQUEST body it sent.
function emptyStreamResponse(): Response {
  const body = new ReadableStream<Uint8Array>({ start(c) { c.close(); } });
  return new Response(body, { status: 200, headers: { "X-Run-Id": "run-test" } });
}

describe("streamCodeAgent request body — resource boundary", () => {
  afterEach(() => vi.restoreAllMocks());

  async function capture(resources: ResourceRef[], sessionId: string) {
    let captured: Record<string, unknown> = {};
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (_url: RequestInfo | URL, init?: RequestInit) => {
        captured = JSON.parse(String(init?.body ?? "{}"));
        return emptyStreamResponse();
      },
    );
    await streamCodeAgent({
      message: "расшифруй запись", projectRoot: "/proj", resources, sessionId,
      onEvent: () => {}, onError: () => {},
    });
    return captured;
  }

  it("sends ResourceRefs (resource_id only) and session_id — never File, bytes, text, or a path", async () => {
    const ref: ResourceRef = {
      resource_id: "res-777", name: "C:/secret/voice.ogg", kind: "audio",
      content_type: "audio/ogg", size: 999,
    };
    const body = await capture([ref], "sess-abc");

    expect(body.session_id).toBe("sess-abc");
    expect(body.resources).toEqual([{ resource_id: "res-777" }]);
    // The legacy eager-text channel is gone.
    expect(body.attachments).toBeUndefined();
    // No content, no path, no bytes anywhere in the serialized body.
    const serialized = JSON.stringify(body);
    expect(serialized).not.toContain("C:/secret");
    expect(serialized).not.toContain("voice.ogg");
    expect(serialized.toLowerCase()).not.toContain("text");
  });

  it("omits resources and session_id when there are none", async () => {
    const body = await capture([], "");
    expect(body.resources).toBeUndefined();
    expect(body.session_id).toBeUndefined();
  });
});
