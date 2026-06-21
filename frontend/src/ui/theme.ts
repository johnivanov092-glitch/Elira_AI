export type Theme = "dark" | "cursor";

const KEY = "elira-theme";

export function getTheme(): Theme {
  return localStorage.getItem(KEY) === "cursor" ? "cursor" : "dark";
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
