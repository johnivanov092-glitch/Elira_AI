/**
 * pickFolder — open the native folder picker via Tauri's dialog API.
 *
 * Returns the selected absolute path (forward-slash normalized), or null if
 * the user cancelled or Tauri isn't available (browser dev mode). Shared by
 * the Code-agent toolbar and the chat Projects panel.
 */
export async function pickFolder(defaultPath?: string): Promise<string | null> {
  const { isTauri } = await import("@tauri-apps/api/core");
  if (!isTauri()) return null;
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const selected = await open({
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
