/**
 * IdeWorkspaceShell.jsx — v4
 * 7 улучшений:
 *   1. Поиск по артефактам
 *   2. Inline редактор (кнопка Изменить → textarea → Применить)
 *   3. Подсветка синтаксиса highlight.js
 *   4. Отправить в чат (Объясни / Баги / Тесты)
 *   5. Git панель (статус, log, diff, commit)
 *   6. История запусков агента
 *   7. Браузер файлов проекта
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent, type ReactNode } from "react";
import {
  ArrowLeft,
  BarChart3,
  Bug,
  Check,
  Code2,
  Copy,
  Database,
  FileCode2,
  FileText,
  Files,
  FolderOpen,
  GitBranch,
  History,
  Loader2,
  MessageSquare,
  Pencil,
  RefreshCw,
  Save,
  Search,
  Terminal,
  TestTube2,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";
import { api } from "../api/ide";
import TerminalPanel from "./TerminalPanel";
import { toast } from "./ToastHost";

const LIBRARY_KEY = "elira_library_files_v7";

type ChatMessage = {
  content?: unknown;
  id?: string | number;
  role?: string;
};

type LibraryFile = {
  id: string;
  name: string;
  preview?: string;
  size?: number;
  source?: string;
  type?: string;
  [key: string]: unknown;
};

type Artifact = {
  content?: string;
  id: string;
  lang?: string;
  name: string;
  preview?: string;
  size?: number;
  source?: string;
  type?: string;
};

type FileTreeItem = {
  ext?: string;
  name: string;
  path: string;
  type?: string;
};

type ToolRunHistoryItem = {
  answer_len?: number;
  finished_at?: string;
  model?: string;
  ok?: boolean;
  route?: string;
  run_id?: string;
};

type GitStatusFile = {
  file?: string;
  status?: string;
};

type GitStatusData = {
  branch?: string;
  clean?: boolean;
  error?: unknown;
  files?: GitStatusFile[];
  ok?: boolean;
  repo?: string;
};

type GitLogCommit = {
  hash?: string;
  message?: string;
};

type GitLogData = {
  commits?: GitLogCommit[];
  error?: unknown;
  ok?: boolean;
  repo?: string;
};

type GitDiffData = {
  diff?: unknown;
  error?: unknown;
  ok?: boolean;
  stat?: unknown;
};

type GitData = {
  diff?: GitDiffData;
  log?: GitLogData;
  status?: GitStatusData;
};

type MainView = "artifacts" | "filetree" | "git" | "history" | "rag";

type RagItem = {
  id: number;
  text: string;
  category: string;
  importance: number;
  access_count?: number;
  created_at?: string;
  score?: number;
};

type RagStatsState = {
  total: number;
  with_embeddings: number;
  model?: string;
  by_category?: Record<string, number>;
};
type FilterTab = "all" | "code" | "files";
type GitTab = "diff" | "log" | "status";
type SaveStatus = "error" | "ok" | "saving" | null;

type IdeWorkspaceShellProps = {
  libraryFiles?: LibraryFile[];
  messages?: ChatMessage[];
  onBackToChat?: () => void;
  onSendToChat?: (text: string) => void;
  setLibraryFiles?: (files: LibraryFile[]) => void;
  /** Path the code-agent just touched (read/write/edit). When this
   *  changes, the shell auto-switches to the "Файлы" sub-tab and
   *  opens the file. autoOpenNonce lets the parent retrigger even
   *  when the same path is revisited. */
  autoOpenFile?: string;
  autoOpenNonce?: number;
  /** Absolute path of the project the code-agent is working in. The shell
   *  keeps the backend "advanced project" (a singleton shared with the chat
   *  "Проекты" tab) in sync with it, so the file tree / read reflect the
   *  same project the agent uses — no separate "открой проект" step. */
  projectRoot?: string;
  /** When set, render ONLY this sub-view and hide the toolbar tabs.
   *  Lets the parent (CodeWorkspaceShell) embed each view in its own
   *  drawer. Internal mainView state is ignored. */
  forceView?: "artifacts" | "filetree" | "git" | "history" | "rag";
  /** Hide the entire toolbar (back button + tabs + terminal toggle).
   *  Useful when the parent already provides a header. */
  hideToolbar?: boolean;
};

type HighlightJs = {
  highlightElement: (element: HTMLElement) => void;
};

type IconComponent = LucideIcon;

declare global {
  interface Window {
    hljs?: HighlightJs;
  }
}

function makeId(p = "id"): string { return `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }
function saveJson(k: string, v: unknown): void { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }
function displayText(value: unknown, fallback = ""): string {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
}

function extractCodeBlocks(messages: ChatMessage[]): Artifact[] {
  const blocks: Artifact[] = [];
  for(const msg of messages){
    if(msg.role!=="assistant")continue;
    const content = typeof msg.content === "string" ? msg.content : String(msg.content || "");
    const regex=/```(\w*)\n([\s\S]*?)```/g;let m: RegExpExecArray | null;
    while((m=regex.exec(content))!==null){
      const lang=m[1]||"text";const code=m[2].trim();
      if(code.length<10)continue;
      const ext={python:"py",javascript:"js",jsx:"jsx",typescript:"ts",tsx:"tsx",rust:"rs",go:"go",java:"java",css:"css",html:"html",json:"json",yaml:"yml",bash:"sh",sql:"sql",markdown:"md"}[lang]||lang||"txt";
      blocks.push({id:`code-${msg.id}-${blocks.length}`,type:"code",name:`${lang}_${blocks.length+1}.${ext}`,lang,content:code,preview:code.slice(0,200),size:code.length,source:"chat"});
    }
  }
  return blocks;
}

async function fileToRecord(file: File): Promise<LibraryFile> {
  let preview="";
  const isText=file.type.startsWith("text/")||/\.(txt|md|json|js|jsx|ts|tsx|py|css|html|yml|yaml|xml|csv|log|ini|toml|rs|go|java|c|cpp|h|rb|sh|bat|sql)$/i.test(file.name);
  if(isText)try{preview=(await file.text()).slice(0,12000);}catch{}
  if(/\.pdf$/i.test(file.name))try{const d=await api.extractUploadedFileText(file);preview=((d.text)||"").slice(0,12000);}catch{}
  return{id:makeId("lib"),name:file.name,size:file.size,type:file.type||"unknown",uploaded_at:new Date().toISOString(),preview,use_in_context:true,source:"code-upload"};
}

function normalizeFileTree(items: unknown): FileTreeItem[] {
  if (!Array.isArray(items)) return [];
  return items
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    .map((item) => {
      const path = String(item.path ?? "");
      return {
        ext: typeof item.ext === "string" ? item.ext : "",
        name: String(item.name ?? path),
        path,
        type: typeof item.type === "string" ? item.type : "",
      };
    });
}

let _hljs: HighlightJs | null = null;
let _hljsP: Promise<HighlightJs | null> | null = null;
function loadHljs(): Promise<HighlightJs | null> {
  if(_hljs)return Promise.resolve(_hljs);
  if(_hljsP)return _hljsP;
  _hljsP=new Promise(res=>{
    if(!document.querySelector('link[href*="atom-one-dark"]')){
      const l=document.createElement("link");l.rel="stylesheet";
      l.href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css";
      document.head.appendChild(l);
    }
    if(window.hljs){_hljs=window.hljs ?? null;res(_hljs);return;}
    const s=document.createElement("script");
    s.src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js";
    s.onload=()=>{_hljs=window.hljs ?? null;res(_hljs);};s.onerror=()=>res(null);
    document.head.appendChild(s);
  });
  return _hljsP;
}

function CodeView({code,lang}: { code?: string; lang?: string }) {
  const ref=useRef<HTMLElement | null>(null);
  const safeCode = code || "";
  useEffect(()=>{
    let alive=true;
    loadHljs().then(h=>{if(!h||!ref.current||!alive)return;ref.current.textContent=safeCode;h.highlightElement(ref.current);});
    return()=>{alive=false;};
  },[safeCode,lang]);
  return(
    <pre style={{flex:1,margin:0,padding:16,overflow:"auto",fontFamily:"var(--font-mono)",fontSize:12,lineHeight:1.55,whiteSpace:"pre-wrap",wordBreak:"break-word",background:"rgba(0,0,0,0.18)"}}>
      <code ref={ref} className={lang?`language-${lang}`:""} style={{fontFamily:"inherit",fontSize:"inherit",background:"transparent"}}>{safeCode}</code>
    </pre>
  );
}

const SB=(e: CSSProperties = {}): CSSProperties => ({padding:"3px 9px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer",fontSize:11,...e});
const SBG=SB({color:"#4ade80",borderColor:"rgba(74,222,128,0.35)"});

function UiIcon({ icon: Icon, size = 14, strokeWidth = 2, style }: { icon: IconComponent; size?: number; strokeWidth?: number; style?: CSSProperties }) {
  return <Icon size={size} strokeWidth={strokeWidth} style={{ display: "block", flexShrink: 0, ...style }} aria-hidden="true" />;
}

function IconText({ icon, children, size = 14, gap = 6, style }: { children: ReactNode; gap?: number; icon: IconComponent; size?: number; style?: CSSProperties }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap, ...style }}>
      <UiIcon icon={icon} size={size} />
      <span>{children}</span>
    </span>
  );
}

export default function IdeWorkspaceShell({messages=[],libraryFiles:propLib,setLibraryFiles:propSetLib,onBackToChat,onSendToChat,autoOpenFile,autoOpenNonce,projectRoot,forceView,hideToolbar}: IdeWorkspaceShellProps){
  const fileRef=useRef<HTMLInputElement | null>(null);
  const [drag,setDrag]=useState(false);
  const [selectedId,setSelectedId]=useState("");
  const [copied,setCopied]=useState(false);
  const [filterTab,setFilterTab]=useState<FilterTab>("all");
  const [search,setSearch]=useState("");
  const [showTerminal,setShowTerminal]=useState(false);
  const [internalMainView,setMainView]=useState<MainView>("artifacts");
  // Parent can force a specific sub-view (drawer mode). When set, the
  // toolbar tabs are hidden and only that view renders.
  const mainView: MainView = forceView ?? internalMainView;
  const [editing,setEditing]=useState(false);
  const [editVal,setEditVal]=useState("");
  const [saveStatus,setSaveStatus]=useState<SaveStatus>(null);
  const [gitTab,setGitTab]=useState<GitTab>("status");
  const [gitData,setGitData]=useState<GitData>({});
  const [gitLoading,setGitLoading]=useState(false);
  const [commitMsg,setCommitMsg]=useState("");
  const [runHistory,setRunHistory]=useState<ToolRunHistoryItem[] | null>(null);
  const [fileTree,setFileTree]=useState<FileTreeItem[] | null>(null);
  const [ftLoading,setFtLoading]=useState(false);
  const [ftSelected,setFtSelected]=useState<string | null>(null);
  const [ftContent,setFtContent]=useState<string | null>(null);

  // RAG sub-tab state
  const [ragItems, setRagItems] = useState<RagItem[]>([]);
  const [ragStats, setRagStats] = useState<RagStatsState | null>(null);
  const [ragLoading, setRagLoading] = useState(false);
  const [ragError, setRagError] = useState<string | null>(null);
  const [ragCategory, setRagCategory] = useState<string>("all");
  const [ragSearch, setRagSearch] = useState<string>("");
  const [ragSearchMode, setRagSearchMode] = useState(false);
  const [ragExpanded, setRagExpanded] = useState<Record<number, boolean>>({});

  const loadRagList = useCallback(async () => {
    setRagLoading(true);
    setRagError(null);
    setRagSearchMode(false);
    try {
      const [statsRes, listRes] = await Promise.all([
        api.getRagStats(),
        api.listRagItems(500),
      ]);
      if (statsRes.ok) {
        const by_category: Record<string, number> = {};
        for (const it of (listRes.items || [])) {
          by_category[it.category] = (by_category[it.category] || 0) + 1;
        }
        setRagStats({
          total: statsRes.total,
          with_embeddings: statsRes.with_embeddings,
          model: statsRes.model,
          by_category,
        });
      }
      setRagItems((listRes.items || []) as RagItem[]);
    } catch (e) {
      setRagError(String((e as Error)?.message || e));
    } finally {
      setRagLoading(false);
    }
  }, []);

  const runRagSearch = useCallback(async () => {
    const q = ragSearch.trim();
    if (!q) { loadRagList(); return; }
    setRagLoading(true);
    setRagError(null);
    setRagSearchMode(true);
    try {
      const res = await api.recallFromRag(q, 30, 0.0);
      setRagItems(((res.items || []) as unknown as RagItem[]));
    } catch (e) {
      setRagError(String((e as Error)?.message || e));
    } finally {
      setRagLoading(false);
    }
  }, [ragSearch, loadRagList]);

  const deleteRagOne = useCallback(async (id: number) => {
    try {
      await api.deleteRagItem(id);
      setRagItems((prev) => prev.filter((i) => i.id !== id));
      setRagStats((prev) => prev ? { ...prev, total: Math.max(0, prev.total - 1) } : prev);
    } catch (e) {
      setRagError(String((e as Error)?.message || e));
    }
  }, []);

  const clearCategoryItems = useCallback(async (category: string) => {
    const label = category === "all" ? "ВСЕ записи RAG" : `все записи категории «${category}»`;
    if (!confirm(`Удалить ${label}? Это нельзя отменить.`)) return;
    try {
      await api.clearRagCategory(category === "all" ? undefined : category);
      await loadRagList();
    } catch (e) {
      setRagError(String((e as Error)?.message || e));
    }
  }, [loadRagList]);

  useEffect(() => {
    if (mainView !== "rag") return;
    if (ragItems.length === 0 && !ragLoading) loadRagList();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mainView]);

  const libraryFiles=propLib||[];
  function setLibraryFiles(next: LibraryFile[]){if(propSetLib)propSetLib(next);saveJson(LIBRARY_KEY,next);}

  const codeBlocks=useMemo(()=>extractCodeBlocks(messages),[messages]);
  const fileArtifacts=useMemo<Artifact[]>(()=>libraryFiles.map(f=>({...f,type:"file",content:f.preview||"",lang:f.name.split(".").pop()||"txt",source:"library"})),[libraryFiles]);
  const allArtifacts=useMemo(()=>{
    let base=filterTab==="code"?codeBlocks:filterTab==="files"?fileArtifacts:[...codeBlocks,...fileArtifacts];
    const q=search.trim().toLowerCase();
    if(q)base=base.filter(a=>a.name.toLowerCase().includes(q)||(a.content||"").toLowerCase().includes(q));
    return base;
  },[filterTab,codeBlocks,fileArtifacts,search]);
  const selected=useMemo(()=>allArtifacts.find(a=>a.id===selectedId)||allArtifacts[0]||null,[allArtifacts,selectedId]);

  useEffect(()=>{setEditing(false);setSaveStatus(null);},[selectedId]);

  async function handleFiles(fl: FileList | File[] | null | undefined){
    const files=Array.from(fl||[]);if(!files.length)return;
    const recs=[];for(const f of files)recs.push(await fileToRecord(f));
    setLibraryFiles([...recs,...libraryFiles]);setSelectedId(recs[0]?.id||"");
  }
  function removeFile(id: string){setLibraryFiles(libraryFiles.filter(f=>f.id!==id));if(selectedId===id)setSelectedId("");}
  const handleCopy=useCallback(()=>{
    if(!selected?.content)return;
    navigator.clipboard.writeText(selected.content).then(()=>{setCopied(true);setTimeout(()=>setCopied(false),2000);});
  },[selected]);
  function onDrop(e: DragEvent<HTMLDivElement>){e.preventDefault();e.stopPropagation();setDrag(false);handleFiles(e.dataTransfer.files);}
  function onDragOver(e: DragEvent<HTMLDivElement>){e.preventDefault();e.stopPropagation();setDrag(true);}
  function onDragLeave(e: DragEvent<HTMLDivElement>){e.preventDefault();e.stopPropagation();setDrag(false);}

  function startEdit(){setEditVal(selected?.content||"");setEditing(true);setSaveStatus(null);}
  function cancelEdit(){setEditing(false);setSaveStatus(null);}
  async function applyEdit(){
    if(!selected)return;
    setSaveStatus("saving");
    try{
      const d=await api.writeFile({path:selected.name,content:editVal,create_dirs:true});
      setSaveStatus(d.ok?"ok":"error");
      if(d.ok){setEditing(false);setTimeout(()=>setSaveStatus(null),2500);}
    }catch{setSaveStatus("error");}
  }

  function askElira(prompt: string){
    if(!selected||!onSendToChat)return;
    onSendToChat(`${prompt}\n\`\`\`${selected.lang||""}\n${(selected.content||"").slice(0,3000)}\n\`\`\``);
  }

  async function fetchGit(tab: GitTab){
    setGitTab(tab);setGitLoading(true);
    try{
      if(tab==="status"){const d=await api.getGitStatus(projectRoot||"") as GitStatusData;setGitData(p=>({...p,status:d}));}
      else if(tab==="log"){const d=await api.getGitLog(20,projectRoot||"") as GitLogData;setGitData(p=>({...p,log:d}));}
      else if(tab==="diff"){const d=await api.getGitDiff({repo_path:projectRoot||"",file_path:""}) as GitDiffData;setGitData(p=>({...p,diff:d}));}
    }catch(e){setGitData(p=>({...p,[tab]:{ok:false,error:String(e)}}));}
    finally{setGitLoading(false);}
  }
  async function doCommit(){
    if(!commitMsg.trim())return;setGitLoading(true);
    try{
      const d=await api.createGitCommit({message:commitMsg,add_all:true,repo_path:projectRoot||""});
      if(d.ok){
        setCommitMsg("");
        fetchGit("status");
        toast.success("Коммит создан");
      } else {
        toast.error("Git: " + String(d.error || "неизвестная ошибка"));
      }
    } catch(e){
      toast.error("Git: " + String(e));
    } finally {
      setGitLoading(false);
    }
  }
  useEffect(()=>{if(mainView==="git"&&!gitData.status)fetchGit("status");},[mainView]);

  useEffect(()=>{
    if(mainView!=="history"||runHistory!==null)return;
    api.listToolRuns(50).then(d=>setRunHistory((d||[]) as ToolRunHistoryItem[])).catch(()=>setRunHistory([]));
  },[mainView]);

  // The drawer reads the file tree / files SCOPED to the agent's projectRoot
  // (passed as `root` to the path-parameterized endpoints). It does NOT open
  // the global "advanced project", so the code-agent drawer stays independent
  // of whatever project the chat's Проекты tab has open. Reset the tree when
  // projectRoot changes so it reloads for the new path.
  useEffect(()=>{
    setFileTree(null);setFtSelected(null);setFtContent(null);
  },[projectRoot]);

  const reloadTree = useCallback(async()=>{
    if(!projectRoot){setFileTree([]);return;}
    setFtLoading(true);
    try{
      const d=await api.getAdvancedProjectTree({maxDepth:3,maxItems:300,root:projectRoot});
      setFileTree(normalizeFileTree(d.items));
    }catch{
      setFileTree([]);
    }finally{
      setFtLoading(false);
    }
  },[projectRoot]);

  useEffect(()=>{
    if(mainView!=="filetree"||fileTree!==null)return;
    if(!projectRoot){setFileTree([]);return;}
    setFtLoading(true);
    api.getAdvancedProjectTree({maxDepth:3,maxItems:300,root:projectRoot}).then(d=>{setFileTree(normalizeFileTree(d.items));setFtLoading(false);}).catch(()=>{setFileTree([]);setFtLoading(false);});
  },[mainView,fileTree,projectRoot]);

  async function openFtFile(item: FileTreeItem){
    if(item.type!=="file")return;
    setFtSelected(item.path);setFtContent(null);
    try{
      const r={json: async()=>await api.readAdvancedProjectFile(item.path, 20000, projectRoot)};
      const d=await r.json();setFtContent(d.ok?String(d.content || ""):"Ошибка: "+String(d.error || ""));
    }catch(e){setFtContent("Ошибка: "+String(e));}
  }

  // Auto-open: when the code-agent touches a file (via autoOpenFile prop),
  // switch the IDE pane to the Файлы tab and load the file content. The
  // nonce ensures we retrigger even if the agent reads the same path twice.
  useEffect(() => {
    if (!autoOpenFile) return;
    setMainView("filetree");
    setFtSelected(autoOpenFile);
    setFtContent(null);
    (async () => {
      try {
        const d = await api.readAdvancedProjectFile(autoOpenFile, 20000, projectRoot);
        setFtContent(d.ok ? String(d.content || "") : "Ошибка: " + String(d.error || ""));
      } catch (e) {
        setFtContent("Ошибка: " + String(e));
      }
    })();
  // autoOpenNonce participates so a re-touch of the same path retriggers.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpenFile, autoOpenNonce]);

  const iconFor = (a: Artifact): IconComponent =>
    a.type === "code"
      ? Code2
      : /\.pdf$/i.test(a.name || "")
        ? FileText
        : /\.(js|jsx|ts|tsx|py|rs|go|java|c|cpp)$/i.test(a.name || "")
          ? FileCode2
          : Files;
  const gitColor=(s?: string)=>({M:"#e2b93d",A:"#4ade80",D:"#ff6b6b","?":"#888"}[s?.[0] as "?" | "A" | "D" | "M"]||"#aaa");

  return(
    <div className="ide-shell" style={{display:"flex",flexDirection:"column",height:"100%",padding:0}}>

      {/* Toolbar — hidden in drawer mode (parent provides its own header) */}
      {!hideToolbar && !forceView && (
      <div style={{display:"flex",alignItems:"center",gap:5,padding:"7px 12px",borderBottom:"1px solid var(--border)",flexWrap:"wrap"}}>
        {onBackToChat && <button onClick={onBackToChat} className="soft-btn" style={{border:"1px solid var(--border)",display:"inline-flex",alignItems:"center",gap:6}}><UiIcon icon={ArrowLeft} size={13} />Чат</button>}
        <div style={{display:"flex",gap:2,marginLeft:6}}>
          {[["artifacts","Артефакты", Files],["git","Git", GitBranch],["filetree","Файлы", FolderOpen],["rag","RAG", Database],["history","История", History]].map(([k,l,Icon])=>(
            <button key={String(k)} className={`soft-btn ${mainView===k?"active":""}`} onClick={()=>setMainView(k as MainView)} style={{fontSize:11,padding:"3px 9px"}}><IconText icon={Icon as IconComponent} size={12} gap={5}>{String(l)}</IconText></button>
          ))}
        </div>
        {mainView==="artifacts"&&(
          <div style={{display:"flex",gap:2,marginLeft:6}}>
            {[["all","Всё"],["code","Код "+codeBlocks.length],["files","Файлы "+fileArtifacts.length]].map(([k,l])=>(
              <button key={String(k)} className={`soft-btn ${filterTab===k?"active":""}`} onClick={()=>setFilterTab(k as FilterTab)} style={{fontSize:11,padding:"3px 9px"}}>{String(l)}</button>
            ))}
          </div>
        )}
        <div style={{marginLeft:"auto"}}>
          <button onClick={()=>setShowTerminal(p=>!p)} className="soft-btn" style={{border:"1px solid var(--border)",fontSize:11,padding:"3px 9px",background:showTerminal?"var(--bg-surface-active)":"transparent",display:"inline-flex",alignItems:"center",gap:6}}>
            <UiIcon icon={Terminal} size={12} />
            <span>{showTerminal ? "Скрыть" : "Показать"} терминал</span>
          </button>
        </div>
      </div>
      )}

      {/* ARTIFACTS */}
      {mainView==="artifacts"&&(
        <div style={{flex:1,display:"grid",gridTemplateColumns:"256px 1fr",minHeight:0}}>
          <div style={{borderRight:"1px solid var(--border)",display:"flex",flexDirection:"column",minHeight:0}}>
            <div style={{padding:"6px 8px",borderBottom:"1px solid var(--border)"}}>
              <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Поиск по имени / коду..."
                style={{width:"100%",padding:"5px 8px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-input)",color:"var(--text-primary)",fontSize:11,outline:"none",boxSizing:"border-box"}}
              />
            </div>
            <div className={`drop-panel ${drag?"active":""}`} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop} onClick={()=>fileRef.current?.click()} style={{margin:7,minHeight:46,padding:10,borderRadius:6}}>
              <div style={{fontSize:11,color:"var(--text-muted)"}}>+ Загрузить файлы</div>
            </div>
            <input ref={fileRef} type="file" multiple hidden onChange={e=>handleFiles(e.target.files)}/>
            <div style={{flex:1,overflow:"auto",padding:"0 4px 8px"}}>
              {allArtifacts.length===0&&(
                <div style={{padding:16,fontSize:11,color:"var(--text-muted)",textAlign:"center"}}>
                  {search?"Ничего не найдено":messages.length===0?"Начни чат — код появится здесь":"Нет блоков кода"}
                </div>
              )}
              {allArtifacts.map(a=>(
                <button key={a.id} onClick={()=>setSelectedId(a.id)} style={{display:"flex",alignItems:"center",gap:8,width:"100%",padding:"7px 10px",margin:"1px 0",borderRadius:6,border:"none",background:selectedId===a.id?"var(--bg-surface-active)":"transparent",color:selectedId===a.id?"var(--text-primary)":"var(--text-secondary)",cursor:"pointer",textAlign:"left",fontSize:11}}>
                  <span style={{fontSize:13,opacity:0.6}}><UiIcon icon={iconFor(a)} size={14} /></span>
                  <div style={{flex:1,minWidth:0}}>
                    <div style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontWeight:selectedId===a.id?500:400}}>{a.name}</div>
                    <div style={{fontSize:10,color:"var(--text-muted)",marginTop:1}}>{a.source==="chat"?"из чата":a.type==="file"?`${Math.round((a.size ?? 0)/1024)||0}K`:""}{a.lang?` · ${a.lang}`:""}</div>
                  </div>
                  {a.source==="library"&&<button onClick={e=>{e.stopPropagation();removeFile(a.id);}} style={{border:"none",background:"transparent",color:"var(--text-muted)",cursor:"pointer",fontSize:11,padding:0}}>X</button>}
                </button>
              ))}
            </div>
          </div>

          <div style={{display:"flex",flexDirection:"column",minHeight:0}}>
            {selected?(
              <>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"7px 12px",borderBottom:"1px solid var(--border)",flexWrap:"wrap",gap:5}}>
                  <div style={{display:"flex",alignItems:"center",gap:8,minWidth:0,flex:1}}>
                    <span style={{fontWeight:500,fontSize:13,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{selected.name}</span>
                    <span style={{fontSize:10,color:"var(--text-muted)",flexShrink:0}}>{selected.lang} | {selected.content?.length||0} симв.</span>
                    {saveStatus==="ok"&&<span style={{fontSize:10,color:"#4ade80"}}>✓ Сохранено</span>}
                    {saveStatus==="error"&&<span style={{fontSize:10,color:"#ff6b6b"}}>✕ Ошибка</span>}
                  </div>
                  <div style={{display:"flex",gap:3,flexWrap:"wrap",flexShrink:0}}>
                    {onSendToChat&&<>
                      <button onClick={()=>askElira("Объясни этот код:")} style={SB()}><IconText icon={MessageSquare} size={12} gap={5}>Объясни</IconText></button>
                      <button onClick={()=>askElira("Найди и исправь баги в этом коде:")} style={SB()}><IconText icon={Bug} size={12} gap={5}>Баги</IconText></button>
                      <button onClick={()=>askElira("Напиши тесты для этого кода:")} style={SB()}><IconText icon={TestTube2} size={12} gap={5}>Тесты</IconText></button>
                    </>}
                    {!editing?(
                      <>
                        <button onClick={startEdit} style={SB()}><IconText icon={Pencil} size={12} gap={5}>Изменить</IconText></button>
                      <button onClick={handleCopy} style={SB({borderColor:"var(--border)"})}>{copied?<IconText icon={Check} size={12} gap={5}>Скопировано</IconText>:<IconText icon={Copy} size={12} gap={5}>Копировать</IconText>}</button>
                      </>
                    ):(
                      <>
                        <button onClick={applyEdit} disabled={saveStatus==="saving"} style={{...SBG,opacity:saveStatus==="saving"?0.5:1}}>{saveStatus==="saving"?<IconText icon={RefreshCw} size={12} gap={5}>Применить</IconText>:<IconText icon={Save} size={12} gap={5}>Применить</IconText>}</button>
                        <button onClick={cancelEdit} style={SB()}><IconText icon={X} size={12} gap={5}>Отмена</IconText></button>
                      </>
                    )}
                  </div>
                </div>
                {editing
                  ?<textarea value={editVal} onChange={e=>setEditVal(e.target.value)} style={{flex:1,margin:0,padding:16,border:"none",outline:"none",resize:"none",fontFamily:"var(--font-mono)",fontSize:12,lineHeight:1.55,color:"var(--text-primary)",background:"rgba(0,0,0,0.2)",whiteSpace:"pre",overflowWrap:"normal",overflowX:"auto"}}/>
                  :<CodeView code={selected.content||selected.preview||"Содержимое недоступно"} lang={selected.lang||""}/>
                }
              </>
            ):(
              <div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-muted)",fontSize:13}}>
                <div style={{textAlign:"center"}}>
                  <div style={{fontSize:32,opacity:0.12,marginBottom:8}}>[]</div>
                  <div>Выбери артефакт слева</div>
                  <div style={{fontSize:11,marginTop:4,opacity:0.6}}>Код из ответов Elira появляется автоматически</div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* GIT */}
      {mainView==="git"&&(
        <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"auto"}}>
          <div style={{display:"flex",gap:4,padding:"10px 14px 0",borderBottom:"1px solid var(--border)"}}>
            {[["status","Статус", BarChart3],["log","История", History],["diff","Изменения", FileText]].map(([k,l,Icon])=>(
              <button key={String(k)} className={`soft-btn ${gitTab===k?"active":""}`} onClick={()=>fetchGit(k as GitTab)} style={{fontSize:11,padding:"4px 12px",marginBottom:-1}}><IconText icon={Icon as IconComponent} size={12} gap={5}>{String(l)}</IconText></button>
            ))}
            {gitLoading&&<span style={{fontSize:11,color:"var(--text-muted)",alignSelf:"center",marginLeft:8}}><UiIcon icon={Loader2} size={13} /></span>}
          </div>
          <div style={{flex:1,padding:16,overflow:"auto"}}>
            {gitTab==="status"&&(()=>{const d=gitData.status;if(!d)return null;
              if(!d.ok)return<div style={{color:"#ff6b6b",fontSize:12}}>{displayText(d.error)}</div>;
              return(
                <div>
                  <div style={{fontSize:12,marginBottom:12}}>
                    <span style={{color:"var(--text-muted)"}}>Ветка: </span><strong style={{color:"var(--accent)"}}>{displayText(d.branch)}</strong>
                    <span style={{color:"var(--text-muted)",marginLeft:16,fontSize:11}}>{displayText(d.repo)}</span>
                  </div>
                  {d.clean
                    ?<div style={{color:"#4ade80",fontSize:12,marginBottom:12}}>✓ Рабочая директория чистая</div>
                    :<div style={{marginBottom:12}}>{(d.files||[]).map((f,i)=>(
                      <div key={i} style={{display:"flex",gap:10,padding:"4px 0",borderBottom:"1px solid var(--border-light)",fontSize:12}}>
                        <span style={{fontFamily:"var(--font-mono)",fontSize:11,color:gitColor(f.status),minWidth:22,flexShrink:0}}>{f.status}</span>
                        <span style={{fontFamily:"var(--font-mono)",fontSize:11,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{f.file}</span>
                      </div>
                    ))}</div>
                  }
                  <div style={{marginTop:16,padding:14,background:"var(--bg-surface)",borderRadius:8,border:"1px solid var(--border)"}}>
                    <div style={{fontSize:11,color:"var(--text-muted)",marginBottom:8}}>Сообщение коммита</div>
                    <div style={{display:"flex",gap:8}}>
                      <input value={commitMsg} onChange={e=>setCommitMsg(e.target.value)} onKeyDown={e=>e.key==="Enter"&&doCommit()}
                        placeholder="feat: описание изменений..."
                        style={{flex:1,padding:"6px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-input)",color:"var(--text-primary)",fontSize:12,outline:"none"}}
                      />
                      <button onClick={doCommit} disabled={!commitMsg.trim()||gitLoading} style={{...SBG,padding:"6px 14px",opacity:(!commitMsg.trim()||gitLoading)?0.45:1}}>
                        {gitLoading?"...":"✓"} Коммит
                      </button>
                    </div>
                    <div style={{fontSize:10,color:"var(--text-muted)",marginTop:5}}>Команда: git add -A && git commit</div>
                  </div>
                </div>
              );
            })()}
            {gitTab==="log"&&(()=>{const d=gitData.log;if(!d)return null;
              if(!d.ok)return<div style={{color:"#ff6b6b",fontSize:12}}>{displayText(d.error)}</div>;
              return(
                <div>
                  <div style={{fontSize:11,color:"var(--text-muted)",marginBottom:10}}>{(d.commits||[]).length} коммитов — {displayText(d.repo)}</div>
                  {(d.commits||[]).map((c,i)=>(
                    <div key={i} style={{display:"flex",gap:10,padding:"6px 0",borderBottom:"1px solid var(--border-light)"}}>
                      <code style={{fontSize:11,color:"var(--accent)",flexShrink:0,fontFamily:"var(--font-mono)"}}>{c.hash}</code>
                      <span style={{fontSize:12}}>{c.message}</span>
                    </div>
                  ))}
                </div>
              );
            })()}
            {gitTab==="diff"&&(()=>{const d=gitData.diff;if(!d)return null;
              if(!d.ok)return<div style={{color:"#ff6b6b",fontSize:12}}>{displayText(d.error)}</div>;
              return(
                <div>
                  {Boolean(d.stat)&&<div style={{fontSize:12,color:"var(--text-muted)",marginBottom:10,whiteSpace:"pre-wrap",fontFamily:"var(--font-mono)"}}>{displayText(d.stat)}</div>}
                  <pre style={{margin:0,fontFamily:"var(--font-mono)",fontSize:11,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",color:"var(--text-primary)",background:"rgba(0,0,0,0.15)",padding:12,borderRadius:8,overflow:"auto",maxHeight:500}}>
                    {displayText(d.diff, "Нет изменений")}
                  </pre>
                </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* FILETREE */}
      {mainView==="filetree"&&(
        <div style={{flex:1,display:"grid",gridTemplateColumns:"256px 1fr",minHeight:0}}>
          <div style={{borderRight:"1px solid var(--border)",overflow:"auto",padding:"6px 4px"}}>
            {ftLoading&&<div style={{padding:16,fontSize:11,color:"var(--text-muted)",display:"inline-flex",alignItems:"center",gap:6}}><UiIcon icon={Loader2} size={12} />Загрузка дерева...</div>}
            {!ftLoading&&fileTree&&fileTree.length===0&&(
              <div style={{padding:16,fontSize:11,color:"var(--text-muted)",lineHeight:1.6}}>
                {projectRoot?(
                  <>
                    Дерево пусто.<br/>
                    Проект: <code style={{fontSize:10,wordBreak:"break-all"}}>{projectRoot}</code><br/>
                    <button onClick={reloadTree} style={{marginTop:8,padding:"3px 10px",fontSize:10,border:"1px solid var(--border)",borderRadius:6,background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>Обновить дерево</button>
                  </>
                ):(
                  <>Проект не выбран.<br/>Укажи папку проекта в панели Code Agent сверху.</>
                )}
              </div>
            )}
            {(fileTree||[]).map((item,i)=>(
              <button key={i} onClick={()=>openFtFile(item)} style={{display:"flex",alignItems:"center",gap:5,width:"100%",padding:`4px ${6+(item.path.split("/").length-1)*10}px`,border:"none",background:ftSelected===item.path?"var(--bg-surface-active)":"transparent",color:ftSelected===item.path?"var(--text-primary)":"var(--text-secondary)",cursor:item.type==="file"?"pointer":"default",textAlign:"left",fontSize:11,borderRadius:4}}>
                <span style={{fontSize:11,opacity:0.4,flexShrink:0}}><UiIcon icon={item.type==="dir" ? FolderOpen : FileText} size={12} /></span>
                <span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",flex:1}}>{item.name}</span>
                {item.type==="file"&&<span style={{fontSize:10,color:"var(--text-muted)",flexShrink:0}}>{item.ext}</span>}
              </button>
            ))}
          </div>
          <div style={{display:"flex",flexDirection:"column",minHeight:0}}>
            {ftSelected&&ftContent!==null
              ?<><div style={{padding:"7px 14px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-muted)",fontFamily:"var(--font-mono)"}}>{ftSelected}</div><CodeView code={ftContent} lang={ftSelected.split(".").pop()||""}/></>
              :ftSelected
                ?<div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-muted)",fontSize:12,gap:6}}><UiIcon icon={Loader2} size={13} />Загрузка...</div>
                :<div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-muted)",fontSize:12}}><div style={{textAlign:"center"}}><div style={{display:"flex",justifyContent:"center",opacity:0.18,marginBottom:8}}><UiIcon icon={FolderOpen} size={28} /></div><div>Выбери файл слева</div></div></div>
            }
          </div>
        </div>
      )}

      {/* RAG */}
      {mainView==="rag"&&(
        <div style={{flex:1,display:"flex",flexDirection:"column",minHeight:0,overflow:"hidden"}}>
          {/* Stats bar */}
          <div style={{padding:"8px 14px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:10,flexWrap:"wrap",background:"var(--bg-surface)"}}>
            <UiIcon icon={Database} size={13} />
            <span style={{fontSize:11,fontWeight:500}}>RAG memory</span>
            {ragStats ? (
              <>
                <span style={{fontSize:11,color:"var(--text-muted)"}}>{ragStats.total} записей</span>
                <span style={{fontSize:11,color:"var(--text-muted)"}}>· с эмбеддингами: {ragStats.with_embeddings}</span>
                {ragStats.model && <span style={{fontSize:10,color:"var(--text-muted)",fontFamily:"var(--font-mono)"}}>· {ragStats.model}</span>}
                {ragStats.by_category && Object.entries(ragStats.by_category).map(([cat,n]) => (
                  <button key={cat} onClick={()=>setRagCategory(cat)} className={`soft-btn ${ragCategory===cat?"active":""}`} style={{fontSize:10,padding:"2px 8px"}}>
                    {cat}: {n}
                  </button>
                ))}
              </>
            ) : <span style={{fontSize:11,color:"var(--text-muted)"}}>загрузка...</span>}
            <button onClick={()=>setRagCategory("all")} className={`soft-btn ${ragCategory==="all"?"active":""}`} style={{fontSize:10,padding:"2px 8px"}}>все</button>
            <button onClick={loadRagList} disabled={ragLoading} className="soft-btn" style={{marginLeft:"auto",fontSize:11,padding:"4px 10px",opacity:ragLoading?0.5:1}}>
              <IconText icon={RefreshCw} size={11} gap={4}>Обновить</IconText>
            </button>
            <button onClick={()=>clearCategoryItems(ragCategory)} disabled={ragLoading} className="soft-btn" style={{fontSize:11,padding:"4px 10px",color:"#ff6b6b",borderColor:"rgba(255,107,107,0.4)"}}>
              <IconText icon={Trash2} size={11} gap={4}>Очистить {ragCategory==="all"?"всё":`«${ragCategory}»`}</IconText>
            </button>
          </div>

          {/* Search */}
          <div style={{padding:"8px 14px",borderBottom:"1px solid var(--border)",display:"flex",gap:6,alignItems:"center"}}>
            <UiIcon icon={Search} size={12} />
            <input
              value={ragSearch}
              onChange={(e)=>setRagSearch(e.target.value)}
              onKeyDown={(e)=>{if(e.key==="Enter")runRagSearch();}}
              placeholder="Семантический поиск по RAG (Enter)..."
              spellCheck={false}
              style={{flex:1,padding:"5px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-input)",color:"var(--text-primary)",fontSize:11,outline:"none"}}
            />
            <button onClick={runRagSearch} disabled={ragLoading} className="soft-btn" style={{fontSize:11,padding:"4px 10px"}}>
              Искать
            </button>
            {ragSearchMode && (
              <button onClick={()=>{setRagSearch("");loadRagList();}} className="soft-btn" style={{fontSize:11,padding:"4px 10px"}}>
                Сбросить
              </button>
            )}
          </div>

          {ragError && (
            <div style={{padding:"6px 14px",background:"rgba(255,107,107,0.08)",color:"#ff6b6b",fontSize:11,borderBottom:"1px solid var(--border)"}}>
              {ragError}
            </div>
          )}

          {/* List */}
          <div style={{flex:1,overflow:"auto",padding:"6px 14px"}}>
            {ragLoading && (
              <div style={{padding:18,fontSize:12,color:"var(--text-muted)",display:"flex",alignItems:"center",gap:8,justifyContent:"center"}}>
                <UiIcon icon={Loader2} size={14}/>
                <span>Загружаю...</span>
              </div>
            )}
            {!ragLoading && ragItems.length === 0 && (
              <div style={{padding:30,fontSize:12,color:"var(--text-muted)",textAlign:"center",lineHeight:1.6}}>
                {ragSearchMode ? "Ничего не найдено по запросу." : (
                  <>
                    RAG пуст.<br/>
                    <span style={{fontSize:11}}>Нажми «Индексировать» в верхней панели Code workspace, или дай агенту задачу с auto-remember=on — записи появятся здесь.</span>
                  </>
                )}
              </div>
            )}
            {!ragLoading && ragItems
              .filter((it)=>ragCategory==="all"||it.category===ragCategory)
              .map((it)=>{
                const isOpen = !!ragExpanded[it.id];
                const preview = (it.text || "").slice(0, 200);
                return (
                  <div key={it.id} style={{marginBottom:6,border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-surface)"}}>
                    <div style={{display:"flex",alignItems:"center",gap:8,padding:"6px 10px",borderBottom:isOpen?"1px solid var(--border)":"none"}}>
                      <span style={{fontSize:10,padding:"1px 7px",borderRadius:10,background:"rgba(99,102,241,0.18)",color:"var(--accent, #6366f1)",fontFamily:"var(--font-mono)",flexShrink:0}}>{it.category}</span>
                      {typeof it.score === "number" && (
                        <span style={{fontSize:10,color:"#4ade80",fontFamily:"var(--font-mono)",flexShrink:0}}>score={it.score.toFixed(2)}</span>
                      )}
                      <span style={{fontSize:10,color:"var(--text-muted)",flexShrink:0}}>imp={it.importance}</span>
                      <button onClick={()=>setRagExpanded((p)=>({...p,[it.id]:!p[it.id]}))} style={{flex:1,minWidth:0,border:"none",background:"transparent",cursor:"pointer",textAlign:"left",fontSize:11,color:"var(--text-secondary)",fontFamily:"var(--font-mono)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",padding:0}}>
                        {preview}
                      </button>
                      {it.created_at && <span style={{fontSize:10,color:"var(--text-muted)",flexShrink:0}}>{it.created_at.slice(0,10)}</span>}
                      <button onClick={()=>{if(confirm("Удалить эту запись?"))deleteRagOne(it.id);}} style={{border:"none",background:"transparent",cursor:"pointer",color:"var(--text-muted)",padding:2,flexShrink:0}} title="Удалить">
                        <UiIcon icon={Trash2} size={11}/>
                      </button>
                    </div>
                    {isOpen && (
                      <pre style={{margin:0,padding:"8px 12px",fontSize:11,fontFamily:"var(--font-mono)",lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",color:"var(--text-primary)",background:"rgba(0,0,0,0.15)",maxHeight:400,overflow:"auto"}}>
                        {it.text}
                      </pre>
                    )}
                  </div>
                );
              })}
          </div>
        </div>
      )}

      {/* HISTORY */}
      {mainView==="history"&&(
        <div style={{flex:1,overflow:"auto",padding:16}}>
          {runHistory===null&&<div style={{fontSize:12,color:"var(--text-muted)",display:"inline-flex",alignItems:"center",gap:6}}><UiIcon icon={Loader2} size={13} />Загрузка...</div>}
          {runHistory!==null&&runHistory.length===0&&<div style={{fontSize:12,color:"var(--text-muted)"}}>История пуста — записи появятся после первых запросов к Elira.</div>}
          {(runHistory||[]).map((r,i)=>(
            <div key={i} style={{padding:"10px 14px",marginBottom:6,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-surface)"}}>
              <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
                <code style={{fontSize:10,fontFamily:"var(--font-mono)",color:"var(--accent)"}}>{r.run_id}</code>
                <span style={{fontSize:10,padding:"1px 7px",borderRadius:20,background:r.ok?"rgba(74,222,128,0.15)":"rgba(255,107,107,0.15)",color:r.ok?"#4ade80":"#ff6b6b"}}>{r.ok?"Готово":"Ошибка"}</span>
                {r.route&&<span style={{fontSize:10,color:"var(--text-muted)"}}>маршрут: {r.route}</span>}
                {r.model&&<span style={{fontSize:10,color:"var(--text-muted)"}}>модель: {r.model}</span>}
                {(r.answer_len ?? 0)>0&&<span style={{fontSize:10,color:"var(--text-muted)"}}>{r.answer_len} симв.</span>}
                <span style={{fontSize:10,color:"var(--text-muted)",marginLeft:"auto"}}>{(r.finished_at||"").replace("T"," ").slice(0,19)}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Terminal */}
      {showTerminal&&(
        <div style={{height:240,borderTop:"1px solid var(--border)",flexShrink:0}}>
          <TerminalPanel/>
        </div>
      )}
    </div>
  );
}
