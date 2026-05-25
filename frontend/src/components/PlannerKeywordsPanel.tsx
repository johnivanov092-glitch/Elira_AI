/**
 * PlannerKeywordsPanel.tsx
 *
 * UI for editing PlannerV2's keyword bags. Each bag (route) is one
 * textarea — one keyword per line. Trailing `*` enables prefix match,
 * `:N` suffix sets weight. Bottom of the panel has a "test query" box
 * that calls /api/chat/classify so you can see where a query routes
 * with the current bags.
 *
 * Lives inside Settings page in EliraChatShell.
 */
import { useCallback, useEffect, useState } from "react";
import { Loader2, RotateCcw, Save, Sparkles, TestTube2 } from "lucide-react";
import {
  classifyQuery,
  getPlannerKeywords,
  savePlannerKeywords,
  type ClassifyResult,
  type PlannerKeywords,
} from "../api/plannerKeywords";
import { UiIcon, IconText } from "./StatusPanels";


// Ordered list of bags shown to the user. `_NEEDS_WEB_PATTERNS` and
// `_CHAT_ONLY_PATTERNS` are not edited here — they're internal escalation
// rules, exposing them would just confuse.
const VISIBLE_BAGS: { key: string; label: string; hint: string }[] = [
  { key: "code", label: "Код", hint: "Пиши/правь код, тесты, рефакторинг — обычный single-shot." },
  { key: "code_agent", label: "Code-агент", hint: "Многошаговые файл-операции через tool use." },
  { key: "multi_agent", label: "Мульти-агент", hint: "Сложная оркестрация: план → ресёрч → код → анализ → рефлексия." },
  { key: "project", label: "Проект", hint: "Файловое дерево, структура, навигация." },
  { key: "research", label: "Исследование", hint: "Веб-поиск, факты, документация." },
  { key: "image", label: "Картинки", hint: "Генерация изображений (FLUX, SDXL)." },
  { key: "python", label: "Python", hint: "Запуск Python-кода / REPL / вычислений." },
  { key: "memory", label: "Память", hint: "Запросы к памяти / RAG." },
  { key: "library", label: "Файл-библиотека", hint: "Работа с загруженными файлами." },
];


function toLines(arr: string[] | undefined): string {
  return (arr || []).join("\n");
}

function fromLines(text: string): string[] {
  return text.split(/\r?\n/).map((s) => s.trim()).filter((s) => s.length > 0);
}


export default function PlannerKeywordsPanel() {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [defaults, setDefaults] = useState<PlannerKeywords>({});
  const [effective, setEffective] = useState<PlannerKeywords>({});

  const [testQuery, setTestQuery] = useState("");
  const [testResult, setTestResult] = useState<ClassifyResult | null>(null);
  const [testRunning, setTestRunning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getPlannerKeywords();
      setDefaults(res.defaults || {});
      setEffective(res.effective || {});
    } catch (e) {
      setError(String((e as Error)?.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const updateBag = useCallback((key: string, text: string) => {
    setEffective((prev) => ({ ...prev, [key]: fromLines(text) }));
  }, []);

  const resetBag = useCallback((key: string) => {
    if (!confirm(`Сбросить «${key}» к дефолтным триггерам? Несохранённые правки пропадут.`)) return;
    setEffective((prev) => ({ ...prev, [key]: [...(defaults[key] || [])] }));
  }, [defaults]);

  const resetAll = useCallback(() => {
    if (!confirm("Сбросить ВСЕ bags к дефолтным? Все твои кастомные триггеры пропадут.")) return;
    // To revert in DB: save empty {} (planner falls back to module defaults).
    setSaving(true);
    setError(null);
    savePlannerKeywords({})
      .then(() => { setStatus("Сброшено к дефолтным"); setTimeout(() => setStatus(null), 3000); return load(); })
      .catch((e) => setError(String(e?.message || e)))
      .finally(() => setSaving(false));
  }, [load]);

  const save = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      // Only send bags that differ from defaults to keep DB small;
      // backend treats missing keys as "use defaults".
      const overrides: PlannerKeywords = {};
      for (const { key } of VISIBLE_BAGS) {
        const cur = effective[key] || [];
        const def = defaults[key] || [];
        const same = cur.length === def.length && cur.every((v, i) => v === def[i]);
        if (!same) overrides[key] = cur;
      }
      const res = await savePlannerKeywords(overrides);
      setStatus(`Сохранено. Активные размеры: ${Object.entries(res.active_counts).map(([k, n]) => `${k}=${n}`).join(", ")}`);
      setTimeout(() => setStatus(null), 4000);
    } catch (e) {
      setError(String((e as Error)?.message || e));
    } finally {
      setSaving(false);
    }
  }, [effective, defaults]);

  const runTest = useCallback(async () => {
    const q = testQuery.trim();
    if (!q || testRunning) return;
    setTestRunning(true);
    setError(null);
    try {
      const res = await classifyQuery(q);
      setTestResult(res);
    } catch (e) {
      setError(String((e as Error)?.message || e));
    } finally {
      setTestRunning(false);
    }
  }, [testQuery, testRunning]);

  if (loading) {
    return (
      <div style={{ padding: 30, fontSize: 12, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 8, justifyContent: "center" }}>
        <UiIcon icon={Loader2} size={14} />
        <span>Загружаю триггеры...</span>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        <div style={{ flex: 1 }}>
          <div className="settings-title">Маршрутизация запросов</div>
          <div className="settings-desc" style={{ fontSize: 11, lineHeight: 1.55, marginTop: 4 }}>
            Триггеры для классификации. Один на строку. Опционально:
            <code style={{ fontFamily: "var(--font-mono)", margin: "0 4px" }}>код*</code> — префикс
            (матчит «код», «коды», «кодом» но не «штрихкод»),
            <code style={{ fontFamily: "var(--font-mono)", margin: "0 4px" }}>триггер:3</code> — задать вес.
          </div>
        </div>
        <button onClick={resetAll} disabled={saving} className="soft-btn" style={{ fontSize: 11, padding: "5px 10px" }}>
          <IconText icon={RotateCcw} size={12} gap={5}>
            Сбросить всё к дефолтам
          </IconText>
        </button>
        <button
          onClick={save}
          disabled={saving}
          style={{
            padding: "5px 14px",
            borderRadius: 6,
            border: "1px solid var(--accent)",
            background: "var(--accent)",
            color: "#fff",
            fontSize: 11,
            cursor: saving ? "not-allowed" : "pointer",
            opacity: saving ? 0.6 : 1,
          }}
        >
          <IconText icon={saving ? Loader2 : Save} size={12} gap={5}>
            {saving ? "Сохраняю..." : "Сохранить"}
          </IconText>
        </button>
      </div>

      {error && (
        <div style={{ padding: "6px 10px", background: "rgba(255,107,107,0.08)", color: "#ff6b6b", fontSize: 11, borderRadius: 6 }}>
          {error}
        </div>
      )}
      {status && (
        <div style={{ padding: "6px 10px", background: "rgba(74,222,128,0.08)", color: "#4ade80", fontSize: 11, borderRadius: 6 }}>
          {status}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 10 }}>
        {VISIBLE_BAGS.map(({ key, label, hint }) => {
          const lines = effective[key] || [];
          const text = toLines(lines);
          return (
            <div key={key} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 8, background: "var(--bg-surface)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                <div>
                  <span style={{ fontWeight: 600, fontSize: 12 }}>{label}</span>
                  <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 6 }}>{lines.length}</span>
                </div>
                <button onClick={() => resetBag(key)} className="soft-btn" style={{ fontSize: 9, padding: "2px 6px" }} title="Сбросить эту категорию к дефолту">
                  <UiIcon icon={RotateCcw} size={10} />
                </button>
              </div>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>{hint}</div>
              <textarea
                value={text}
                onChange={(e) => updateBag(key, e.target.value)}
                spellCheck={false}
                style={{
                  width: "100%",
                  minHeight: 140,
                  maxHeight: 280,
                  padding: "6px 8px",
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: "var(--bg-input)",
                  color: "var(--text-primary)",
                  fontSize: 11,
                  fontFamily: "var(--font-mono)",
                  outline: "none",
                  resize: "vertical",
                  boxSizing: "border-box",
                  lineHeight: 1.5,
                }}
              />
            </div>
          );
        })}
      </div>

      {/* Test box */}
      <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "var(--bg-surface)", marginTop: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
          <UiIcon icon={TestTube2} size={13} />
          <span style={{ fontSize: 12, fontWeight: 600 }}>Тест запроса</span>
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
            (без LLM — только смотрит куда поедет ваш запрос)
          </span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "stretch" }}>
          <input
            value={testQuery}
            onChange={(e) => setTestQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") runTest(); }}
            placeholder="напр.: прочитай foo.py и поправь баг"
            style={{
              flex: 1,
              padding: "7px 10px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 12,
              outline: "none",
              boxSizing: "border-box",
            }}
          />
          <button
            onClick={runTest}
            disabled={!testQuery.trim() || testRunning}
            className="soft-btn"
            style={{ fontSize: 11, padding: "5px 14px", opacity: !testQuery.trim() || testRunning ? 0.5 : 1 }}
          >
            <IconText icon={testRunning ? Loader2 : Sparkles} size={12} gap={5}>
              {testRunning ? "Считаю..." : "Куда поедет"}
            </IconText>
          </button>
        </div>

        {testResult && (
          <div style={{ marginTop: 10, padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-input)" }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", fontSize: 11 }}>
              <span style={{ padding: "2px 9px", borderRadius: 10, background: "var(--accent-soft, rgba(99,102,241,0.18))", color: "var(--accent, #6366f1)", fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                route = {testResult.route}
              </span>
              <span style={{ color: "var(--text-muted)" }}>tools: {testResult.tools.join(", ") || "(none)"}</span>
            </div>
            <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              scores: {Object.entries(testResult.scores).filter(([, v]) => v > 0).map(([k, v]) => `${k}=${v}`).join("  ") || "(all zero)"}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
