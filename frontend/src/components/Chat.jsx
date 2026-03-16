import { useState } from 'react'

export default function Chat({ messages, onSend, busy }) {
  const [text, setText] = useState('')

  async function submit(e) {
    e.preventDefault()
    const value = text.trim()
    if (!value || busy) return
    setText('')
    await onSend(value)
  }

  return (
    <section className="chat-shell">
      <header className="chat-header">
        <div>
          <div className="panel-title">Чат</div>
          <div className="panel-subtitle">Новый UI без Streamlit</div>
        </div>
      </header>

      <div className="messages">
        {messages.length === 0 ? (
          <div className="empty-state">
            <div className="empty-title">Готов к работе</div>
            <div className="empty-subtitle">Напиши сообщение — ответ пойдёт через FastAPI и Ollama.</div>
          </div>
        ) : (
          messages.map((msg, index) => (
            <div key={index} className={`message ${msg.role}`}>
              <div className="message-role">{msg.role === 'user' ? 'Ты' : 'Jarvis'}</div>
              <div className="message-content">{msg.content}</div>
            </div>
          ))
        )}
      </div>

      <form className="composer" onSubmit={submit}>
        <textarea
          className="composer-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Напиши задачу..."
          rows={3}
        />
        <button className="send-button" type="submit" disabled={busy}>
          {busy ? 'Думаю...' : 'Отправить'}
        </button>
      </form>
    </section>
  )
}
