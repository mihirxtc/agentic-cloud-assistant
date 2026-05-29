import {
  BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { CHART_COLORS } from '../../utils/constants'
import DarkTooltip from '../ui/DarkTooltip'

export default function InfrastructurePanel({ data, loading, onScan, lastRefresh }) {
  const tiles = data ? [
    { key: 'EC2', value: data.ec2?.count              ?? 0, color: CHART_COLORS.blue },
    { key: 'S3',  value: data.s3?.count               ?? 0, color: CHART_COLORS.success },
    { key: 'IAM', value: data.iam?.user_count          ?? 0, color: CHART_COLORS.warning },
    { key: 'SG',  value: data.security_groups?.count   ?? 0, color: CHART_COLORS.error },
    { key: 'VPC', value: data.vpc?.count               ?? 0, color: '#a78bfa' },
  ] : []
  const barData    = tiles.map((t) => ({ name: t.key, value: t.value, fill: t.color }))
  const tileLabels = { EC2: 'EC2 Instances', S3: 'S3 Buckets', IAM: 'IAM Users', SG: 'Security Groups', VPC: 'VPCs' }

  return (
    <section className="aca-panel" style={{ gridColumn: 'span 5' }}>
      <div className="aca-panel-hd">
        <div className="aca-row" style={{ gap: 8 }}>
          <span className="aca-panel-title">Infrastructure</span>
          {data && (
            <span className="aca-badge muted mono">
              {(data.ec2?.count ?? 0) + (data.s3?.count ?? 0) + (data.iam?.user_count ?? 0) + (data.security_groups?.count ?? 0) + (data.vpc?.count ?? 0)} resources
            </span>
          )}
        </div>
        <div className="aca-row" style={{ gap: 10, alignItems: 'center' }}>
          {lastRefresh && !loading && (
            <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
              {lastRefresh.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
          <button className="aca-btn-ghost small" onClick={onScan} disabled={loading}>
            {loading ? 'Scanning…' : 'Refresh Scan'}
          </button>
        </div>
      </div>
      <div className="aca-panel-body">
        {!data && !loading && <div className="aca-empty">Run a scan to load infrastructure data</div>}
        {loading && (
          <div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
              {[0,1,2,3,4].map((i) => <div key={i} className="aca-skel" style={{ height: 82 }} />)}
            </div>
            <div className="aca-skel" style={{ height: 160, marginTop: 12 }} />
          </div>
        )}
        {data && !loading && (
          <div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
              {tiles.map((t) => (
                <div key={t.key} className="aca-tile">
                  <div className="aca-tile-value mono">{t.value}</div>
                  <div className="aca-tile-label">
                    <span style={{ width: 6, height: 6, borderRadius: 1, background: t.color, display: 'inline-block' }} />
                    {tileLabels[t.key]}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ height: 160, marginTop: 14 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={barData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="name" tick={{ fill: 'var(--text-secondary)', fontSize: 11 }}
                    axisLine={{ stroke: 'var(--border)' }} tickLine={false} />
                  <YAxis tick={{ fill: 'var(--text-secondary)', fontSize: 11 }}
                    axisLine={{ stroke: 'var(--border)' }} tickLine={false} width={30} />
                  <Tooltip content={<DarkTooltip />} cursor={{ fill: 'var(--bg-hover)' }} />
                  <Bar dataKey="value" radius={[3, 3, 0, 0]}>
                    {barData.map((d, i) => <Cell key={i} fill={d.fill} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
