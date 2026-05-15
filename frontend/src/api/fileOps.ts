import { request } from "./client";

export type FileOpResponse = Record<string, unknown>;

export type FileDiffRequest = {
  path?: string;
  new_content?: string;
  old_content?: string;
  [key: string]: unknown;
};

export type FileWriteRequest = {
  path?: string;
  content?: string;
  create_dirs?: boolean;
  [key: string]: unknown;
};

export type ExtractTextResponse = {
  text?: string;
  [key: string]: unknown;
};

export async function extractUploadedFileText(
  file: File,
): Promise<ExtractTextResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return request<ExtractTextResponse>("/api/files/extract-text", {
    method: "POST",
    body: formData,
  });
}

export async function diffFile(
  body: FileDiffRequest = {},
): Promise<FileOpResponse> {
  return request<FileOpResponse>("/api/file-ops/diff", {
    method: "POST",
    body,
  });
}

export async function writeFile(
  body: FileWriteRequest = {},
): Promise<FileOpResponse> {
  return request<FileOpResponse>("/api/file-ops/write", {
    method: "POST",
    body,
  });
}
