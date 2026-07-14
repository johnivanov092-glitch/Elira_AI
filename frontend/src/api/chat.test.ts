import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the HTTP layer so we can assert the timeout attachToChat passes.
const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));
vi.mock("./client", () => ({ request: requestMock, safeRequest: vi.fn() }));

import { attachToChat } from "./chat";

function fakeFile(name: string): File {
  const blob = new Blob([new Uint8Array([1, 2, 3])]);
  return Object.assign(blob, { name, lastModified: 0 }) as unknown as File;
}

describe("attachToChat bounded audio timeout", () => {
  beforeEach(() => {
    requestMock.mockReset();
    requestMock.mockResolvedValue({ ok: true, filename: "x", kind: "audio", text: "t", chars: 1 });
  });

  it("uses a 3630s bounded timeout for an audio (.mp4) upload", async () => {
    await attachToChat(fakeFile("voice.mp4"));
    expect(requestMock.mock.calls[0][1].timeoutMs).toBe(3_630_000);
  });

  it("keeps the 120s timeout for a non-audio file", async () => {
    await attachToChat(fakeFile("notes.txt"));
    expect(requestMock.mock.calls[0][1].timeoutMs).toBe(120_000);
  });
});
