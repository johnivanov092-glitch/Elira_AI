import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { sendCodeAgentInput } from "./codeAgent";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));
vi.mock("./client", async (importOriginal) => ({
  ...await importOriginal<typeof import("./client")>(), request: requestMock,
}));
afterEach(() => requestMock.mockReset());

describe("user input delivery", () => {
  it("reconciles a lost POST reply by ID through a read without resending", async () => {
    const receipt = { request_id: "a".repeat(32), text: "уточнение", state: "applied" };
    requestMock.mockRejectedValueOnce(new Error("lost connection")).mockResolvedValueOnce({ items: [receipt] });
    expect(await sendCodeAgentInput("run", "chat", receipt.request_id, receipt.text)).toEqual(receipt);
    expect(requestMock).toHaveBeenCalledTimes(2);
    expect(requestMock.mock.calls[0][1].method).toBe("POST");
    expect(requestMock.mock.calls[1][0]).toBe("/api/code-agent/runs/run/inputs?session_id=chat");
    expect(requestMock.mock.calls[1][1].method).toBeUndefined();
  });
  it("does not retry a rejected or mismatched delivery", async () => {
    requestMock.mockRejectedValueOnce(new ApiError("finished", 409));
    await expect(sendCodeAgentInput("run", "chat", "a".repeat(32), "уточнение")).rejects.toThrow("finished");
    expect(requestMock).toHaveBeenCalledTimes(1);
  });
});
