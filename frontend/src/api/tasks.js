// tasks.js — tasks and pipelines API

import { request } from "./client";
import { withParams } from "./apiUtils";

export async function listTasks(status) {
  return request(withParams("/api/tasks/list", { status }));
}

export async function getTaskStats() {
  return request("/api/tasks/stats");
}

export async function getTasksOverview(filter = "active") {
  let tasks = [];
  if (filter === "active") {
    const [todo, inProgress] = await Promise.all([listTasks("todo"), listTasks("in_progress")]);
    tasks = [...(todo?.tasks || []), ...(inProgress?.tasks || [])];
  } else if (filter === "all") {
    const data = await listTasks();
    tasks = data?.tasks || [];
  } else {
    const data = await listTasks(filter);
    tasks = data?.tasks || [];
  }
  const stats = await getTaskStats();
  return { tasks, stats };
}

export async function createTask(body = {}) {
  return request("/api/tasks/create", { method: "POST", body });
}

export async function updateTask(taskId, body = {}) {
  return request(`/api/tasks/update/${encodeURIComponent(taskId)}`, { method: "PUT", body });
}

export async function deleteTask(taskId) {
  return request(`/api/tasks/delete/${encodeURIComponent(taskId)}`, { method: "DELETE" });
}

export async function listPipelines() {
  const payload = await request("/api/pipelines/list");
  return payload?.pipelines || [];
}

export async function createPipeline(body = {}) {
  return request("/api/pipelines/create", { method: "POST", body });
}

export async function runPipeline(pipelineId) {
  return request(`/api/pipelines/run/${encodeURIComponent(pipelineId)}`, { method: "POST" });
}

export async function updatePipeline(pipelineId, body = {}) {
  return request(`/api/pipelines/update/${encodeURIComponent(pipelineId)}`, { method: "PUT", body });
}

export async function deletePipeline(pipelineId) {
  return request(`/api/pipelines/delete/${encodeURIComponent(pipelineId)}`, { method: "DELETE" });
}
