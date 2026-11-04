export default function ScanCoverage({ data }) {
  const coverage = data?.coverage
  const complete = coverage?.status === 'complete'
  return (
    <div role="status" className="aca-summary" style={{ marginBottom: 12 }}>
      <strong>{complete ? 'Completed built-in checks' : `Incomplete inspection (${coverage?.status || 'unknown'})`}</strong>
      {!complete && <div>Uninspected resources and unknown details may contain additional issues.</div>}
      {data?.completed_at && <div>Completed at {data.completed_at}</div>}
      <details>
        <summary>Inspection coverage and errors</summary>
        {Object.entries(coverage?.services || {}).map(([name, service]) => (
          <div key={name} style={{ marginTop: 8 }}>
            <strong>{name}: {service.status}</strong>
            <div>{service.inventory_complete ? 'Inventory collected' : 'Inventory incomplete'} · {service.observed_count ?? 'Unknown'} observed</div>
            {service.scope && <div>{service.scope}</div>}
            {(service.errors || []).map((error, i) => <div key={i}>
              {[error.resource_id, error.operation, error.code, error.message].filter(Boolean).join(' · ')}
            </div>)}
          </div>
        ))}
        {Object.entries(coverage?.rules || {}).filter(([, rule]) => rule.unknown_resources?.length).map(([name, rule]) => (
          <div key={name}>{name}: unknown for {rule.unknown_resources.join(', ')}</div>
        ))}
      </details>
    </div>
  )
}
