// tasks.ts — tasks and pipelines API

import { request } from "./client";
import { withParams } from "./apiUtils";

export async function listTasks(status?: string) {
  return request(withParams("/api/tasks/list", { status })) as Promise<Record<string, unknown>>;
}

export async function getTaskStats() {
  return request("/api/tasks/stats");
}

export async function getTasksOverview(filter = "active") {
  let tasks: unknown[] = [];
  if (filter === "active") {
    const [todo, inProgress] = await Promise.all([listTasks("todo"), listTasks("in_progress")]);
    tasks = [...((todo?.tasks as unknown[]) || []), ...((inProgress?.tasks as unknown[]) || [])];
  } else if (filter === "all") {
    const data = await listTasks();
    tasks = (data?.tasks as unknown[]) || [];
  } else {
    const data = await listTasks(filter);
    tasks = (data?.tasks as unknown[]) || [];
  }
  const stats = await getTaskStats();
  return { tasks, stats };
}

export async function createTask(body: Record<string, unknown> = {}) {
  return request("/api/tasks/create", { method: "POST", body });
}

export async function updateTask(taskId: string, body: Record<string, unknown> = {}) {
  return request(`/api/tasks/update/${encodeURIComponent(taskId)}`, { method: "PUT", body });
}

export async function deleteTask(taskId: string) {
  return request(`/api/tasks/delete/${encodeURIComponent(taskId)}`, { method: "DELETE" });
}

export async function listPipelines() {
  const payload = await request("/api/pipelines/list") as Record<string, unknown>;
  return (payload?.pipelines as unknown[]) || [];
}

export async function createPipeline(body: Record<string, unknown> = {}) {
  return request("/api/pipelines/create", { method: "POST", body });
}

export async function runPipeline(pipelineId: string) {
  return request(`/api/pipelines/run/${encodeURIComponent(pipelineId)}`, { method: "POST" });
}

export async function updatePipeline(pipelineId: string, body: Record<string, unknown> = {}) {
  return request(`/api/pipelines/update/${encodeURIComponent(pipelineId)}`, { method: "PUT", body });
}

export async function deletePipeline(pipelineId: string) {
  return request(`/api/pipelines/delete/${encodeURIComponent(pipelineId)}`, { method: "DELETE" });
}
