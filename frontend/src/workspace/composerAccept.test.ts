/// <reference types="vite/client" />
import { describe, it, expect } from "vitest";
// Vite `?raw` import — the Composer source as a string, no node:fs (no @types/node dep).
import composerSource from "./Composer.tsx?raw";

// R1/R2 contract: the file picker is NOT limited to a static extension allowlist —
// any file registers as a durable resource and the extension only informs an
// adapter LATER. The R1 intake removed the narrow `accept="…"` (so a WhatsApp
// .mp4/.ogg voice note, often typed video/mp4 which audio/* would miss, is never
// rejected by the picker). This guards against a regression that re-adds a narrow
// accept excluding mp4.
const accepts = [...composerSource.matchAll(/accept="([^"]*)"/g)].map((m) => m[1]);

describe("Composer file accept", () => {
  it("does not narrow the picker to a static list that excludes mp4/ogg voice notes", () => {
    const narrowed = accepts.some(
      (a) => a.trim() !== "" && a.trim() !== "*/*" && !a.includes(".mp4"),
    );
    expect(narrowed).toBe(false);
  });
});
