import { afterEach, describe, expect, it, vi } from "vitest";
import {
  listPendingWorkflowRequests,
  resolveWorkflowRequest,
  streamWorkflowEvents,
} from "./workflows";


describe("workflow request API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("replays pending requests through the workflow control-plane endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          requests: [
            {
              type: "item/request",
              request_id: "req-1",
              workflow_id: "wf-1",
              run_id: "run-1",
              step_id: "step-1",
              kind: "input",
              status: "pending",
              message: "Choose folder",
              schema: {},
              sensitive: false,
              action: "",
              created_at: "2026-08-11T00:00:00Z",
              updated_at: "2026-08-11T00:00:00Z",
              resolved_at: null,
            },
          ],
          total: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const requests = await listPendingWorkflowRequests();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/agent-os/workflow-requests?status=actionable",
    );
    expect(requests).toHaveLength(1);
    expect(requests[0].kind).toBe("input");
  });

  it("resolves a request with the correlated request id and typed action", async () => {
    let capturedBody: unknown;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (_url: RequestInfo | URL, init?: RequestInit) => {
        capturedBody = JSON.parse(String(init?.body ?? "{}"));
        return new Response(
          JSON.stringify({
            request: {
              type: "item/request",
              request_id: "req-2",
              workflow_id: "wf-1",
              run_id: "run-1",
              step_id: "step-1",
              kind: "input",
              status: "resolved",
              message: "Install helper",
              schema: {},
              sensitive: false,
              action: "accept",
              created_at: "2026-08-11T00:00:00Z",
              updated_at: "2026-08-11T00:00:01Z",
              resolved_at: "2026-08-11T00:00:01Z",
            },
            run: { run_id: "run-1", status: "completed" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      },
    );

    await resolveWorkflowRequest("req-2", "accept", { project_root: "D:/Project" });

    expect(capturedBody).toEqual({
      action: "accept",
      values: { project_root: "D:/Project" },
    });
  });

  it("streams durable events and advances the reconnect cursor", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        [
          "id: 7",
          "event: stream/ready",
          'data: {"type":"stream/ready","cursor":7}',
          "",
          "id: 8",
          "event: item/request",
          'data: {"id":8,"event_id":"evt-8","event_type":"item/request","payload":{"request_id":"req-8"},"source_agent_id":"","created_at":"2026-08-11T00:00:00Z"}',
          "",
        ].join("\n"),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    const events: string[] = [];
    const cursors: number[] = [];

    await streamWorkflowEvents({
      afterId: 7,
      onEvent: (event) => events.push(event.event_type),
      onCursor: (cursor) => cursors.push(cursor),
    });

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/agent-os/events/stream?after_id=7",
    );
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("Accept")).toBe("text/event-stream");
    expect(events).toEqual(["item/request"]);
    expect(cursors).toEqual([7, 8]);
  });
});
