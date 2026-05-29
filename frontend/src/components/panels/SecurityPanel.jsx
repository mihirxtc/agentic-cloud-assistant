import { useState, useMemo, Fragment } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import { CHART_COLORS } from '../../utils/constants'
import DarkTooltip from '../ui/DarkTooltip'

const DIRECT_FIX_RULES = new Set(['SSH_PORT_OPEN', 'RDP_PORT_OPEN'])

export default function SecurityPanel({ data, loading, onScan, onFix, onDirectFix }) {
  const [open,            setOpen]            = useState(null)
  const [directFixStatus, setDirectFixStatus] = useState({})

  const counts = useMemo(() => {
    if (!data) return null
    const c = { HIGH: 0, MEDIUM: 0, LOW: 0 }
    data.findings.forEach((f) => { c[f.severity] = (c[f.severity] || 0) + 1 })
    return c
  }, [data])

  const handleDirectFix = async (f) => {
    setDirectFixStatus(s => ({ ...s, [f.finding_id]: 'fixing' }))
    try {
      await onDirectFix(f)
      setDirectFixStatus(s => ({ ...s, [f.finding_id]: 'done' }))
    } catch (e) {
      setDirectFixStatus(s => ({ ...s, [f.finding_id]: 'error:' + e.message }))
    }
  }

  const pieData = counts ? [
    { name: 'HIGH',   value: counts.HIGH,   color: CHART_COLORS.error },
    { name: 'MEDIUM', value: counts.MEDIUM, color: CHART_COLORS.warning },
    { name: 'LOW',    value: counts.LOW,    color: CHART_COLORS.muted },
  ] : []

  const sorted = data
    ? [...data.findings].sort((a, b) =>
        ['HIGH','MEDIUM','LOW'].indexOf(a.severity) - ['HIGH','MEDIUM','LOW'].indexOf(b.severity))
    : []

  return (
    <section className="aca-panel" style={{ gridColumn: 'span 7' }}>
      <div className="aca-panel-hd">
        <span className="aca-panel-title">Security</span>
        <button className="aca-btn-primary" onClick={onScan} disabled={loading}>
          {loading ? 'Running…' : data ? 'Rescan' : 'Run Scan'}
        </button>
      </div>
      <div className="aca-panel-body">
        {!data && !loading && <div className="aca-empty">Run a scan to surface security findings</div>}
        {loading && <div className="aca-skel" style={{ height: 380 }} />}
        {data && !loading && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 160px', gap: 10, alignItems: 'stretch' }}>
              {[['HIGH','high',counts.HIGH],['MEDIUM','medium',counts.MEDIUM],['LOW','low',counts.LOW]].map(([label, cls, n]) => (
                <div key={label} className="aca-tile" style={{ padding: '12px 14px' }}>
                  <div className="aca-tile-value mono" style={{
                    fontSize: 24,
                    color: cls === 'high' ? 'var(--error)' : cls === 'medium' ? 'var(--warning)' : 'var(--text-secondary)',
                  }}>{n}</div>
                  <div className="aca-tile-label">{label}</div>
                </div>
              ))}
              <div className="aca-tile" style={{ padding: 6, position: 'relative' }}>
                <ResponsiveContainer width="100%" height={120}>
                  <PieChart>
                    <Pie data={pieData} innerRadius={30} outerRadius={52} paddingAngle={2} dataKey="value" stroke="none">
                      {pieData.map((d, i) => <Cell key={i} fill={d.color} />)}
                    </Pie>
                    <Tooltip content={<DarkTooltip />} />
                  </PieChart>
                </ResponsiveContainer>
                <div style={{
                  position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
                  alignItems: 'center', justifyContent: 'center', pointerEvents: 'none',
                }}>
                  <div className="mono" style={{ fontSize: 20, color: 'var(--text-primary)' }}>{data.findings.length}</div>
                  <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
                    findings
                  </div>
                </div>
              </div>
            </div>

            <div className="aca-summary">{data.llm_summary}</div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {sorted.map((f) => {
                const isOpen = open === f.finding_id
                return (
                  <div key={f.finding_id} className="aca-finding" data-open={isOpen}>
                    <div className="aca-row" style={{ justifyContent: 'space-between', cursor: 'pointer' }}
                      onClick={() => setOpen(isOpen ? null : f.finding_id)}>
                      <div className="aca-row" style={{ gap: 10, minWidth: 0, flex: 1 }}>
                        <span className={'aca-badge ' + f.severity.toLowerCase()}>{f.severity}</span>
                        <span style={{ color: 'var(--text-primary)', fontSize: 13 }}>{f.title}</span>
                        <span className="mono" style={{
                          color: 'var(--text-muted)', fontSize: 11,
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>{f.resource_id}</span>
                      </div>
                      <span style={{
                        color: 'var(--text-muted)', fontSize: 12,
                        transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s',
                      }}>▸</span>
                    </div>
                    {isOpen && (
                      <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
                        <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.55 }}>
                          {f.description}
                        </p>
                        <p style={{ margin: 0, color: 'var(--text-primary)', fontSize: 12, lineHeight: 1.55 }}>
                          <span style={{
                            color: 'var(--text-muted)', textTransform: 'uppercase',
                            fontSize: 10, letterSpacing: '0.06em', marginRight: 8,
                          }}>Fix</span>
                          {f.recommendation}
                        </p>
                        <dl className="aca-kv">
                          <dt>Resource</dt><dd>{f.resource_type}</dd>
                          <dt>Rule</dt><dd>{f.rule}</dd>
                          {Object.entries(f.metadata || {}).map(([k, v]) => (
                            <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>
                          ))}
                        </dl>
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                          {DIRECT_FIX_RULES.has(f.rule) && (
                            <>
                              {directFixStatus[f.finding_id] === 'done' ? (
                                <span style={{ color: 'var(--success)', fontSize: 12 }}>
                                  ✓ Open rule revoked — rescanning…
                                </span>
                              ) : directFixStatus[f.finding_id]?.startsWith('error:') ? (
                                <span style={{ color: 'var(--error)', fontSize: 12 }}>
                                  ✗ {directFixStatus[f.finding_id].slice(6)}
                                </span>
                              ) : (
                                <button
                                  className="aca-btn-primary"
                                  style={{ fontSize: 12, padding: '4px 10px' }}
                                  disabled={directFixStatus[f.finding_id] === 'fixing'}
                                  onClick={() => handleDirectFix(f)}
                                >
                                  {directFixStatus[f.finding_id] === 'fixing' ? '…Revoking' : '⚡ Direct Fix'}
                                </button>
                              )}
                            </>
                          )}
                          <button className="aca-btn-ghost small" onClick={() => onFix(f)}>
                            Fix with Terraform →
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
