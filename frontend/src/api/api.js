const API_BASE = 'http://127.0.0.1:8000'

async function parseJson(response) {
  const text = await response.text()

  if (!response.ok) {
    throw new Error(text || `HTTP ${response.status}`)
  }

  try {
    return text ? JSON.parse(text) : {}
  } catch {
    throw new Error('Backend returned invalid JSON')
  }
}

async function apiGet(path) {
  return parseJson(await fetch(`${API_BASE}${path}`))
}

async function apiSend(path, method = 'POST', payload = undefined) {
  return parseJson(await fetch(`${API_BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  }))
}

export async function getModels() { return apiGet('/api/models') }
export async function getProfiles() { return apiGet('/api/profiles') }
export async function getSettings() { return apiGet('/api/settings') }
export async function saveSettings(payload) { return apiSend('/api/settings', 'POST', payload) }

export async function getLibraryFiles() { return apiGet('/api/library/files') }
export async function setLibraryActive(filename, active) {
  return apiSend('/api/library/activate', 'POST', { filename, active })
}
export async function deleteLibraryFile(filename) {
  return parseJson(await fetch(`${API_BASE}/api/library/files/${encodeURIComponent(filename)}`, { method: 'DELETE' }))
}

export async function getMemoryProfiles() { return apiGet('/api/memory/profiles') }
export async function getMemoryItems(profile) { return apiGet(`/api/memory/items/${encodeURIComponent(profile)}`) }
export async function addMemory(payload) { return apiSend('/api/memory/add', 'POST', payload) }
export async function searchMemory(payload) { return apiSend('/api/memory/search', 'POST', payload) }
export async function deleteMemory(profile, itemId) {
  return parseJson(await fetch(`${API_BASE}/api/memory/items/${encodeURIComponent(profile)}/${encodeURIComponent(itemId)}`, {
    method: 'DELETE',
  }))
}

export async function sendChat(payload) { return apiSend('/api/chat/send', 'POST', payload) }
export async function runAgent(payload) { return apiSend('/api/agents/run', 'POST', payload) }
