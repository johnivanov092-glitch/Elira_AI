import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CodeAgentToolCall } from "../api/codeAgent";
import { ToolCallGroup } from "./ToolCallGroup";

const read: CodeAgentToolCall = {
  step: 1, tool: "read_file", arguments: { path: "src/example.ts" }, result: "File contents", ok: true,
};

describe("compact tool activity", () => {
  it.each([
    ["read_file", "Читаю файл"],
    ["grep", "Ищу в проекте"],
    ["web_search", "Ищу в интернете"],
    ["Шаг 2/3: Проверка файла", "Шаг 2/3: Проверка файла"],
  ])("shows the actual active %s event even with collapsed history", (tool, label) => {
    const html = renderToStaticMarkup(<ToolCallGroup calls={[read]} activeTool={tool} />);
    const summary = html.slice(0, html.indexOf("</summary>"));
    expect(summary).toContain("Действия");
    expect(summary).toContain(label);
    expect(summary).not.toContain("вызов");
    expect(summary).not.toContain("в истории");
    expect(summary).toContain("tool-activity-spinner");
    expect(summary).toContain('aria-live="polite"');
    expect(html).not.toMatch(/<details[^>]*\sopen(?:=|\s|>)/);
    expect(html).toContain("результат ещё не получен");
  });

  it("keeps completed history collapsed and stops all live animation", () => {
    const html = renderToStaticMarkup(<ToolCallGroup calls={[read, { ...read, step: 2 }]} />);
    const summary = html.slice(0, html.indexOf("</summary>"));
    expect(summary).toContain("Действия");
    expect(summary).not.toContain("вызов");
    expect(summary).not.toContain("read_file");
    expect(html).not.toContain("tool-activity-spinner");
    expect(html).not.toContain("tool-activity-live");
    expect(html).not.toMatch(/<details[^>]*\sopen(?:=|\s|>)/);
  });

  it("keeps heavy arguments and results unmounted until their native disclosure opens", () => {
    const result = "x".repeat(5000) + "END_OF_RESULT";
    const html = renderToStaticMarkup(<ToolCallGroup calls={[{ ...read, arguments: { path: "a.ts", offset: 250, nested: { encoding: "utf-8" } }, result, exit_code: 0 }]} />);
    expect(html.match(/<details/g)).toHaveLength(2);
    expect(html.match(/<summary/g)).toHaveLength(2);
    expect(html).not.toContain("Аргументы");
    expect(html).not.toContain("encoding");
    expect(html).not.toContain("250");
    expect(html).not.toContain(result);
    expect(html).toContain("Код выхода: 0");
    expect(html).not.toContain("bg-[#121216]");
  });

  it("keeps failed calls inside the disclosure without an external error card or count", () => {
    const html = renderToStaticMarkup(<ToolCallGroup calls={[{ ...read, ok: false, result: "Файл не найден" }]} />);
    const summary = html.slice(0, html.indexOf("</summary>"));
    expect(summary).toContain("Действия");
    expect(summary).not.toContain("ошиб");
    expect(summary).not.toContain("text-danger");
    expect(summary).not.toContain("read_file");
    const history = html.slice(html.indexOf("</summary>"), html.lastIndexOf("</details>"));
    expect(history).toContain("read_file");
    expect(history).toContain("bg-danger");
    const afterHistory = html.slice(html.lastIndexOf("</details>"));
    expect(afterHistory).not.toContain("Файл не найден");
    expect(afterHistory).not.toContain("read_file");
    expect(afterHistory).not.toContain("text-danger");
    expect(html).not.toMatch(/<details[^>]*\sopen(?:=|\s|>)/);
    expect(html).not.toContain("tool-activity-spinner");
  });

  it("does not animate a stopped operation or report unverified calls as success", () => {
    const html = renderToStaticMarkup(<ToolCallGroup calls={[{ ...read, ok: undefined }]} activeTool="read_file" stopReason="timeout" />);
    const summary = html.slice(0, html.indexOf("</summary>"));
    expect(summary).toContain("Действия");
    expect(summary).not.toContain("таймаут");
    expect(summary).not.toContain("text-danger");
    expect(html).toContain("таймаут · задача не завершена");
    expect(html).not.toContain("tool-activity-spinner");
    expect(html).not.toContain("bg-success");
  });

});
