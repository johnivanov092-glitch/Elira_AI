export default function Sidebar({ activeView, setActiveView, models, profiles, selectedModel, selectedProfile, onModelChange, onProfileChange }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-dot" />
        <div>
          <div className="brand-title">Jarvis</div>
          <div className="brand-subtitle">React + Vite workspace</div>
        </div>
      </div>

      <nav className="nav-block">
        <div className="section-title">Навигация</div>
        <button className={`nav-item ${activeView === 'chat' ? 'active' : ''}`} onClick={() => setActiveView('chat')}>Chat</button>
        <button className={`nav-item ${activeView === 'agents' ? 'active' : ''}`} onClick={() => setActiveView('agents')}>Agents</button>
        <button className={`nav-item ${activeView === 'memory' ? 'active' : ''}`} onClick={() => setActiveView('memory')}>Memory</button>
        <button className={`nav-item ${activeView === 'library' ? 'active' : ''}`} onClick={() => setActiveView('library')}>Library</button>
        <button className={`nav-item ${activeView === 'settings' ? 'active' : ''}`} onClick={() => setActiveView('settings')}>Settings</button>
      </nav>

      <div className="control-block">
        <div className="section-title">Модель</div>
        <select value={selectedModel} onChange={(e) => onModelChange(e.target.value)} className="select">
          {models.map((m) => (
            <option key={m.name} value={m.name}>{m.name}</option>
          ))}
        </select>
      </div>

      <div className="control-block">
        <div className="section-title">Профиль</div>
        <select value={selectedProfile} onChange={(e) => onProfileChange(e.target.value)} className="select">
          {profiles.map((p) => (
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
        </select>
      </div>
    </aside>
  )
}
