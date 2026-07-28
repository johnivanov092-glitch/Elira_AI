import { afterEach, describe, expect, it, vi } from "vitest";
import { streamCodeAgent } from "./codeAgent";

// Context sizing is server-owned. The frontend never sends num_ctx, including
// when an older caller still supplies the deprecated argument.

function emptyStreamResponse(): Response {
  const body = new ReadableStream<Uint8Array>({ start(c) { c.close(); } });
  return new Response(body, { status: 200, headers: { "X-Run-Id": "run-test" } });
}

async function capturedBody(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  let captured: Record<string, unknown> = {};
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (_url: RequestInfo | URL, init?: RequestInit) => {
      captured = JSON.parse(String(init?.body ?? "{}"));
      return emptyStreamResponse();
    },
  );
  await streamCodeAgent({
    message: "задача", projectRoot: "/proj",
    onEvent: () => {}, onError: () => {},
    ...args,
  } as never);
  return captured;
}

describe("adaptive context — Auto sends no num_ctx", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Auto (default): num_ctx is ABSENT from the stream payload", async () => {
    const body = await capturedBody({});
    expect("num_ctx" in body).toBe(false);
  });

  it("ignores a legacy numCtx argument instead of capping the server", async () => {
    const body = await capturedBody({ numCtx: 524288 });
    expect("num_ctx" in body).toBe(false);
  });
});
