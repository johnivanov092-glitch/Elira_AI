import { useEffect, useState } from 'react'

export default function SettingsView({ models, profiles, selectedModel, selectedProfile, onSave }) {
  const [modelName, setModelName] = useState(selectedModel || '')
  const [profileName, setProfileName] = useState(selectedProfile || '')
  const [status, setStatus] = useState('')

  useEffect(() => setModelName(selectedModel || ''), [selectedModel])
  useEffect(() => setProfileName(selectedProfile || ''), [selectedProfile])

  async function handleSave() {
    setStatus('Сохраняю...')
    try {
      await onSave({ model_name: modelName, profile_name: profileName })
      setStatus('Сохранено')
    } catch (e) {
      setStatus(`Ошибка: ${String(e.message || e)}`)
    }
  }

  return (
    <section className="view-shell">
      <div className="chat-header">
        <div>
          <div className="panel-title">Settings</div>
          <div className="panel-subtitle">Настройки модели и профиля по умолчанию</div>
        </div>
      </div>

      <div className="view-content">
        <div className="form-card">
          <div className="section-title">Модель по умолчанию</div>
          <select className="select" value={modelName} onChange={(e) => setModelName(e.target.value)}>
            {models.map((m) => (
              <option key={m.name} value={m.name}>{m.name}</option>
            ))}
          </select>
        </div>

        <div className="form-card">
          <div className="section-title">Профиль по умолчанию</div>
          <select className="select" value={profileName} onChange={(e) => setProfileName(e.target.value)}>
            {profiles.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
        </div>

        <div className="toolbar-row">
          <button className="send-button narrow" onClick={handleSave}>Сохранить</button>
          <div className="muted-text">{status}</div>
        </div>
      </div>
    </section>
  )
}
