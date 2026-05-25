/**
 * CodeWorkspaceShell.tsx
 *
 * Unified "Код" workspace: streaming code-agent chat on the left, IDE
 * view (artifacts / git / files / history) on the right. Top toolbar
 * owns Back / model / project-root / max-steps / project-prompt.
 *
 * When the agent touches a file (read_file / write_file / edit_file),
 * we forward the path to the IDE pane so it auto-opens the file in
 * the "Файлы" sub-tab.
 *
 * Layout state (split %, IDE collapsed) is persisted to localStorage.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Cpu, Database, FileText, FolderOpen, RefreshCw, Save, X } from "lucide-react";
import IdeWorkspaceShell from "./IdeWorkspaceShell";
import CodeAgentChatShell from "./CodeAgentChatShell";
import { api } from "../api/ide";
import { UiIcon, IconText } from "./StatusPanels";

const ROOT_KEY = "elira_code_agent_root";
const MODEL_KEY = "elira_code_agent_model";
const SPLIT_KEY = "elira_code_workspace_split";
const COLLAPSE_KEY = "elira_code_workspace_collapse";
const STEPS_KEY = "elira_code_agent_steps";
const CTX_KEY_PREFIX = "elira_code_agent_ctx_";
const AUTO_REMEMBER_KEY = "elira_code_agent_auto_remember";
const DEFAULT_ROOT = "D:/AIWork/Elira_AI";
const DEFAULT_MODEL = "qwen2.5-coder:7b";
const DEFAULT_MAX_STEPS = 20;
const DEFAULT_NUM_CTX = 16384;
const CTX_OPTIONS = [4096, 8192, 16384, 32768, 65536];
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
  // num_ctx is per-model (different models have different effective context).
  const [numCtx, setNumCtx] = useState<number>(() => readNumber(CTX_KEY_PREFIX + readString(MODEL_KEY, DEFAULT_MODEL), DEFAULT_NUM_CTX));
  const [autoRemember, setAutoRemember] = useState<boolean>(() => readBool(AUTO_REMEMBER_KEY, true));

  // Index project state
  const [indexing, setIndexing] = useState(false);
  const [indexStatus, setIndexStatus] = useState<string | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [splitPct, setSplitPct] = useState<number>(() => Math.max(20, Math.min(80, readNumber(SPLIT_KEY, 45))));
  const [ideCollapsed, setIdeCollapsed] = useState<boolean>(() => readBool(COLLAPSE_KEY, false));

  const [models, setModels] = useState<Model[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);

  // Auto-open: the agent telling the IDE "I just touched this file" — bump
  // a request counter alongside so even re-touches of the same path retrigger.
  const [autoOpen, setAutoOpen] = useState<{ path: string; nonce: number } | null>(null);

  // Project prompt panel
  const [promptOpen, setPromptOpen] = useState(false);
  const [promptText, setPromptText] = useState("");
  const [promptLoading, setPromptLoading] = useState(false);
  const [promptStatus, setPromptStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [promptError, setPromptError] = useState<string | null>(null);

  const splitRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);

  useEffect(() => writeString(ROOT_KEY, projectRoot), [projectRoot]);
  useEffect(() => writeString(MODEL_KEY, model), [model]);
  useEffect(() => writeNumber(STEPS_KEY, maxSteps), [maxSteps]);
  useEffect(() => writeNumber(SPLIT_KEY, splitPct), [splitPct]);
  useEffect(() => writeBool(COLLAPSE_KEY, ideCollapsed), [ideCollapsed]);
  useEffect(() => writeBool(AUTO_REMEMBER_KEY, autoRemember), [autoRemember]);
  // Persist num_ctx per-model and switch when model changes.
  useEffect(() => { writeNumber(CTX_KEY_PREFIX + model, numCtx); }, [model, numCtx]);
  useEffect(() => {
    const saved = readNumber(CTX_KEY_PREFIX + model, DEFAULT_NUM_CTX);
    setNumCtx(saved);
    // intentionally exclude numCtx from deps — switching model reads its own saved ctx
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model]);

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

  // Splitter drag
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

  const handleAgentTouchedFile = useCallback((path: string) => {
    if (ideCollapsed) return; // don't fight a user who hid the IDE
    setAutoOpen({ path, nonce: Date.now() });
  }, [ideCollapsed]);

  const openPromptEditor = useCallback(async () => {
    setPromptOpen(true);
    setPromptStatus("idle");
    setPromptError(null);
    setPromptLoading(true);
    try {
      const info = await api.getProjectPrompt(projectRoot);
      setPromptText(info.content || "");
    } catch (e) {
      setPromptError(String((e as Error)?.message || e));
      setPromptText("");
    } finally {
      setPromptLoading(false);
    }
  }, [projectRoot]);

  const runIndex = useCallback(async () => {
    if (indexing) return;
    setIndexing(true);
    setIndexStatus("Индексирую...");
    setIndexError(null);
    try {
      const res = await api.indexProject({ projectRoot, replace: true });
      if (!res.ok) {
        setIndexError(res.error || "Индексация не удалась");
        setIndexStatus(null);
        return;
      }
      const okMsg = `✓ ${res.files_processed} файлов, ${res.chunks_indexed} чанков в RAG`;
      const errCount = res.failed_chunks ?? 0;
      setIndexStatus(errCount ? `${okMsg} (${errCount} ошибок embedding)` : okMsg);
      if (errCount && res.chunks_indexed === 0) {
        setIndexError("Все чанки провалились — скорее всего nomic-embed-text не установлен. Запусти: ollama pull nomic-embed-text");
      }
      setTimeout(() => setIndexStatus(null), 6000);
    } catch (e) {
      setIndexError(String((e as Error)?.message || e));
      setIndexStatus(null);
    } finally {
      setIndexing(false);
    }
  }, [indexing, projectRoot]);

  const savePrompt = useCallback(async () => {
    setPromptStatus("saving");
    setPromptError(null);
    try {
      await api.setProjectPromptApi(projectRoot, promptText);
      setPromptStatus("saved");
      setTimeout(() => setPromptStatus("idle"), 2000);
    } catch (e) {
      setPromptStatus("error");
      setPromptError(String((e as Error)?.message || e));
    }
  }, [projectRoot, promptText]);

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

        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>ctx</span>
          <select
            value={numCtx}
            onChange={(e) => setNumCtx(Number(e.target.value) || DEFAULT_NUM_CTX)}
            title="Размер окна контекста Ollama (num_ctx). Сохраняется per-model. Больше = агент помнит дольше, но жрёт VRAM."
            style={{
              padding: "5px 7px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 11,
              outline: "none",
              fontFamily: "var(--font-mono)",
            }}
          >
            {(CTX_OPTIONS.includes(numCtx) ? CTX_OPTIONS : [numCtx, ...CTX_OPTIONS]).map((n) => (
              <option key={n} value={n}>{n.toLocaleString()}</option>
            ))}
          </select>
        </div>

        <button
          onClick={openPromptEditor}
          className="soft-btn"
          title="Редактировать .elira/agent.md — проектный системный промпт"
          style={{ fontSize: 11, padding: "5px 10px" }}
        >
          <IconText icon={FileText} size={12} gap={5}>
            Промпт проекта
          </IconText>
        </button>

        <button
          onClick={runIndex}
          disabled={indexing}
          className="soft-btn"
          title="Проиндексировать файлы проекта в RAG: агент сможет искать код семантически через инструмент recall"
          style={{ fontSize: 11, padding: "5px 10px", opacity: indexing ? 0.6 : 1 }}
        >
          <IconText icon={Database} size={12} gap={5}>
            {indexing ? "Индексирую..." : "Индексировать"}
          </IconText>
        </button>

        <label
          title="Сохранять короткое summary каждого успешного запуска агента в RAG (категория agent_turn). Агент сможет «вспомнить» прошлые задачи через recall."
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            fontSize: 11,
            color: autoRemember ? "var(--text-primary)" : "var(--text-muted)",
            cursor: "pointer",
            padding: "5px 8px",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: autoRemember ? "var(--accent-soft, rgba(99,102,241,0.15))" : "transparent",
          }}
        >
          <input
            type="checkbox"
            checked={autoRemember}
            onChange={(e) => setAutoRemember(e.target.checked)}
            style={{ margin: 0 }}
          />
          <span>Запоминать</span>
        </label>

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

      {/* Index status banner */}
      {(indexStatus || indexError) && (
        <div
          style={{
            padding: "6px 14px",
            borderBottom: "1px solid var(--border)",
            background: indexError ? "rgba(255,107,107,0.08)" : "rgba(74,222,128,0.08)",
            color: indexError ? "#ff6b6b" : "#4ade80",
            fontSize: 11,
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <UiIcon icon={Database} size={12} />
          <span style={{ flex: 1 }}>{indexError || indexStatus}</span>
          <button
            onClick={() => { setIndexError(null); setIndexStatus(null); }}
            className="soft-btn"
            style={{ fontSize: 10, padding: "2px 6px" }}
          >
            <UiIcon icon={X} size={10} />
          </button>
        </div>
      )}

      {/* Project-prompt editor (inline) */}
      {promptOpen && (
        <div
          style={{
            padding: "10px 14px",
            borderBottom: "1px solid var(--border)",
            background: "var(--bg-surface)",
            flexShrink: 0,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <UiIcon icon={FileText} size={13} />
            <div style={{ fontSize: 12, fontWeight: 500 }}>Проектный системный промпт</div>
            <code style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              .elira/agent.md
            </code>
            <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
              {promptStatus === "saved" && <span style={{ fontSize: 10, color: "#4ade80" }}>✓ Сохранено</span>}
              {promptStatus === "error" && <span style={{ fontSize: 10, color: "#ff6b6b" }}>✕ Ошибка</span>}
              <button
                onClick={savePrompt}
                disabled={promptStatus === "saving" || promptLoading}
                className="soft-btn"
                style={{ fontSize: 11, padding: "4px 10px", opacity: promptStatus === "saving" || promptLoading ? 0.5 : 1 }}
              >
                <IconText icon={Save} size={12} gap={4}>
                  Сохранить
                </IconText>
              </button>
              <button
                onClick={() => setPromptOpen(false)}
                className="soft-btn"
                style={{ fontSize: 11, padding: "4px 8px" }}
              >
                <UiIcon icon={X} size={12} />
              </button>
            </div>
          </div>
          {promptError && (
            <div style={{ fontSize: 10, color: "#ff6b6b", marginBottom: 6, fontFamily: "var(--font-mono)" }}>{promptError}</div>
          )}
          <textarea
            value={promptText}
            onChange={(e) => setPromptText(e.target.value)}
            disabled={promptLoading}
            placeholder="Например:\n- Стиль кода: PEP8, type hints обязательны\n- Не трогать backend/legacy/\n- Все коммиты на русском, conventional commits"
            spellCheck={false}
            style={{
              width: "100%",
              minHeight: 140,
              maxHeight: 320,
              padding: "8px 10px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 11,
              outline: "none",
              fontFamily: "var(--font-mono)",
              resize: "vertical",
              boxSizing: "border-box",
              lineHeight: 1.5,
            }}
          />
          <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-muted)" }}>
            Содержимое подгружается агентом в системный промпт на каждом запуске. Полезно для проектных правил, стиля,
            запрещённых директорий, конвенций коммитов.
          </div>
        </div>
      )}

      {/* SPLIT PANES */}
      <div ref={splitRef} style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <div
          style={{
            flex: ideCollapsed ? "1 1 100%" : `0 0 ${splitPct}%`,
            minWidth: 0,
            borderRight: ideCollapsed ? "none" : "1px solid var(--border)",
          }}
        >
          <CodeAgentChatShell
            projectRoot={projectRoot}
            model={model}
            maxSteps={maxSteps}
            numCtx={numCtx}
            autoRemember={autoRemember}
            onAgentTouchedFile={handleAgentTouchedFile}
          />
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
                autoOpenFile={autoOpen?.path}
                autoOpenNonce={autoOpen?.nonce}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export type { CodeWorkspaceShellProps };
