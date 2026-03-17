
import { useState } from "react";
import { Search, MessageSquare, Folder, Settings, Plus, Code } from "lucide-react";

export default function JarvisLayout(){

const [tab,setTab]=useState("chat")

return (
<div className="app">

<aside className="sidebar">

<button className="newChat"><Plus size={16}/> Новый чат</button>

<div className="nav">
<button><Search size={16}/> Search</button>
<button><MessageSquare size={16}/> Chats</button>
<button><Folder size={16}/> Projects</button>
<button><Settings size={16}/> Settings</button>
</div>

</aside>

<main className="main">

<header className="top">

<div className="agentTitle">
🤖 Jarvis Агент ИИ
</div>

<div className="tabs">
<button onClick={()=>setTab("chat")} className={tab==="chat"?"active":""}>Чат</button>
<button onClick={()=>setTab("code")} className={tab==="code"?"active":""}>
<Code size={16}/> Code
</button>
</div>

</header>

<section className="content">

<div className="chatArea">

<div className="composer">
<input placeholder="Напиши задачу… Jarvis сам выберет режим"/>
</div>

</div>

<div className="codePreview">
Code preview
</div>

</section>

</main>

</div>
)
}
