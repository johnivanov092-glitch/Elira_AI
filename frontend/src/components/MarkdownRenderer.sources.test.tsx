import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import MarkdownRenderer, { SourceCitationLink } from "./MarkdownRenderer";
import type { SourceCitation } from "../api/codeAgent";

const citation: SourceCitation = {
  source_id: "w_1", status: "matched", claim_support: "not_assessed",
  source: { id: "w_1", origin_run_id: "run", tool: "web_fetch", url: "https://example.org/source", title: "Test",
    status: "excerpt", quote: "20% in one test", content_hash: "abc", excerpt_hash: "def", doc_id: "",
    chunk_id: null, offset: 0, fetched_at: 1, quote_verified: true, presented: true, claim_support: "not_assessed", error: "" },
};

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
    const html = renderToStaticMarkup(<MarkdownRenderer content={'Result [[source:w_1]]\n\n[Site](https://example.com)\n\n`[[source:example]]`\n\n```\n[[source:fenced]]\n```'} citations={[citation]} />);
    expect(html).toContain('href="https://example.org/source"');
    expect(html).toContain('data-source-id="w_1"');
    expect(html).toContain('aria-label="Источник: Test — example.org"');
    expect(html).toContain("lucide-globe");
    expect(html).toContain(">example.org</span>");
    expect(html).not.toContain("[1]");
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
  it("shows the site for untitled receipts and keeps the original URL and excerpt", () => {
    const url = "https://docs.python.org/3.10/library/csv.html#csv.writer";
    const untitled: SourceCitation = { ...citation, source: { ...citation.source!, url, title: "" } };
    const html = renderToStaticMarkup(<SourceCitationLink citation={untitled} />);
    expect(html).toContain(">docs.python.org</span>");
    expect(html).toContain(`href="${url}"`);
    expect(html).toContain('data-source-id="w_1"');
    expect(html).toContain("Полученный фрагмент: 20% in one test");
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).not.toContain("<img");
    for (const unsafe of ["javascript:alert(1)", "data:text/html,test", "file:///C:/secret", "/relative"]) {
      const invalid: SourceCitation = { ...citation, source: { ...citation.source!, url: unsafe } };
      const rejected = renderToStaticMarkup(<SourceCitationLink citation={invalid} />);
      expect(rejected).not.toContain("href=");
      expect(rejected).toContain("Ссылка на источник недоступна");
    }
  });
});
