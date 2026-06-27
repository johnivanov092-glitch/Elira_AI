export type Theme = "dark" | "cursor" | "light" | "bw" | "cyber" | "glass" | "solar";

/** Picker metadata — single source of truth for the Settings → Тема UI.
 *  `swatch` is the accent colour shown on the chip; `dark` flags whether the
 *  theme uses a dark surface (drives a light/dark contrast dot on the swatch). */
export const THEMES: { id: Theme; label: string; swatch: string; dark: boolean }[] = [
  { id: "dark", label: "Тёмная", swatch: "#8b93f8", dark: true },
  { id: "cursor", label: "Cursor", swatch: "#4a9eff", dark: true },
  { id: "light", label: "Светлая", swatch: "#4a7aff", dark: false },
  { id: "bw", label: "Чёрно-белая", swatch: "#1a1a1a", dark: false },
  { id: "cyber", label: "Неон", swatch: "#00ff9f", dark: true },
  { id: "glass", label: "Стекло", swatch: "#c084fc", dark: true },
  { id: "solar", label: "Сепия", swatch: "#b58900", dark: false },
];

const KEY = "elira-theme";

const VALID = new Set<Theme>(THEMES.map((t) => t.id));

function isTheme(v: string | null): v is Theme {
  return v !== null && VALID.has(v as Theme);
}

export function getTheme(): Theme {
  const stored = localStorage.getItem(KEY);
  return isTheme(stored) ? stored : "dark";
}

export function applyTheme(t: Theme): void {
  document.documentElement.dataset.theme = t;
}

export function setTheme(t: Theme): void {
  localStorage.setItem(KEY, t);
  applyTheme(t);
}

/** Apply the persisted theme on startup (dark by default). */
export function initTheme(): void {
  applyTheme(getTheme());
}
