/**
 * pickFolder — open the native folder picker via Tauri's dialog API.
 *
 * Returns the selected absolute path (forward-slash normalized), or null if
 * the user cancelled or Tauri isn't available (browser dev mode). Shared by
 * the Code-agent toolbar and the chat Projects panel.
 */
export async function pickFolder(defaultPath?: string): Promise<string | null> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any;
  if (!w.__TAURI__) return null;
  try {
    const mod = await import("@tauri-apps/api/dialog");
    const selected = await mod.open({
      directory: true,
      multiple: false,
      defaultPath: defaultPath || undefined,
      title: "Выбери папку проекта",
    });
    if (typeof selected === "string" && selected) {
      return selected.replace(/\\/g, "/");
    }
    return null;
  } catch (err) {
    console.error("pickFolder failed:", err);
    return null;
  }
}
