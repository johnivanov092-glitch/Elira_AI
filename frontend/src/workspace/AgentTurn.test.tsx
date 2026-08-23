import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AgentTurnView } from "./AgentTurn";
import type { AgentTurnData } from "./types";

describe("AgentTurn structured analysis status", () => {
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
