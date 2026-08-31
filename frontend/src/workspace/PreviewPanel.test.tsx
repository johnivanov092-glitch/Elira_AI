import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PreviewPanel } from "./PreviewPanel";

describe("PreviewPanel live server", () => {
  it("renders the server-owned loopback URL in an iframe", () => {
    const html = renderToStaticMarkup(
      <PreviewPanel
        artifacts={{
          downloads: [],
          server: {
            url: "http://localhost:5174",
            port: 5174,
            pid: 4242,
            key: "1:http://localhost:5174",
          },
        }}
        project="C:/workspace/crm"
        onClose={() => undefined}
      />,
    );

    expect(html).toContain('title="live preview"');
    expect(html).toContain('src="http://localhost:5174"');
  });

  it("renders a generated PDF through the inline view endpoint", () => {
    const html = renderToStaticMarkup(
      <PreviewPanel
        artifacts={{
          downloads: [{
            url: "/api/skills/download/report.pdf",
            name: "report.pdf",
            key: "1:/api/skills/download/report.pdf",
            documentQa: {
              status: "passed",
              page_count: 1,
              vision_status: "passed",
            },
          }],
        }}
        project="C:/workspace/crm"
        onClose={() => undefined}
      />,
    );

    expect(html).toContain('title="PDF preview"');
    expect(html).toContain('src="http://127.0.0.1:8000/api/skills/view/report.pdf"');
    expect(html).toContain('data-document-qa="passed"');
    expect(html).toContain("Проверено");
    expect(html).toContain("1 стр.");
  });

  it("lists every download and previews the newest previewable artifact", () => {
    const html = renderToStaticMarkup(
      <PreviewPanel
        artifacts={{
          downloads: [
            {
              url: "/api/skills/download/proposal-ddr4.pdf",
              name: "proposal-ddr4.pdf",
              key: "1:/api/skills/download/proposal-ddr4.pdf",
            },
            {
              url: "/api/skills/download/proposal-ddr5.pdf",
              name: "proposal-ddr5.pdf",
              key: "2:/api/skills/download/proposal-ddr5.pdf",
            },
          ],
        }}
        project="C:/workspace/crm"
        onClose={() => undefined}
      />,
    );

    expect(html.match(/data-artifact-option=/g)).toHaveLength(2);
    expect(html).toContain("proposal-ddr4.pdf");
    expect(html).toContain("proposal-ddr5.pdf");
    expect(html).toContain("/api/skills/view/proposal-ddr5.pdf");
  });
});
