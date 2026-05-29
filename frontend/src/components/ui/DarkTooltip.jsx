export default function DarkTooltip({ active, payload, label, prefix = '' }) {
  if (!active || !payload || !payload.length) return null
  return (
    <div style={{
      background: 'var(--bg-elevated)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '8px 12px', fontSize: 12,
      fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-primary)',
    }}>
      {label && (
        <div style={{ color: 'var(--text-secondary)', fontSize: 10, textTransform: 'uppercase', marginBottom: 4 }}>
          {label}
        </div>
      )}
      {payload.map((p, i) => (
        <div key={i}>{prefix}{typeof p.value === 'number' ? p.value.toLocaleString() : p.value}</div>
      ))}
    </div>
  )
}
