import { useEffect, useMemo, useState } from "react";
import { api } from "../api/ide";
import AppHeader from "./AppHeader";
import ChatInputHotfix from "./ChatInputHotfix";
import SettingsPanelHotfix from "./SettingsPanelHotfix";

export default function ChatPageHotfix() {
  const [llmOptions, setLlmOptions] = useState([]);
  const [llm, setLlm] = useState("qwen3:8b");
  const [profile, setProfile] = useState("Сбалансированный");
  const [activeTab, setActiveTab] = useState("chat");
  const [chats, setChats] = useState([]);
  const [text, setText] = useState("");
  const [errorText, setErrorText] = useState("");

  useEffect(() => {
    loadModels();
    loadChats();
  }, []);

  async function loadModels() {
    try {
      const items = await api.listOllamaModels();
      setLlmOptions(items || []);
      if (items?.length) setLlm(items[0].name || items[0]);
    } catch (e) {
      setErrorText(e.message || "Ошибка загрузки моделей");
    }
  }

  async function loadChats() {
    try {
      const items = await api.listChats();
      setChats(items || []);
    } catch (e) {
      setErrorText(e.message || "Ошибка загрузки чатов");
    }
  }

  async function handleNewChat() {
    try {
      await api.createChat({ title: "Новый чат" });
      await loadChats();
      setErrorText("");
    } catch (e) {
      setErrorText(e.message || "Ошибка создания чата");
    }
  }

  const pinned = useMemo(() => chats.filter((item) => item.pinned), [chats]);
  const allChats = useMemo(() => chats.filter((item) => !item.pinned), [chats]);

  return (
    <div className="chat-page-hotfix" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <AppHeader
        title="Jarvis"
        llm={llm}
        setLlm={setLlm}
        llmOptions={llmOptions}
        profile={profile}
        setProfile={setProfile}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
      />

      <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", minHeight: 0, flex: 1 }}>
        <aside style={{ borderRight: "1px solid rgba(255,255,255,0.08)", padding: 16 }}>
          <button className="soft-btn" onClick={handleNewChat} style={{ width: "100%", marginBottom: 18 }}>
            + Новый чат
          </button>

          <div style={{ display: "grid", gap: 12 }}>
            <div>Поиск</div>
            <div>Чаты</div>
            <div>Память</div>
            <div>Проекты</div>
            <div>Настройки</div>
          </div>

          <div style={{ marginTop: 28, fontSize: 13, opacity: 0.9 }}>Закреплённые</div>
          <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
            {pinned.length ? pinned.map((item) => (
              <div key={item.id} className="soft-btn" style={{ textAlign: "left" }}>{item.title}</div>
            )) : <div style={{ opacity: 0.7 }}>Здесь пока пусто.</div>}
          </div>

          <div style={{ marginTop: 24, fontSize: 13, opacity: 0.9 }}>Все чаты</div>
          <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
            {allChats.length ? allChats.map((item) => (
              <div key={item.id} className="soft-btn" style={{ textAlign: "left" }}>{item.title}</div>
            )) : <div style={{ opacity: 0.7 }}>Здесь пока пусто.</div>}
          </div>
        </aside>

        <main style={{ padding: 18, minWidth: 0 }}>
          {activeTab === "settings" ? (
            <SettingsPanelHotfix />
          ) : (
            <>
              <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 14 }}>Чаты</div>

              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <div className="soft-btn" style={{ textAlign: "left" }}>
                  Jarvis готов. Начни новый чат или напиши сообщение.
                </div>

                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <button className="soft-btn">Сохранить в памяти</button>
                  <button className="soft-btn">Закрепить в памяти</button>
                </div>
              </div>

              {errorText ? (
                <div
                  style={{
                    marginTop: 16,
                    background: "rgba(120,30,30,0.45)",
                    border: "1px solid rgba(255,120,120,0.25)",
                    padding: 14,
                    borderRadius: 14,
                  }}
                >
                  {errorText}
                </div>
              ) : null}

              <div style={{ minHeight: 240 }} />

              <ChatInputHotfix value={text} onChange={setText} onSubmit={() => {}} />
            </>
          )}
        </main>
      </div>
    </div>
  );
}
