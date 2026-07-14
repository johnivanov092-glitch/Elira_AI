import { describe, it, expect } from "vitest";
import { normalizeError } from "./client";

describe("normalizeError surfaces the backend attach note", () => {
  it("returns `note` (e.g. the size-limit reason) so the composer chip shows it", () => {
    expect(normalizeError({ note: "Файл больше 100 МБ" }, 413)).toBe("Файл больше 100 МБ");
  });

  it("still prefers detail/message/error over note", () => {
    expect(normalizeError({ detail: "boom", note: "x" }, 400)).toBe("boom");
  });
});
