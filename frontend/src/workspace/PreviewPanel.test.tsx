import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PreviewPanel } from "./PreviewPanel";

describe("PreviewPanel live server", () => {
  it("renders the server-owned loopback URL in an iframe", () => {
    const html = renderToStaticMarkup(
      <PreviewPanel
        artifacts={{
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
});
