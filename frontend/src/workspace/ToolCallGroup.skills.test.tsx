import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ToolCallGroup } from "./ToolCallGroup";
import type { CodeAgentToolCall } from "../api/codeAgent";

describe("skill loading in existing tool rows", () => {
  const call: CodeAgentToolCall = {
    step: 1, tool: "runtime_control", arguments: { operation: "skill_load", name: "python" },
    result: "Receipt", ok: true,
    skill: { name: "python", title: "Python", sha256: "a", reason: "Исправление сервиса", already_loaded: false },
  };
  it("shows the runtime title and reason, including persisted calls", () => {
    const saved: CodeAgentToolCall = JSON.parse(JSON.stringify(call));
    const html = renderToStaticMarkup(<ToolCallGroup calls={[saved]} />);
    expect(html).toContain("Навык загружен");
    expect(html).toContain("Python · Исправление сервиса");
  });
  it("does not label a failed or legacy call as confirmed loading", () => {
    const html = renderToStaticMarkup(<ToolCallGroup calls={[{ ...call, ok: false, skill: undefined }]} />);
    expect(html).toContain("Ошибка загрузки навыка");
    expect(html).not.toContain("Навык загружен");
  });
});
