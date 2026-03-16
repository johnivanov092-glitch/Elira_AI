const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return response.json();
}

export function getPhase10Status() {
  return request("/api/phase10/status");
}

export function runResearchPipeline(query, options = {}) {
  return request("/api/phase10/research/run", {
    method: "POST",
    body: JSON.stringify({
      query,
      multi_search: options.multi_search !== false,
      dedupe: options.dedupe !== false,
      documentation_mode: !!options.documentation_mode,
      max_results: options.max_results || 12,
    }),
  });
}

export function runBrowserRuntime(url, actions = []) {
  return request("/api/phase10/browser/run", {
    method: "POST",
    body: JSON.stringify({ url, actions }),
  });
}
