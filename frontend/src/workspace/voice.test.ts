import { afterEach, describe, expect, it, vi } from "vitest";
const fake = vi.hoisted(() => ({ synthesize: vi.fn() }));
vi.mock("../api/voice", () => ({ synthesizeSpeech: fake.synthesize }));
import { plainForSpeech, speechChunks, speak, stop } from "./voice";

afterEach(() => { stop(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("accepted text speech", () => {
  it("keeps the complete prose while bounding requests", () => {
    const text = "Начало. " + "Слово ".repeat(1200) + "Конец. [[source:w_1]]";
    const chunks = speechChunks(text);
    expect(chunks.length).toBeGreaterThan(1);
    expect(chunks.every(chunk => chunk.length <= 2000)).toBe(true);
    expect(chunks.join(" ")).toBe(plainForSpeech(text));
    expect(chunks.at(-1)).toContain("Конец.");
    expect(chunks.join(" ")).not.toContain("source:");
  });
  it("does not play a synthesis response that arrived after Stop", async () => {
    let resolve!: (blob: Blob) => void;
    fake.synthesize.mockReturnValueOnce(new Promise<Blob>(r => { resolve = r; }));
    const audio = vi.fn();
    vi.stubGlobal("Audio", audio);
    const pending = speak("Принятый ответ.");
    stop();
    resolve(new Blob());
    expect(await pending).toBe(false);
    expect(audio).not.toHaveBeenCalled();
  });
  it("plays all chunks in order and releases every blob", async () => {
    fake.synthesize.mockReset().mockResolvedValue(new Blob());
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.stubGlobal("Audio", class {
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      pause() {}
      async play() { queueMicrotask(() => this.onended?.()); }
    });
    const text = "Слова ".repeat(800) + "Конец.";
    expect(await speak(text)).toBe(true);
    expect(fake.synthesize.mock.calls.map(call => call[0])).toEqual(speechChunks(text));
    expect(revoke).toHaveBeenCalledTimes(speechChunks(text).length);
  });
});
