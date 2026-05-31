import { request } from "./client";

export type PlannerKeywords = Record<string, string[]>;

export type KeywordsResponse = {
  effective: PlannerKeywords;  // what planner uses right now
  user: PlannerKeywords;       // user overrides saved in DB (may be subset)
  defaults: PlannerKeywords;   // shipped defaults (full set)
};

export type ClassifyResult = {
  route: string;
  tools: string[];
  query: string;
  strategy: string;
  scores: Record<string, number>;
  temporal?: Record<string, unknown>;
  web_plan?: Record<string, unknown>;
};

export async function getPlannerKeywords(): Promise<KeywordsResponse> {
  return request<KeywordsResponse>("/api/chat/keywords");
}

export async function savePlannerKeywords(keywords: PlannerKeywords): Promise<{ ok: boolean; saved: PlannerKeywords; active_counts: Record<string, number> }> {
  return request("/api/chat/keywords", {
    method: "PUT",
    body: { keywords },
  });
}

export async function classifyQuery(query: string): Promise<ClassifyResult> {
  return request<ClassifyResult>("/api/chat/classify", {
    method: "POST",
    body: { query },
  });
}
