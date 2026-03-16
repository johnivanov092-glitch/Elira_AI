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

export function getPhase11Status() {
  return request("/api/phase11/status");
}

export function previewPatch(file_path, new_content) {
  return request("/api/phase11/patch/preview", {
    method: "POST",
    body: JSON.stringify({ file_path, new_content }),
  });
}

export function applyPatch(file_path, new_content, expected_old_sha256 = null) {
  return request("/api/phase11/patch/apply", {
    method: "POST",
    body: JSON.stringify({ file_path, new_content, expected_old_sha256 }),
  });
}

export function rollbackPatch(backup_id) {
  return request("/api/phase11/patch/rollback", {
    method: "POST",
    body: JSON.stringify({ backup_id }),
  });
}

export function verifyPatch(file_path) {
  return request("/api/phase11/patch/verify", {
    method: "POST",
    body: JSON.stringify({ file_path }),
  });
}

export function listPatchBackups(limit = 50) {
  return request(`/api/phase11/patch/backups?limit=${limit}`);
}
