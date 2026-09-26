import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FolderState } from "../api/chatFolders";

const KEY = "elira.chatFolders.v1";
const EMPTY: FolderState = { folders: [], assign: {}, collapsed: {} };
const LEGACY: FolderState = {
  folders: [{ id: "legacy", name: "Старая папка" }],
  assign: { "old-session": "legacy" },
  collapsed: { legacy: true },
};
const SERVER: FolderState = {
  folders: [{ id: "work", name: "Работа" }, { id: "personal", name: "Личное" }],
  assign: { "session-1": "work", "session-2": "personal" },
  collapsed: { work: false, personal: true },
};

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

function response(state: FolderState | null): Response {
  return new Response(JSON.stringify({ ok: true, state }), {
    headers: { "Content-Type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

let storage: MemoryStorage;
beforeEach(() => {
  vi.resetModules();
  storage = new MemoryStorage();
  vi.stubGlobal("localStorage", storage);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("chat folder persistence across frontend origins", () => {
  it("hydrates a fresh localStorage from backend without initializing over existing state", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(response(SERVER));
    vi.stubGlobal("fetch", fetch);
    const store = await import("./chatFolders");
    expect(store.getSnapshot()).toEqual(EMPTY);

    await store.synchronize();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toContain("/api/code-agent/chat-folders");
    expect(fetch.mock.calls[0][1]?.method).toBe("GET");
    expect(store.getSnapshot()).toEqual(SERVER);
    expect(JSON.parse(storage.getItem(KEY)!)).toEqual(SERVER);
    expect(store.getSyncSnapshot()).toEqual({ loading: false, error: null });
  });

  it("initializes legacy state once and uses the server winner if initialization races", async () => {
    storage.setItem(KEY, JSON.stringify(LEGACY));
    const initialize = deferred<Response>();
    const fetch = vi.fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(response(null))
      .mockReturnValueOnce(initialize.promise)
      .mockResolvedValueOnce(response(SERVER));
    vi.stubGlobal("fetch", fetch);
    const store = await import("./chatFolders");
    expect(store.getSnapshot()).toEqual(LEGACY);

    const pending = store.synchronize();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(fetch.mock.calls[1][1]?.method).toBe("PUT");
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({ state: LEGACY });
    expect(store.getSnapshot()).toEqual(LEGACY);
    // Another client initialized first; PUT-init returns that canonical state.
    initialize.resolve(response(SERVER));
    await pending;
    expect(store.getSnapshot()).toEqual(SERVER);
    expect(JSON.parse(storage.getItem(KEY)!)).toEqual(SERVER);

    await store.synchronize();
    expect(fetch.mock.calls.map(([, init]) => init?.method)).toEqual(["GET", "PUT", "GET"]);
    expect(store.getSnapshot()).toEqual(SERVER);
  });

  it("retains cache on GET failure, never PUTs empty state, and restores on retry", async () => {
    const cached = JSON.stringify(LEGACY);
    storage.setItem(KEY, cached);
    const fetch = vi.fn<typeof globalThis.fetch>()
      .mockRejectedValueOnce(new Error("backend unavailable"))
      .mockResolvedValueOnce(response(SERVER));
    vi.stubGlobal("fetch", fetch);
    const store = await import("./chatFolders");
    const statuses: ReturnType<typeof store.getSyncSnapshot>[] = [];
    const unsubscribe = store.subscribe(() => statuses.push(store.getSyncSnapshot()));

    await store.synchronize();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).toEqual(LEGACY);
    expect(storage.getItem(KEY)).toBe(cached);
    expect(store.getSyncSnapshot()).toEqual({ loading: false, error: "backend unavailable" });
    expect(statuses).toContainEqual({ loading: false, error: "backend unavailable" });

    await store.synchronize();
    expect(fetch.mock.calls.map(([, init]) => init?.method)).toEqual(["GET", "GET"]);
    expect(store.getSnapshot()).toEqual(SERVER);
    expect(JSON.parse(storage.getItem(KEY)!)).toEqual(SERVER);
    expect(store.getSyncSnapshot()).toEqual({ loading: false, error: null });
    unsubscribe();
  });

  it("drops a confirmed missing-folder rejection and continues queued creation with a visible error", async () => {
    storage.setItem(KEY, JSON.stringify(LEGACY));
    vi.stubGlobal("crypto", { randomUUID: () => "replacement" });
    const created: FolderState = {
      folders: [{ id: "fld-replacement", name: "Новая папка" }], assign: {}, collapsed: {},
    };
    const create = deferred<Response>();
    const fetch = vi.fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(response(LEGACY))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Folder not found" }), {
        status: 404, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(response(EMPTY))
      .mockReturnValueOnce(create.promise);
    vi.stubGlobal("fetch", fetch);
    const store = await import("./chatFolders");

    store.renameFolder("legacy", "Переименовано");
    store.createFolder("Новая папка");
    const drained = store.synchronize();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
    expect(store.getSnapshot()).toEqual(EMPTY);
    expect(JSON.parse(storage.getItem(KEY)!)).toEqual(EMPTY);
    expect(fetch.mock.calls.map(([, init]) => init?.method)).toEqual(["GET", "PATCH", "GET", "PATCH"]);
    expect(JSON.parse(String(fetch.mock.calls[3][1]?.body))).toEqual({
      operation: "create", folder_id: "fld-replacement", name: "Новая папка",
    });

    create.resolve(response(created));
    await drained;
    expect(store.getSnapshot()).toEqual(created);
    expect(JSON.parse(storage.getItem(KEY)!)).toEqual(created);
    expect(store.getSyncSnapshot()).toEqual({
      loading: false, error: "Изменение папок не применено: Folder not found",
    });
  });

  it("serializes rapid actions behind hydration and publishes only acknowledged mutations", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-folder" });
    const id = "fld-test-folder";
    const created: FolderState = { folders: [{ id, name: "Создано" }], assign: {}, collapsed: {} };
    const renamed: FolderState = { ...created, folders: [{ id, name: "Переименовано" }] };
    const assigned: FolderState = { ...renamed, assign: { session: id } };
    const collapsed: FolderState = { ...assigned, collapsed: { [id]: true } };
    const expanded: FolderState = { ...assigned, collapsed: { [id]: false } };
    const initialGet = deferred<Response>();
    const initialCreate = deferred<Response>();
    const rejectedRename = deferred<Response>();
    const fetch = vi.fn<typeof globalThis.fetch>()
      .mockReturnValueOnce(initialGet.promise)
      .mockReturnValueOnce(initialCreate.promise)
      .mockReturnValueOnce(rejectedRename.promise)
      .mockResolvedValueOnce(response(created))
      .mockResolvedValueOnce(response(renamed))
      .mockResolvedValueOnce(response(assigned))
      .mockResolvedValueOnce(response(collapsed))
      .mockResolvedValueOnce(response(expanded));
    vi.stubGlobal("fetch", fetch);
    const store = await import("./chatFolders");

    expect(store.createFolder("Создано")).toBe(id);
    store.renameFolder(id, "Переименовано");
    store.assignToFolder("session", id);
    store.toggleCollapsed(id);
    store.toggleCollapsed(id);
    const initialDrain = store.synchronize();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).toEqual(EMPTY);

    initialGet.resolve(response(EMPTY));
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(store.getSnapshot()).toEqual(EMPTY);
    initialCreate.resolve(response(created));
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(store.getSnapshot()).toEqual(created);
    rejectedRename.resolve(new Response(JSON.stringify({ detail: "rename rejected" }), {
      status: 409, headers: { "Content-Type": "application/json" },
    }));
    await initialDrain;
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(store.getSnapshot()).toEqual(created);
    expect(JSON.parse(storage.getItem(KEY)!)).toEqual(created);
    expect(store.getSyncSnapshot()).toEqual({ loading: false, error: "rename rejected" });

    await store.synchronize();
    expect(fetch.mock.calls.map(([, init]) => init?.method)).toEqual([
      "GET", "PATCH", "PATCH", "GET", "PATCH", "PATCH", "PATCH", "PATCH",
    ]);
    const patches = fetch.mock.calls
      .filter(([, init]) => init?.method === "PATCH")
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(patches).toEqual([
      { operation: "create", folder_id: id, name: "Создано" },
      { operation: "rename", folder_id: id, name: "Переименовано" },
      { operation: "rename", folder_id: id, name: "Переименовано" },
      { operation: "assign", session_id: "session", folder_id: id },
      { operation: "collapse", folder_id: id, collapsed: true },
      { operation: "collapse", folder_id: id, collapsed: false },
    ]);
    expect(store.getSnapshot()).toEqual(expanded);
    expect(JSON.parse(storage.getItem(KEY)!)).toEqual(expanded);
    expect(store.getSyncSnapshot()).toEqual({ loading: false, error: null });
  });
});
