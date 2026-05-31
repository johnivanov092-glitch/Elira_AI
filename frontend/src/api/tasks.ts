import { request } from "./client";

type QueryParam = string | number | boolean | null | undefined;

export type TaskId = string | number;
export type TaskItem = Record<string, unknown>;
export type TaskStats = Record<string, unknown>;
export type TaskResponse = Record<string, unknown>;

export type TaskWriteRequest = {
  title?: string;
  description?: string;
  status?: string;
  [key: string]: unknown;
};

export type TasksOverview = {
  tasks: TaskItem[];
  stats: TaskStats;
};

type TasksPayload = {
  tasks?: TaskItem[];
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

function normalizeTasks(payload: TasksPayload | TaskItem[]): TaskItem[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.tasks)) return payload.tasks;
  return [];
}

export async function listTasks(status?: string): Promise<TasksPayload> {
  return request<TasksPayload>(withParams("/api/tasks/list", { status }));
}

export async function getTaskStats(): Promise<TaskStats> {
  return request<TaskStats>("/api/tasks/stats");
}

export async function getTasksOverview(
  filter = "active",
): Promise<TasksOverview> {
  let tasks: TaskItem[] = [];
  if (filter === "active") {
    const [todo, inProgress] = await Promise.all([
      listTasks("todo"),
      listTasks("in_progress"),
    ]);
    tasks = [...normalizeTasks(todo), ...normalizeTasks(inProgress)];
  } else if (filter === "all") {
    tasks = normalizeTasks(await listTasks());
  } else {
    tasks = normalizeTasks(await listTasks(filter));
  }
  const stats = await getTaskStats();
  return { tasks, stats };
}

export async function createTask(
  body: TaskWriteRequest = {},
): Promise<TaskResponse> {
  return request<TaskResponse>("/api/tasks/create", { method: "POST", body });
}

export async function updateTask(
  taskId: TaskId,
  body: TaskWriteRequest = {},
): Promise<TaskResponse> {
  return request<TaskResponse>(
    `/api/tasks/update/${encodeURIComponent(String(taskId))}`,
    { method: "PUT", body },
  );
}

export async function deleteTask(taskId: TaskId): Promise<TaskResponse> {
  return request<TaskResponse>(
    `/api/tasks/delete/${encodeURIComponent(String(taskId))}`,
    { method: "DELETE" },
  );
}
