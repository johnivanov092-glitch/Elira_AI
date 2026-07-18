import { afterEach, describe, expect, it, vi } from "vitest";
import { normalizeKind, toWireResource, uploadResource, type ResourceRef } from "./resources";

describe("normalizeKind", () => {
  it("keeps audio as audio and video as video (never collapses to document)", () => {
    expect(normalizeKind("audio")).toBe("audio");
    expect(normalizeKind("video")).toBe("video");
    expect(normalizeKind("image")).toBe("image");
    expect(normalizeKind("document")).toBe("document");
  });
  it("falls back to other for unknown kinds", () => {
    expect(normalizeKind("weird")).toBe("other");
    expect(normalizeKind(undefined)).toBe("other");
    expect(normalizeKind(42)).toBe("other");
  });
});

describe("toWireResource", () => {
  it("sends only the resource_id (no bytes, name, or path)", () => {
    const ref: ResourceRef = {
      resource_id: "abc123", name: "voice.ogg", kind: "audio",
      content_type: "audio/ogg", size: 10,
    };
    expect(toWireResource(ref)).toEqual({ resource_id: "abc123" });
  });
});

describe("uploadResource", () => {
  afterEach(() => vi.restoreAllMocks());

  it("posts multipart with session_id and returns the ResourceRef; audio stays audio", async () => {
    const seen: { url?: string; body?: FormData } = {};
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async (url: RequestInfo | URL, init?: RequestInit) => {
        seen.url = String(url);
        seen.body = init?.body as FormData;
        return new Response(
          JSON.stringify({ resource_id: "d".repeat(32), name: "voice.ogg", kind: "audio", content_type: "audio/ogg", size: 123 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      },
    );
    const file = new File([new Uint8Array([1, 2, 3])], "voice.ogg", { type: "audio/ogg" });
    const ref = await uploadResource(file, "sess-xyz");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(seen.url).toContain("/api/media/resources");
    expect(seen.body).toBeInstanceOf(FormData);
    expect(seen.body?.get("session_id")).toBe("sess-xyz");
    expect(seen.body?.get("file")).toBeInstanceOf(File);
    expect(ref).toEqual({
      resource_id: "d".repeat(32), name: "voice.ogg", kind: "audio",
      content_type: "audio/ogg", size: 123,
    });
  });

  it("throws on a non-2xx upload so the composer can mark the chip errored", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ detail: "resource_too_large" }),
      { status: 413, headers: { "Content-Type": "application/json" } },
    ));
    const file = new File([new Uint8Array([1])], "big.bin");
    await expect(uploadResource(file, "sess-1")).rejects.toThrow("resource_too_large");
  });

  it("rejects a malformed success response instead of creating a dead chip", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ resource_id: "", name: "x.bin", kind: "other", size: 1 }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    const file = new File([new Uint8Array([1])], "x.bin");
    await expect(uploadResource(file, "sess-1")).rejects.toThrow("invalid resource response");
  });
});
