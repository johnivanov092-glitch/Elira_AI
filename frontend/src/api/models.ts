import { request } from "./client";


export type LocalModelsResponse = {
  ok?: boolean;
  models: unknown[];
  count?: number;
  error?: string;
  warnings?: string;
};

export async function listLocalModels(): Promise<LocalModelsResponse> {
  return request<LocalModelsResponse>("/api/models");
}
