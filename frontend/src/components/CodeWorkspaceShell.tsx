/**
 * CodeWorkspaceShell.tsx
 *
 * Unified "Код" workspace: code-agent chat on the left, IDE view
 * (artifacts / git / files / history) on the right. Single top bar
 * owns "back to chat", LLM model picker, project root and max-steps
 * — these are shared by the agent embedded on the left.
 *
 * The vertical divider between the two panes is drag-resizable and
 * the split ratio is persisted to localStorage.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Cpu, FolderOpen, RefreshCw } from "lucide-react";
import IdeWorkspaceShell from "./IdeWorkspaceShell";
import CodeAgentChatShell from "./CodeAgentChatShell";
import { api } from "../api/ide";
import { UiIcon, IconText } from "./StatusPanels";

const ROOT_KEY = "elira_code_agent_root";
const MODEL_KEY = "elira_code_agent_model";
const SPLIT_KEY = "elira_code_workspace_split";
const COLLAPSE_KEY = "elira_code_workspace_collapse";
const STEPS_KEY = "elira_code_agent_steps";
const DEFAULT_ROOT = "D:/AIWork/Elira_AI";
const DEFAULT_MODEL = "qwen2.5-coder:7b";
const DEFAULT_MAX_STEPS = 20;
// Models with reliable tool-calling support in Ollama. Used to flag picker rows.
const TOOL_FRIENDLY = ["qwen2.5-coder", "qwen2.5", "qwen3", "llama3.2", "llama3.1", "mistral-nemo", "command-r"];

type Model = { name: string; size?: number };

function readNumber(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  } catch {
    return fallback;
  }
}
function writeNumber(key: string, n: number) {
  try { localStorage.setItem(key, String(n)); } catch {}
}
function readString(key: string, fallback: string): string {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function writeString(key: string, v: string) {
  try { localStorage.setItem(key, v); } catch {}
}
function readBool(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    return raw === "1" || raw === "true";
  } catch {
    return fallback;
  }
}
function writeBool(key: string, v: boolean) {
  try { localStorage.setItem(key, v ? "1" : "0"); } catch {}
}

function isToolFriendly(name: string): boolean {
  const low = name.toLowerCase();
  return TOOL_FRIENDLY.some((prefix) => low.startsWith(prefix));
}

function formatSize(bytes?: number): string {
  if (!bytes) return "";
  const gb = bytes / (1024 ** 3);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 ** 2);
  return `${mb.toFixed(0)} MB`;
}

type CodeWorkspaceShellProps = {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  messages?: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  libraryFiles?: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  setLibraryFiles?: (files: any[]) => void;
  onBackToChat?: () => void;
  onSendToChat?: (text: string) => void;
};

export default function CodeWorkspaceShell(props: CodeWorkspaceShellProps) {
  const { messages, libraryFiles, setLibraryFiles, onBackToChat, onSendToChat } = props;

  const [projectRoot, setProjectRoot] = useState<string>(() => readString(ROOT_KEY, DEFAULT_ROOT));
  const [model, setModel] = useState<string>(() => readString(MODEL_KEY, DEFAULT_MODEL));
  const [maxSteps, setMaxSteps] = useState<number>(() => readNumber(STEPS_KEY, DEFAULT_MAX_STEPS));
  const [splitPct, setSplitPct] = useState<number>(() => Math.max(20, Math.min(80, readNumber(SPLIT_KEY, 45))));
  const [ideCollapsed, setIdeCollapsed] = useState<boolean>(() => readBool(COLLAPSE_KEY, false));

  const [models, setModels] = useState<Model[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);

  const splitRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);

  useEffect(() => writeString(ROOT_KEY, projectRoot), [projectRoot]);
  useEffect(() => writeString(MODEL_KEY, model), [model]);
  useEffect(() => writeNumber(STEPS_KEY, maxSteps), [maxSteps]);
  useEffect(() => writeNumber(SPLIT_KEY, splitPct), [splitPct]);
  useEffect(() => writeBool(COLLAPSE_KEY, ideCollapsed), [ideCollapsed]);

  const loadModels = useCallback(async () => {
    setModelsLoading(true);
    setModelsError(null);
    try {
      const { models: raw } = await api.listOllamaModels();
      const list: Model[] = (raw || [])
        .map((m: unknown) => {
          if (typeof m === "string") return { name: m };
          if (m && typeof m === "object") {
            const obj = m as Record<string, unknown>;
            const name = typeof obj.name === "string" ? obj.name
              : typeof obj.model === "string" ? obj.model : "";
            const size = typeof obj.size === "number" ? obj.size : undefined;
            return name ? { name, size } : null;
          }
          return null;
        })
        .filter((v): v is Model => Boolean(v));
      // Sort: tool-friendly first, then alphabetical
      list.sort((a, b) => {
        const at = isToolFriendly(a.name) ? 0 : 1;
        const bt = isToolFriendly(b.name) ? 0 : 1;
        if (at !== bt) return at - bt;
        return a.name.localeCompare(b.name);
      });
      setModels(list);
    } catch (e) {
      setModelsError(String((e as Error)?.message || e));
      setModels([]);
    } finally {
      setModelsLoading(false);
    }
  }, []);

  useEffect(() => { loadModels(); }, [loadModels]);

  // Drag handlers for the resizable splitter
  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!draggingRef.current || !splitRef.current) return;
      const rect = splitRef.current.getBoundingClientRect();
      const pct = ((e.clientX - rect.left) / rect.width) * 100;
      setSplitPct(Math.max(20, Math.min(80, pct)));
    }
    function onUp() {
      if (draggingRef.current) {
        draggingRef.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const startDrag = useCallback(() => {
    draggingRef.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  const knownModels = useMemo<Model[]>(() => {
    const ms = models || [];
    if (model && !ms.some((m) => m.name === model)) {
      return [{ name: model }, ...ms];
    }
    return ms;
  }, [models, model]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* TOP BAR */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          borderBottom: "1px solid var(--border)",
          flexShrink: 0,
          flexWrap: "wrap",
        }}
      >
        {onBackToChat && (
          <button
            onClick={onBackToChat}
            className="soft-btn"
            style={{ fontSize: 12, padding: "5px 10px" }}
            title="Назад к обычному чату с Elira"
          >
            <IconText icon={ArrowLeft} size={13} gap={6}>
              Назад
            </IconText>
          </button>
        )}

        {/* Project root */}
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <UiIcon icon={FolderOpen} size={13} />
          <input
            value={projectRoot}
            onChange={(e) => setProjectRoot(e.target.value)}
            placeholder="D:/AIWork/MyProject"
            spellCheck={false}
            title="Корень проекта для code-агента"
            style={{
              width: 240,
              padding: "5px 8px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 11,
              outline: "none",
              fontFamily: "var(--font-mono)",
            }}
          />
        </div>

        {/* Model picker */}
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <UiIcon icon={Cpu} size={13} />
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={modelsLoading}
            title={modelsError ? `Ошибка списка моделей: ${modelsError}` : "Модель Ollama для code-агента"}
            style={{
              padding: "5px 7px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 11,
              outline: "none",
              minWidth: 180,
              maxWidth: 280,
              fontFamily: "var(--font-mono)",
            }}
          >
            {knownModels.length === 0 && (
              <option value={model}>{model || "qwen2.5-coder:7b"}</option>
            )}
            {knownModels.map((m) => (
              <option key={m.name} value={m.name}>
                {isToolFriendly(m.name) ? "★ " : "  "}
                {m.name}
                {m.size ? `  (${formatSize(m.size)})` : ""}
              </option>
            ))}
          </select>
          <button
            onClick={loadModels}
            disabled={modelsLoading}
            className="soft-btn"
            title="Обновить список моделей"
            style={{ padding: "5px 7px", fontSize: 11, opacity: modelsLoading ? 0.6 : 1 }}
          >
            <UiIcon icon={RefreshCw} size={11} />
          </button>
        </div>

        {/* Max steps */}
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>steps</span>
          <input
            type="number"
            min={1}
            max={50}
            value={maxSteps}
            onChange={(e) => setMaxSteps(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            title="Максимум tool-call шагов в одном запуске"
            style={{
              width: 50,
              padding: "5px 6px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 11,
              outline: "none",
              fontFamily: "var(--font-mono)",
            }}
          />
        </div>

        {/* Collapse IDE toggle */}
        <button
          onClick={() => setIdeCollapsed((v) => !v)}
          className="soft-btn"
          title={ideCollapsed ? "Показать IDE-панель справа" : "Скрыть IDE-панель справа"}
          style={{ marginLeft: "auto", fontSize: 11, padding: "5px 9px" }}
        >
          <IconText icon={ideCollapsed ? ChevronLeft : ChevronRight} size={12} gap={4}>
            {ideCollapsed ? "Показать IDE" : "Скрыть IDE"}
          </IconText>
        </button>
      </div>

      {/* SPLIT PANES */}
      <div ref={splitRef} style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <div
          style={{
            flex: ideCollapsed ? "1 1 100%" : `0 0 ${splitPct}%`,
            minWidth: 0,
            borderRight: ideCollapsed ? "none" : "1px solid var(--border)",
          }}
        >
          <CodeAgentChatShell projectRoot={projectRoot} model={model} maxSteps={maxSteps} />
        </div>

        {!ideCollapsed && (
          <>
            <div
              onMouseDown={startDrag}
              style={{
                width: 5,
                cursor: "col-resize",
                background: "var(--border)",
                flexShrink: 0,
                position: "relative",
              }}
              title="Тяни чтобы изменить размер"
            >
              <div
                style={{
                  position: "absolute",
                  top: "50%",
                  left: "50%",
                  transform: "translate(-50%, -50%)",
                  width: 1,
                  height: 24,
                  background: "var(--text-muted)",
                  opacity: 0.4,
                }}
              />
            </div>
            <div style={{ flex: `0 0 calc(${100 - splitPct}% - 5px)`, minWidth: 0 }}>
              <IdeWorkspaceShell
                messages={messages as never}
                libraryFiles={libraryFiles as never}
                setLibraryFiles={setLibraryFiles as never}
                onSendToChat={onSendToChat}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export type { CodeWorkspaceShellProps };
