/// <reference types="vite/client" />
import { describe, it, expect } from "vitest";
// Vite `?raw` import — the Composer source as a string, no node:fs (no @types/node dep).
import composerSource from "./Composer.tsx?raw";

// Guards the Composer file <input accept="…"> so a WhatsApp .mp4 voice note (often
// typed video/mp4 by the OS, which audio/* does NOT match) is accepted and reaches
// the STT path instead of being rejected by the picker.
const accepts = [...composerSource.matchAll(/accept="([^"]*)"/g)].map((m) => m[1]);

describe("Composer file accept", () => {
  it("declares .mp4, audio/mp4 and video/mp4", () => {
    const hasMp4 = accepts.some(
      (a) => a.includes(".mp4") && a.includes("audio/mp4") && a.includes("video/mp4"),
    );
    expect(hasMp4).toBe(true);
  });
});
