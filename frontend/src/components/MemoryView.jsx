import { useEffect, useMemo, useState } from 'react'

export default function MemoryView({
  profiles,
  selectedProfile,
  onRefreshProfileItems,
  items,
  onAdd,
  onSearch,
  searchResults,
  onDeleteItem,
}) {
  const [text, setText] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')

  useEffect(() => {
    if (selectedProfile) {
      onRefreshProfileItems(selectedProfile)
    }
  }, [selectedProfile])

  async function handleAdd() {
    const value = text.trim()
    if (!value || !selectedProfile) return
    setStatus('Сохраняю...')
    try {
      await onAdd({ profile: selectedProfile, text: value, source: 'manual' })
      setText('')
      setStatus('Сохранено')
    } catch (e) {
      setStatus(`Ошибка: ${String(e.message || e)}`)
    }
  }

  async function handleSearch() {
    if (!query.trim() || !selectedProfile) return
    setStatus('Ищу...')
    try {
      await onSearch({ profile: selectedProfile, query, limit: 10 })
      setStatus('Поиск выполнен')
    } catch (e) {
      setStatus(`Ошибка: ${String(e.message || e)}`)
    }
  }

  const profileOptions = useMemo(() => profiles.map((p) => p.name), [profiles])

  return (
    <section className="view-shell">
      <div className="chat-header">
        <div>
          <div className="panel-title">Memory</div>
          <div className="panel-subtitle">Память по профилям, поиск и ручное добавление знаний</div>
        </div>
      </div>

      <div className="view-content memory-grid">
        <div className="memory-column">
          <div className="form-card">
            <div className="section-title">Текущий профиль</div>
            <div className="memory-pill">{selectedProfile || '—'}</div>
            <div className="memory-small">Доступные профили: {profileOptions.join(', ') || 'нет'}</div>
          </div>

          <div className="form-card">
            <div className="section-title">Добавить память</div>
            <textarea
              className="composer-input"
              rows={6}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Например: Пользователь предпочитает ответы на русском и хочет desktop-first UX."
            />
            <div className="toolbar-row">
              <button className="send-button narrow" onClick={handleAdd}>Добавить</button>
              <div className="muted-text">{status}</div>
            </div>
          </div>

          <div className="form-card">
            <div className="section-title">Поиск по памяти</div>
            <input
              className="text-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Введите запрос для поиска..."
            />
            <div className="toolbar-row">
              <button className="ghost-button compact" onClick={handleSearch}>Искать</button>
            </div>
            <div className="table-list">
              {searchResults.length === 0 ? (
                <div className="memory-small">Результатов пока нет.</div>
              ) : (
                searchResults.map((item) => (
                  <div key={item.id} className="file-row">
                    <div className="file-main">
                      <div className="file-name">{item.text}</div>
                      <div className="file-meta">{item.source} • {item.created_at}</div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        <div className="memory-column">
          <div className="form-card">
            <div className="section-title">Записи памяти</div>
            <div className="table-list">
              {items.length === 0 ? (
                <div className="memory-small">Для этого профиля записей пока нет.</div>
              ) : (
                items.map((item) => (
                  <div key={item.id} className="file-row">
                    <div className="file-main">
                      <div className="file-name">{item.text}</div>
                      <div className="file-meta">{item.source} • {item.created_at}</div>
                    </div>
                    <div className="file-actions">
                      <button className="tag-button danger" onClick={() => onDeleteItem(selectedProfile, item.id)}>Удалить</button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
