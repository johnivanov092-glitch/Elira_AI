import type { Turn } from "./types";

/** Drop transient fields (running flag, active tool, blob URLs) before saving. */
export function serializeTurns(turns: Turn[]): unknown[] {
  return turns.map((t) => {
    if (t.kind === "agent") return { ...t, running: false, activeTool: undefined };
    if (t.kind === "files") return { ...t, files: t.files.map((f) => ({ ...f, url: undefined })) };
    return t;
  });
}

/** Parse stored turns back into the live shape; tolerant of legacy/unknown rows. */
export function deserializeTurns(raw: unknown[]): Turn[] {
  if (!Array.isArray(raw)) return [];
  const out: Turn[] = [];
  for (const x of raw) {
    if (!x || typeof x !== "object" || !("kind" in x)) continue;
    const t = x as Turn;
    if (t.kind === "user" || t.kind === "files") out.push(t);
    else if (t.kind === "agent") out.push({ ...t, running: false, activeTool: undefined });
  }
  return out;
}
