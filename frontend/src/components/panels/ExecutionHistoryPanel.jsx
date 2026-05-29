import { useState, useEffect, Fragment } from 'react'
import { callTool } from '../../api/mcpClient'

export default function ExecutionHistoryPanel() {
  const [items,   setItems]   = useState(null)
  const [loading, setLoading] = useState(false)
  const [open,    setOpen]    = useState(null)

  const load = async () => {
    setLoading(true)
    try {
      const r = await callTool('get_execution_history_tool')
      setItems([...(r.executions ?? [])].reverse())
    } finally { setLoading(false) }
  }

  useEffect(() => { load() }, []) // eslint-disable-line

  const statusClass = (s) =>
    s === 'complete'          ? 'success' :
    s === 'rejected'          ? 'muted'   :
    s === 'awaiting_approval' ? 'warning' :
    s === 'failed' || s === 'plan_failed' ? 'error' : 'muted'

  return (
    <section id="execution-history-panel" className="aca-panel" style={{ gridColumn: 'span 12' }}>
      <div className="aca-panel-hd">
        <span className="aca-panel-title">Execution History</span>
        <button className="aca-btn-ghost small" onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>
      <div className="aca-panel-body" style={{ padding: 0, maxHeight: 420, overflowY: 'auto' }}>
        {loading && !items && (
          <div style={{ padding: 20 }}>
            {[0,1,2,3].map((i) => (
              <div key={i} className="aca-skel" style={{ height: 42, marginBottom: 8 }} />
            ))}
          </div>
        )}
        {items && items.length === 0 && (
          <div className="aca-empty" style={{ padding: 24 }}>No executions yet</div>
        )}
        {items && items.length > 0 && (
          <table className="aca-table">
            <thead>
              <tr>
                <th style={{ width: 180 }}>Timestamp</th>
                <th style={{ width: 140 }}>Status</th>
                <th>Description</th>
                <th style={{ width: 140 }}>Changes</th>
                <th style={{ width: 30 }}></th>
              </tr>
            </thead>
            <tbody>
              {items.map((x) => {
                const id     = x.execution_id
                const isOpen = open === id
                const add    = x.resources_to_add    ?? 0
                const mod    = x.resources_to_change ?? 0
                const del    = x.resources_to_destroy ?? 0
                const desc   = x.description ?? ''
                return (
                  <Fragment key={id}>
                    <tr className="expandable" onClick={() => setOpen(isOpen ? null : id)}>
                      <td className="mono" style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                        {x.timestamp ? new Date(x.timestamp).toLocaleString() : '—'}
                      </td>
                      <td>
                        <span className={'aca-badge ' + statusClass(x.status)}>
                          {(x.status ?? '').replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td style={{ color: 'var(--text-primary)' }}>
                        {desc.length > 60 ? desc.slice(0, 60) + '…' : desc}
                      </td>
                      <td className="mono" style={{ fontSize: 11 }}>
                        {add > 0 && <span style={{ color: 'var(--success)' }}>+{add}</span>}
                        {mod > 0 && <span style={{ color: 'var(--warning)', marginLeft: 8 }}>~{mod}</span>}
                        {del > 0 && <span style={{ color: 'var(--error)',   marginLeft: 8 }}>−{del}</span>}
                        {add === 0 && mod === 0 && del === 0 && <span style={{ color: 'var(--text-muted)' }}>—</span>}
                      </td>
                      <td style={{
                        color: 'var(--text-muted)',
                        transform: isOpen ? 'rotate(90deg)' : 'none',
                        transition: 'transform 0.15s',
                      }}>▸</td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={5} style={{ background: 'var(--bg-elevated)', padding: 16, overflow: 'hidden' }}>
                          <div style={{ color: 'var(--text-muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
                            Execution ID
                          </div>
                          <div className="mono" style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 12 }}>
                            {id}
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: x.apply_output ? '1fr 1fr' : '1fr', gap: 12, minWidth: 0 }}>
                            <div style={{ minWidth: 0 }}>
                              <div className="aca-panel-title" style={{ marginBottom: 6 }}>Plan output</div>
                              <pre className="aca-code" style={{ maxHeight: 200, margin: 0, overflowX: 'auto' }}>
                                {x.plan_output || '(no plan output)'}
                              </pre>
                            </div>
                            {x.apply_output && (
                              <div style={{ minWidth: 0 }}>
                                <div className="aca-panel-title" style={{ marginBottom: 6 }}>Apply output</div>
                                <pre className="aca-code" style={{
                                  maxHeight: 200, margin: 0, overflowX: 'auto',
                                  color: x.status === 'complete' ? 'var(--success)'
                                       : x.status === 'failed'   ? 'var(--error)'
                                       : 'var(--text-primary)',
                                }}>{x.apply_output}</pre>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
