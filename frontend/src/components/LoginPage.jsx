import { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import Logo from './ui/Logo'

export default function LoginPage() {
  const { login }               = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err,      setErr]      = useState(null)

  const submit = (e) => {
    e.preventDefault()
    if (!login(username, password)) setErr('Invalid credentials')
  }

  return (
    <div style={{
      minHeight: '100vh', background: 'var(--bg-root)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <form onSubmit={submit} style={{
        width: 380, background: 'var(--bg-panel)',
        border: '1px solid var(--border)', borderRadius: 12,
        padding: '40px 32px', display: 'flex', flexDirection: 'column', gap: 20,
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
          <Logo size={32} />
          <div style={{ fontSize: 18, color: 'var(--text-primary)', marginTop: 8 }}>
            Agentic Cloud Assistant
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>AWS Infrastructure Management</div>
        </div>

        {err && (
          <div style={{
            background: 'var(--error-dim)', border: '1px solid var(--error)',
            borderRadius: 6, padding: '8px 12px', color: 'var(--error)', fontSize: 12,
          }}>{err}</div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div>
            <label style={{
              display: 'block', fontSize: 11, color: 'var(--text-secondary)',
              letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 5,
            }}>Username</label>
            <input
              className="aca-input" autoFocus
              value={username} onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div>
            <label style={{
              display: 'block', fontSize: 11, color: 'var(--text-secondary)',
              letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 5,
            }}>Password</label>
            <input
              className="aca-input" type="password"
              value={password} onChange={(e) => setPassword(e.target.value)}
            />
          </div>
        </div>

        <button type="submit" className="aca-btn-primary" style={{ padding: '10px 14px', fontSize: 13 }}>
          Sign in
        </button>
        <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: 'center' }}>
          demo: admin / mscProject@DMU
        </div>
      </form>
    </div>
  )
}
