import type { DocumentQa } from "../api/codeAgent";
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
  /** Present only when the runtime validated the exact published PDF/DOCX bytes. */
  documentQa?: DocumentQa;
  /** Identity of the producing tool-call (its position in the run) folded with the
   *  URL — so re-generating the SAME filename (identical URL) yields a NEW key and
   *  re-opens the panel, instead of being suppressed as unchanged. */
  key: string;
};

export type ServerArtifact = {
  url: string;
  port?: number;
  pid?: number;
  key: string;
};

export type Artifacts = {
  file?: FileArtifact;
  console?: string;
  downloads: DownloadArtifact[];
  server?: ServerArtifact;
};

function safeLoopbackUrl(value: unknown): string | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    if ((url.protocol !== "http:" && url.protocol !== "https:")
      || !["localhost", "127.0.0.1"].includes(host)) return undefined;
    return url.href.replace(/\/$/, "");
  } catch {
    return undefined;
  }
}

/** Derive previewable artifacts from a run's tool calls: the latest written
 *  file (write_file/edit_file), every successful generated download, and the
 *  latest shell/sandbox output. Tool-call identity is preserved even when two
 *  publications happen to use the same visible filename. */
export function deriveArtifacts(turns: Turn[]): Artifacts {
  let file: FileArtifact | undefined;
  let consoleOut: string | undefined;
  const downloads: DownloadArtifact[] = [];
  let server: ServerArtifact | undefined;
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
      }
      if (c.ok !== false && c.download_url) {
        // Deterministic: comes from the runtime's verified structured fields (set
        // ONLY after the file is verified on disk), not from any URL the model may
        // or may not have written into its answer. The key folds this call's
        // position so a repeat publish of the SAME url re-opens the panel.
        const download = {
          url: c.download_url,
          name: c.download_name || c.touched_path?.split(/[\\/]/).pop() || "файл",
          key: `${callIdx}:${c.download_url}`,
          documentQa: c.document_qa,
        };
        downloads.push(download);
      }
      if (c.tool === "run_server") {
        const action = String(c.arguments?.action || "start").toLowerCase();
        const url = c.ok !== false ? safeLoopbackUrl(c.actual_url || c.local_url) : undefined;
        if (url && c.server_started !== false) {
          server = {
            url,
            port: c.actual_port ?? c.port,
            pid: c.pid,
            key: `${callIdx}:${url}`,
          };
        } else if (c.ok !== false && action === "stop_all") {
          server = undefined;
        } else if (c.ok !== false && action === "stop"
          && (!server?.pid || Number(c.arguments?.pid) === server.pid)) {
          server = undefined;
        }
      } else if (c.tool === "sandbox_run" || c.tool === "run_bash") {
        consoleOut = c.result;
      }
    }
  }
  return { file, console: consoleOut, downloads, server };
}

export function serverArtifactKey(a: Artifacts): string {
  return a.server?.key ?? "";
}

/** Stable key for the current download artifact (tool-call position + URL) — drives
 *  auto-open, and distinguishes two generations of the SAME filename. */
export function downloadArtifactKey(a: Artifacts): string {
  return a.downloads.at(-1)?.key ?? "";
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
