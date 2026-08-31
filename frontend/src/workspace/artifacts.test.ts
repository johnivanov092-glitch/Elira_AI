import { describe, it, expect } from "vitest";
import { deriveArtifacts, downloadArtifactKey } from "./artifacts";
import type { Turn } from "./types";
import type { CodeAgentToolCall } from "../api/codeAgent";

/** Minimal agent turn carrying the given (partial) tool calls. deriveArtifacts
 *  only reads `kind` and `toolCalls`, so the rest is filled with harmless defaults. */
function agentTurn(calls: Partial<CodeAgentToolCall>[]): Turn {
  return {
    kind: "agent",
    id: "t1",
    text: "",
    running: false,
    toolCalls: calls.map((c, i) => ({
      step: i,
      tool: "",
      arguments: {},
      result: "",
      ...c,
    })) as CodeAgentToolCall[],
  } as Turn;
}

describe("deriveArtifacts — file_gen download artifact", () => {
  it("turns a successful file_gen tool call into a deterministic download artifact", () => {
    const a = deriveArtifacts([
      agentTurn([
        {
          tool: "file_gen",
          ok: true,
          download_url: "/api/skills/download/report.docx",
          download_name: "report.docx",
        },
      ]),
    ]);
    expect(a.downloads).toEqual([{
      url: "/api/skills/download/report.docx",
      name: "report.docx",
      key: "1:/api/skills/download/report.docx",
    }]);
    expect(downloadArtifactKey(a)).toBe("1:/api/skills/download/report.docx");
  });

  it("gives a repeat generation of the same filename a distinct key (re-opens the panel)", () => {
    const url = "/api/skills/download/report.docx";
    const first = deriveArtifacts([
      agentTurn([{ tool: "file_gen", ok: true, download_url: url, download_name: "report.docx" }]),
    ]);
    // The same file regenerated later in the run — earlier calls shift its position.
    const second = deriveArtifacts([
      agentTurn([
        { tool: "read_file" },
        { tool: "file_gen", ok: true, download_url: url, download_name: "report.docx" },
      ]),
    ]);
    expect(first.downloads[0]?.url).toBe(second.downloads[0]?.url); // identical URL…
    expect(downloadArtifactKey(first)).not.toBe(downloadArtifactKey(second)); // …distinct key
  });

  it("falls back to the touched_path basename when download_name is absent", () => {
    const a = deriveArtifacts([
      agentTurn([
        {
          tool: "file_gen",
          ok: true,
          download_url: "/api/skills/download/x.xlsx",
          touched_path: "generated/x.xlsx",
        },
      ]),
    ]);
    expect(a.downloads[0]?.name).toBe("x.xlsx");
  });

  it("ignores a failed file_gen (no download artifact)", () => {
    const a = deriveArtifacts([
      agentTurn([{ tool: "file_gen", ok: false, download_url: "/api/skills/download/x.docx" }]),
    ]);
    expect(a.downloads).toEqual([]);
    expect(downloadArtifactKey(a)).toBe("");
  });

  it("does not treat write_file as a download, and keeps its file artifact", () => {
    const a = deriveArtifacts([
      agentTurn([
        { tool: "write_file", touched_path: "src/a.ts", new_content: "x", diff_action: "create" },
      ]),
    ]);
    expect(a.downloads).toEqual([]);
    expect(a.file?.path).toBe("src/a.ts");
  });
});

describe("deriveArtifacts — resource_publish download artifact (R4B)", () => {
  it("turns a successful resource_publish into a download artifact", () => {
    const a = deriveArtifacts([
      agentTurn([
        {
          tool: "resource_publish",
          ok: true,
          download_url: "/api/skills/download/clip.mp3",
          download_name: "clip.mp3",
        },
      ]),
    ]);
    expect(a.downloads).toEqual([{
      url: "/api/skills/download/clip.mp3",
      name: "clip.mp3",
      key: "1:/api/skills/download/clip.mp3",
    }]);
  });

  it("ignores a failed resource_publish (no download card)", () => {
    const a = deriveArtifacts([
      agentTurn([{ tool: "resource_publish", ok: false, download_url: "/api/skills/download/x.mp3" }]),
    ]);
    expect(a.downloads).toEqual([]);
  });

  it("gives the SAME published url from two tool-calls distinct artifact keys", () => {
    const url = "/api/skills/download/clip.mp3";
    const first = deriveArtifacts([
      agentTurn([{ tool: "resource_publish", ok: true, download_url: url, download_name: "clip.mp3" }]),
    ]);
    const second = deriveArtifacts([
      agentTurn([
        { tool: "run_bash" },
        { tool: "resource_publish", ok: true, download_url: url, download_name: "clip.mp3" },
      ]),
    ]);
    expect(first.downloads[0]?.url).toBe(second.downloads[0]?.url);
    expect(downloadArtifactKey(first)).not.toBe(downloadArtifactKey(second));
  });

  it("keeps every distinct published file in production order", () => {
    const a = deriveArtifacts([
      agentTurn([
        {
          tool: "resource_publish",
          ok: true,
          download_url: "/api/skills/download/proposal-ddr4.docx",
          download_name: "proposal-ddr4.docx",
        },
        {
          tool: "resource_publish",
          ok: true,
          download_url: "/api/skills/download/proposal-ddr5.docx",
          download_name: "proposal-ddr5.docx",
        },
      ]),
    ]);

    expect(a.downloads.map((item) => item.name)).toEqual([
      "proposal-ddr4.docx",
      "proposal-ddr5.docx",
    ]);
    expect(downloadArtifactKey(a)).toBe("2:/api/skills/download/proposal-ddr5.docx");
  });

  it("carries the server-owned document QA receipt into the download artifact", () => {
    const a = deriveArtifacts([
      agentTurn([{
        tool: "resource_publish",
        ok: true,
        download_url: "/api/skills/download/proposal.pdf",
        download_name: "proposal.pdf",
        document_qa: {
          status: "passed",
          sha256: "abc",
          page_count: 1,
          vision_status: "passed",
        },
      }]),
    ]);

    expect(a.downloads[0]?.documentQa).toMatchObject({
      status: "passed",
      page_count: 1,
      sha256: "abc",
    });
  });

  it("keeps repeated successful publications with the same visible filename", () => {
    const a = deriveArtifacts([
      agentTurn([
        {
          tool: "resource_publish",
          ok: true,
          download_url: "/api/skills/download/report.pdf?v=1",
          download_name: "report.pdf",
        },
        {
          tool: "resource_publish",
          ok: true,
          download_url: "/api/skills/download/report.pdf?v=2",
          download_name: "report.pdf",
        },
      ]),
    ]);

    expect(a.downloads).toHaveLength(2);
    expect(a.downloads[0]?.key).not.toBe(a.downloads[1]?.key);
  });
});

describe("deriveArtifacts — live server preview", () => {
  it("creates a preview artifact from the server-owned actual_url", () => {
    const call = {
      tool: "run_server",
      ok: true,
      arguments: { action: "start", command: "npm run dev" },
      actual_url: "http://localhost:5174",
      actual_port: 5174,
      pid: 4242,
    } satisfies Partial<CodeAgentToolCall>;
    const a = deriveArtifacts([agentTurn([call])]);
    expect(a.server).toEqual({
      url: "http://localhost:5174",
      port: 5174,
      pid: 4242,
      key: "1:http://localhost:5174",
    });
  });

  it("clears only the matching preview on a successful stop", () => {
    const calls = [
      {
        tool: "run_server", ok: true,
        arguments: { action: "start", command: "npm run dev" },
        actual_url: "http://localhost:5174", actual_port: 5174, pid: 4242,
      },
      { tool: "run_server", ok: true, arguments: { action: "stop", pid: 4242 } },
    ] satisfies Partial<CodeAgentToolCall>[];
    const a = deriveArtifacts([agentTurn(calls)]);
    expect(a.server).toBeUndefined();
  });

  it("keeps the preview when an unrelated or failed server call occurs", () => {
    const calls = [
      {
        tool: "run_server", ok: true,
        arguments: { action: "start", command: "npm run dev" },
        actual_url: "http://localhost:5174", actual_port: 5174, pid: 4242,
      },
      { tool: "run_server", ok: false, arguments: { action: "logs", pid: 9999 } },
    ] satisfies Partial<CodeAgentToolCall>[];
    const a = deriveArtifacts([agentTurn(calls)]);
    expect(a.server?.url).toBe("http://localhost:5174");
  });

  it("rejects a non-loopback URL even when a tool event claims success", () => {
    const a = deriveArtifacts([agentTurn([{
      tool: "run_server",
      ok: true,
      actual_url: "https://example.com/app",
      actual_port: 443,
      pid: 4242,
    }])]);
    expect(a.server).toBeUndefined();
  });
});
