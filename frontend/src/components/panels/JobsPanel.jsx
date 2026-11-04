import { useState, useEffect } from 'react'
import { listJobs, getJob, cancelJob } from '../../api/jobs'
import { callTool } from '../../api/mcpClient'
import ActionReview from '../ui/ActionReview'

export default function JobsPanel() {
  const [jobs, setJobs] = useState([])
  const [selected, setSelected] = useState(null)
  const [evidence, setEvidence] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let disposed = false
    let timer
    const refresh = async () => {
      try {
        const result = await listJobs()
        if (!disposed) { setJobs(result.jobs); setError('') }
      } catch (error) { if (!disposed) setError(error.message) }
      if (!disposed) timer = setTimeout(refresh, 3000)
    }
    refresh()
    return () => { disposed = true; clearTimeout(timer) }
  }, [])
  const act = async (task) => {
    setBusy(true); setError('')
    try { await task() } catch (error) { setError(error.message) }
    finally { setBusy(false) }
  }
  const inspect = async (job) => {
    setEvidence(null)
    const detail = await getJob(job.job_id)
    setSelected(detail)
  }
  return <section className="aca-panel" style={{ gridColumn: 'span 12' }}>
    <div className="aca-panel-hd"><span className="aca-panel-title">Background Jobs</span></div>
    <div className="aca-panel-body">
      <p>Jobs continue when you leave this page. Progress shows actual backend stages; interrupted changes require review.</p>
      {error && <p role="alert">{error}</p>}
      {!jobs.length && <p>No background jobs yet.</p>}
      {jobs.map((job) => <div key={job.job_id} style={{ borderTop: '1px solid var(--border)', padding: '10px 0' }}>
        <strong>{job.operation === '_approved_action' ? 'Approved infrastructure change' : job.operation.replaceAll('_', ' ')}</strong>
        <div>{job.aws_identity?.account_id} · {job.aws_identity?.region} · {job.status.replaceAll('_', ' ')}</div>
        <div role="status">{job.stage} · {new Date(job.updated_at * 1000).toLocaleString()}</div>
        <button className="aca-btn-ghost small" disabled={busy} onClick={() => act(() => inspect(job))}>View progress and result</button>
        {job.status === 'queued' && <button className="aca-btn-ghost small" disabled={busy} onClick={() => act(async () => { await cancelJob(job.job_id); setJobs((await listJobs()).jobs) })}>Cancel queued job</button>}
      </div>)}
      {selected && <div role="region" aria-label="Job detail" className="aca-summary">
        <strong>{selected.job_id} · {selected.status}</strong>
        {(selected.events || []).map((event, i) => <div key={i}>{new Date(event.at * 1000).toLocaleTimeString()} · {event.stage}</div>)}
        <button className="aca-btn-ghost small" disabled={busy} onClick={() => act(() => inspect(selected))}>Refresh detail</button>
        <button className="aca-btn-ghost small" onClick={() => { setSelected(null); setEvidence(null) }}>Close</button>
        {selected.error && <p>{selected.error}</p>}
        {selected.result?.status === 'awaiting_approval' && <ActionReview key={selected.result.execution_id} proposal={selected.result} onComplete={() => act(() => inspect(selected))} />}
        {selected.result && <details><summary>Saved result</summary><pre className="aca-code" style={{ maxHeight: 360, overflow: 'auto' }}>{JSON.stringify(selected.result, null, 2)}</pre></details>}
        {selected.execution_id && ['reconciliation_required', 'interrupted', 'failed'].includes(selected.status) && <button className="aca-btn-ghost small" disabled={busy} onClick={() => act(async () => setEvidence(await callTool('inspect_interrupted_action', { execution_id: selected.execution_id, aws_region: selected.aws_identity?.region })))}>Inspect execution outcome</button>}
        {evidence && <div><p>{evidence.message}</p><details><summary>Outcome evidence</summary><pre className="aca-code" style={{ maxHeight: 300, overflow: 'auto' }}>{JSON.stringify(evidence, null, 2)}</pre></details>
          {evidence.recoverable && <button className="aca-btn-primary" disabled={busy} onClick={() => act(async () => {
            await callTool('reconcile_action', { execution_id: evidence.execution_id, evidence_digest: evidence.evidence_digest, aws_region: evidence.aws_identity.region })
            await inspect(selected)
          })}>Confirm reviewed completion</button>}
        </div>}
      </div>}
    </div>
  </section>
}
