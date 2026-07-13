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
    expect(a.download).toEqual({
      url: "/api/skills/download/report.docx",
      name: "report.docx",
      key: "1:/api/skills/download/report.docx",
    });
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
    expect(first.download?.url).toBe(second.download?.url); // identical URL…
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
    expect(a.download?.name).toBe("x.xlsx");
  });

  it("ignores a failed file_gen (no download artifact)", () => {
    const a = deriveArtifacts([
      agentTurn([{ tool: "file_gen", ok: false, download_url: "/api/skills/download/x.docx" }]),
    ]);
    expect(a.download).toBeUndefined();
    expect(downloadArtifactKey(a)).toBe("");
  });

  it("does not treat write_file as a download, and keeps its file artifact", () => {
    const a = deriveArtifacts([
      agentTurn([
        { tool: "write_file", touched_path: "src/a.ts", new_content: "x", diff_action: "create" },
      ]),
    ]);
    expect(a.download).toBeUndefined();
    expect(a.file?.path).toBe("src/a.ts");
  });
});
