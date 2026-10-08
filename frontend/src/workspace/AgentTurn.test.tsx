import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AgentTurnView } from "./AgentTurn";
import type { AgentTurnData } from "./types";

describe("AgentTurn structured analysis status", () => {
  it("renders visible notes before their tools and keeps unclassified text out of the answer", () => {
    const turn: AgentTurnData = {
      kind: "agent", id: "agent-notes", running: true, answerState: "draft", text: "Незавершённый ответ",
      reasoning: "Отдельное рассуждение",
      stepNotes: [
        { id: "before-resume", step: 1, text: "Читаю конфиг.", toolCallIndex: 0 },
        { id: "after-resume", step: 1, text: "Проверяю порт.", toolCallIndex: 1 },
      ],
      toolCalls: [
        { step: 1, tool: "read_file", arguments: { path: "config.txt" }, result: "ok", ok: true },
        { step: 1, tool: "run_bash", arguments: { command: "check-port" }, result: "ok", ok: true },
      ],
    };
    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);
    expect(html.indexOf("Читаю конфиг.")).toBeLessThan(html.indexOf("config.txt"));
    expect(html.indexOf("config.txt")).toBeLessThan(html.indexOf("Проверяю порт."));
    expect(html.indexOf("Проверяю порт.")).toBeLessThan(html.indexOf("check-port"));
    expect(html).not.toContain("Незавершённый ответ");
    expect(html).not.toContain("Отдельное рассуждение");
    expect(html).not.toContain("Рассуждение");
    const thinking = renderToStaticMarkup(<AgentTurnView turn={{ ...turn, text: "", reasoningActive: true }} />);
    expect(thinking).toContain("Рассуждаю");
    expect(thinking.match(/Рассуждаю/g)).toHaveLength(1);
    expect(thinking).not.toContain("Отдельное рассуждение");
    const beforeTools = renderToStaticMarkup(<AgentTurnView turn={{ ...turn, text: "", reasoningActive: true, toolCalls: [], stepNotes: [] }} />);
    expect(beforeTools).toContain("Рассуждаю");
    expect(beforeTools).not.toContain("<details");
    const restored = JSON.parse(JSON.stringify({ ...turn, running: false, answerState: "accepted", text: "Готово." }));
    const accepted = renderToStaticMarkup(<AgentTurnView turn={restored} />);
    expect(accepted).toContain("data-step-note=\"before-resume\"");
    expect(accepted).toContain("Готово.");
    expect(accepted).not.toContain("Рассуждаю");
    expect(accepted).not.toContain("Рассуждение");
  });
  it.each([false, true])("uses site citation chips in the answer and source details (media=%s)", (withMedia) => {
    const turn: AgentTurnData = {
      kind: "agent", id: "agent-source", toolCalls: [], running: false,
      text: "Документация Python [[source:w_writer]].\n\nПример работы с CSV.",
      answerState: "accepted", stopReason: "answer",
      citations: [{
        source_id: "w_writer", status: "matched", claim_support: "not_assessed",
        source: {
          id: "w_writer", origin_run_id: "thinking-web", tool: "web_fetch",
          url: "https://docs.python.org/3.10/library/csv.html", title: "", status: "excerpt",
          quote: "The file should be opened with newline=''.", fetched_at: 1,
          content_hash: "abc", excerpt_hash: "def", doc_id: "", chunk_id: null, offset: 3000,
          quote_verified: true, presented: true, claim_support: "not_assessed", error: "",
        },
      }],
      media: withMedia ? [{
        type: "image", url: "https://images.example/diagram.jpg", source_url: "https://source.example/diagram",
        title: "Пример", source: "source.example",
      }] : undefined,
    };
    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);
    expect(html.match(/data-source-id="w_writer"/g)).toHaveLength(2);
    expect(html.match(/>docs\.python\.org<\/span>/g)).toHaveLength(2);
    expect(html).toContain("<blockquote");
    expect(html).not.toContain("[1]");
    expect(html).not.toContain("[[source:w_writer]]");
  });

  it("lists pages that did not open under an accepted answer only", () => {
    const failed = {
      id: "f1", origin_run_id: "r", tool: "web_fetch", url: "https://www.reddit.com/r/x/1", title: "",
      status: "failed" as const, fetched_at: null, content_hash: "", excerpt_hash: "", doc_id: "",
      chunk_id: null, offset: null, quote: "", quote_verified: false, presented: false,
      claim_support: "not_assessed" as const, error: "ERROR: HTTP 403 (https://www.reddit.com/r/x/1)",
    };
    const accepted: AgentTurnData = {
      kind: "agent", id: "agent-unopened", toolCalls: [], running: false, text: "Ответ по прочитанному.",
      answerState: "accepted", stopReason: "answer", sources: [failed],
    };
    const html = renderToStaticMarkup(<AgentTurnView turn={accepted} />);
    expect(html).toContain("data-unopened-sources");
    expect(html).toContain("Не открылись: ");
    expect(html).toContain(">reddit.com</a>");
    expect(html).toContain("403 — доступ закрыт");
    for (const turn of [
      { ...accepted, running: true, answerState: "draft" as const },
      { ...accepted, text: "Ответ.\n\nНе открылись: reddit.com (403)." },
    ]) {
      expect(renderToStaticMarkup(<AgentTurnView turn={turn} />)).not.toContain("data-unopened-sources");
    }
  });

  it("shows the live planning phase instead of a generic spinner", () => {
    const turn: AgentTurnData = {
      kind: "agent",
      id: "agent-2",
      toolCalls: [],
      text: "",
      running: true,
      brainPhase: "planning",
    };

    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);

    expect(html).toContain("Строит план…");
    expect(html).not.toContain("Думает…");
  });

  it("labels a needs-input answer for the user", () => {
    const turn: AgentTurnData = {
      kind: "agent",
      id: "agent-3",
      toolCalls: [],
      text: "Ты играешь в PvE или PvP?",
      running: false,
      answerStatus: "needs_input",
    };

    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);

    expect(html).toContain("Статус: нужен ответ пользователя");
    expect(html).not.toContain("факты не подтверждены");
  });

  it("shows real server throughput, prompt cache, and TTFT", () => {
    const turn: AgentTurnData = {
      kind: "agent",
      id: "agent-metrics",
      toolCalls: [],
      text: "Готово.",
      running: false,
      genTokens: 80,
      tokensPerSecond: 40,
      promptTokensPerSecond: 350.5,
      cachedPromptTokens: 900,
      promptTokens: 1200,
      cacheHitRatio: 0.75,
      ttftMs: 420,
    };

    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);

    expect(html).toContain("40.0 т/с");
    expect(html).toContain("prompt 350.5 т/с");
    expect(html).toContain("cache 75.0%");
    expect(html).toContain("TTFT 0.42 с");
  });

  it("renders answer images as a sourced gallery", () => {
    const turn: AgentTurnData = {
      kind: "agent",
      id: "agent-images",
      toolCalls: [],
      text: "Вот несколько изображений Паньгу.\n\nНиже — подробное описание мифа.",
      running: false,
      media: [{
        type: "image",
        url: "https://images.example/pangu.jpg",
        source_url: "https://source.example/pangu",
        title: "Паньгу",
        source: "source.example",
      }],
    };

    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);

    expect(html).toContain("answer-media-gallery");
    expect(html).toContain("Паньгу");
    expect(html).toContain("source.example");
    expect(html).toContain("loading=\"lazy\"");
    expect(html).toContain("href=\"https://source.example/pangu\"");
    expect(html).not.toContain("src=\"https://images.example/pangu.jpg\"");
    expect(html.indexOf("Вот несколько")).toBeLessThan(html.indexOf("answer-media-gallery"));
    expect(html.indexOf("answer-media-gallery")).toBeLessThan(html.indexOf("подробное описание"));
  });

  it("does not render a model-authored remote Markdown image directly", () => {
    const turn: AgentTurnData = {
      kind: "agent",
      id: "agent-markdown-image",
      toolCalls: [],
      text: "![Непроверенная картинка](https://evil.example/api/extra/tracker.svg)",
      running: false,
    };

    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);

    expect(html).not.toContain("<img");
    expect(html).toContain("href=\"https://evil.example/api/extra/tracker.svg\"");
  });

  it("renders a download chip beside the voice action for a published artifact", () => {
    const turn: AgentTurnData = {
      kind: "agent",
      id: "agent-download",
      toolCalls: [{
        step: 12,
        tool: "resource_publish",
        arguments: { project_path: "generated/report.xlsx" },
        result: "Published report.xlsx",
          ok: true,
          download_url: "/api/skills/download/report.xlsx",
          download_name: "report.xlsx",
      }],
      text: "Файл готов.",
      running: false,
    };

    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);

    expect(html).toContain("Озвучить");
    expect(html).toContain("Скачать");
    expect(html).toContain('download="report.xlsx"');
    expect(html.indexOf("Озвучить")).toBeLessThan(html.indexOf("Скачать"));
  });

  it("shows the runtime-owned QA receipt on a validated document chip", () => {
    const turn: AgentTurnData = {
      kind: "agent",
      id: "agent-validated-download",
      toolCalls: [{
        step: 12,
        tool: "resource_publish",
        arguments: { project_path: "generated/proposal.pdf" },
        result: "Published proposal.pdf",
        ok: true,
        download_url: "/api/skills/download/proposal.pdf",
        download_name: "proposal.pdf",
        document_qa: {
          status: "passed",
          sha256: "abc",
          page_count: 2,
          expected_page_count: 2,
          vision_status: "passed",
        },
      }],
      text: "Файл готов.",
      running: false,
    };

    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);

    expect(html).toContain('data-document-qa="passed"');
    expect(html).toContain("2 стр.");
  });

  it("renders every published artifact as its own download chip", () => {
    const turn: AgentTurnData = {
      kind: "agent",
      id: "agent-downloads",
      toolCalls: [
        {
          step: 12,
          tool: "resource_publish",
          arguments: { project_path: "generated/proposal-ddr4.docx" },
          result: "Published proposal-ddr4.docx",
          ok: true,
          download_url: "/api/skills/download/proposal-ddr4.docx",
          download_name: "proposal-ddr4.docx",
        },
        {
          step: 13,
          tool: "resource_publish",
          arguments: { project_path: "generated/proposal-ddr5.docx" },
          result: "Published proposal-ddr5.docx",
          ok: true,
          download_url: "/api/skills/download/proposal-ddr5.docx",
          download_name: "proposal-ddr5.docx",
        },
      ],
      text: "Оба файла готовы.",
      running: false,
    };

    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);

    expect(html.match(/data-download-chip=/g)).toHaveLength(2);
    expect(html).toContain('download="proposal-ddr4.docx"');
    expect(html).toContain('download="proposal-ddr5.docx"');
  });

  it("never inserts the gallery inside a fenced code block", () => {
    const turn: AgentTurnData = {
      kind: "agent",
      id: "agent-code-images",
      toolCalls: [],
      text: "```python\nx = 1\n\nprint(x)\n```\n\nПояснение после кода.",
      running: false,
      media: [{
        type: "image",
        url: "https://images.example/code.png",
        source_url: "https://source.example/code",
        title: "Пример",
        source: "source.example",
      }],
    };

    const html = renderToStaticMarkup(<AgentTurnView turn={turn} />);

    expect(html.indexOf("print(x)")).toBeLessThan(html.indexOf("answer-media-gallery"));
    expect(html.indexOf("answer-media-gallery")).toBeLessThan(html.indexOf("Пояснение после"));
  });
});
