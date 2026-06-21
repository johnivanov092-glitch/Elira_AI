import type { Turn } from "./types";

export type FileArtifact = {
  path: string;
  content: string;
  old?: string;
  action?: string;
};

export type Artifacts = {
  file?: FileArtifact;
  console?: string;
};

/** Derive previewable artifacts from a run's tool calls: the latest written
 *  file (write_file/edit_file) and the latest shell/sandbox output. */
export function deriveArtifacts(turns: Turn[]): Artifacts {
  let file: FileArtifact | undefined;
  let consoleOut: string | undefined;
  for (const t of turns) {
    if (t.kind !== "agent") continue;
    for (const c of t.toolCalls) {
      if ((c.tool === "write_file" || c.tool === "edit_file") && c.touched_path) {
        file = {
          path: c.touched_path,
          content: c.new_content ?? "",
          old: c.old_content,
          action: c.diff_action,
        };
      } else if (c.tool === "sandbox_run" || c.tool === "run_bash") {
        consoleOut = c.result;
      }
    }
  }
  return { file, console: consoleOut };
}

/** Stable key for the current file artifact (path + size) — drives auto-open. */
export function fileArtifactKey(a: Artifacts): string {
  if (!a.file) return "";
  return `${a.file.path}:${a.file.content.length}`;
}

export type DiffLine = { sign: " " | "-" | "+"; text: string };

/** Minimal line diff: trim common prefix/suffix, mark the middle. */
export function lineDiff(oldText: string, newText: string, limit = 400): DiffLine[] {
  const a = oldText.split("\n");
  const b = newText.split("\n");
  let s = 0;
  while (s < a.length && s < b.length && a[s] === b[s]) s++;
  let ea = a.length;
  let eb = b.length;
  while (ea > s && eb > s && a[ea - 1] === b[eb - 1]) { ea--; eb--; }
  const out: DiffLine[] = [];
  for (let i = Math.max(0, s - 2); i < s; i++) out.push({ sign: " ", text: a[i] });
  for (let i = s; i < ea; i++) out.push({ sign: "-", text: a[i] });
  for (let i = s; i < eb; i++) out.push({ sign: "+", text: b[i] });
  for (let i = eb; i < Math.min(b.length, eb + 2); i++) out.push({ sign: " ", text: b[i] });
  return out.slice(0, limit);
}
