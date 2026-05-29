export default function Logo({ size = 22 }) {
  return (
    <div style={{
      width: size + 10, height: size, borderRadius: 4,
      background: 'var(--text-primary)', color: '#000',
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: Math.round(size * 0.5), fontWeight: 600, letterSpacing: '0.02em',
    }}>ACA</div>
  )
}
