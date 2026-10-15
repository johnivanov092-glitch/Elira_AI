// The companion's native side (src-tauri/src/companion.rs). In a plain browser (dev preview)
// every call is a no-op or a clear error, so the view still renders.
import { invoke, isTauri } from "@tauri-apps/api/core";

export interface ScreenCapture {
  image_base64: string;
  title: string;
  app_name: string;
  width: number;
  height: number;
  source: "window" | "screen";
}

export interface HitRegion { x: number; y: number; width: number; height: number }

export const inTauri = (): boolean => isTauri();

export async function captureScreen(): Promise<ScreenCapture> {
  if (!isTauri()) throw new Error("снимок экрана работает только в приложении Elira");
  return invoke<ScreenCapture>("companion_capture");
}

export async function setHitRegions(regions: HitRegion[]): Promise<void> {
  if (!isTauri()) return;
  await invoke("companion_set_regions", { regions });
}

export async function openMainChat(sessionId: string | null): Promise<void> {
  if (!isTauri()) return;
  await invoke("companion_open_main", { sessionId });
}

export async function startWindowDrag(): Promise<void> {
  if (!isTauri()) return;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  await getCurrentWindow().startDragging();
}

/** Ctrl+Shift+E (registered natively) asks the companion to explain the screen. */
export async function onExplainScreen(handler: () => void): Promise<() => void> {
  if (!isTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen("companion://explain-screen", () => handler());
}

export function pngFile(capture: ScreenCapture): File {
  const binary = atob(capture.image_base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], `screen-${Date.now()}.png`, { type: "image/png" });
}
