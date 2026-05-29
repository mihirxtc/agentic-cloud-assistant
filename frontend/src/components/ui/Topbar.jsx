import Logo from './Logo'

export default function Topbar({ securityScore, onOpenSettings, onLogout, region, hasKeys }) {
  const scoreClass =
    securityScore == null ? null :
    securityScore >= 80   ? 'success' :
    securityScore >= 50   ? 'warning' : 'error'

  return (
    <header style={{
      position: 'sticky', top: 0, zIndex: 50, height: 48,
      borderBottom: '1px solid var(--border)',
      background: 'rgba(0,0,0,0.8)', backdropFilter: 'blur(12px)',
      WebkitBackdropFilter: 'blur(12px)',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 24px',
    }}>
      <div className="aca-row" style={{ gap: 12 }}>
        <Logo />
        <span style={{ color: 'var(--text-primary)', fontSize: 13, fontWeight: 500 }}>
          Agentic Cloud Assistant
        </span>
        <span style={{ color: 'var(--text-muted)' }}>/</span>
        <span style={{ color: 'var(--text-secondary)', fontSize: 13 }}>AWS Infrastructure</span>
      </div>
      <div className="aca-row" style={{ gap: 8 }}>
        {securityScore != null && (
          <span className={'aca-badge ' + scoreClass} style={{ padding: '4px 8px', fontSize: 10 }}>
            SEC {securityScore}
          </span>
        )}
        <button className="aca-btn-ghost small" onClick={onOpenSettings}>{region}</button>
        <button className="aca-btn-ghost small" onClick={onOpenSettings}>
          <span className={'aca-dot-indicator ' + (hasKeys ? 'green' : 'red')} /> API KEYS
        </button>
        <button className="aca-btn-ghost small danger" onClick={onLogout}>LOGOUT</button>
      </div>
    </header>
  )
}
