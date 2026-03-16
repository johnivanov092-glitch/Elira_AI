import { useEffect, useMemo, useState } from 'react'
import Sidebar from './components/Sidebar.jsx'
import Chat from './components/Chat.jsx'
import AgentPanel from './components/AgentPanel.jsx'
import SettingsView from './components/SettingsView.jsx'
import LibraryView from './components/LibraryView.jsx'
import MemoryView from './components/MemoryView.jsx'
import AgentsView from './components/AgentsView.jsx'
import {
  getModels,
  getProfiles,
  getSettings,
  saveSettings,
  getLibraryFiles,
  setLibraryActive,
  deleteLibraryFile,
  getMemoryProfiles,
  getMemoryItems,
  addMemory,
  searchMemory,
  deleteMemory,
  sendChat,
  runAgent,
} from './api/api.js'

function safeArray(value) {
  return Array.isArray(value) ? value : []
}

export default function App() {
  const [activeView, setActiveView] = useState('chat')
  const [models, setModels] = useState([])
  const [profiles, setProfiles] = useState([])
  const [settings, setSettings] = useState(null)
  const [selectedModel, setSelectedModel] = useState('')
  const [selectedProfile, setSelectedProfile] = useState('')
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [agentOpen, setAgentOpen] = useState(true)
  const [lastMeta, setLastMeta] = useState({})
  const [error, setError] = useState('')
  const [libraryFiles, setLibraryFiles] = useState([])
  const [memoryProfiles, setMemoryProfiles] = useState([])
  const [memoryItems, setMemoryItems] = useState([])
  const [memorySearchResults, setMemorySearchResults] = useState([])
  const [agentTimeline, setAgentTimeline] = useState([])
  const [agentAnswer, setAgentAnswer] = useState('')

  async function refreshLibrary() {
    const result = await getLibraryFiles()
    setLibraryFiles(safeArray(result?.files))
  }

  async function refreshMemoryProfiles() {
    const result = await getMemoryProfiles()
    setMemoryProfiles(safeArray(result?.profiles))
  }

  async function refreshMemoryItems(profile) {
    if (!profile) return
    const result = await getMemoryItems(profile)
    setMemoryItems(safeArray(result?.items))
  }

  useEffect(() => {
    async function boot() {
      try {
        setError('')
        const [modelsRes, profilesRes, settingsRes, libraryRes, memoryProfilesRes] = await Promise.all([
          getModels(),
          getProfiles(),
          getSettings(),
          getLibraryFiles(),
          getMemoryProfiles(),
        ])

        const nextModels = safeArray(modelsRes?.models)
        const nextProfiles = safeArray(profilesRes?.profiles)
        const nextLibrary = safeArray(libraryRes?.files)
        const nextMemoryProfiles = safeArray(memoryProfilesRes?.profiles)

        setModels(nextModels)
        setProfiles(nextProfiles)
        setSettings(settingsRes || {})
        setLibraryFiles(nextLibrary)
        setMemoryProfiles(nextMemoryProfiles)

        const defaultModel =
          settingsRes?.defaults?.model_name ||
          nextModels?.[0]?.name ||
          ''

        const defaultProfile =
          settingsRes?.defaults?.profile_name ||
          profilesRes?.default_profile ||
          nextProfiles?.[0]?.name ||
          ''

        setSelectedModel(defaultModel)
        setSelectedProfile(defaultProfile)

        if (defaultProfile) {
          const memoryRes = await getMemoryItems(defaultProfile)
          setMemoryItems(safeArray(memoryRes?.items))
        }
      } catch (e) {
        setError(String(e?.message || e))
      }
    }

    boot()
  }, [])

  useEffect(() => {
    if (activeView === 'memory' && selectedProfile) {
      refreshMemoryItems(selectedProfile)
    }
  }, [activeView, selectedProfile])

  async function handleSend(userInput) {
    const nextHistory = messages.map((m) => ({ role: m.role, content: m.content }))
    const userMessage = { role: 'user', content: userInput }

    setMessages((prev) => [...prev, userMessage])
    setBusy(true)
    setAgentOpen(true)
    setError('')

    try {
      const result = await sendChat({
        model_name: selectedModel,
        profile_name: selectedProfile,
        user_input: userInput,
        history: nextHistory,
        use_memory: true,
        use_library: true,
      })

      if (!result?.ok) {
        throw new Error(
          safeArray(result?.warnings).join('\n') ||
          result?.error ||
          'Chat request failed'
        )
      }

      const answer = result?.answer || 'Пустой ответ.'
      setMessages((prev) => [...prev, { role: 'assistant', content: answer }])
      setLastMeta(result?.meta || {})
      setAgentTimeline(safeArray(result?.timeline))
      setAgentAnswer(answer)
    } catch (e) {
      const text = `Ошибка: ${String(e?.message || e)}`
      setMessages((prev) => [...prev, { role: 'assistant', content: text }])
      setLastMeta({ error: String(e?.message || e) })
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function handleRunAgent(userInput) {
    setBusy(true)
    setAgentOpen(true)
    setError('')

    try {
      const result = await runAgent({
        model_name: selectedModel,
        profile_name: selectedProfile,
        user_input: userInput,
        use_memory: true,
        use_library: true,
      })

      if (!result?.ok) {
        throw new Error(
          safeArray(result?.meta?.warnings).join('\n') ||
          result?.error ||
          'Agent failed'
        )
      }

      setAgentTimeline(safeArray(result?.timeline))
      setAgentAnswer(result?.answer || '')
      setLastMeta(result?.meta || {})
    } catch (e) {
      setLastMeta({ error: String(e?.message || e) })
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function handleSaveSettings(payload) {
    const result = await saveSettings(payload)
    setSettings(result || {})
    setSelectedModel(result?.defaults?.model_name || '')
    setSelectedProfile(result?.defaults?.profile_name || '')
  }

  async function handleToggleLibrary(filename, active) {
    await setLibraryActive(filename, active)
    await refreshLibrary()
  }

  async function handleDeleteLibrary(filename) {
    await deleteLibraryFile(filename)
    await refreshLibrary()
  }

  async function handleAddMemory(payload) {
    await addMemory(payload)
    await refreshMemoryProfiles()
    await refreshMemoryItems(payload.profile)
  }

  async function handleSearchMemory(payload) {
    const result = await searchMemory(payload)
    setMemorySearchResults(safeArray(result?.items))
  }

  async function handleDeleteMemory(profile, itemId) {
    await deleteMemory(profile, itemId)
    await refreshMemoryProfiles()
    await refreshMemoryItems(profile)
  }

  const statusText = useMemo(() => {
    if (error) return error
    if (!settings) return 'Загрузка backend...'
    return 'Backend подключён'
  }, [error, settings])

  function renderMainView() {
    switch (activeView) {
      case 'settings':
        return (
          <SettingsView
            models={models}
            profiles={profiles}
            selectedModel={selectedModel}
            selectedProfile={selectedProfile}
            onSave={handleSaveSettings}
          />
        )
      case 'library':
        return (
          <LibraryView
            files={libraryFiles}
            onToggle={handleToggleLibrary}
            onDelete={handleDeleteLibrary}
            refresh={refreshLibrary}
          />
        )
      case 'memory':
        return (
          <MemoryView
            profiles={memoryProfiles}
            selectedProfile={selectedProfile}
            onRefreshProfileItems={refreshMemoryItems}
            items={memoryItems}
            onAdd={handleAddMemory}
            onSearch={handleSearchMemory}
            searchResults={memorySearchResults}
            onDeleteItem={handleDeleteMemory}
          />
        )
      case 'agents':
        return (
          <AgentsView
            selectedModel={selectedModel}
            selectedProfile={selectedProfile}
            onRunAgent={handleRunAgent}
            timeline={agentTimeline}
            agentAnswer={agentAnswer}
            agentMeta={lastMeta}
          />
        )
      case 'chat':
      default:
        return <Chat messages={messages} onSend={handleSend} busy={busy} />
    }
  }

  return (
    <div className="app-shell">
      <Sidebar
        activeView={activeView}
        setActiveView={setActiveView}
        models={models}
        profiles={profiles}
        selectedModel={selectedModel}
        selectedProfile={selectedProfile}
        onModelChange={setSelectedModel}
        onProfileChange={setSelectedProfile}
      />

      <main className={`workspace ${agentOpen && activeView === 'chat' ? 'with-panel' : ''}`}>
        <div className="topbar">
          <div>
            <div className="topbar-title">Jarvis Workspace</div>
            <div className="topbar-subtitle">{statusText}</div>
          </div>

          {activeView === 'chat' ? (
            <button className="ghost-button" onClick={() => setAgentOpen((v) => !v)}>
              {agentOpen ? 'Скрыть панель' : 'Открыть панель'}
            </button>
          ) : (
            <div className="muted-text">Раздел: {activeView}</div>
          )}
        </div>

        <div className="content-grid">
          {renderMainView()}

          {activeView === 'chat' ? (
            <AgentPanel
              open={agentOpen}
              selectedModel={selectedModel}
              selectedProfile={selectedProfile}
              lastMeta={lastMeta}
              timeline={agentTimeline}
            />
          ) : null}
        </div>
      </main>
    </div>
  )
}
