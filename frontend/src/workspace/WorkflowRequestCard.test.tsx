import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { WorkflowRequest } from "../api/workflows";
import { WorkflowRequestCard } from "./WorkflowRequestCard";


function request(kind: WorkflowRequest["kind"]): WorkflowRequest {
  return {
    type: "item/request",
    request_id: `req-${kind}`,
    workflow_id: "wf-1",
    run_id: "run-1",
    step_id: "step-1",
    kind,
    status: "pending",
    message: `Resolve ${kind}`,
    schema: kind === "input"
      ? {
          type: "object",
          properties: { project_root: { type: "string", title: "Рабочая папка" } },
          required: ["project_root"],
        }
      : {},
    sensitive: kind === "secret",
    action: "",
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:00Z",
    resolved_at: null,
  };
}


describe("WorkflowRequestCard", () => {
  it("renders schema-backed input without exposing internal provider fields", () => {
    const html = renderToStaticMarkup(
      <WorkflowRequestCard request={request("input")} onResolve={async () => {}} />,
    );

    expect(html).toContain("Рабочая папка");
    expect(html).toContain("name=\"project_root\"");
    expect(html).toContain("Продолжить workflow");
    expect(html).not.toContain("provider_ref");
  });

  it("accepts only an opaque vault reference for a secret request", () => {
    const html = renderToStaticMarkup(
      <WorkflowRequestCard request={request("secret")} onResolve={async () => {}} />,
    );

    expect(html).toContain("Использовать существующий secret_ref");
    expect(html).toContain("name=\"secret_ref\"");
    expect(html).not.toContain("name=\"password\"");
  });

  it("prefills an existing opaque reference without exposing a secret value", () => {
    const secretRequest = {
      ...request("secret"),
      schema: {
        "x-elira-secret-kind": "password",
        "x-elira-existing-secret-ref": "sref_saved",
      },
    };
    const html = renderToStaticMarkup(
      <WorkflowRequestCard request={secretRequest} onResolve={async () => {}} />,
    );

    expect(html).toContain("sref_saved");
    expect(html).not.toContain("secret_value");
  });

  it("does not fake elevation when the native UAC bridge is unavailable", () => {
    const html = renderToStaticMarkup(
      <WorkflowRequestCard request={request("elevation")} onResolve={async () => {}} />,
    );

    expect(html).toContain("Требуются права Windows");
    expect(html).toContain("Запустить UAC bootstrap");
    expect(html).toContain("disabled=\"\"");
  });

  it("renders the product approval decision in the workflow", () => {
    const html = renderToStaticMarkup(
      <WorkflowRequestCard request={request("approval")} onResolve={async () => {}} />,
    );

    expect(html).toContain("Разрешить");
    expect(html).toContain("Отклонить");
  });

  it("warns before retrying an execution interrupted at an uncertain point", () => {
    const interrupted = { ...request("approval"), status: "needs_reconciliation" as const };
    const html = renderToStaticMarkup(
      <WorkflowRequestCard request={interrupted} onResolve={async () => {}} />,
    );

    expect(html).toContain("внешний результат мог уже измениться");
    expect(html).toContain("Я проверил — повторить действие");
  });
});
