import type { Turn } from "./types";

export type FileArtifact = {
  path: string;
  content: string;
  old?: string;
  action?: string;
};

/** A generated binary artifact (Word/Excel) offered to the user as a download.
 *  Distinct from FileArtifact (which is text with a diff/preview) — a .docx has
 *  no text preview, so the UI renders a download button instead. */
export type DownloadArtifact = {
  url: string;
  name: string;
  /** Identity of the producing tool-call (its position in the run) folded with the
   *  URL — so re-generating the SAME filename (identical URL) yields a NEW key and
   *  re-opens the panel, instead of being suppressed as unchanged. */
  key: string;
};

export type Artifacts = {
  file?: FileArtifact;
  console?: string;
  download?: DownloadArtifact;
};

/** Derive previewable artifacts from a run's tool calls: the latest written
 *  file (write_file/edit_file), the latest generated download (file_gen), and
 *  the latest shell/sandbox output. */
export function deriveArtifacts(turns: Turn[]): Artifacts {
  let file: FileArtifact | undefined;
  let consoleOut: string | undefined;
  let download: DownloadArtifact | undefined;
  let callIdx = 0; // global tool-call position across the run — the download's identity
  for (const t of turns) {
    if (t.kind !== "agent") continue;
    for (const c of t.toolCalls) {
      callIdx++;
      if ((c.tool === "write_file" || c.tool === "edit_file") && c.touched_path) {
        file = {
          path: c.touched_path,
          content: c.new_content ?? "",
          old: c.old_content,
          action: c.diff_action,
        };
      } else if (c.tool === "file_gen" && c.ok !== false && c.download_url) {
        // Deterministic: comes from the runtime's verified structured fields, not
        // from any URL the model may or may not have written into its answer. The
        // key folds this call's position so a repeat generation re-opens the panel.
        download = {
          url: c.download_url,
          name: c.download_name || c.touched_path?.split(/[\\/]/).pop() || "файл",
          key: `${callIdx}:${c.download_url}`,
        };
      } else if (c.tool === "sandbox_run" || c.tool === "run_bash") {
        consoleOut = c.result;
      }
    }
  }
  return { file, console: consoleOut, download };
}

/** Stable key for the current download artifact (tool-call position + URL) — drives
 *  auto-open, and distinguishes two generations of the SAME filename. */
export function downloadArtifactKey(a: Artifacts): string {
  return a.download ? a.download.key : "";
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
