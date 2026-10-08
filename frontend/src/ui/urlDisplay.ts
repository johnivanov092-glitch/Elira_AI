/** Shorten a URL for display; callers keep the full URL in the link target. */
export function displayHostname(raw: string): string {
  try {
    return new URL(raw).hostname.replace(/^www\./, "");
  } catch {
    return raw;
  }
}
