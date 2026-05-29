import { useState } from 'react'
import { useApiKeys } from '../contexts/ApiKeyContext'
import Field from './ui/Field'

export default function SettingsModal({ onClose }) {
  const { keys, setAllKeys, clearAllKeys } = useApiKeys()
  const [tab,   setTab]   = useState('CLOUD')
  const [draft, setDraft] = useState(keys)
  const [flash, setFlash] = useState(false)

  const set = (k, v) => setDraft((d) => ({ ...d, [k]: v }))

  const save = () => {
    setAllKeys(draft)
    setFlash(true)
    setTimeout(() => { setFlash(false); onClose() }, 1200)
  }

  const handleClear = () => {
    clearAllKeys()
    onClose()
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100,
      background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(8px)',
      WebkitBackdropFilter: 'blur(8px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 460, maxHeight: '90vh', display: 'flex', flexDirection: 'column',
        background: 'var(--bg-panel)', border: '1px solid var(--border-bright)',
        borderRadius: 12, overflow: 'hidden',
      }}>
        <div className="aca-panel-hd" style={{ padding: '14px 20px' }}>
          <span className="aca-panel-title">Settings</span>
          <button className="aca-btn-ghost small" onClick={onClose}>×</button>
        </div>
        <div style={{ padding: '0 20px' }}>
          <div className="aca-tab-bar">
            <button className="aca-tab" data-active={tab === 'CLOUD'} onClick={() => setTab('CLOUD')}>
              Cloud credentials
            </button>
            <button className="aca-tab" data-active={tab === 'LLM'} onClick={() => setTab('LLM')}>
              LLM keys
            </button>
          </div>
        </div>
        <div style={{ padding: 20, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 14 }}>
          {tab === 'CLOUD' ? (
            <>
              <div style={{
                background: 'var(--bg-elevated)', padding: '8px 12px',
                borderRadius: 6, color: 'var(--text-secondary)', fontSize: 12,
              }}>
                Blank = uses ~/.aws/credentials
              </div>
              <Field label="AWS Access Key ID" saved={!!keys.aws_access_key}>
                <input className="aca-input" type="password"
                  value={draft.aws_access_key || ''}
                  onChange={(e) => set('aws_access_key', e.target.value)} />
              </Field>
              <Field label="AWS Secret Access Key" saved={!!keys.aws_secret_key}>
                <input className="aca-input" type="password"
                  value={draft.aws_secret_key || ''}
                  onChange={(e) => set('aws_secret_key', e.target.value)} />
              </Field>
              <Field label="AWS Region">
                <select className="aca-select"
                  value={draft.region || 'us-east-1'}
                  onChange={(e) => set('region', e.target.value)}>
                  <option>us-east-1</option>
                  <option>eu-west-1</option>
                  <option>ap-southeast-1</option>
                  <option>ap-southeast-2</option>
                </select>
              </Field>
            </>
          ) : (
            <>
              <div style={{
                background: 'var(--bg-elevated)', padding: '8px 12px',
                borderRadius: 6, color: 'var(--text-secondary)', fontSize: 12,
              }}>
                Keys stored in browser only.
              </div>
              <Field label="Default model">
                <select className="aca-select"
                  value={draft.model || 'groq'}
                  onChange={(e) => set('model', e.target.value)}>
                  <option value="groq">Groq</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="ollama">Ollama</option>
                </select>
              </Field>
              <Field label="Groq API key" saved={!!keys.groq_key} hint="Used for fast inference and chat">
                <input className="aca-input" type="password"
                  value={draft.groq_key || ''}
                  onChange={(e) => set('groq_key', e.target.value)} />
              </Field>
              <Field label="Anthropic API key" saved={!!keys.anthropic_key} hint="Used for agent reasoning">
                <input className="aca-input" type="password"
                  value={draft.anthropic_key || ''}
                  onChange={(e) => set('anthropic_key', e.target.value)} />
              </Field>
              <Field label="Ollama URL" hint="Local inference endpoint">
                <input className="aca-input"
                  placeholder="http://localhost:11434"
                  value={draft.ollama_url || ''}
                  onChange={(e) => set('ollama_url', e.target.value)} />
              </Field>
            </>
          )}
        </div>
        <div style={{
          padding: '14px 20px', borderTop: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', gap: 8,
        }}>
          <button className="aca-btn-ghost small danger" onClick={handleClear}>Clear all</button>
          <div className="aca-row" style={{ gap: 8 }}>
            <button className="aca-btn-ghost small" onClick={onClose}>Cancel</button>
            <button className="aca-btn-primary" onClick={save}>
              {flash ? '✓ Saved' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
