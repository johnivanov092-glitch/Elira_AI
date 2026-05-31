import { request } from "./client";

type QueryParam = string | number | boolean | null | undefined;

export type GitResponse = Record<string, unknown>;

export type GitDiffRequest = {
  repo_path?: string;
  file_path?: string;
  [key: string]: unknown;
};

export type GitCommitRequest = {
  message?: string;
  add_all?: boolean;
  [key: string]: unknown;
};

function withParams(
  path: string,
  params: Record<string, QueryParam> = {},
): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    query.set(key, String(value));
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export async function getGitStatus(repoPath = ""): Promise<GitResponse> {
  return request<GitResponse>(withParams("/api/git/status", { repo_path: repoPath }));
}

export async function getGitLog(limit = 20, repoPath = ""): Promise<GitResponse> {
  return request<GitResponse>(withParams("/api/git/log", { limit, repo_path: repoPath }));
}

export async function getGitDiff(
  body: GitDiffRequest = { repo_path: "", file_path: "" },
): Promise<GitResponse> {
  return request<GitResponse>("/api/git/diff", { method: "POST", body });
}

export async function createGitCommit(
  body: GitCommitRequest = {},
): Promise<GitResponse> {
  return request<GitResponse>("/api/git/commit", { method: "POST", body });
}
