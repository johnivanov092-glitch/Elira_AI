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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import TerminalPanel from "./TerminalPanel";

const LIBRARY_KEY = "jarvis_library_files_v7";
const API = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

function makeId(p="id"){return`${p}-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;}
function saveJson(k,v){try{localStorage.setItem(k,JSON.stringify(v));}catch{}}

function extractCodeBlocks(messages){
  const blocks=[];
  for(const msg of messages){
    if(msg.role!=="assistant")continue;
    const regex=/```(\w*)\n([\s\S]*?)```/g;let m;
    while((m=regex.exec(msg.content||""))!==null){
      const lang=m[1]||"text";const code=m[2].trim();
      if(code.length<10)continue;
      const ext={python:"py",javascript:"js",jsx:"jsx",typescript:"ts",tsx:"tsx",rust:"rs",go:"go",java:"java",css:"css",html:"html",json:"json",yaml:"yml",bash:"sh",sql:"sql",markdown:"md"}[lang]||lang||"txt";
      blocks.push({id:`code-${msg.id}-${blocks.length}`,type:"code",name:`${lang}_${blocks.length+1}.${ext}`,lang,content:code,preview:code.slice(0,200),size:code.length,source:"chat"});
    }
  }
  return blocks;
}

async function fileToRecord(file){
  let preview="";
  const isText=file.type.startsWith("text/")||/\.(txt|md|json|js|jsx|ts|tsx|py|css|html|yml|yaml|xml|csv|log|ini|toml|rs|go|java|c|cpp|h|rb|sh|bat|sql)$/i.test(file.name);
  if(isText)try{preview=(await file.text()).slice(0,12000);}catch{}
  if(/\.pdf$/i.test(file.name))try{const fd=new FormData();fd.append("file",file);const r=await fetch(`${API}/api/files/extract-text`,{method:"POST",body:fd});if(r.ok)preview=((await r.json()).text||"").slice(0,12000);}catch{}
  return{id:makeId("lib"),name:file.name,size:file.size,type:file.type||"unknown",uploaded_at:new Date().toISOString(),preview,use_in_context:true,source:"code-upload"};
}

let _hljs=null,_hljsP=null;
function loadHljs(){
  if(_hljs)return Promise.resolve(_hljs);
  if(_hljsP)return _hljsP;
  _hljsP=new Promise(res=>{
    if(!document.querySelector('link[href*="atom-one-dark"]')){
      const l=document.createElement("link");l.rel="stylesheet";
      l.href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css";
      document.head.appendChild(l);
    }
    if(window.hljs){_hljs=window.hljs;res(_hljs);return;}
    const s=document.createElement("script");
    s.src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js";
    s.onload=()=>{_hljs=window.hljs;res(_hljs);};s.onerror=()=>res(null);
    document.head.appendChild(s);
  });
  return _hljsP;
}

function CodeView({code,lang}){
  const ref=useRef(null);
  useEffect(()=>{
    let alive=true;
    loadHljs().then(h=>{if(!h||!ref.current||!alive)return;ref.current.textContent=code;h.highlightElement(ref.current);});
    return()=>{alive=false;};
  },[code,lang]);
  return(
    <pre style={{flex:1,margin:0,padding:16,overflow:"auto",fontFamily:"var(--font-mono)",fontSize:12,lineHeight:1.55,whiteSpace:"pre-wrap",wordBreak:"break-word",background:"rgba(0,0,0,0.18)"}}>
      <code ref={ref} className={lang?`language-${lang}`:""} style={{fontFamily:"inherit",fontSize:"inherit",background:"transparent"}}>{code}</code>
    </pre>
  );
}

const SB=(e={})=>({padding:"3px 9px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer",fontSize:11,...e});
const SBG=SB({color:"#4ade80",borderColor:"rgba(74,222,128,0.35)"});
const SBB=SB({color:"#60a5fa",borderColor:"rgba(96,165,250,0.35)"});

export default function IdeWorkspaceShell({messages=[],libraryFiles:propLib,setLibraryFiles:propSetLib,onBackToChat,onSendToChat}){
  const fileRef=useRef(null);
  const [drag,setDrag]=useState(false);
  const [selectedId,setSelectedId]=useState("");
  const [copied,setCopied]=useState(false);
  const [filterTab,setFilterTab]=useState("all");
  const [search,setSearch]=useState("");
  const [showTerminal,setShowTerminal]=useState(false);
  const [mainView,setMainView]=useState("artifacts");
  const [editing,setEditing]=useState(false);
  const [editVal,setEditVal]=useState("");
  const [saveStatus,setSaveStatus]=useState(null);
  const [gitTab,setGitTab]=useState("status");
  const [gitData,setGitData]=useState({});
  const [gitLoading,setGitLoading]=useState(false);
  const [commitMsg,setCommitMsg]=useState("");
  const [runHistory,setRunHistory]=useState(null);
  const [fileTree,setFileTree]=useState(null);
  const [ftLoading,setFtLoading]=useState(false);
  const [ftSelected,setFtSelected]=useState(null);
  const [ftContent,setFtContent]=useState(null);

  const libraryFiles=propLib||[];
  function setLibraryFiles(next){if(propSetLib)propSetLib(next);saveJson(LIBRARY_KEY,next);}

  const codeBlocks=useMemo(()=>extractCodeBlocks(messages),[messages]);
  const fileArtifacts=useMemo(()=>libraryFiles.map(f=>({...f,type:"file",content:f.preview||"",lang:f.name.split(".").pop()||"txt",source:"library"})),[libraryFiles]);
  const allArtifacts=useMemo(()=>{
    let base=filterTab==="code"?codeBlocks:filterTab==="files"?fileArtifacts:[...codeBlocks,...fileArtifacts];
    const q=search.trim().toLowerCase();
    if(q)base=base.filter(a=>a.name.toLowerCase().includes(q)||(a.content||"").toLowerCase().includes(q));
    return base;
  },[filterTab,codeBlocks,fileArtifacts,search]);
  const selected=useMemo(()=>allArtifacts.find(a=>a.id===selectedId)||allArtifacts[0]||null,[allArtifacts,selectedId]);

  useEffect(()=>{setEditing(false);setSaveStatus(null);},[selectedId]);

  async function handleFiles(fl){
    const files=Array.from(fl||[]);if(!files.length)return;
    const recs=[];for(const f of files)recs.push(await fileToRecord(f));
    setLibraryFiles([...recs,...libraryFiles]);setSelectedId(recs[0]?.id||"");
  }
  function removeFile(id){setLibraryFiles(libraryFiles.filter(f=>f.id!==id));if(selectedId===id)setSelectedId("");}
  const handleCopy=useCallback(()=>{
    if(!selected?.content)return;
    navigator.clipboard.writeText(selected.content).then(()=>{setCopied(true);setTimeout(()=>setCopied(false),2000);});
  },[selected]);
  function onDrop(e){e.preventDefault();e.stopPropagation();setDrag(false);handleFiles(e.dataTransfer.files);}
  function onDragOver(e){e.preventDefault();e.stopPropagation();setDrag(true);}
  function onDragLeave(e){e.preventDefault();e.stopPropagation();setDrag(false);}

  function startEdit(){setEditVal(selected?.content||"");setEditing(true);setSaveStatus(null);}
  function cancelEdit(){setEditing(false);setSaveStatus(null);}
  async function applyEdit(){
    setSaveStatus("saving");
    try{
      const r=await fetch(`${API}/api/file-ops/write`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:selected.name,content:editVal,create_dirs:true})});
      const d=await r.json();
      setSaveStatus(d.ok?"ok":"error");
      if(d.ok){setEditing(false);setTimeout(()=>setSaveStatus(null),2500);}
    }catch{setSaveStatus("error");}
  }

  function askJarvis(prompt){
    if(!selected||!onSendToChat)return;
    onSendToChat(`${prompt}\n\`\`\`${selected.lang||""}\n${(selected.content||"").slice(0,3000)}\n\`\`\``);
  }

  async function fetchGit(tab){
    setGitTab(tab);setGitLoading(true);
    try{
      let r,d;
      if(tab==="status"){r=await fetch(`${API}/api/git/status`);d=await r.json();setGitData(p=>({...p,status:d}));}
      else if(tab==="log"){r=await fetch(`${API}/api/git/log?limit=20`);d=await r.json();setGitData(p=>({...p,log:d}));}
      else if(tab==="diff"){r=await fetch(`${API}/api/git/diff`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({repo_path:"",file_path:""})});d=await r.json();setGitData(p=>({...p,diff:d}));}
    }catch(e){setGitData(p=>({...p,[tab]:{ok:false,error:String(e)}}));}
    finally{setGitLoading(false);}
  }
  async function doCommit(){
    if(!commitMsg.trim())return;setGitLoading(true);
    try{
      const r=await fetch(`${API}/api/git/commit`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:commitMsg,add_all:true})});
      const d=await r.json();
      if(d.ok){setCommitMsg("");fetchGit("status");}else alert("Git error: "+d.error);
    }catch(e){alert(String(e));}finally{setGitLoading(false);}
  }
  useEffect(()=>{if(mainView==="git"&&!gitData.status)fetchGit("status");},[mainView]);

  useEffect(()=>{
    if(mainView!=="history"||runHistory!==null)return;
    fetch(`${API}/api/tools/run-history?limit=50`).then(r=>r.json()).then(d=>setRunHistory(d.runs||[])).catch(()=>setRunHistory([]));
  },[mainView]);

  useEffect(()=>{
    if(mainView!=="filetree"||fileTree!==null)return;
    setFtLoading(true);
    fetch(`${API}/api/advanced/project/tree?max_depth=3&max_items=300`).then(r=>r.json()).then(d=>{setFileTree(d.items||[]);setFtLoading(false);}).catch(()=>{setFileTree([]);setFtLoading(false);});
  },[mainView]);

  async function openFtFile(item){
    if(item.type!=="file")return;
    setFtSelected(item.path);setFtContent(null);
    try{
      const r=await fetch(`${API}/api/advanced/project/read`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:item.path,max_chars:20000})});
      const d=await r.json();setFtContent(d.ok?d.content:"Ошибка: "+d.error);
    }catch(e){setFtContent("Ошибка: "+String(e));}
  }

  const iconFor=a=>a.type==="code"?"◇":/\.pdf$/i.test(a.name||"")?"📑":/\.(js|jsx|ts|tsx|py|rs|go|java|c|cpp)$/i.test(a.name||"")?"◈":"📄";
  const gitColor=s=>({M:"#e2b93d",A:"#4ade80",D:"#ff6b6b","?":"#888"}[s?.[0]]||"#aaa");

  return(
    <div className="ide-shell" style={{display:"flex",flexDirection:"column",height:"100%",padding:0}}>

      {/* Toolbar */}
      <div style={{display:"flex",alignItems:"center",gap:5,padding:"7px 12px",borderBottom:"1px solid var(--border)",flexWrap:"wrap"}}>
        <button onClick={onBackToChat} className="soft-btn" style={{border:"1px solid var(--border)"}}>← Chat</button>
        <span style={{fontSize:13,fontWeight:600}}>Code</span>
        <div style={{display:"flex",gap:2,marginLeft:6}}>
          {[["artifacts","Артефакты"],["git","🔀 Git"],["filetree","📁 Файлы"],["history","📋 История"]].map(([k,l])=>(
            <button key={k} className={`soft-btn ${mainView===k?"active":""}`} onClick={()=>setMainView(k)} style={{fontSize:11,padding:"3px 9px"}}>{l}</button>
          ))}
        </div>
        {mainView==="artifacts"&&(
          <div style={{display:"flex",gap:2,marginLeft:6}}>
            {[["all","Всё"],["code","Код "+codeBlocks.length],["files","Файлы "+fileArtifacts.length]].map(([k,l])=>(
              <button key={k} className={`soft-btn ${filterTab===k?"active":""}`} onClick={()=>setFilterTab(k)} style={{fontSize:11,padding:"3px 9px"}}>{l}</button>
            ))}
          </div>
        )}
        <div style={{marginLeft:"auto"}}>
          <button onClick={()=>setShowTerminal(p=>!p)} className="soft-btn" style={{border:"1px solid var(--border)",fontSize:11,padding:"3px 9px",background:showTerminal?"var(--bg-surface-active)":"transparent"}}>
            {showTerminal?"▼":"▶"} Terminal
          </button>
        </div>
      </div>

      {/* ARTIFACTS */}
      {mainView==="artifacts"&&(
        <div style={{flex:1,display:"grid",gridTemplateColumns:"256px 1fr",minHeight:0}}>
          <div style={{borderRight:"1px solid var(--border)",display:"flex",flexDirection:"column",minHeight:0}}>
            <div style={{padding:"6px 8px",borderBottom:"1px solid var(--border)"}}>
              <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="🔍 Поиск по имени / коду..."
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
                  <span style={{fontSize:13,opacity:0.6}}>{iconFor(a)}</span>
                  <div style={{flex:1,minWidth:0}}>
                    <div style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontWeight:selectedId===a.id?500:400}}>{a.name}</div>
                    <div style={{fontSize:10,color:"var(--text-muted)",marginTop:1}}>{a.source==="chat"?"из чата":a.type==="file"?`${Math.round(a.size/1024)||0}K`:""}{a.lang?` · ${a.lang}`:""}</div>
                  </div>
                  {a.source==="library"&&<button onClick={e=>{e.stopPropagation();removeFile(a.id);}} style={{border:"none",background:"transparent",color:"var(--text-muted)",cursor:"pointer",fontSize:11,padding:0}}>✕</button>}
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
                    <span style={{fontSize:10,color:"var(--text-muted)",flexShrink:0}}>{selected.lang} · {selected.content?.length||0} ch</span>
                    {saveStatus==="ok"&&<span style={{fontSize:10,color:"#4ade80"}}>✓ Сохранено</span>}
                    {saveStatus==="error"&&<span style={{fontSize:10,color:"#ff6b6b"}}>✕ Ошибка</span>}
                  </div>
                  <div style={{display:"flex",gap:3,flexWrap:"wrap",flexShrink:0}}>
                    {onSendToChat&&<>
                      <button onClick={()=>askJarvis("Объясни этот код:")} style={SB()}>💬 Объясни</button>
                      <button onClick={()=>askJarvis("Найди и исправь баги в этом коде:")} style={SB()}>🐛 Баги</button>
                      <button onClick={()=>askJarvis("Напиши тесты для этого кода:")} style={SB()}>🧪 Тесты</button>
                    </>}
                    {!editing?(
                      <>
                        <button onClick={startEdit} style={SB()}>✏️ Изменить</button>
                        <button onClick={handleCopy} style={SB({borderColor:"var(--border)"})}>{copied?"✓ Скопировано":"⧉ Копировать"}</button>
                      </>
                    ):(
                      <>
                        <button onClick={applyEdit} disabled={saveStatus==="saving"} style={{...SBG,opacity:saveStatus==="saving"?0.5:1}}>{saveStatus==="saving"?"⏳":"💾"} Применить</button>
                        <button onClick={cancelEdit} style={SB()}>✕ Отмена</button>
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
                  <div style={{fontSize:32,opacity:0.12,marginBottom:8}}>◇</div>
                  <div>Выбери артефакт слева</div>
                  <div style={{fontSize:11,marginTop:4,opacity:0.6}}>Код из ответов Jarvis появляется автоматически</div>
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
            {[["status","📊 Статус"],["log","📋 Log"],["diff","📄 Diff"]].map(([k,l])=>(
              <button key={k} className={`soft-btn ${gitTab===k?"active":""}`} onClick={()=>fetchGit(k)} style={{fontSize:11,padding:"4px 12px",marginBottom:-1}}>{l}</button>
            ))}
            {gitLoading&&<span style={{fontSize:11,color:"var(--text-muted)",alignSelf:"center",marginLeft:8}}>⏳</span>}
          </div>
          <div style={{flex:1,padding:16,overflow:"auto"}}>
            {gitTab==="status"&&(()=>{const d=gitData.status;if(!d)return null;
              if(!d.ok)return<div style={{color:"#ff6b6b",fontSize:12}}>{d.error}</div>;
              return(
                <div>
                  <div style={{fontSize:12,marginBottom:12}}>
                    <span style={{color:"var(--text-muted)"}}>Ветка: </span><strong style={{color:"var(--accent)"}}>{d.branch}</strong>
                    <span style={{color:"var(--text-muted)",marginLeft:16,fontSize:11}}>{d.repo}</span>
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
                        {gitLoading?"⏳":"✓"} Commit
                      </button>
                    </div>
                    <div style={{fontSize:10,color:"var(--text-muted)",marginTop:5}}>git add -A && git commit</div>
                  </div>
                </div>
              );
            })()}
            {gitTab==="log"&&(()=>{const d=gitData.log;if(!d)return null;
              if(!d.ok)return<div style={{color:"#ff6b6b",fontSize:12}}>{d.error}</div>;
              return(
                <div>
                  <div style={{fontSize:11,color:"var(--text-muted)",marginBottom:10}}>{(d.commits||[]).length} коммитов — {d.repo}</div>
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
              if(!d.ok)return<div style={{color:"#ff6b6b",fontSize:12}}>{d.error}</div>;
              return(
                <div>
                  {d.stat&&<div style={{fontSize:12,color:"var(--text-muted)",marginBottom:10,whiteSpace:"pre-wrap",fontFamily:"var(--font-mono)"}}>{d.stat}</div>}
                  <pre style={{margin:0,fontFamily:"var(--font-mono)",fontSize:11,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",color:"var(--text-primary)",background:"rgba(0,0,0,0.15)",padding:12,borderRadius:8,overflow:"auto",maxHeight:500}}>
                    {d.diff||"Нет изменений"}
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
            {ftLoading&&<div style={{padding:16,fontSize:11,color:"var(--text-muted)"}}>⏳ Загрузка дерева...</div>}
            {!ftLoading&&fileTree&&fileTree.length===0&&(
              <div style={{padding:16,fontSize:11,color:"var(--text-muted)",lineHeight:1.6}}>
                Проект не открыт.<br/>Напиши Jarvis:<br/><code style={{fontSize:10}}>открой проект /путь</code>
              </div>
            )}
            {(fileTree||[]).map((item,i)=>(
              <button key={i} onClick={()=>openFtFile(item)} style={{display:"flex",alignItems:"center",gap:5,width:"100%",padding:`4px ${6+(item.path.split("/").length-1)*10}px`,border:"none",background:ftSelected===item.path?"var(--bg-surface-active)":"transparent",color:ftSelected===item.path?"var(--text-primary)":"var(--text-secondary)",cursor:item.type==="file"?"pointer":"default",textAlign:"left",fontSize:11,borderRadius:4}}>
                <span style={{fontSize:11,opacity:0.4,flexShrink:0}}>{item.type==="dir"?"▸":"·"}</span>
                <span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",flex:1}}>{item.name}</span>
                {item.type==="file"&&<span style={{fontSize:10,color:"var(--text-muted)",flexShrink:0}}>{item.ext}</span>}
              </button>
            ))}
          </div>
          <div style={{display:"flex",flexDirection:"column",minHeight:0}}>
            {ftSelected&&ftContent!==null
              ?<><div style={{padding:"7px 14px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-muted)",fontFamily:"var(--font-mono)"}}>{ftSelected}</div><CodeView code={ftContent} lang={ftSelected.split(".").pop()||""}/></>
              :ftSelected
                ?<div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-muted)",fontSize:12}}>⏳ Загрузка...</div>
                :<div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-muted)",fontSize:12}}><div style={{textAlign:"center"}}><div style={{fontSize:28,opacity:0.1,marginBottom:8}}>📁</div><div>Выбери файл слева</div></div></div>
            }
          </div>
        </div>
      )}

      {/* HISTORY */}
      {mainView==="history"&&(
        <div style={{flex:1,overflow:"auto",padding:16}}>
          {runHistory===null&&<div style={{fontSize:12,color:"var(--text-muted)"}}>⏳ Загрузка...</div>}
          {runHistory!==null&&runHistory.length===0&&<div style={{fontSize:12,color:"var(--text-muted)"}}>История пуста — записи появятся после первых запросов к Jarvis.</div>}
          {(runHistory||[]).map((r,i)=>(
            <div key={i} style={{padding:"10px 14px",marginBottom:6,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-surface)"}}>
              <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
                <code style={{fontSize:10,fontFamily:"var(--font-mono)",color:"var(--accent)"}}>{r.run_id}</code>
                <span style={{fontSize:10,padding:"1px 7px",borderRadius:20,background:r.ok?"rgba(74,222,128,0.15)":"rgba(255,107,107,0.15)",color:r.ok?"#4ade80":"#ff6b6b"}}>{r.ok?"✓ OK":"✕ Error"}</span>
                {r.route&&<span style={{fontSize:10,color:"var(--text-muted)"}}>route: {r.route}</span>}
                {r.model&&<span style={{fontSize:10,color:"var(--text-muted)"}}>model: {r.model}</span>}
                {r.answer_len>0&&<span style={{fontSize:10,color:"var(--text-muted)"}}>{r.answer_len} chars</span>}
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
