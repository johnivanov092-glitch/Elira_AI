import { describe, it, expect, vi } from "vitest";
import { normalizeError, waitForBackend } from "./client";

describe("normalizeError surfaces the backend attach note", () => {
  it("returns `note` (e.g. the size-limit reason) so the composer chip shows it", () => {
    expect(normalizeError({ note: "Файл больше 100 МБ" }, 413)).toBe("Файл больше 100 МБ");
  });

  it("still prefers detail/message/error over note", () => {
    expect(normalizeError({ detail: "boom", note: "x" }, 400)).toBe("boom");
  });
});

it("waits for admission before treating a staged release as ready", async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ status: "ok", admitted: false, draining: true })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ status: "ok", admitted: true, draining: false })));
  vi.stubGlobal("fetch", fetch);
  try {
    expect(await waitForBackend(2, 0)).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(2);
  } finally {
    vi.unstubAllGlobals();
  }
});
