const API = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(url, options = {}) {
  const res = await fetch(API + url, {
    method: options.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });

  const contentType = res.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await res.json()
    : await res.text();

  if (!res.ok) {
    const message =
      typeof payload === "string"
        ? payload
        : payload?.detail || payload?.message || "API error";
    throw new Error(message);
  }

  return payload;
}

function asArray(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.data)) return payload.data;
  return [];
}

export const api = {
  runPhase20: ({ goal, selected_paths }) =>
    request("/api/jarvis/phase20/run", {
      method: "POST",
      body: { goal, selected_paths },
    }),

  listPhase20History: async () => {
    const payload = await request("/api/jarvis/phase20/history/list");
    return asArray(payload);
  },

  getPhase20HistoryItem: (id) =>
    request(`/api/jarvis/phase20/history/get?id=${encodeURIComponent(id)}`),
};
