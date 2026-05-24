import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { api } from "../api/ide";

type ArtifactMessage = {
  content?: unknown;
  id?: string | number;
  role?: string;
};

type CodeBlockArtifact = {
  code: string;
  id: string;
  lang: string;
  name: string;
};

type RunOutput = {
  error?: unknown;
  locals?: Record<string, unknown>;
  ok?: boolean;
  stderr?: string;
  stdout?: string;
  traceback?: unknown;
};

type AnalysisEntry = {
  line?: ReactNode;
  name?: ReactNode;
};

type AnalysisImport = {
  text?: ReactNode;
};

type CodeAnalysis = {
  blank_lines?: ReactNode;
  classes?: AnalysisEntry[];
  code_lines?: ReactNode;
  comment_lines?: ReactNode;
  error?: ReactNode;
  filename?: ReactNode;
  functions?: AnalysisEntry[];
  imports?: AnalysisImport[];
  language?: ReactNode;
  [key: string]: unknown;
};

type SaveResult = {
  diff?: unknown;
  error?: unknown;
  ok?: boolean;
  path?: unknown;
  size?: unknown;
  stats?: Record<string, unknown>;
};

type ArtifactPanelProps = {
  messages?: ArtifactMessage[];
  onClose: () => void;
  streamingCode?: string;
};

type TabId = "analysis" | "code" | "output" | "save";

const TABS: { id: TabId; label: string }[] = [
  { id: "code", label: "Код" },
  { id: "output", label: "Вывод" },
  { id: "analysis", label: "Анализ" },
  { id: "save", label: "Сохранить" },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : String(error || "");
}

function displayValue(value: unknown, fallback = "—"): string {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
}

function extractCodeBlocks(messages: ArtifactMessage[] = []): CodeBlockArtifact[] {
  const blocks: CodeBlockArtifact[] = [];
  for (const msg of messages) {
    if (msg.role !== "assistant") continue;
    const content = typeof msg.content === "string" ? msg.content : String(msg.content || "");
    const regex = /```(\w*)\n([\s\S]*?)```/g;
    let match: RegExpExecArray | null;
    while ((match = regex.exec(content)) !== null) {
      const lang = match[1] || "text";
      const code = match[2].trim();
      if (code.length < 10) continue;
      const ext =
        {
          python: "py",
          javascript: "js",
          jsx: "jsx",
          typescript: "ts",
          tsx: "tsx",
          rust: "rs",
          go: "go",
          java: "java",
          css: "css",
          html: "html",
          json: "json",
          yaml: "yml",
          bash: "sh",
          sql: "sql",
          markdown: "md",
        }[lang] || lang || "txt";
      blocks.push({
        id: `code-${msg.id}-${blocks.length}`,
        name: `${lang}_${blocks.length + 1}.${ext}`,
        lang,
        code,
      });
    }
  }
  return blocks;
}

function isRunnable(lang?: string) {
  return ["python", "py"].includes((lang || "").toLowerCase());
}

export default function ArtifactPanel({ messages = [], streamingCode = "", onClose }: ArtifactPanelProps) {
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState<RunOutput | null>(null);
  const [analysis, setAnalysis] = useState<CodeAnalysis | null>(null);
  const [copied, setCopied] = useState(false);
  const [tab, setTab] = useState<TabId>("code");
  const [saving, setSaving] = useState(false);
  const [savePath, setSavePath] = useState("");
  const [saveResult, setSaveResult] = useState<SaveResult | null>(null);

  const blocks = useMemo(() => {
    const fromMessages = extractCodeBlocks(messages);
    if (streamingCode) {
      const regex = /```(\w*)\n([\s\S]*?)```/g;
      let match: RegExpExecArray | null;
      while ((match = regex.exec(streamingCode)) !== null) {
        const lang = match[1] || "text";
        const code = match[2].trim();
        if (code.length >= 10) {
          fromMessages.push({
            id: `stream-${fromMessages.length}`,
            name: `streaming.${lang || "txt"}`,
            lang,
            code,
          });
        }
      }
    }
    return fromMessages;
  }, [messages, streamingCode]);

  const current = blocks[selectedIdx] || blocks[blocks.length - 1] || null;

  useEffect(() => {
    if (blocks.length > 0 && selectedIdx >= blocks.length) {
      setSelectedIdx(blocks.length - 1);
    }
  }, [blocks.length, selectedIdx]);

  const handleCopy = useCallback(() => {
    if (!current) return;
    navigator.clipboard.writeText(current.code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }, [current]);

  const handleDownload = useCallback(() => {
    if (!current) return;
    const blob = new Blob([current.code], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = current.name;
    a.click();
    URL.revokeObjectURL(url);
  }, [current]);

  async function handleRun() {
    if (!current || !isRunnable(current.lang)) return;
    setRunning(true);
    setOutput(null);
    setTab("output");
    try {
      const data = await api.runPythonCode(current.code);
      setOutput(data as RunOutput);
    } catch (e: unknown) {
      setOutput({ ok: false, error: errorMessage(e), stdout: "", stderr: "" });
    } finally {
      setRunning(false);
    }
  }

  async function handleAnalyze() {
    if (!current) return;
    setAnalysis(null);
    setTab("analysis");
    try {
      const data = await api.analyzeCode({
        code: current.code,
        language: current.lang,
        filename: current.name,
      });
      setAnalysis((isRecord(data.analysis) ? data.analysis : data) as CodeAnalysis);
    } catch (e: unknown) {
      setAnalysis({ error: errorMessage(e) });
    }
  }

  async function handleSaveToFile() {
    if (!current || !savePath.trim()) return;
    setSaving(true);
    setSaveResult(null);
    try {
      const diffData = await api.diffFile({ path: savePath.trim(), new_content: current.code });

      const writeData = await api.writeFile({
        path: savePath.trim(),
        content: current.code,
        create_dirs: true,
      });
      setSaveResult({
        ...writeData,
        diff: diffData.diff,
        stats: isRecord(diffData.stats) ? diffData.stats : {},
      });
    } catch (e: unknown) {
      setSaveResult({ ok: false, error: errorMessage(e) });
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    if (current?.name && !savePath) setSavePath(current.name);
  }, [current, savePath]);

  if (!blocks.length) return null;

  return (
    <div
      style={{
        width: 420,
        minWidth: 340,
        maxWidth: "45vw",
        borderLeft: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        background: "var(--bg-sidebar)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 12px",
          borderBottom: "1px solid var(--border)",
          gap: 8,
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
          {current?.name || "Код"}
          <span style={{ marginLeft: 6, fontSize: 10, color: "var(--text-muted)", fontWeight: 400 }}>{current?.lang}</span>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {current && isRunnable(current.lang) && (
            <button onClick={handleRun} disabled={running} style={btnStyle("#2d5a2d", "#4ade80")} title="Запустить">
              {running ? "…" : "▶"} Run
            </button>
          )}
          <button onClick={handleAnalyze} style={btnStyle()} title="Анализ кода">
            Анализ
          </button>
          <button onClick={handleCopy} style={btnStyle()} title="Копировать">
            {copied ? "Скопировано" : "Копия"}
          </button>
          <button onClick={handleDownload} style={btnStyle()} title="Скачать файл">
            Скачать
          </button>
          <button onClick={onClose} style={btnStyle()} title="Закрыть">
            ✕
          </button>
        </div>
      </div>

      {blocks.length > 1 && (
        <div
          style={{
            display: "flex",
            gap: 2,
            padding: "4px 8px",
            borderBottom: "1px solid var(--border)",
            overflow: "auto",
          }}
        >
          {blocks.map((b, i) => (
            <button
              key={b.id}
              onClick={() => {
                setSelectedIdx(i);
                setOutput(null);
                setAnalysis(null);
                setSaveResult(null);
                setTab("code");
              }}
              style={{
                padding: "3px 8px",
                borderRadius: 6,
                border: "none",
                fontSize: 10,
                cursor: "pointer",
                whiteSpace: "nowrap",
                background: i === selectedIdx ? "var(--bg-surface-active)" : "transparent",
                color: i === selectedIdx ? "var(--text-primary)" : "var(--text-muted)",
              }}
            >
              {b.name}
            </button>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 2, padding: "4px 8px", borderBottom: "1px solid var(--border)" }}>
        {TABS.map((item) => (
          <button
            key={item.id}
            onClick={() => setTab(item.id)}
            style={{
              padding: "3px 10px",
              borderRadius: 6,
              border: "none",
              fontSize: 10,
              cursor: "pointer",
              background: tab === item.id ? "var(--bg-surface-active)" : "transparent",
              color: tab === item.id ? "var(--text-primary)" : "var(--text-muted)",
            }}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "code" && current && (
        <pre
          style={{
            flex: 1,
            margin: 0,
            padding: 12,
            overflow: "auto",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            lineHeight: 1.55,
            color: "var(--text-primary)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {current.code}
        </pre>
      )}

      {tab === "output" && (
        <div style={{ flex: 1, overflow: "auto", padding: 12 }}>
          {running ? (
            <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Выполняется...</div>
          ) : output ? (
            output.ok ? (
              <>
                {output.stdout && (
                  <div>
                    <div style={sectionTitle}>STDOUT</div>
                    <pre style={outputPre}>{output.stdout}</pre>
                  </div>
                )}
                {output.stderr && (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ ...sectionTitle, color: "#f0ad4e" }}>STDERR</div>
                    <pre style={{ ...outputPre, color: "#f0ad4e" }}>{output.stderr}</pre>
                  </div>
                )}
                {output.locals && Object.keys(output.locals).length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <div style={sectionTitle}>Переменные</div>
                    {Object.entries(output.locals).map(([k, v]) => (
                      <div key={k} style={{ fontFamily: "var(--font-mono)", fontSize: 11, marginBottom: 2 }}>
                        <span style={{ color: "var(--accent)" }}>{k}</span> = {displayValue(v)}
                      </div>
                    ))}
                  </div>
                )}
                {!output.stdout && !output.stderr && (!output.locals || !Object.keys(output.locals).length) && (
                  <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Код выполнен без вывода</div>
                )}
              </>
            ) : (
              <>
                <div style={{ color: "#ff6b6b", fontSize: 12, marginBottom: 6 }}>Ошибка выполнения</div>
                <pre style={{ ...outputPre, color: "#ff6b6b" }}>{displayValue(output.error || output.traceback, "Unknown error")}</pre>
              </>
            )
          ) : (
            <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
              {current && isRunnable(current.lang)
                ? "Нажми Run, чтобы выполнить код"
                : "Выполнение доступно только для Python"}
            </div>
          )}
        </div>
      )}

      {tab === "analysis" && (
        <div style={{ flex: 1, overflow: "auto", padding: 12 }}>
          {analysis ? (
            analysis.error ? (
              <div style={{ color: "#ff6b6b", fontSize: 12 }}>{analysis.error}</div>
            ) : (
              <div style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={statRow}><span>Файл:</span><strong>{analysis.filename || "—"}</strong></div>
                <div style={statRow}><span>Язык:</span><strong>{analysis.language || current?.lang}</strong></div>
                <div style={statRow}><span>Строк кода:</span><strong>{analysis.code_lines ?? "—"}</strong></div>
                <div style={statRow}><span>Пустых строк:</span><strong>{analysis.blank_lines ?? "—"}</strong></div>
                <div style={statRow}><span>Комментариев:</span><strong>{analysis.comment_lines ?? "—"}</strong></div>

                {(analysis.functions?.length ?? 0) > 0 && (
                  <div>
                    <div style={sectionTitle}>Функции ({analysis.functions?.length ?? 0})</div>
                    {analysis.functions?.map((f, i) => (
                      <div key={i} style={{ fontFamily: "var(--font-mono)", fontSize: 11, marginBottom: 2 }}>
                        <span style={{ color: "var(--accent)" }}>{f.name}</span>
                        <span style={{ color: "var(--text-muted)" }}> :L{f.line}</span>
                      </div>
                    ))}
                  </div>
                )}

                {(analysis.classes?.length ?? 0) > 0 && (
                  <div>
                    <div style={sectionTitle}>Классы ({analysis.classes?.length ?? 0})</div>
                    {analysis.classes?.map((c, i) => (
                      <div key={i} style={{ fontFamily: "var(--font-mono)", fontSize: 11, marginBottom: 2 }}>
                        <span style={{ color: "#e2b93d" }}>{c.name}</span>
                        <span style={{ color: "var(--text-muted)" }}> :L{c.line}</span>
                      </div>
                    ))}
                  </div>
                )}

                {(analysis.imports?.length ?? 0) > 0 && (
                  <div>
                    <div style={sectionTitle}>Импорты ({analysis.imports?.length ?? 0})</div>
                    {analysis.imports?.map((imp, i) => (
                      <div key={i} style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-secondary)", marginBottom: 1 }}>
                        {imp.text}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          ) : (
            <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Нажми «Анализ», чтобы разобрать код</div>
          )}
        </div>
      )}

      {tab === "save" && (
        <div style={{ flex: 1, overflow: "auto", padding: 12 }}>
          <div style={{ marginBottom: 12 }}>
            <div style={sectionTitle}>Путь файла (в workspace)</div>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                value={savePath}
                onChange={(e) => setSavePath(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSaveToFile()}
                placeholder="Например: scripts/sort.py"
                style={saveInput}
              />
              <button onClick={handleSaveToFile} disabled={saving || !savePath.trim()} style={{ ...saveBtnStyle, opacity: saving ? 0.55 : 1 }}>
                {saving ? "…" : "💾"} Сохранить
              </button>
            </div>
          </div>

          {saveResult && (
            <div style={{ marginTop: 12 }}>
              {saveResult.ok ? (
                <>
                  <div style={{ color: "#4ade80", fontSize: 12, marginBottom: 8 }}>
                    Готово: {displayValue(saveResult.path, "")} ({displayValue(saveResult.size, "0")} байт)
                  </div>
                  {saveResult.diff && (
                    <div>
                      <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
                        Diff: +{displayValue(saveResult.stats?.added, "0")} / -{displayValue(saveResult.stats?.removed, "0")}
                      </div>
                      <pre style={diffPre}>{displayValue(saveResult.diff, "")}</pre>
                    </div>
                  )}
                </>
              ) : (
                <div style={{ color: "#ff6b6b", fontSize: 12 }}>Ошибка: {displayValue(saveResult.error, "")}</div>
              )}
            </div>
          )}

          <div style={{ marginTop: 16, fontSize: 11, color: "var(--text-muted)", lineHeight: 1.5 }}>
            Файлы сохраняются в <code style={inlineCode}>data/workspace/</code>
            <br />
            Можно указывать подпапки, например: <code style={inlineCode}>src/utils/helper.py</code>
          </div>
        </div>
      )}
    </div>
  );
}

const btnStyle = (bg?: string, color?: string): CSSProperties => ({
  padding: "3px 8px",
  borderRadius: 6,
  border: "1px solid var(--border)",
  background: bg || "var(--bg-surface)",
  color: color || "var(--text-secondary)",
  cursor: "pointer",
  fontSize: 11,
});

const outputPre: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  lineHeight: 1.5,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  color: "var(--text-primary)",
};

const statRow: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  padding: "4px 0",
  borderBottom: "1px solid var(--border-light)",
  color: "var(--text-secondary)",
};

const sectionTitle: CSSProperties = {
  fontSize: 10,
  color: "var(--text-muted)",
  marginBottom: 4,
};

const saveInput: CSSProperties = {
  flex: 1,
  padding: "6px 10px",
  borderRadius: 8,
  fontSize: 12,
  border: "1px solid var(--border)",
  background: "var(--bg-input)",
  color: "var(--text-primary)",
  outline: "none",
};

const saveBtnStyle: CSSProperties = {
  padding: "6px 14px",
  borderRadius: 8,
  fontSize: 11,
  border: "1px solid rgba(74,196,222,0.3)",
  background: "rgba(74,196,222,0.12)",
  color: "#4ac4de",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const diffPre: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  lineHeight: 1.45,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  color: "var(--text-secondary)",
  maxHeight: 200,
  overflow: "auto",
  padding: 8,
  borderRadius: 6,
  background: "rgba(0,0,0,0.2)",
};

const inlineCode: CSSProperties = {
  background: "rgba(255,255,255,0.06)",
  padding: "1px 4px",
  borderRadius: 3,
};
