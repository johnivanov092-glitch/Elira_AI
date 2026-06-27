export function modelName(m: unknown): string {
  if (typeof m === "string") return m;
  if (m && typeof m === "object") {
    const o = m as Record<string, unknown>;
    return String(o.name ?? o.id ?? o.model ?? "");
  }
  return "";
}
