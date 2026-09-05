import { expect, it, vi } from "vitest";
import { consumeCodeAgentStream } from "./codeAgent";

it("reports EOF without a terminal event and retains partial text", async () => {
  const onEvent = vi.fn();
  const onError = vi.fn();
  await consumeCodeAgentStream(new Response('data: {"type":"delta","text":"partial"}\n\n'), { onEvent, onError });
  expect(onEvent).toHaveBeenCalledWith({ type: "delta", text: "partial" });
  expect(onError).toHaveBeenCalledTimes(1);
  expect(onError.mock.calls[0][0].message).toMatch(/done|заверш/i);
});

it("accepts a terminal event without a trailing blank line", async () => {
  const onError = vi.fn();
  const onEvent = vi.fn();
  await consumeCodeAgentStream(new Response('data: {"type":"done","ok":true}'), { onEvent, onError });
  expect(onEvent).toHaveBeenCalledWith({ type: "done", ok: true });
  expect(onError).not.toHaveBeenCalled();
});

it("does not report intentional reader abort as a transport failure", async () => {
  const onError = vi.fn();
  const body = new ReadableStream({ start(controller) { controller.error(new DOMException("Stopped", "AbortError")); } });
  await consumeCodeAgentStream(new Response(body), { onError });
  expect(onError).not.toHaveBeenCalled();
});
