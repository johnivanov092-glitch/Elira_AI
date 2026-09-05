import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import MarkdownRenderer from "./MarkdownRenderer";
import type { SourceCitation } from "../api/codeAgent";

describe("runtime source pointers", () => {
  it("preserves a whole fenced example instead of interpreting its pointer", () => {
    const html = renderToStaticMarkup(<MarkdownRenderer content={'```text\n[[source:example]]\n```'} citations={[]} />);
    expect(html).toContain("[[source:example]]");
    expect(html).not.toContain("не сопоставлен");
    expect(html).not.toContain("href=");
  });
  it("leaves pending and unresolved pointers without a link", () => {
    for (const citations of [undefined, []]) {
      const html = renderToStaticMarkup(<MarkdownRenderer content="Result [[source:missing]]" citations={citations} />);
      expect(html).not.toContain("href=");
      expect(html).toContain("источник");
    }
  });
  it("resolves only the runtime URL and preserves ordinary links and code examples", () => {
    const citation: SourceCitation = {
      source_id: "w_1", status: "matched", claim_support: "not_assessed",
      source: { id: "w_1", origin_run_id: "run", tool: "web_fetch", url: "https://example.org/source", title: "Test",
        status: "excerpt", quote: "20% in one test", content_hash: "abc", excerpt_hash: "def", doc_id: "",
        chunk_id: null, offset: 0, fetched_at: 1, quote_verified: true, presented: true, claim_support: "not_assessed", error: "" },
    };
    const html = renderToStaticMarkup(<MarkdownRenderer content={'Result [[source:w_1]]\n\n[Site](https://example.com)\n\n`[[source:example]]`\n\n```\n[[source:fenced]]\n```'} citations={[citation]} />);
    expect(html).toContain('href="https://example.org/source"');
    expect(html).toContain('href="https://example.com"');
    expect(html).toContain("[[source:example]]");
    expect(html).toContain("[[source:fenced]]");
    expect(html).not.toContain("не сопоставлен");
    for (const markup of ["**Result [[source:w_1]]**", "*Result [[source:w_1]]*", "~~Result [[source:w_1]]~~"]) {
      const formatted = renderToStaticMarkup(<MarkdownRenderer content={markup} citations={[citation]} />);
      expect(formatted).toContain('href="https://example.org/source"');
      expect(formatted).not.toContain("[[source:w_1]]");
    }
  });
});
