import type { WebSourceEvidence } from "../api/codeAgent";
import { displayHostname } from "../ui/urlDisplay";

export type UnopenedSource = { url: string; host: string; reason: string };

const pageKey = (url: string) => url.split("#", 1)[0];

/** Short reason for a page that did not open; the raw error stays in the run journal. */
export function unopenedReason(error: string): string {
  const text = error.replace(/^ERROR:\s*/i, "");
  if (/временно пропущен/i.test(text)) return "пропущен после недавнего сбоя, проверка позже";
  const http = /HTTP (\d{3})/.exec(text);
  if (http) {
    if (http[1] === "404") return "404 — страницы нет";
    if (http[1] === "403") return "403 — доступ закрыт";
    if (http[1] === "429") return "429 — слишком много запросов";
    return `HTTP ${http[1]}`;
  }
  const wall = /^не открылась: ([^(]+?)\s*\(/.exec(text);
  if (wall) return wall[1];
  if (/timed? ?out|timeout/i.test(text)) return "не ответил вовремя";
  if (/NameResolution|getaddrinfo|Name or service not known/i.test(text)) return "сайт не найден";
  if (/Connection aborted|RemoteDisconnected|Connection reset|refused/i.test(text)) return "соединение оборвано";
  if (/Max retries exceeded/i.test(text)) return "сайт недоступен";
  if (/empty or non-HTML|empty page/i.test(text)) return "пустая страница";
  if (/browser fallback failed|navigating/i.test(text)) return "страница не отрисовалась";
  return text.replace(/\s*\(https?:\/\/[^)]*\)\s*$/, "").slice(0, 80) || "не открылась";
}

/** Pages this answer tried but could not read. A page that opened later in the run, or a
 *  find phrase missing on an opened page, is not "unopened". */
export function unopenedSources(sources: WebSourceEvidence[] | undefined): UnopenedSource[] {
  if (!sources?.length) return [];
  const read = new Set(sources.filter(s => s.status === "fetched" || s.status === "excerpt").map(s => pageKey(s.url)));
  const seen = new Set<string>();
  const out: UnopenedSource[] = [];
  for (const source of sources) {
    const key = pageKey(source.url || "");
    if (source.status !== "failed" || !key || read.has(key) || seen.has(key)) continue;
    if (/find phrase not found/i.test(source.error || "")) continue;
    seen.add(key);
    out.push({ url: key, host: displayHostname(key), reason: unopenedReason(source.error || "") });
  }
  return out;
}
