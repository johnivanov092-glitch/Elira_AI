import { afterEach, describe, expect, it, vi } from "vitest";
import { importResourceToLibrary, listLibraryFilesTyped } from "./library";

describe("Library API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("imports an attached durable resource as active Library context", async () => {
    let body: FormData | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
      body = init?.body as FormData;
      return new Response(JSON.stringify({ ok: true, id: 8 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    await importResourceToLibrary("a".repeat(32), { useInContext: true });

    expect(body?.get("resource_id")).toBe("a".repeat(32));
    expect(body?.get("use_in_context")).toBe("true");
  });

  it("rejects a typed backend failure instead of showing a false saved state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      ok: false,
      error: "resource_not_found",
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(importResourceToLibrary("f".repeat(32))).rejects.toThrow(
      "resource_not_found",
    );
  });

  it("maps indexing and last-used state for the Library chip", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      items: [{
        id: 4,
        name: "guide.pdf",
        size: 123,
        type: "application/pdf",
        source: "upload",
        use_in_context: 1,
        status: "ready",
        content_chars: 42000,
        preview_chars: 12000,
        last_used_at: "2026-08-26 10:00:00",
        created_at: "2026-08-26 09:00:00",
      }],
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    const files = await listLibraryFilesTyped();

    expect(files[0]).toMatchObject({
      id: 4,
      active: true,
      status: "ready",
      contentChars: 42000,
      previewChars: 12000,
      lastUsedAt: "2026-08-26 10:00:00",
    });
  });
});
