import { describe, expect, it } from "vitest";
import type { WebSourceEvidence } from "../api/codeAgent";
import { unopenedReason, unopenedSources } from "./unopenedSources";

const source = (url: string, status: WebSourceEvidence["status"], error = ""): WebSourceEvidence => ({
  id: url + status, origin_run_id: "r", tool: "web_fetch", url, title: "", status, fetched_at: null,
  content_hash: "", excerpt_hash: "", doc_id: "", chunk_id: null, offset: null, quote: "",
  quote_verified: false, presented: false, claim_support: "not_assessed", error,
});

describe("unopenedSources", () => {
  it("lists each failed page once with a short reason", () => {
    const list = unopenedSources([
      source("https://www.reddit.com/r/x/1", "failed", "ERROR: HTTP 403 (https://www.reddit.com/r/x/1)"),
      source("https://www.reddit.com/r/x/1#top", "failed", "ERROR: HTTP 403 (https://www.reddit.com/r/x/1#top)"),
      source("https://news.kz/a", "failed",
        "ERROR: fetch failed: HTTPSConnectionPool(host='news.kz', port=443): Read timed out. (https://news.kz/a)"),
    ]);
    expect(list).toEqual([
      { url: "https://www.reddit.com/r/x/1", host: "reddit.com", reason: "403 — доступ закрыт" },
      { url: "https://news.kz/a", host: "news.kz", reason: "не ответил вовремя" },
    ]);
  });

  it("skips pages that were read in the same run and missing find phrases", () => {
    expect(unopenedSources([
      source("https://a.kz/p", "failed", "ERROR: HTTP 500 (https://a.kz/p)"),
      source("https://a.kz/p", "excerpt"),
      source("https://b.kz/doc", "failed", "ERROR: find phrase not found in extracted text (https://b.kz/doc)"),
      source("https://c.kz/q", "discovered"),
    ])).toEqual([]);
    expect(unopenedSources(undefined)).toEqual([]);
  });

  it.each([
    ["ERROR: HTTP 404 (https://x.org/a)", "404 — страницы нет"],
    ["не открылась: страница проверки от ботов (https://x.org) — возьми другой источник из выдачи", "страница проверки от ботов"],
    ["ERROR: fetch failed: ... NameResolutionError(...) (https://x.invalid)", "сайт не найден"],
    ["ERROR: empty or non-HTML response from https://x.org (https://x.org)", "пустая страница"],
    ["ERROR: browser fallback failed: Page.content: the page is navigating (https://x.org)", "страница не отрисовалась"],
    ["Источник временно пропущен после HTTP 503; следующая проверка при обращении после 12:00.",
      "пропущен после недавнего сбоя, проверка позже"],
  ])("reason for %s", (error, reason) => {
    expect(unopenedReason(error)).toBe(reason);
  });
});
