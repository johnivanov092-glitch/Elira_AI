import React from "react";
import ReactDOM from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import App from "./App";
import "./theme.css";
import "./styles.css";
import "./styles/markdown.css";
import { initTheme } from "./ui/theme";

initTheme();

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

/** The desktop companion window loads the same bundle; dev builds also open it in a browser tab. */
function isCompanionView(): boolean {
  try {
    if (isTauri()) return getCurrentWindow().label === "companion";
  } catch {
    /* not inside a Tauri window */
  }
  return import.meta.env.DEV && new URLSearchParams(window.location.search).get("view") === "companion";
}

const CompanionApp = isCompanionView() ? React.lazy(() => import("./companion/CompanionApp")) : null;

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    {CompanionApp ? <React.Suspense fallback={null}><CompanionApp /></React.Suspense> : <App />}
  </React.StrictMode>,
);
