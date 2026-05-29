import { useState, useEffect, useRef } from 'react'
import { callTool } from '../../api/mcpClient'

export default function ChatPanel({ model, apiKey }) {
  const [messages, setMessages] = useState([])
  const [input,    setInput]    = useState('')
  const [loading,  setLoading]  = useState(false)
  const listRef = useRef(null)

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [messages, loading])

  const send = async () => {
    const msg = input.trim()
    if (!msg || loading) return
    const newMsg = { role: 'user', content: msg }
    setMessages((m) => [...m, newMsg])
    setInput('')
    setLoading(true)
    try {
      const history = [...messages, newMsg].map((m) => ({ role: m.role, content: m.content }))
      const r = await callTool('aws_chat', { message: msg, model, api_key: apiKey, history })
      setMessages((m) => [...m, { role: 'assistant', content: r.reply }])
    } catch (e) {
      setMessages((m) => [...m, { role: 'assistant', content: `Error: ${e.message || 'Failed to get a response.'}` }])
    } finally { setLoading(false) }
  }

  const modelBadgeClass = model === 'groq' ? 'blue' : model === 'anthropic' ? 'warning' : 'muted'
  const modelLabel      = model === 'groq' ? 'GROQ' : model === 'anthropic' ? 'ANTHROPIC' : 'OLLAMA'

  return (
    <section className="aca-panel" style={{ gridColumn: 'span 5' }}>
      <div className="aca-panel-hd">
        <div className="aca-row" style={{ gap: 10 }}>
          <span className="aca-panel-title">Chat</span>
          <span className={'aca-badge ' + modelBadgeClass}>{modelLabel}</span>
        </div>
        {messages.length > 0 && (
          <button className="aca-btn-ghost small" onClick={() => setMessages([])}>Clear</button>
        )}
      </div>
      <div ref={listRef} style={{
        height: 380, overflowY: 'auto', padding: 16,
        display: 'flex', flexDirection: 'column', gap: 10,
      }}>
        {messages.length === 0 && !loading && (
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: 'var(--text-muted)', fontStyle: 'italic', textAlign: 'center', padding: '0 24px',
          }}>
            Ask anything about your AWS infrastructure
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={'aca-chat-msg ' + m.role}>{m.content}</div>
        ))}
        {loading && (
          <div className="aca-chat-msg assistant">
            <span className="aca-dots"><span /><span /><span /></span>
          </div>
        )}
      </div>
      <div style={{
        borderTop: '1px solid var(--border)', padding: '12px 16px',
        display: 'flex', gap: 8, alignItems: 'flex-end',
      }}>
        <textarea
          className="aca-textarea" rows={2} value={input}
          placeholder="Ask about EC2, IAM, costs…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
        />
        <button className="aca-btn-primary" onClick={send} disabled={loading || !input.trim()}>
          Send
        </button>
      </div>
    </section>
  )
}
