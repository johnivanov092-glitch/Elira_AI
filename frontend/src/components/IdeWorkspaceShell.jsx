/**
 * IdeWorkspaceShell.jsx — v3 (Claude-like Code / Artifacts tab)
 *
 * Работает как артефакты Claude:
 *   • Слева: список артефактов (код из чата + файлы из библиотеки)
 *   • Справа: превью/редактор с кнопкой Copy
 *   • Drag-and-drop для загрузки новых файлов
 *   • Код автоматически извлекается из ответов Jarvis
 */

import { useCallback, useMemo, useRef, useState } from "react";

const LIBRARY_KEY = "jarvis_library_files_v7";
const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

function makeId(p = "id") { return `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }
function saveJson(k, v) { localStorage.setItem(k, JSON.stringify(v)); }

/** Извлекает блоки кода из markdown-ответов ассистента */
function extractCodeBlocks(messages) {
  const blocks = [];
  for (const msg of messages) {
    if (msg.role !== "assistant") continue;
    const content = msg.content || "";
    const regex = /```(\w*)\n([\s\S]*?)```/g;
    let match;
    while ((match = regex.exec(content)) !== null) {
      const lang = match[1] || "text";
      const code = match[2].trim();
      if (code.length < 10) continue; // пропускаем совсем мелкие
      // Угадываем имя файла по языку
      const ext = { python: "py", javascript: "js", jsx: "jsx", typescript: "ts", tsx: "tsx", rust: "rs", go: "go", java: "java", css: "css", html: "html", json: "json", yaml: "yml", bash: "sh", sql: "sql", markdown: "md" }[lang] || lang || "txt";
      const firstLine = code.split("\n")[0].slice(0, 40);
      blocks.push({
        id: `code-${msg.id}-${blocks.length}`,
        type: "code",
        name: `${lang || "snippet"}_${blocks.length + 1}.${ext}`,
        lang,
        content: code,
        preview: code.slice(0, 200),
        size: code.length,
        source: "chat",
        firstLine,
      });
    }
  }
  return blocks;
}

async function fileToRecord(file) {
  let preview = "";
  const isText = file.type.startsWith("text/") || file.name.match(/\.(txt|md|json|js|jsx|ts|tsx|py|css|html|yml|yaml|xml|csv|log|ini|toml|rs|go|java|c|cpp|h|rb|sh|bat|sql)$/i);
  if (isText) try { preview = (await file.text()).slice(0, 12000); } catch {}
  if (file.name.match(/\.pdf$/i)) try {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch(`${API_BASE}/api/files/extract-text`, { method: "POST", body: fd });
    if (r.ok) preview = ((await r.json()).text || "").slice(0, 12000);
  } catch {}
  return { id: makeId("lib"), name: file.name, size: file.size, type: file.type || "unknown", uploaded_at: new Date().toISOString(), preview, use_in_context: true, source: "code-upload" };
}


export default function IdeWorkspaceShell({ messages = [], libraryFiles: propLib, setLibraryFiles: propSetLib, onBackToChat }) {
  const fileRef = useRef(null);
  const [drag, setDrag] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [copied, setCopied] = useState(false);
  const [tab, setTab] = useState("all"); // "all" | "code" | "files"

  const libraryFiles = propLib || [];
  function setLibraryFiles(next) { if (propSetLib) propSetLib(next); saveJson(LIBRARY_KEY, next); }

  // Извлекаем код из чата
  const codeBlocks = useMemo(() => extractCodeBlocks(messages), [messages]);

  // Файлы библиотеки как артефакты
  const fileArtifacts = useMemo(() => libraryFiles.map(f => ({
    ...f,
    type: "file",
    content: f.preview || "",
    lang: f.name.split(".").pop() || "txt",
    source: "library",
  })), [libraryFiles]);

  // Объединённый список
  const allArtifacts = useMemo(() => {
    if (tab === "code") return codeBlocks;
    if (tab === "files") return fileArtifacts;
    return [...codeBlocks, ...fileArtifacts];
  }, [tab, codeBlocks, fileArtifacts]);

  const selected = useMemo(() => allArtifacts.find(a => a.id === selectedId) || allArtifacts[0] || null, [allArtifacts, selectedId]);

  async function handleFiles(fl) {
    const files = Array.from(fl || []); if (!files.length) return;
    const recs = []; for (const f of files) recs.push(await fileToRecord(f));
    setLibraryFiles([...recs, ...libraryFiles]);
    setSelectedId(recs[0]?.id || "");
  }

  function removeFile(id) {
    const next = libraryFiles.filter(f => f.id !== id);
    setLibraryFiles(next);
    if (selectedId === id) setSelectedId("");
  }

  const handleCopy = useCallback(() => {
    if (!selected?.content) return;
    navigator.clipboard.writeText(selected.content).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
  }, [selected]);

  function onDrop(e) { e.preventDefault(); e.stopPropagation(); setDrag(false); handleFiles(e.dataTransfer.files); }
  function onDragOver(e) { e.preventDefault(); e.stopPropagation(); setDrag(true); }
  function onDragLeave(e) { e.preventDefault(); e.stopPropagation(); setDrag(false); }

  const iconFor = (a) => {
    if (a.type === "code") return "◇";
    if (a.name?.match(/\.pdf$/i)) return "📑";
    if (a.name?.match(/\.(js|jsx|ts|tsx|py|rs|go|java|c|cpp)$/i)) return "◈";
    return "📄";
  };

  return (
    <div className="ide-shell" style={{display:"flex",flexDirection:"column",height:"100%",padding:0}}>
      {/* Toolbar */}
      <div style={{display:"flex",alignItems:"center",gap:8,padding:"8px 16px",borderBottom:"1px solid var(--border)"}}>
        <button onClick={onBackToChat} className="soft-btn" style={{border:"1px solid var(--border)"}}>← Chat</button>
        <div style={{fontSize:14,fontWeight:600}}>Code</div>
        <div style={{display:"flex",gap:2,marginLeft:12}}>
          {[["all","Всё"],["code",`Код (${codeBlocks.length})`],["files",`Файлы (${fileArtifacts.length})`]].map(([k,l]) => (
            <button key={k} className={`soft-btn ${tab===k?"active":""}`} onClick={()=>setTab(k)} style={{fontSize:11,padding:"3px 10px"}}>{l}</button>
          ))}
        </div>
        <div style={{marginLeft:"auto",fontSize:11,color:"var(--text-muted)"}}>
          {codeBlocks.length} блоков кода · {libraryFiles.length} файлов
        </div>
      </div>

      {/* Body */}
      <div style={{flex:1,display:"grid",gridTemplateColumns:"260px 1fr",minHeight:0}}>
        {/* Left: artifact list */}
        <div style={{borderRight:"1px solid var(--border)",display:"flex",flexDirection:"column",minHeight:0}}>
          {/* Drop zone */}
          <div
            className={`drop-panel ${drag?"active":""}`}
            onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            style={{margin:8,minHeight:60,padding:12,borderRadius:"var(--radius-md)"}}
          >
            <div style={{fontSize:11,color:"var(--text-muted)"}}>+ Загрузить файлы</div>
          </div>
          <input ref={fileRef} type="file" multiple hidden onChange={e=>handleFiles(e.target.files)}/>

          {/* List */}
          <div style={{flex:1,overflow:"auto",padding:"0 4px 8px"}}>
            {allArtifacts.length === 0 && (
              <div style={{padding:16,fontSize:11,color:"var(--text-muted)",textAlign:"center"}}>
                {messages.length === 0 ? "Начни чат — код появится здесь" : "Нет блоков кода в чате"}
              </div>
            )}
            {allArtifacts.map(a => (
              <button
                key={a.id}
                onClick={() => setSelectedId(a.id)}
                style={{
                  display:"flex", alignItems:"center", gap:8, width:"100%",
                  padding:"8px 10px", margin:"1px 0",
                  borderRadius:"var(--radius-sm)", border:"none",
                  background: selectedId === a.id ? "var(--bg-surface-active)" : "transparent",
                  color: selectedId === a.id ? "var(--text-primary)" : "var(--text-secondary)",
                  cursor:"pointer", textAlign:"left", fontSize:11, transition:"background 0.1s",
                }}
              >
                <span style={{fontSize:13,opacity:0.6}}>{iconFor(a)}</span>
                <div style={{flex:1,minWidth:0}}>
                  <div style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontWeight:selectedId===a.id?500:400}}>{a.name}</div>
                  <div style={{fontSize:10,color:"var(--text-muted)",marginTop:1}}>
                    {a.source === "chat" ? "из чата" : a.type === "file" ? `${Math.round(a.size/1024)||0}K` : ""}
                    {a.lang ? ` · ${a.lang}` : ""}
                  </div>
                </div>
                {a.source === "library" && (
                  <button onClick={e=>{e.stopPropagation();removeFile(a.id);}} style={{border:"none",background:"transparent",color:"var(--text-muted)",cursor:"pointer",fontSize:11}}>✕</button>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Right: preview */}
        <div style={{display:"flex",flexDirection:"column",minHeight:0}}>
          {selected ? (
            <>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"8px 16px",borderBottom:"1px solid var(--border)"}}>
                <div>
                  <span style={{fontWeight:500,fontSize:13}}>{selected.name}</span>
                  <span style={{marginLeft:8,fontSize:10,color:"var(--text-muted)"}}>{selected.lang} · {selected.content?.length || 0} chars</span>
                </div>
                <button onClick={handleCopy} className="soft-btn" style={{border:"1px solid var(--border)",fontSize:11,padding:"3px 10px"}}>
                  {copied ? "✓ Скопировано" : "⧉ Копировать"}
                </button>
              </div>
              <pre style={{
                flex:1, margin:0, padding:16, overflow:"auto",
                fontFamily:"var(--font-mono)", fontSize:12, lineHeight:1.55,
                color:"var(--text-primary)", background:"rgba(0,0,0,0.15)",
                whiteSpace:"pre-wrap", wordBreak:"break-word",
              }}>
                {selected.content || selected.preview || "Содержимое недоступно"}
              </pre>
            </>
          ) : (
            <div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-muted)",fontSize:13}}>
              <div style={{textAlign:"center"}}>
                <div style={{fontSize:32,opacity:0.15,marginBottom:8}}>◇</div>
                <div>Выбери артефакт слева</div>
                <div style={{fontSize:11,marginTop:4,opacity:0.7}}>Код из ответов Jarvis появляется автоматически</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
