import { useState } from 'react'
import TimelinePanel from './TimelinePanel.jsx'

export default function AgentsView({
  selectedModel,
  selectedProfile,
  onRunAgent,
  timeline,
  agentAnswer,
  agentMeta,
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')

  async function handleRun(e) {
    e.preventDefault()
    const value = text.trim()
    if (!value || busy) return

    setBusy(true)
    setStatus('Запускаю агента...')
    try {
      await onRunAgent(value)
      setStatus('Агент завершил выполнение')
    } catch (e) {
      setStatus(`Ошибка: ${String(e.message || e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="view-shell">
      <div className="chat-header">
        <div>
          <div className="panel-title">Agents</div>
          <div className="panel-subtitle">Planner + timeline + synthesis на базе текущего backend</div>
        </div>
      </div>

      <div className="view-content agents-grid">
        <div className="memory-column">
          <div className="form-card">
            <div className="section-title">Параметры запуска</div>
            <div className="memory-small">Модель: {selectedModel || '—'}</div>
            <div className="memory-small">Профиль: {selectedProfile || '—'}</div>
          </div>

          <form className="form-card" onSubmit={handleRun}>
            <div className="section-title">Задача агенту</div>
            <textarea
              className="composer-input"
              rows={6}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Например: проанализируй текущую архитектуру и предложи следующие 3 шага развития."
            />
            <div className="toolbar-row">
              <button className="send-button narrow" type="submit" disabled={busy}>
                {busy ? 'Запуск...' : 'Запустить агента'}
              </button>
              <div className="muted-text">{status}</div>
            </div>
          </form>

          <div className="form-card">
            <div className="section-title">Итоговый ответ</div>
            <div className="agent-answer-box">{agentAnswer || 'Ответ появится после запуска агента.'}</div>
          </div>
        </div>

        <div className="memory-column">
          <div className="form-card">
            <div className="section-title">Timeline</div>
            <TimelinePanel timeline={timeline} />
          </div>

          <div className="form-card">
            <div className="section-title">Meta</div>
            <pre className="meta-box">{JSON.stringify(agentMeta || {}, null, 2)}</pre>
          </div>
        </div>
      </div>
    </section>
  )
}
