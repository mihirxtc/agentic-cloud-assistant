import { useState } from 'react'
import { useApiKeys } from '../contexts/useApiKeys'
import Field from './ui/Field'

export default function SettingsModal({ onClose }) {
  const { keys, setAllKeys, clearAllKeys, serverConfig, configError, awsIdentity, awsIdentityError } = useApiKeys()
  const [draft, setDraft] = useState(keys)
  const set = (key, value) => setDraft((previous) => ({ ...previous, [key]: value }))

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 100, background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }} onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Settings" onClick={(event) => event.stopPropagation()}
        style={{ width: 460, maxHeight: '90vh', overflowY: 'auto', background: 'var(--bg-panel)',
          border: '1px solid var(--border-bright)', borderRadius: 12 }}>
        <div className="aca-panel-hd">
          <span className="aca-panel-title">Settings</span>
          <button className="aca-btn-ghost small" onClick={onClose} aria-label="Close settings">×</button>
        </div>
        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 13 }}>
            AWS and model credentials are managed on the server. This browser saves only your model and region preferences.
          </p>
          <Field label="AWS region">
            <select className="aca-select" value={draft.region} onChange={(event) => set('region', event.target.value)}>
              {['us-east-1', 'eu-west-1', 'ap-southeast-1', 'ap-southeast-2'].map((region) => <option key={region}>{region}</option>)}
            </select>
          </Field>
          <Field label="Default model provider">
            <select className="aca-select" value={draft.model} onChange={(event) => set('model', event.target.value)}>
              <option value="groq">Groq</option>
              <option value="anthropic">Anthropic</option>
              <option value="ollama">Ollama</option>
            </select>
          </Field>
          <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
            {configError ? <p role="alert">{configError}</p> : !serverConfig ? <p>Loading server status…</p> : (
              <>
                <p>{awsIdentity ? `AWS account ${awsIdentity.account_id} · ${awsIdentity.region}` : awsIdentityError || 'AWS identity has not been verified.'}</p>
                {Object.entries(serverConfig.providers).map(([provider, configured]) => (
                  <p key={provider}>{provider}: {provider === 'ollama' ? 'Server endpoint selected; availability checked when used' : configured ? 'Configured' : 'Not configured'}</p>
                ))}
              </>
            )}
          </div>
        </div>
        <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between' }}>
          <button className="aca-btn-ghost small" onClick={() => { clearAllKeys(); onClose() }}>Reset preferences</button>
          <button className="aca-btn-primary" onClick={() => { setAllKeys(draft); onClose() }}>Save preferences</button>
        </div>
      </div>
    </div>
  )
}
