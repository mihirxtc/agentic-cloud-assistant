export default function Field({ label, saved, hint, children }) {
  return (
    <div>
      <div className="aca-row" style={{ justifyContent: 'space-between', marginBottom: 5 }}>
        <label style={{
          fontSize: 11, color: 'var(--text-secondary)',
          letterSpacing: '0.06em', textTransform: 'uppercase',
        }}>{label}</label>
        {saved && <span style={{ color: 'var(--success)', fontSize: 10 }}>✓ saved</span>}
      </div>
      {children}
      {hint && <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>{hint}</div>}
    </div>
  )
}
