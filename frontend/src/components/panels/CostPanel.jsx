import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { CHART_COLORS } from '../../utils/constants'
import DarkTooltip from '../ui/DarkTooltip'

export default function CostPanel({ data, loading, onLoad }) {
  const current  = data?.current_month?.amount  ?? 0
  const currency = data?.current_month?.currency ?? 'USD'
  const period   = data?.current_month?.period   ?? ''
  const trend    = data?.monthly_trend           ?? []
  const services = data?.by_service              ?? []
  const previous = trend.length >= 2 ? trend[trend.length - 2].amount : current
  const delta    = previous > 0 ? ((current - previous) / previous) * 100 : 0
  const deltaUp  = delta > 0
  const maxSvc   = services.length > 0 ? Math.max(...services.map((s) => s.amount)) : 1

  return (
    <section className="aca-panel" style={{ gridColumn: 'span 7' }}>
      <div className="aca-panel-hd">
        <span className="aca-panel-title">Cost</span>
        <button className="aca-btn-primary" onClick={onLoad} disabled={loading}>
          {loading ? 'Loading…' : data ? 'Refresh' : 'Load Data'}
        </button>
      </div>
      <div className="aca-panel-body">
        {!data && !loading && <div className="aca-empty">Load to view month-over-month spend</div>}
        {loading && <div className="aca-skel" style={{ height: 340 }} />}
        {data && !loading && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div className="aca-tile">
                <div className="aca-tile-value mono" style={{ color: 'var(--accent-blue)' }}>
                  ${current.toFixed(2)}
                </div>
                <div className="aca-tile-label">{currency} · {period}</div>
              </div>
              <div className="aca-tile">
                <div className="aca-tile-value mono" style={{ color: deltaUp ? 'var(--error)' : 'var(--success)' }}>
                  {deltaUp ? '↑' : '↓'} {Math.abs(delta).toFixed(1)}%
                </div>
                <div className="aca-tile-label">Month over month</div>
              </div>
            </div>

            {data.anomaly?.detected && (
              <div style={{
                background: 'var(--warning-dim)', border: '1px solid var(--warning)',
                borderRadius: 6, padding: '10px 14px', display: 'flex', gap: 10, alignItems: 'flex-start',
              }}>
                <span style={{ color: 'var(--warning)', fontSize: 14 }}>⚠</span>
                <div style={{ color: 'var(--warning)', fontSize: 12, lineHeight: 1.5 }}>
                  <strong style={{ fontWeight: 600 }}>{data.anomaly.service} anomaly detected.</strong>{' '}
                  {data.anomaly.note}
                </div>
              </div>
            )}

            <div style={{ height: 180 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="costFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%"   stopColor={CHART_COLORS.blue} stopOpacity={0.4} />
                      <stop offset="100%" stopColor={CHART_COLORS.blue} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="month" tick={{ fill: 'var(--text-secondary)', fontSize: 11 }}
                    axisLine={{ stroke: 'var(--border)' }} tickLine={false} />
                  <YAxis tick={{ fill: 'var(--text-secondary)', fontSize: 11 }}
                    axisLine={{ stroke: 'var(--border)' }} tickLine={false}
                    tickFormatter={(v) => '$' + v} width={52} />
                  <Tooltip content={<DarkTooltip prefix="$" />} />
                  <Area type="monotone" dataKey="amount"
                    stroke={CHART_COLORS.blue} strokeWidth={2} fill="url(#costFill)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            <div>
              <div className="aca-panel-title" style={{ marginBottom: 8 }}>Top services</div>
              {services.map((s) => (
                <div key={s.service} className="aca-bar-row">
                  <div className="name">{s.service}</div>
                  <div className="track"><div className="fill" style={{ width: (s.amount / maxSvc) * 100 + '%' }} /></div>
                  <div className="amount mono">${s.amount.toFixed(2)}</div>
                </div>
              ))}
            </div>

            <div className="aca-summary">{data.llm_summary}</div>
          </div>
        )}
      </div>
    </section>
  )
}
