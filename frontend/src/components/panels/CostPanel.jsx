import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { CHART_COLORS } from '../../utils/constants'
import { costView } from '../../utils/cost'
import DarkTooltip from '../ui/DarkTooltip'

export default function CostPanel({ data, loading, onLoad }) {
  const { amount: current, currency, percentage: delta, comparisonLabel } = costView(data)
  const period = data?.current_month?.period || ''
  const trend = data?.monthly_trend || []
  const services = data?.by_service || []
  const deltaUp = delta != null && delta > 0
  const maxSvc   = services.length > 0 ? Math.max(1, ...services.map((s) => Math.abs(s.amount))) : 1

  return (
    <section className="aca-panel" style={{ gridColumn: 'span 7' }}>
      <div className="aca-panel-hd">
        <span className="aca-panel-title">Cost</span>
        <button className="aca-btn-primary" onClick={onLoad} disabled={loading}>
          {loading ? 'Loading…' : data ? 'Refresh' : 'Load Data'}
        </button>
      </div>
      <div className="aca-panel-body">
        {!data && !loading && <div className="aca-empty">Load to view month-to-date spend and completed-month comparisons</div>}
        {loading && <div className="aca-skel" style={{ height: 340 }} />}
        {data && !loading && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div className="aca-tile">
                <div className="aca-tile-value mono" style={{ color: 'var(--accent-blue)' }}>
                  {current == null ? 'Unavailable' : current.toFixed(2)}
                </div>
                <div className="aca-tile-label">Month to date · {currency} · {period}{data.current_month?.estimated && ' · Estimated'}</div>
              </div>
              <div className="aca-tile">
                <div className="aca-tile-value mono" style={{ color: delta == null ? 'var(--text-muted)' : deltaUp ? 'var(--error)' : 'var(--success)' }}>
                  {delta == null ? 'Unavailable' : `${deltaUp ? '↑' : delta < 0 ? '↓' : '→'} ${Math.abs(delta).toFixed(1)}%`}
                </div>
                <div className="aca-tile-label">{comparisonLabel} · completed months{data.comparison?.estimated && ' · Estimated'}</div>
              </div>
            </div>

            {data.anomaly?.detected && (
              <div style={{
                background: 'var(--warning-dim)', border: '1px solid var(--warning)',
                borderRadius: 6, padding: '10px 14px', display: 'flex', gap: 10, alignItems: 'flex-start',
              }}>
                <span style={{ color: 'var(--warning)', fontSize: 14 }}>⚠</span>
                <div style={{ color: 'var(--warning)', fontSize: 12, lineHeight: 1.5 }}>
                  <strong style={{ fontWeight: 600 }}>Completed-month spend increase.</strong>{' '}
                  {data.anomaly.message}
                </div>
              </div>
            )}

            {Object.entries(data.errors || {}).map(([part, message]) => <p key={part} role="status">{part.replaceAll('_', ' ')}: {message}</p>)}
            {delta == null && <p role="status">{data.comparison?.message || 'No comparable completed-month data.'}</p>}
            <div className="aca-panel-title">Completed calendar months · {trend[0]?.currency || 'currency unavailable'}</div>
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
                    tickFormatter={(v) => String(v)} width={52} />
                  <Tooltip content={<DarkTooltip />} />
                  <Area type="monotone" dataKey="amount"
                    stroke={CHART_COLORS.blue} strokeWidth={2} fill="url(#costFill)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            <div>
              <div className="aca-panel-title" style={{ marginBottom: 8 }}>Top services · month to date</div>
              {services.map((s) => (
                <div key={s.service} className="aca-bar-row">
                  <div className="name">{s.service}</div>
                  <div className="track"><div className="fill" style={{ width: Math.abs(s.amount) / maxSvc * 100 + '%', background: s.amount < 0 ? 'var(--success)' : undefined }} /></div>
                  <div className="amount mono">{s.amount.toFixed(2)} {s.currency}</div>
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
